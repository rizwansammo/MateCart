from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

from commerce.core.contact import E164_PATTERN
from commerce.core.models import TimestampedModel
from commerce.core.standards import (
    LANGUAGE_CHOICES,
    PLATFORM_LANGUAGES,
    validate_country_code,
    validate_currency_code,
    validate_timezone,
)
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel


class StorePreset(models.TextChoices):
    """The five approved business presets. One commerce engine; the preset drives storefront
    layout, merchandising, product attributes and default modules (Phase 05)."""

    FASHION = "fashion", "Fashion & Apparel"
    GROCERY = "grocery", "Grocery & Essentials"
    ELECTRONICS = "electronics", "Electronics & Gadgets"
    GENERAL = "general", "General / Multi-category"
    BOOKS = "books", "Bookstore & Booksellers"


class StoreSettings(TenantOwnedModel, TimestampedModel):
    """The store's business configuration: exactly one row per tenant.

    Identity and lifecycle (handle, status) live on Tenant. Theme and branding (Phase 05), domains
    (Phase 17) and module availability (Phase 05) get their own tables when those phases begin.
    """

    name = models.CharField(max_length=120)
    legal_name = models.CharField(max_length=200, blank=True)
    preset = models.CharField(max_length=32, choices=StorePreset.choices)
    country = models.CharField(
        max_length=2, validators=[validate_country_code], help_text="ISO 3166-1 alpha-2."
    )
    default_currency = models.CharField(
        max_length=3, validators=[validate_currency_code], help_text="ISO 4217."
    )
    timezone = models.CharField(
        max_length=64, validators=[validate_timezone], help_text="IANA time zone."
    )
    default_language = models.CharField(max_length=16, choices=LANGUAGE_CHOICES)
    supported_languages = ArrayField(models.CharField(max_length=16, choices=LANGUAGE_CHOICES))
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(
        max_length=16, blank=True, validators=[RegexValidator(E164_PATTERN)], help_text="E.164."
    )

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "store settings"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant"], name="stores_storesettings_one_per_tenant"),
            models.CheckConstraint(
                condition=Q(preset__in=StorePreset.values), name="stores_storesettings_preset_valid"
            ),
            models.CheckConstraint(
                condition=Q(country__regex=r"^[A-Z]{2}$"),
                name="stores_storesettings_country_format",
            ),
            models.CheckConstraint(
                condition=Q(default_currency__regex=r"^[A-Z]{3}$"),
                name="stores_storesettings_currency_format",
            ),
            models.CheckConstraint(
                condition=Q(supported_languages__contains=[F("default_language")]),
                name="stores_storesettings_default_language_supported",
            ),
            models.CheckConstraint(
                condition=Q(
                    supported_languages__contained_by=list(PLATFORM_LANGUAGES),
                    supported_languages__len__gte=1,
                ),
                name="stores_storesettings_languages_known",
            ),
            models.CheckConstraint(
                condition=Q(contact_phone="") | Q(contact_phone__regex=E164_PATTERN),
                name="stores_storesettings_contact_phone_e164",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        languages = self.supported_languages or []
        if len(set(languages)) != len(languages):
            raise ValidationError({"supported_languages": "Each language may appear only once."})
        if self.default_language and self.default_language not in languages:
            raise ValidationError(
                {"default_language": "The default language must be one of the supported languages."}
            )


BRAND_COLOR_PATTERN = r"^#[0-9A-F]{6}$"


class StoreTheme(TenantOwnedModel, TimestampedModel):
    """The store's appearance on top of its preset (D-061): optional brand colours, logos and
    short brand texts. One row per store, created on first save; absent values fall back to the
    preset. The business preset itself stays on StoreSettings and is not changed here.

    Images reference Phase 04 media assets (validated from their bytes, tenant-namespaced). They
    become publicly readable only while assigned here (``catalog.public.is_public_asset``).
    """

    primary_color = models.CharField(
        max_length=7, blank=True, validators=[RegexValidator(BRAND_COLOR_PATTERN)]
    )
    accent_color = models.CharField(
        max_length=7, blank=True, validators=[RegexValidator(BRAND_COLOR_PATTERN)]
    )
    logo = models.ForeignKey(
        "media.MediaAsset", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    logo_dark = models.ForeignKey(
        "media.MediaAsset", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    favicon = models.ForeignKey(
        "media.MediaAsset", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    tagline = models.CharField(max_length=160, blank=True)
    description = models.CharField(max_length=500, blank=True)
    announcement = models.CharField(max_length=160, blank=True)
    footer_text = models.CharField(max_length=300, blank=True)
    show_name_with_logo = models.BooleanField(default=True)
    translations = models.JSONField(default=dict, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="logo", name="stores_storetheme_logo_same_tenant"),
            TenantForeignKeyConstraint(
                field="logo_dark", name="stores_storetheme_logo_dark_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="favicon", name="stores_storetheme_favicon_same_tenant"
            ),
            models.UniqueConstraint(fields=["tenant"], name="stores_storetheme_one_per_tenant"),
            models.CheckConstraint(
                condition=Q(primary_color="") | Q(primary_color__regex=BRAND_COLOR_PATTERN),
                name="stores_storetheme_primary_color_format",
            ),
            models.CheckConstraint(
                condition=Q(accent_color="") | Q(accent_color__regex=BRAND_COLOR_PATTERN),
                name="stores_storetheme_accent_color_format",
            ),
        ]


MODULE_KEY_PATTERN = r"^[a-z][a-z0-9_]{1,48}$"


class StoreModule(TenantOwnedModel):
    """A store's explicit choice for one module of the code-owned registry
    (``stores.modules``). No row means the preset default applies (D-062)."""

    key = models.CharField(max_length=50, validators=[RegexValidator(MODULE_KEY_PATTERN)])
    enabled = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant", "key"], name="stores_storemodule_unique"),
            models.CheckConstraint(
                condition=Q(key__regex=MODULE_KEY_PATTERN), name="stores_storemodule_key_format"
            ),
        ]
