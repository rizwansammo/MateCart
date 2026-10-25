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
