"""Conversation files (D-096). Website chat accepts images (JPEG, PNG, WebP) and PDF documents.

Bytes are verified by the catalogue media inspector (``commerce.media.services.inspect``): the
declared type and the filename are ignored, executables and anything else are refused, images
must fully decode within size and pixel limits. Files are stored under ``tenant_storage_path``
with generated names and are served only through checked views: staff with ``messages.manage``,
or the visitor whose conversation contains the message. Nothing is public because a message
references it.
"""

from __future__ import annotations

import hashlib
import re
from typing import IO, Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, Http404
from rest_framework.exceptions import ValidationError

from commerce.core import ratelimit
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User
from commerce.media import services as media
from commerce.media.models import CONTENT_TYPES

from . import policy, services
from .models import (
    AttachmentStatus,
    ChannelType,
    Conversation,
    DeliveryStatus,
    Message,
    MessageAttachment,
    MessageDirection,
    MessageKind,
    Participant,
)

UPLOADS_PER_HOUR = 30
_NAME = re.compile(r"[^\w .()-]+", re.UNICODE)


def _display_name(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return _NAME.sub("", base).strip()[:120]


def _read(stream: IO[bytes]) -> tuple[bytes, media.Inspected]:
    data = media._read_limited(stream, int(settings.MATECART_MEDIA_MAX_BYTES))
    try:
        found = media.inspect(data)
    except media.UnsupportedMedia:
        raise DomainError(
            "Send a JPEG, PNG or WebP image, or a PDF document.", "unsupported_attachment"
        ) from None
    return data, found


def _attach(message: Message, data: bytes, found: media.Inspected, filename: str) -> None:
    _, extension = CONTENT_TYPES[found.content_type]
    row = MessageAttachment(
        message=message,
        status=AttachmentStatus.STORED,
        content_type=found.content_type,
        size_bytes=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        width=found.width,
        height=found.height,
        filename=filename,
    )
    row.file.save(f"attachment{extension}", ContentFile(data), save=False)
    storage, name = row.file.storage, row.file.name
    try:
        row.save()
    except BaseException:
        storage.delete(name)
        raise


def _kind(found: media.Inspected) -> str:
    return MessageKind.IMAGE if found.content_type.startswith("image/") else MessageKind.DOCUMENT


def visitor_upload(
    *, participant: Participant, stream: IO[bytes], filename: str, client_key: Any
) -> tuple[Message, bool]:
    key = services.clean_client_key(client_key)
    if not key:
        raise ValidationError({"client_key": ["A client key is required."]})
    ratelimit.consume(
        "chat-visitor-upload", str(participant.pk), limit=UPLOADS_PER_HOUR, window=3600
    )
    data, found = _read(stream)
    with transaction.atomic():
        message, created = services.receive_inbound(
            participant=participant,
            kind=_kind(found),
            body="",
            client_key=key,
            before_publish=lambda row: _attach(row, data, found, _display_name(filename)),
        )
    return Message.objects.select_related("attachment").get(pk=message.pk), created


def staff_upload(
    *,
    conversation: Conversation,
    actor: User,
    stream: IO[bytes],
    filename: str,
    client_key: Any,
) -> tuple[Message, bool]:
    if conversation.channel != ChannelType.WEBSITE:
        raise ConflictError(
            "Files can be sent in website chat. For other channels, share a product or a link.",
            "attachment_channel_unsupported",
        )
    key = services.clean_client_key(client_key)
    if not key:
        raise ValidationError({"client_key": ["A client key is required."]})
    ratelimit.consume("chat-staff-upload", str(actor.pk), limit=UPLOADS_PER_HOUR * 4, window=3600)
    data, found = _read(stream)
    with transaction.atomic():
        locked = (
            Conversation.objects.select_for_update()
            .select_related("participant")
            .get(pk=conversation.pk)
        )
        existing = Message.objects.filter(conversation=locked, client_key=key).first()
        if existing is not None:
            return existing, False
        rules = policy.reply_policy(locked, None, locked.participant)
        if not rules.can_reply:
            raise ConflictError(rules.message, rules.reason)
        message = services._append(
            locked,
            direction=MessageDirection.OUTBOUND,
            kind=_kind(found),
            body="",
            delivery_status=DeliveryStatus.ACCEPTED,
            sender_user=actor,
            client_key=key,
        )
        _attach(message, data, found, _display_name(filename))
        message = Message.objects.select_related("attachment", "sender_user").get(pk=message.pk)
        services._mark_read_locked(locked, actor)
        services._publish_message(locked, message)
    return message, True


def file_response(row: MessageAttachment) -> FileResponse:
    """Stream with headers that stop browsers from sniffing or executing the file."""
    if row.status != AttachmentStatus.STORED or not row.file:
        raise Http404
    try:
        handle = row.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    is_image = row.content_type.startswith("image/")
    extension = row.file.name.rsplit(".", 1)[-1] if "." in row.file.name else "bin"
    response = FileResponse(
        handle,
        content_type=row.content_type,
        as_attachment=not is_image,
        filename=f"{'image' if is_image else 'document'}.{extension}",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["Cache-Control"] = "private, no-store"
    return response
