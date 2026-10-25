from django.db import models
from django.db.models import Q

from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.keys import tenant_storage_path
from commerce.tenancy.models import TenantOwnedModel


class MediaKind(models.TextChoices):
    IMAGE = "image", "Image"
    DOCUMENT = "document", "Document"


# The only formats MateCart stores. The type is detected from the file's content, never taken from
# the client's Content-Type or filename. SVG and HTML are refused: they can carry scripts.
CONTENT_TYPES: dict[str, tuple[str, str]] = {
    "image/jpeg": (MediaKind.IMAGE, ".jpg"),
    "image/png": (MediaKind.IMAGE, ".png"),
    "image/webp": (MediaKind.IMAGE, ".webp"),
    "application/pdf": (MediaKind.DOCUMENT, ".pdf"),
}


class MediaAsset(TenantOwnedModel, TimestampedModel):
    """One uploaded file. The binary lives in storage under ``tenants/<tenant>/media/...``
    (``tenant_storage_path``); this row holds verified metadata only.

    Assets are referenced with PROTECT (product galleries, category and collection images, size
    guides), so a file still in use cannot be deleted. Files are never served from MEDIA_ROOT
    directly: the API streams them after checking staff access or publication.
    """

    file = models.FileField(upload_to=tenant_storage_path, max_length=255)
    kind = models.CharField(max_length=16, choices=MediaKind.choices)
    content_type = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    alt_text = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="uploaded_by", name="media_mediaasset_uploader_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=MediaKind.values), name="media_mediaasset_kind_valid"
            ),
            models.CheckConstraint(
                condition=Q(content_type__in=list(CONTENT_TYPES)),
                name="media_mediaasset_content_type_allowed",
            ),
            models.CheckConstraint(
                condition=Q(size_bytes__gt=0), name="media_mediaasset_size_positive"
            ),
            models.CheckConstraint(
                condition=Q(checksum_sha256__regex=r"^[0-9a-f]{64}$"),
                name="media_mediaasset_checksum_format",
            ),
            models.CheckConstraint(
                condition=(
                    Q(kind=MediaKind.IMAGE, width__gt=0, height__gt=0)
                    | Q(kind=MediaKind.DOCUMENT, width__isnull=True, height__isnull=True)
                ),
                name="media_mediaasset_dimensions_match_kind",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "-created_at"], name="media_asset_tenant_time")]

    def __str__(self) -> str:
        return f"{self.kind} {self.pk}"
