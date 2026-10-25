"""Read helpers for the active store's configuration."""

from dataclasses import dataclass

from commerce.tenancy.context import get_active_tenant_id

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
