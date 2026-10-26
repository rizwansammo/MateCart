"""Store appearance (D-061): brand colours, logos and brand texts on top of the preset.

The business preset (StoreSettings.preset) is never changed here: appearance changes cannot
alter product types, prices, inventory or payment behaviour. Colours are validated for
readable text; images must be Phase 04 media assets of the right shape.
"""

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.catalog.services import clean_translations
from commerce.core.domain import DomainError, validated
from commerce.identity.models import User
from commerce.media.models import MediaAsset, MediaKind

from . import colors
from .models import StorePreset, StoreSettings, StoreTheme

PRESET_LABELS: dict[str, str] = dict(StorePreset.choices)
TEXT_FIELDS = ("tagline", "description", "announcement", "footer_text")
TRANSLATABLE = {"tagline": 160, "description": 500, "announcement": 160, "footer_text": 300}
IMAGE_FIELDS = ("logo", "logo_dark", "favicon")
AUDITED = [
    "primary_color",
    "accent_color",
    "logo_id",
    "logo_dark_id",
    "favicon_id",
    *TEXT_FIELDS,
    "show_name_with_logo",
]
MIN_LOGO_HEIGHT = 32
FAVICON_SIDES = (32, 1024)


def store_settings() -> StoreSettings:
    settings = StoreSettings.objects.first()
    if settings is None:
        raise DomainError("Store settings are missing; provision the store first.", "store_missing")
    return settings


def current_theme() -> StoreTheme:
    """The saved theme, or an unsaved one with every value at its preset default."""
    return StoreTheme.objects.select_related("logo", "logo_dark", "favicon").first() or StoreTheme()


def _clean_color(field: str, value: str) -> str:
    if not value:
        return ""
    color = colors.normalize_hex(value)
    if not colors.HEX_COLOR.match(color):
        raise ValidationError({field: ["Use a colour like #1F6F4A."]})
    if not colors.readable(color):
        raise ValidationError(
            {
                field: [
                    "Text on this colour would be hard to read. Choose a lighter or darker shade."
                ]
            }
        )
    return color


def _check_image(field: str, asset: MediaAsset | None) -> None:
    if asset is None:
        return
    if asset.kind != MediaKind.IMAGE or asset.width is None or asset.height is None:
        raise ValidationError({field: ["Choose a PNG, JPEG or WebP image."]})
    if field == "favicon":
        low, high = FAVICON_SIDES
        if asset.width != asset.height or not low <= asset.width <= high:
            raise ValidationError({field: [f"Use a square image between {low} and {high} pixels."]})
    elif asset.height < MIN_LOGO_HEIGHT:
        raise ValidationError({field: [f"Use a logo at least {MIN_LOGO_HEIGHT} pixels tall."]})


def update(*, data: Mapping[str, Any], actor: User | None, can_rename: bool) -> StoreTheme:
    with transaction.atomic():
        settings = StoreSettings.objects.select_for_update().first()
        if settings is None:
            raise DomainError(
                "Store settings are missing; provision the store first.", "store_missing"
            )
        theme = StoreTheme.objects.select_for_update().first() or StoreTheme()
        creating = theme._state.adding
        before = {name: getattr(theme, name) for name in AUDITED}
        for field in ("primary_color", "accent_color"):
            if field in data:
                setattr(theme, field, _clean_color(field, data[field] or ""))
        for field in IMAGE_FIELDS:
            if field in data:
                _check_image(field, data[field])
                setattr(theme, field, data[field])
        for field in TEXT_FIELDS:
            if field in data:
                setattr(theme, field, " ".join(str(data[field]).split()))
        if "show_name_with_logo" in data:
            theme.show_name_with_logo = bool(data["show_name_with_logo"])
        if "translations" in data:
            theme.translations = clean_translations(data["translations"], fields=TRANSLATABLE)
        validated(theme, exclude=["tenant"])
        theme.save()
        changes = field_changes(
            before, {name: getattr(theme, name) for name in AUDITED}, fields=AUDITED
        )
        if "store_name" in data and data["store_name"] != settings.name:
            if not can_rename:
                raise PermissionDenied("Only staff who manage store settings can rename the store.")
            changes["store_name"] = {"from": settings.name, "to": data["store_name"]}
            settings.name = data["store_name"]
            validated(settings, exclude=["tenant"])
            settings.save(update_fields=["name", "updated_at"])
        record_audit_event(
            action="store.theme.created" if creating else "store.theme.updated",
            resource=theme,
            actor=actor,
            changes=changes,
        )
    return theme


def reset_colors(*, actor: User | None) -> StoreTheme:
    """Return the colours to the preset's own palette. Logos and texts are kept."""
    with transaction.atomic():
        theme = StoreTheme.objects.select_for_update().first()
        if theme is None:
            return StoreTheme()
        before = {"primary_color": theme.primary_color, "accent_color": theme.accent_color}
        theme.primary_color = theme.accent_color = ""
        theme.save(update_fields=["primary_color", "accent_color", "updated_at"])
        record_audit_event(
            action="store.theme.reset",
            resource=theme,
            actor=actor,
            changes=field_changes(
                before, {"primary_color": "", "accent_color": ""}, fields=list(before)
            ),
        )
    return theme
