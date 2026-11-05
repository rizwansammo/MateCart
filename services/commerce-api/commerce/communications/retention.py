"""Conversation retention (D-096). Runs hourly from the beat; every step is idempotent.

- Closed conversations inactive for ``MATECART_CONVERSATION_RETENTION_DAYS`` (default 365) and
  guest website conversations inactive for ``MATECART_GUEST_CHAT_RETENTION_DAYS`` (default 180)
  are redacted: message text, product snapshots, provider message ids, attachment files and
  internal notes are deleted. The rows stay, so counts, response times and order links remain
  as business evidence. Linked orders keep their own records under their own rules.
- A participant whose conversations are all redacted loses their display name and phone.
- Expired guest chat credentials are cleared.
- Delivery attempts older than 90 days are deleted; the message keeps its final state.
- Provider tokens are erased the moment a channel is disconnected (``connections.disconnect``).

This is a data-minimisation control, not a claim of compliance with any particular law.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    AttachmentStatus,
    ChannelType,
    Conversation,
    ConversationNote,
    ConversationStatus,
    DeliveryAttempt,
    Message,
    MessageAttachment,
    Participant,
)

ATTEMPT_RETENTION = timedelta(days=90)
BATCH = 200


def _due(now: datetime) -> list[uuid.UUID]:
    closed_before = now - timedelta(days=settings.MATECART_CONVERSATION_RETENTION_DAYS)
    guest_before = now - timedelta(days=settings.MATECART_GUEST_CHAT_RETENTION_DAYS)
    rows = Conversation.objects.filter(redacted_at__isnull=True).filter(
        Q(status=ConversationStatus.CLOSED, closed_at__lt=closed_before)
        | Q(
            channel=ChannelType.WEBSITE,
            participant__customer__isnull=True,
            last_message_at__lt=guest_before,
        )
    )
    return list(rows.values_list("pk", flat=True)[:BATCH])


def redact(conversation_id: uuid.UUID | str, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    files: list[tuple[object, str]] = []
    with transaction.atomic():
        conversation = (
            Conversation.objects.select_for_update()
            .filter(pk=conversation_id, redacted_at__isnull=True)
            .first()
        )
        if conversation is None:
            return False
        for attachment in MessageAttachment.objects.select_for_update().filter(
            message__conversation=conversation
        ):
            if attachment.file:
                files.append((attachment.file.storage, attachment.file.name))
            attachment.file = ""
            attachment.status = AttachmentStatus.REMOVED
            attachment.filename = ""
            attachment.source_url = ""
            attachment.provider_media_id = ""
            attachment.size_bytes = 0
            attachment.save()
        Message.objects.filter(conversation=conversation).update(
            body="", product_snapshot={}, external_id="", client_key=""
        )
        ConversationNote.objects.filter(conversation=conversation).delete()
        if conversation.status != ConversationStatus.CLOSED:
            conversation.status = ConversationStatus.CLOSED
            conversation.closed_at = now
        conversation.last_preview = ""
        conversation.redacted_at = now
        conversation.save()
        participant = Participant.objects.select_for_update().get(pk=conversation.participant_id)
        if not Conversation.objects.filter(
            participant=participant, redacted_at__isnull=True
        ).exists():
            participant.display_name = ""
            participant.phone = ""
            participant.token_hash = ""
            participant.token_expires_at = None
            participant.save(
                update_fields=["display_name", "phone", "token_hash", "token_expires_at"]
            )

        def remove() -> None:
            for storage, name in files:
                storage.delete(name)  # type: ignore[attr-defined]

        transaction.on_commit(remove)
    return True


def apply(now: datetime | None = None) -> dict[str, int]:
    now = now or timezone.now()
    redacted = sum(1 for pk in _due(now) if redact(pk, now))
    expired = (
        Participant.objects.filter(channel=ChannelType.WEBSITE, token_expires_at__lt=now)
        .exclude(token_hash="")
        .update(token_hash="", token_expires_at=None)
    )
    attempts, _ = DeliveryAttempt.objects.filter(created_at__lt=now - ATTEMPT_RETENTION).delete()
    return {"redacted": redacted, "guest_tokens_cleared": expired, "attempts_deleted": attempts}
