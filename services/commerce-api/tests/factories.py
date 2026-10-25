"""Small, explicit builders for test data. Each one enters the tenant context it writes to."""

from typing import Any

from commerce.identity.models import AccountType, User
from commerce.stores.models import StorePreset, StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant


def make_user(tenant: Tenant, *, account_type: str = AccountType.CUSTOMER, **fields: Any) -> User:
    with tenant_context(tenant):
        return User.objects.create_user(account_type=account_type, **fields)


def make_store_settings(tenant: Tenant, **overrides: Any) -> StoreSettings:
    values: dict[str, Any] = {
        "name": "Test Store",
        "preset": StorePreset.GENERAL,
        "country": "BD",
        "default_currency": "BDT",
        "timezone": "Asia/Dhaka",
        "default_language": "en",
        "supported_languages": ["en", "bn"],
        **overrides,
    }
    with tenant_context(tenant):
        store = StoreSettings(**values)
        store.full_clean()
        store.save()
        return store
