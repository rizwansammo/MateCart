"""Read helpers for the active store's configuration, and NetaMate's theme assignment."""

from dataclasses import dataclass

from django.db import transaction
from rest_framework.exceptions import ValidationError

from commerce.audit.services import record_audit_event
from commerce.core.domain import DomainError, validated
from commerce.tenancy.context import get_active_tenant_id

from . import themes
from .models import StoreSettings


@dataclass(frozen=True)
class StoreIdentity:
    name: str
    country: str


def current_store() -> StoreIdentity:
    """The store's display name and country, used in messages and phone-number parsing. A store
    always has settings after provisioning; the fallback keeps messages readable regardless."""
    get_active_tenant_id()
    row = StoreSettings.objects.values("name", "country").first()
    if row is None:
        return StoreIdentity(name="MateCart", country="")
    return StoreIdentity(name=row["name"], country=row["country"])


def assign_theme(*, theme_id: str | None, actor_label: str) -> StoreSettings:
    """Assign a storefront theme to the active store (D-069). Trusted NetaMate operation: reached
    only from the ``assign_store_theme`` management command today, and later from the Central
    Console through deployment operations (Phases 16-17). Never exposed over HTTP, never reached
    by merchants or customers. ``None`` returns the store to its preset's default theme.

    Only ``active_theme`` changes; products, prices, stock, content and branding are untouched.
    A future licence check belongs before this call, in the platform, not in the store.
    """
    target = theme_id or ""
    if target and not themes.is_valid_id(target):
        raise ValidationError({"theme": ["Use a theme identifier such as fashion-editorial."]})
    with transaction.atomic():
        settings = StoreSettings.objects.select_for_update().first()
        if settings is None:
            raise DomainError(
                "Store settings are missing; provision the store first.", "store_missing"
            )
        spec = themes.theme(target) if target else None
        if target and spec is None:
            raise ValidationError({"theme": [f"There is no theme called {target}."]})
        if spec is not None and spec.preset != settings.preset:
            raise ValidationError(
                {
                    "theme": [
                        f"{target} is a {spec.preset} theme; this store uses the "
                        f"{settings.preset} preset."
                    ]
                }
            )
        previous = settings.active_theme
        if previous == target:
            return settings
        settings.active_theme = target
        validated(settings, exclude=["tenant"])
        settings.save(update_fields=["active_theme", "updated_at"])
        record_audit_event(
            action="store.theme.assigned",
            resource=settings,
            actor_label=actor_label,
            changes={"active_theme": {"from": previous, "to": target}},
        )
    return settings
