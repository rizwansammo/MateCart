"""Uploading, describing and deleting media files (D-055).

Validation happens on the server, from the bytes: the declared Content-Type and filename are
ignored. Images must decode completely as JPEG, PNG or WebP within size and pixel limits (which
also stops decompression bombs); documents must be PDF. The stored name is generated
(``tenant_storage_path``), so no client text reaches the filesystem.
"""

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import IO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import ProtectedError
from PIL import Image, UnidentifiedImageError

from commerce.audit.services import field_changes, record_audit_event
from commerce.core import ratelimit
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User
from commerce.tenancy.context import get_active_tenant_id

from .models import CONTENT_TYPES, MediaAsset, MediaKind

MAX_IMAGE_SIDE = 8000
MAX_IMAGE_PIXELS = 25_000_000
UPLOADS_PER_HOUR = 300
_PIL_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class UnsupportedMedia(DomainError):
    default_detail = "Upload a JPEG, PNG or WebP image, or a PDF document."
    default_code = "unsupported_media"


@dataclass(frozen=True)
class Inspected:
    content_type: str
    width: int | None
    height: int | None


def _inspect_image(data: bytes) -> Inspected:
    try:
        with warnings.catch_warnings():
            # Pillow warns (rather than fails) just below its bomb limit: treat that as a refusal.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                content_type = _PIL_FORMATS.get(probe.format or "")
                width, height = probe.size
                if content_type is None:
                    raise UnsupportedMedia()
                if (
                    width > MAX_IMAGE_SIDE
                    or height > MAX_IMAGE_SIDE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise UnsupportedMedia(
                        f"Images can be at most {MAX_IMAGE_SIDE} by {MAX_IMAGE_SIDE} pixels.",
                        code="image_too_large",
                    )
                probe.verify()
            # verify() leaves the image unusable; decode a fresh copy completely to catch
            # truncated or corrupt pixel data.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except UnsupportedMedia:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise UnsupportedMedia() from exc
    return Inspected(content_type, width, height)


def inspect(data: bytes) -> Inspected:
    """The verified type and dimensions of ``data``, or UnsupportedMedia."""
    if data.startswith(b"%PDF-") and b"%%EOF" in data[-1024:]:
        return Inspected("application/pdf", None, None)
    return _inspect_image(data)


def _read_limited(stream: IO[bytes], limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise DomainError(
            f"Files can be at most {limit // (1024 * 1024)} MB.", code="file_too_large"
        )
    if not data:
        raise UnsupportedMedia()
    return data


def upload(*, stream: IO[bytes], alt_text: str = "", actor: User | None) -> MediaAsset:
    ratelimit.consume(
        "media-upload", str(get_active_tenant_id()), limit=UPLOADS_PER_HOUR, window=3600
    )
    data = _read_limited(stream, settings.MATECART_MEDIA_MAX_BYTES)
    found = inspect(data)
    kind, extension = CONTENT_TYPES[found.content_type]
    asset = MediaAsset(
        tenant_id=get_active_tenant_id(),  # before the file is stored: it names the folder
        kind=kind,
        content_type=found.content_type,
        size_bytes=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        width=found.width,
        height=found.height,
        alt_text=alt_text.strip()[:300],
        uploaded_by=actor,
    )
    # The generated storage name keeps only this extension, derived from the detected type.
    asset.file.save(f"upload{extension}", ContentFile(data), save=False)
    try:
        with transaction.atomic():
            asset.save()
            record_audit_event(
                action="media.asset.uploaded",
                resource=asset,
                actor=actor,
                changes={
                    "kind": asset.kind,
                    "content_type": asset.content_type,
                    "size_bytes": asset.size_bytes,
                },
            )
    except BaseException:
        asset.file.delete(save=False)
        raise
    return asset


def update(*, asset: MediaAsset, alt_text: str, actor: User | None) -> MediaAsset:
    with transaction.atomic():
        locked = MediaAsset.objects.select_for_update().get(pk=asset.pk)
        before = {"alt_text": locked.alt_text}
        locked.alt_text = alt_text.strip()[:300]
        locked.save(update_fields=["alt_text", "updated_at"])
        record_audit_event(
            action="media.asset.updated",
            resource=locked,
            actor=actor,
            changes=field_changes(before, {"alt_text": locked.alt_text}, fields=["alt_text"]),
        )
    return locked


def delete(*, asset: MediaAsset, actor: User | None) -> None:
    """Delete an asset nothing references. The file is removed only after the row is gone."""
    with transaction.atomic():
        locked = MediaAsset.objects.select_for_update().get(pk=asset.pk)
        name = locked.file.name
        record_audit_event(
            action="media.asset.deleted",
            resource=locked,
            actor=actor,
            changes={"kind": locked.kind, "content_type": locked.content_type},
        )
        try:
            locked.delete()
        except ProtectedError as exc:
            raise ConflictError(
                "This file is still used by a product, category or collection.",
                code="media_in_use",
            ) from exc
        storage = locked.file.storage
        transaction.on_commit(lambda: storage.delete(name))


def is_image(asset: MediaAsset) -> bool:
    return asset.kind == MediaKind.IMAGE
