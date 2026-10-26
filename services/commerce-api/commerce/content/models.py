"""Store content (D-063): pages (about, contact, policies) and homepage sections.

Content is plain text in bounded fields: no HTML, Markdown or scripts are stored, so the
storefront renders it escaped. Pages are draft or published; homepage sections are ordered and
enabled or disabled. Only published pages and enabled sections are public.
"""

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from commerce.catalog.models import Collection
from commerce.core.models import TimestampedModel
from commerce.media.models import MediaAsset
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

PAGE_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
INTERNAL_LINK_PATTERN = r"^/(?!/)[A-Za-z0-9._~/-]*$"


class PageType(models.TextChoices):
    PAGE = "page", "Information page"
    POLICY = "policy", "Policy"


class PageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"


class ContentPage(TenantOwnedModel, TimestampedModel):
    slug = models.CharField(max_length=80, validators=[RegexValidator(PAGE_SLUG_PATTERN)])
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True, max_length=20000)
    page_type = models.CharField(max_length=16, choices=PageType.choices, default=PageType.PAGE)
    status = models.CharField(max_length=16, choices=PageStatus.choices, default=PageStatus.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    show_in_footer = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    translations = models.JSONField(default=dict, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant", "slug"], name="content_page_slug_unique"),
            models.CheckConstraint(
                condition=Q(slug__regex=PAGE_SLUG_PATTERN), name="content_page_slug_format"
            ),
            models.CheckConstraint(
                condition=Q(page_type__in=PageType.values), name="content_page_type_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=PageStatus.values), name="content_page_status_valid"
            ),
            models.CheckConstraint(
                condition=~Q(status=PageStatus.PUBLISHED) | Q(published_at__isnull=False),
                name="content_page_published_has_time",
            ),
        ]

    def __str__(self) -> str:
        return self.slug


class SectionType(models.TextChoices):
    HERO = "hero", "Hero banner"
    CATEGORY_SHOWCASE = "category_showcase", "Category showcase"
    COLLECTION_HIGHLIGHT = "collection_highlight", "Collection highlight"
    BRAND_STORY = "brand_story", "Brand story"
    TRUST_STRIP = "trust_strip", "Trust strip"


class HomeSection(TenantOwnedModel, TimestampedModel):
    section_type = models.CharField(max_length=32, choices=SectionType.choices)
    position = models.PositiveIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)
    title = models.CharField(max_length=120, blank=True)
    subtitle = models.CharField(max_length=200, blank=True)
    body = models.CharField(max_length=1000, blank=True)
    image = models.ForeignKey(
        MediaAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    collection = models.ForeignKey(
        Collection, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    cta_label = models.CharField(max_length=40, blank=True)
    cta_url = models.CharField(
        max_length=200, blank=True, validators=[RegexValidator(INTERNAL_LINK_PATTERN)]
    )
    translations = models.JSONField(default=dict, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="image", name="content_section_image_same_tenant"),
            TenantForeignKeyConstraint(
                field="collection", name="content_section_collection_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(section_type__in=SectionType.values),
                name="content_section_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(cta_url="") | Q(cta_url__regex=INTERNAL_LINK_PATTERN),
                name="content_section_cta_internal",
            ),
            models.CheckConstraint(
                condition=Q(collection__isnull=True)
                | Q(section_type=SectionType.COLLECTION_HIGHLIGHT),
                name="content_section_collection_only_highlight",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "position"], name="content_section_order")]
