"""Background work: outbound delivery, media downloads, sweeps and retention (D-094, D-096).

Every task is idempotent (tasks are acknowledged late and may run twice):

- Delivery claims a message by moving it from ``queued`` to ``sending`` under its row lock;
  a second run finds it already claimed and does nothing.
- Everything a delivery needs is read from the message row, never from task arguments: a task
  re-queued by the sweep after a broker outage, or retried later, sends exactly the stored
  template values (D-099). They are cleared once the message is settled.
- Under the same lock the messaging policy is checked again (``policy.delivery_check``): a
  message that is no longer allowed (window ended, opt-out, closed, channel off, template no
  longer approved) is failed with that reason and never sent or retried.
- A definite refusal fails the message with its reason; ``not_sent`` and rate limits retry a
  bounded number of times; anything that may have reached the provider becomes ``unknown`` and
  is never resent automatically (Meta's send APIs have no idempotency key).
- A message stuck in ``sending`` (worker lost mid-call) also becomes ``unknown``.
- There is no failover to another channel: a failed WhatsApp message never becomes SMS or email.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from commerce.media import services as media
from commerce.tenancy.context import get_active_tenant_id
from commerce.tenancy.tasks import TenantTask

from . import meta, realtime
from .models import (
    AttachmentStatus,
    ChannelConnection,
    ConnectionStatus,
    Conversation,
    DeliveryAttempt,
    DeliveryStatus,
    Message,
    MessageAttachment,
    MessageKind,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
RETRY_DELAYS = (60, 300, 900)
STUCK_SENDING = timedelta(minutes=10)
STALE_QUEUED = timedelta(minutes=2)
CONNECTION_STATES = {
    "revoked": ConnectionStatus.REVOKED,
    "permission": ConnectionStatus.PERMISSION_MISSING,
    "suspended": ConnectionStatus.SUSPENDED,
}


def enqueue_delivery(message_id: Any, *, tenant_id: str = "") -> None:
    """Queue delivery of a stored, queued message. Callers registering an on-commit callback
    pass ``tenant_id`` captured at registration, so the callback never depends on the context
    it happens to run in."""
    try:
        deliver_message.delay(str(message_id), tenant_id=tenant_id or str(get_active_tenant_id()))
    except Exception:  # broker down: the sweep re-queues queued messages
        logger.warning("Delivery not queued (message=%s); the sweep will retry", message_id)


def _publish_delivery(message: Message) -> None:
    realtime.send_now(
        realtime.staff_conversation_group(message.conversation_id),
        {
            "type": "delivery",
            "message": str(message.pk),
            "status": message.delivery_status,
            "error_code": message.error_code,
        },
    )


def _finish(message: Message, status: str, *, code: str = "", external_id: str = "") -> None:
    message.delivery_status = status
    message.error_code = code[:60]
    message.status_changed_at = timezone.now()
    fields = ["delivery_status", "error_code", "status_changed_at"]
    if status != DeliveryStatus.QUEUED and message.template_parameters:
        message.template_parameters = []  # settled: the values are no longer needed
        fields.append("template_parameters")
    if external_id:
        message.external_id = external_id
        fields.append("external_id")
    message.save(update_fields=fields)


def _mark_connection(connection: ChannelConnection, kind: str, code: str) -> None:
    state = CONNECTION_STATES.get(kind)
    if state is None:
        return
    ChannelConnection.objects.filter(pk=connection.pk, disconnected_at__isnull=True).update(
        status=state, last_error=code[:60], updated_at=timezone.now()
    )


@shared_task(base=TenantTask, name="communications.deliver_message")
def deliver_message(
    message_id: str, template_parameters: Sequence[str] | None = None, *, tenant_id: str
) -> str:
    """Deliver one queued message. ``template_parameters`` is read only from tasks queued
    before Phase 14.1 (they carried the values as an argument); the stored values win."""
    from . import policy

    with transaction.atomic():
        message = (
            Message.objects.select_for_update()
            .filter(pk=message_id, delivery_status=DeliveryStatus.QUEUED)
            .first()
        )
        if message is None:
            return "skipped"
        conversation = Conversation.objects.select_related("participant").get(
            pk=message.conversation_id
        )
        connection = (
            ChannelConnection.objects.filter(pk=message.connection_id).first()
            if message.connection_id
            else None
        )
        fields = ["delivery_status", "attempts", "status_changed_at"]
        parameters = [str(p) for p in message.template_parameters]
        if message.kind == MessageKind.TEMPLATE and not parameters and template_parameters:
            parameters = [str(p) for p in template_parameters]
            message.template_parameters = parameters  # retries and the sweep keep them
            fields.append("template_parameters")
        decision = policy.delivery_check(message, conversation, connection, parameters)
        message.attempts += 1
        attempt = message.attempts
        if not decision.allowed:
            # Permanently refused by policy: recorded, failed, never sent or retried.
            message.save(update_fields=["attempts"])
            DeliveryAttempt.objects.create(
                message=message, attempt=attempt, outcome="blocked", error_code=decision.reason
            )
            _finish(message, DeliveryStatus.FAILED, code=decision.reason)
        else:
            message.delivery_status = DeliveryStatus.SENDING
            message.status_changed_at = timezone.now()
            if message.kind != MessageKind.TEMPLATE and message.reply_tag != decision.tag:
                message.reply_tag = decision.tag  # the tag actually used for this send
                fields.append("reply_tag")
            message.save(update_fields=fields)
    if not decision.allowed or connection is None:  # delivery_check refuses a missing one
        _publish_delivery(message)
        return "blocked"
    outcome, code, external_id = "accepted", "", ""
    recipient = conversation.participant.external_id
    try:
        if message.kind == MessageKind.TEMPLATE:
            external_id = meta.send_template(
                connection,
                recipient,
                name=message.template_name,
                language=message.template_language,
                parameters=parameters,
            )
        else:
            external_id = meta.send_text(connection, recipient, message.body, tag=decision.tag)
    except meta.GraphError as exc:
        code = exc.code
        if exc.kind == "unknown":
            outcome = "unknown"
        elif exc.retryable and attempt < MAX_ATTEMPTS:
            outcome = "retry"
        else:
            outcome = "failed"
            _mark_connection(connection, exc.kind, exc.code)
    except Exception:
        logger.exception("Unexpected delivery error (message=%s)", message_id)
        outcome, code = "unknown", "unexpected_error"
    with transaction.atomic():
        message = Message.objects.select_for_update().get(pk=message_id)
        DeliveryAttempt.objects.create(
            message=message, attempt=attempt, outcome=outcome, error_code=code
        )
        if message.delivery_status != DeliveryStatus.SENDING:
            return "superseded"
        if outcome == "accepted":
            _finish(message, DeliveryStatus.ACCEPTED, external_id=external_id)
        elif outcome == "retry":
            _finish(message, DeliveryStatus.QUEUED, code=code)
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            # Only the id travels: the retry reads the stored values and re-checks the policy.
            transaction.on_commit(
                lambda: deliver_message.apply_async(
                    (message_id,), {"tenant_id": tenant_id}, countdown=delay
                )
            )
        else:
            _finish(
                message,
                DeliveryStatus.UNKNOWN if outcome == "unknown" else DeliveryStatus.FAILED,
                code=code,
            )
    _publish_delivery(message)
    return outcome


def resend(*, message: Message, actor: Any) -> Message:
    """Staff deliberately retry a failed or unknown message. An unknown outcome may already have
    reached the customer, so the inbox asks for confirmation before calling this."""
    from commerce.audit.services import record_audit_event
    from commerce.core.domain import ConflictError

    from . import policy

    with transaction.atomic():
        locked = Message.objects.select_for_update().get(pk=message.pk)
        if locked.delivery_status not in (DeliveryStatus.FAILED, DeliveryStatus.UNKNOWN):
            raise ConflictError(
                "Only failed or unknown messages can be sent again.", "not_retryable"
            )
        if locked.kind == MessageKind.TEMPLATE:
            raise ConflictError("Send the template again from the composer.", "template_resend")
        conversation = Conversation.objects.select_related("participant").get(
            pk=locked.conversation_id
        )
        connection = (
            ChannelConnection.objects.filter(pk=locked.connection_id).first()
            if locked.connection_id
            else None
        )
        rules = policy.reply_policy(conversation, connection, conversation.participant)
        if not rules.can_reply:
            raise ConflictError(rules.message, rules.reason)
        before = locked.delivery_status
        locked.delivery_status = DeliveryStatus.QUEUED
        locked.reply_tag = rules.tag
        locked.status_changed_at = timezone.now()
        locked.save(update_fields=["delivery_status", "reply_tag", "status_changed_at"])
        record_audit_event(
            action="communications.message.resent",
            resource=locked,
            actor=actor,
            changes={"delivery_status": {"from": before, "to": "queued"}},
        )
        message_id, tenant = locked.pk, str(get_active_tenant_id())
        transaction.on_commit(lambda: enqueue_delivery(message_id, tenant_id=tenant))
    return locked


# --- Media ----------------------------------------------------------------------------------------


def enqueue_media(attachment_id: Any, *, tenant_id: str = "") -> None:
    try:
        fetch_attachment.delay(
            str(attachment_id), tenant_id=tenant_id or str(get_active_tenant_id())
        )
    except Exception:
        logger.warning("Media download not queued (attachment=%s)", attachment_id)


@shared_task(base=TenantTask, name="communications.fetch_attachment")
def fetch_attachment(attachment_id: str, *, tenant_id: str) -> str:
    row = (
        MessageAttachment.objects.select_related("message")
        .filter(pk=attachment_id, status=AttachmentStatus.PENDING)
        .first()
    )
    if row is None:
        return "skipped"
    connection = (
        ChannelConnection.objects.filter(pk=row.message.connection_id).first()
        if row.message.connection_id
        else None
    )
    limit = int(settings.MATECART_MEDIA_MAX_BYTES)
    try:
        if connection is None or not connection.can_send:
            raise meta.MediaRefused("connection_unavailable")
        if connection.is_simulated:
            raise meta.MediaRefused("simulated_media")
        token = ""
        url = row.source_url
        if row.provider_media_id:
            token = meta.connection_token(connection)
            url = meta.whatsapp_media_url(connection, row.provider_media_id)
        data = meta.download(url, token=token, limit=limit)
        found = media.inspect(data)
    except meta.MediaRefused as exc:
        return _attachment_failed(row, exc.code, AttachmentStatus.FAILED)
    except meta.GraphError as exc:
        return _attachment_failed(row, exc.code, AttachmentStatus.FAILED)
    except media.UnsupportedMedia:
        return _attachment_failed(row, "unsupported_type", AttachmentStatus.UNSUPPORTED)
    store_bytes(row, data, found)
    return "stored"


def store_bytes(row: MessageAttachment, data: bytes, found: media.Inspected) -> None:
    from commerce.media.models import CONTENT_TYPES

    _, extension = CONTENT_TYPES[found.content_type]
    row.file.save(f"attachment{extension}", ContentFile(data), save=False)
    try:
        with transaction.atomic():
            locked = MessageAttachment.objects.select_for_update().get(pk=row.pk)
            locked.file = row.file
            locked.status = AttachmentStatus.STORED
            locked.content_type = found.content_type
            locked.size_bytes = len(data)
            locked.checksum_sha256 = hashlib.sha256(data).hexdigest()
            locked.width, locked.height = found.width, found.height
            locked.source_url = ""
            locked.provider_media_id = ""
            locked.error_code = ""
            locked.save()
    except BaseException:
        row.file.delete(save=False)
        raise
    realtime.send_now(
        realtime.staff_conversation_group(row.message.conversation_id),
        {"type": "attachment", "message": str(row.message_id), "status": "stored"},
    )


def _attachment_failed(row: MessageAttachment, code: str, status: str) -> str:
    MessageAttachment.objects.filter(pk=row.pk, status=AttachmentStatus.PENDING).update(
        status=status,
        error_code=code[:40],
        source_url="",
        provider_media_id="",
        updated_at=timezone.now(),
    )
    return status


# --- Sweeps ---------------------------------------------------------------------------------------


@shared_task(base=TenantTask, name="communications.sweep_tenant")
def sweep_tenant(*, tenant_id: str) -> dict[str, int]:
    """Re-queue messages whose delivery task was lost and settle sends a dead worker left."""
    now = timezone.now()
    stuck = Message.objects.filter(
        delivery_status=DeliveryStatus.SENDING, status_changed_at__lt=now - STUCK_SENDING
    ).update(
        delivery_status=DeliveryStatus.UNKNOWN,
        error_code="worker_lost",
        status_changed_at=now,
        template_parameters=[],  # unknown is never resent automatically
    )
    queued = list(
        Message.objects.filter(
            delivery_status=DeliveryStatus.QUEUED, status_changed_at__lt=now - STALE_QUEUED
        ).values_list("pk", flat=True)[:200]
    )
    for pk in queued:
        enqueue_delivery(pk)
    pending = list(
        MessageAttachment.objects.filter(
            status=AttachmentStatus.PENDING, updated_at__lt=now - STALE_QUEUED
        ).values_list("pk", flat=True)[:100]
    )
    for pk in pending:
        enqueue_media(pk)
    return {"stuck": stuck, "queued": len(queued), "media": len(pending)}


@shared_task(base=TenantTask, name="communications.housekeeping_tenant")
def housekeeping_tenant(*, tenant_id: str) -> dict[str, int]:
    from . import retention, services

    closed = services.auto_close_resolved()
    return {"auto_closed": closed, **retention.apply()}


def _tenant() -> str | None:
    from commerce.tenancy.models import Tenant
    from commerce.tenancy.resolution import configured_tenant_id

    tenant_id = configured_tenant_id()
    if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
        return None
    return str(tenant_id)


@shared_task(name="communications.sweep")
def sweep() -> dict[str, int]:
    """Beat entry point (every minute)."""
    tenant_id = _tenant()
    return sweep_tenant(tenant_id=tenant_id) if tenant_id else {}


@shared_task(name="communications.housekeeping")
def housekeeping() -> dict[str, int]:
    """Beat entry point (hourly): auto-close and retention."""
    tenant_id = _tenant()
    return housekeeping_tenant(tenant_id=tenant_id) if tenant_id else {}
