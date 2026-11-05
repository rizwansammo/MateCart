"""Processing verified provider webhooks (D-094).

The request already runs in this deployment's tenant context (configuration only, never the
payload). Each event is routed to a live connection of this store by the provider account id it
names (Page id, Instagram account id or WhatsApp phone number id). An unknown account is ignored:
nothing in the payload can choose a tenant, a connection or a customer account.

Replays are harmless: inbound messages are unique per connection and provider id, delivery
receipts only move states forward, and media downloads are claimed once.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from commerce.tenancy.context import get_active_tenant_id

from . import meta, services, tasks
from .models import (
    AttachmentStatus,
    ChannelConnection,
    ConnectionStatus,
    Message,
    MessageAttachment,
    MessageKind,
    Participant,
)

logger = logging.getLogger(__name__)


@dataclass
class Result:
    stored: int = 0
    duplicates: int = 0
    statuses: int = 0
    ignored: int = 0


def _connection(channel: str, account_id: str) -> ChannelConnection | None:
    if not account_id:
        return None
    return ChannelConnection.objects.filter(
        channel=channel, external_account_id=account_id, disconnected_at__isnull=True
    ).first()


def _participant(connection: ChannelConnection, event: meta.InboundEvent) -> Participant:
    row = Participant.objects.filter(connection=connection, external_id=event.sender_id).first()
    if row is None:
        try:
            with transaction.atomic():
                row = Participant.objects.create(
                    channel=connection.channel,
                    connection=connection,
                    external_id=event.sender_id,
                    display_name=event.sender_name[:120],
                    phone=f"+{event.sender_id}"
                    if connection.channel == "whatsapp" and event.sender_id.isdigit()
                    else "",
                )
        except IntegrityError:
            row = Participant.objects.get(connection=connection, external_id=event.sender_id)
    elif event.sender_name and row.display_name != event.sender_name[:120]:
        # The provider's profile name is display data only; it never identifies an account.
        Participant.objects.filter(pk=row.pk).update(display_name=event.sender_name[:120])
        row.display_name = event.sender_name[:120]
    return row


def _store_inbound(connection: ChannelConnection, event: meta.InboundEvent, result: Result) -> None:
    participant = _participant(connection, event)
    kind = {
        "text": MessageKind.TEXT,
        "image": MessageKind.IMAGE,
        "document": MessageKind.DOCUMENT,
    }.get(event.kind, MessageKind.UNSUPPORTED)
    body = event.text[:4096]
    media = event.media
    pending: list[object] = []

    def attach(message: Message) -> None:
        if media is None or kind not in (MessageKind.IMAGE, MessageKind.DOCUMENT):
            return
        has_source = bool(media.media_id or media.url)
        attachment = MessageAttachment.objects.create(
            message=message,
            status=AttachmentStatus.PENDING if has_source else AttachmentStatus.FAILED,
            filename="".join(ch for ch in media.filename if ch.isprintable())[:120],
            provider_media_id=media.media_id[:128],
            source_url=media.url[:2000],
            error_code="" if has_source else "media_missing",
        )
        if has_source:
            pending.append(attachment.pk)

    with transaction.atomic():
        _, created = services.receive_inbound(
            participant=participant,
            kind=kind,
            body=body,
            external_id=event.message_id,
            provider_timestamp=event.timestamp,
            before_publish=attach,
        )
        tenant = str(get_active_tenant_id())
        for attachment_id in pending:
            transaction.on_commit(
                functools.partial(tasks.enqueue_media, attachment_id, tenant_id=tenant)
            )
    if created:
        result.stored += 1
    else:
        result.duplicates += 1


def _apply_status(connection: ChannelConnection, event: meta.StatusEvent, result: Result) -> None:
    result.statuses += services.apply_status(
        connection=connection,
        status=event.status,
        message_ids=event.message_ids,
        watermark=event.watermark,
        recipient_id=event.recipient_id,
        error_code=event.error_code,
        pricing_category=event.pricing_category,
    )


def _account(event: meta.AccountEvent) -> None:
    # WhatsApp account updates name the WhatsApp Business Account id.
    ChannelConnection.objects.filter(
        channel=event.channel,
        business_account_id=event.account_id,
        disconnected_at__isnull=True,
    ).update(
        status=ConnectionStatus.SUSPENDED,
        last_error="provider_restriction",
        updated_at=timezone.now(),
    )


def process(payload: Mapping[str, Any]) -> Result:
    """Apply a verified webhook body. Raises on database errors so the provider retries."""
    result = Result()
    for event in meta.parse_webhook(payload):
        if isinstance(event, meta.AccountEvent):
            _account(event)
            continue
        connection = _connection(event.channel, event.account_id)
        if connection is None:
            result.ignored += 1
            continue
        # A switched-off channel still records what customers send (nothing verified is lost);
        # only sending stops (policy.reply_policy).
        if isinstance(event, meta.InboundEvent):
            _store_inbound(connection, event, result)
        else:
            _apply_status(connection, event, result)
    return result
