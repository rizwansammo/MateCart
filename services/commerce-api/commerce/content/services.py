"""Content rules (D-063). Every change runs in one transaction with its audit event."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.catalog.services import clean_translations
from commerce.core.domain import ConflictError, save_unique, validated
from commerce.identity.models import User
from commerce.media.models import MediaKind
from commerce.stores.models import StorePreset, StoreSettings

from .models import ContentPage, HomeSection, PageStatus, SectionType

MAX_SECTIONS = 20
MAX_PAGES = 50
# Markup is not content here: anything that looks like an HTML tag is refused, so stored text is
# always plain and the storefront escapes it (no script injection path).
_MARKUP = re.compile(r"<\s*[A-Za-z!/?]")
PAGE_TEXT = {"title": 160, "body": 20000}
SECTION_TEXT = {"title": 120, "subtitle": 200, "body": 1000, "cta_label": 40}

# Default homepage per preset, after the approved prototype's Content screen.
DEFAULT_SECTIONS: dict[str, list[tuple[str, str, str]]] = {
    StorePreset.FASHION: [
        (SectionType.HERO, "New season", "Woven slowly. Worn for years."),
        (SectionType.CATEGORY_SHOWCASE, "Shop by category", ""),
        (SectionType.COLLECTION_HIGHLIGHT, "New in", ""),
        (SectionType.BRAND_STORY, "Our story", ""),
        (SectionType.TRUST_STRIP, "Why shop with us", "Easy exchange · Cash on delivery"),
    ],
    StorePreset.GROCERY: [
        (SectionType.HERO, "Fresh groceries, delivered fast", ""),
        (SectionType.CATEGORY_SHOWCASE, "Shop by category", ""),
        (SectionType.COLLECTION_HIGHLIGHT, "Daily essentials", ""),
        (
            SectionType.TRUST_STRIP,
            "Why shop with us",
            "Freshness · Fast delivery · Cash on delivery",
        ),
    ],
    StorePreset.ELECTRONICS: [
        (SectionType.HERO, "Product spotlight", ""),
        (SectionType.TRUST_STRIP, "Our promise", "Official warranty · Easy replacement"),
        (SectionType.CATEGORY_SHOWCASE, "Shop by category", ""),
        (SectionType.COLLECTION_HIGHLIGHT, "Just landed", ""),
    ],
    StorePreset.GENERAL: [
        (SectionType.HERO, "Deals for every day", ""),
        (SectionType.CATEGORY_SHOWCASE, "Shop by category", ""),
        (SectionType.COLLECTION_HIGHLIGHT, "Top picks", ""),
        (SectionType.TRUST_STRIP, "Why shop with us", "Genuine products · Cash on delivery"),
    ],
    StorePreset.BOOKS: [
        (SectionType.HERO, "Book of the month", ""),
        (SectionType.COLLECTION_HIGHLIGHT, "New releases", ""),
        (SectionType.CATEGORY_SHOWCASE, "Browse by genre", ""),
        (SectionType.BRAND_STORY, "About our bookshop", ""),
    ],
}


def plain_text(field: str, value: str) -> str:
    text = value.strip()
    if _MARKUP.search(text):
        raise ValidationError({field: ["Use plain text: HTML and scripts are not allowed."]})
    return text


def _clean_translations(value: Any, fields: Mapping[str, int]) -> dict[str, dict[str, str]]:
    cleaned = clean_translations(value, fields=fields)
    for language, texts in cleaned.items():
        for key, text in texts.items():
            plain_text(f"translations.{language}.{key}", text)
    return cleaned


def _audit(action: str, resource: Any, actor: User | None, **changes: Any) -> None:
    record_audit_event(action=action, resource=resource, actor=actor, changes=changes)


# --- Pages ----------------------------------------------------------------------------------------

PAGE_FIELDS = ["slug", "title", "page_type", "show_in_footer", "position"]


def save_page(
    *, page: ContentPage | None, data: Mapping[str, Any], actor: User | None
) -> ContentPage:
    with transaction.atomic():
        creating = page is None
        if creating and ContentPage.objects.count() >= MAX_PAGES:
            raise ValidationError({"pages": [f"A store can have at most {MAX_PAGES} pages."]})
        target = (
            ContentPage()
            if page is None
            else ContentPage.objects.select_for_update().get(pk=page.pk)
        )
        before = {name: getattr(target, name) for name in PAGE_FIELDS}
        for field in ("title", "body"):
            if field in data:
                setattr(target, field, plain_text(field, data[field]))
        for field in ("page_type", "show_in_footer", "position"):
            if field in data:
                setattr(target, field, data[field])
        if data.get("slug"):
            target.slug = data["slug"]
        elif creating:
            target.slug = slugify(target.title)[:80].strip("-") or "page"
        if "translations" in data:
            target.translations = _clean_translations(data["translations"], PAGE_TEXT)
        validated(target, exclude=["tenant"])
        save_unique(
            target, {"content_page_slug_unique": ("slug", "Another page uses this address.")}
        )
        _audit(
            "content.page.created" if creating else "content.page.updated",
            target,
            actor,
            **field_changes(
                before, {name: getattr(target, name) for name in PAGE_FIELDS}, fields=PAGE_FIELDS
            ),
            **({"body_changed": True} if "body" in data else {}),
        )
    return target


def set_page_status(*, page: ContentPage, status: str, actor: User | None) -> ContentPage:
    with transaction.atomic():
        target = ContentPage.objects.select_for_update().get(pk=page.pk)
        if status == target.status:
            return target
        if status == PageStatus.PUBLISHED and not target.body.strip():
            raise ValidationError({"body": ["Write the page before publishing it."]})
        previous, target.status = target.status, status
        if status == PageStatus.PUBLISHED:
            target.published_at = timezone.now()
        target.save(update_fields=["status", "published_at", "updated_at"])
        _audit(
            "content.page.status_changed", target, actor, status={"from": previous, "to": status}
        )
    return target


def delete_page(*, page: ContentPage, actor: User | None) -> None:
    with transaction.atomic():
        _audit("content.page.deleted", page, actor, slug=page.slug)
        page.delete()


# --- Homepage sections ----------------------------------------------------------------------------

SECTION_FIELDS = ["section_type", "is_enabled", "title", "image_id", "collection_id", "cta_url"]


def _check_section(section: HomeSection) -> None:
    if section.image is not None and section.image.kind != MediaKind.IMAGE:
        raise ValidationError({"image": ["Choose an image."]})
    if section.section_type == SectionType.COLLECTION_HIGHLIGHT:
        if section.collection is None and section.is_enabled:
            raise ValidationError({"collection": ["Choose the collection to highlight."]})
    elif section.collection is not None:
        raise ValidationError({"collection": ["Only collection highlights show a collection."]})
    if bool(section.cta_label) != bool(section.cta_url):
        raise ValidationError({"cta_url": ["Give the button both a label and a link."]})


def save_section(
    *, section: HomeSection | None, data: Mapping[str, Any], actor: User | None
) -> HomeSection:
    with transaction.atomic():
        creating = section is None
        if section is None:
            count = HomeSection.objects.count()
            if count >= MAX_SECTIONS:
                raise ValidationError(
                    {"sections": [f"A homepage has at most {MAX_SECTIONS} sections."]}
                )
            target = HomeSection(position=count)
        else:
            target = HomeSection.objects.select_for_update().get(pk=section.pk)
        before = {name: getattr(target, name) for name in SECTION_FIELDS}
        for field in ("section_type", "is_enabled", "image", "collection", "cta_url"):
            if field in data:
                setattr(target, field, data[field] if data[field] is not None else _blank(field))
        for field in ("title", "subtitle", "body", "cta_label"):
            if field in data:
                setattr(target, field, plain_text(field, data[field]))
        if "translations" in data:
            target.translations = _clean_translations(data["translations"], SECTION_TEXT)
        _check_section(target)
        validated(target, exclude=["tenant"])
        target.save()
        _audit(
            "content.section.created" if creating else "content.section.updated",
            target,
            actor,
            **field_changes(
                before,
                {name: getattr(target, name) for name in SECTION_FIELDS},
                fields=SECTION_FIELDS,
            ),
        )
    return target


def _blank(field: str) -> Any:
    return "" if field == "cta_url" else None


def delete_section(*, section: HomeSection, actor: User | None) -> None:
    with transaction.atomic():
        _audit("content.section.deleted", section, actor, section_type=section.section_type)
        section.delete()
        _renumber(list(HomeSection.objects.order_by("position", "pk")))


def _renumber(sections: Sequence[HomeSection]) -> None:
    for position, section in enumerate(sections):
        section.position = position
    HomeSection.objects.bulk_update(sections, ["position"])


def reorder_sections(*, ordered: Sequence[HomeSection], actor: User | None) -> None:
    """Set the homepage order. ``ordered`` must contain every section exactly once."""
    with transaction.atomic():
        current = list(HomeSection.objects.select_for_update().order_by("position", "pk"))
        if sorted(s.pk for s in ordered) != sorted(s.pk for s in current) or len(
            {s.pk for s in ordered}
        ) != len(ordered):
            raise ValidationError({"sections": ["List every homepage section exactly once."]})
        by_pk = {section.pk: section for section in current}
        _renumber([by_pk[section.pk] for section in ordered])
        settings = StoreSettings.objects.first()
        if settings is not None:
            _audit("content.section.reordered", settings, actor, count=len(ordered))


def apply_default_sections(*, actor: User | None) -> list[HomeSection]:
    """Create the preset's suggested homepage, only when the homepage is still empty.
    Collection highlights start disabled until a collection is chosen."""
    with transaction.atomic():
        settings = StoreSettings.objects.select_for_update().first()
        if settings is None:
            raise ConflictError(
                "Store settings are missing; provision the store first.", "store_missing"
            )
        if HomeSection.objects.exists():
            raise ConflictError("The homepage already has sections.", "sections_exist")
        created = HomeSection.objects.bulk_create(
            HomeSection(
                section_type=kind,
                position=position,
                title=title,
                subtitle=subtitle,
                is_enabled=kind != SectionType.COLLECTION_HIGHLIGHT,
            )
            for position, (kind, title, subtitle) in enumerate(DEFAULT_SECTIONS[settings.preset])
        )
        _audit(
            "content.section.defaults_applied",
            settings,
            actor,
            preset=settings.preset,
            count=len(created),
        )
    return created


def public_asset_ids() -> set[Any]:
    """Images used by enabled homepage sections (public media gate)."""
    return set(
        HomeSection.objects.filter(is_enabled=True, image__isnull=False).values_list(
            "image_id", flat=True
        )
    )
