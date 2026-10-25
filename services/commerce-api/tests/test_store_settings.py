"""Store configuration: model validation plus the PostgreSQL constraints behind it."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from tests.factories import make_store_settings

pytestmark = pytest.mark.django_db


def test_each_tenant_has_its_own_settings(tenant_a: Tenant, tenant_b: Tenant) -> None:
    make_store_settings(tenant_a, name="Store A")
    make_store_settings(tenant_b, name="Store B", country="US", default_currency="USD")

    with tenant_context(tenant_a):
        assert StoreSettings.objects.get().name == "Store A"
    with tenant_context(tenant_b):
        assert StoreSettings.objects.get().default_currency == "USD"


def test_a_tenant_has_exactly_one_settings_row(tenant_a: Tenant) -> None:
    make_store_settings(tenant_a)

    with pytest.raises(ValidationError, match="already exists"):
        make_store_settings(tenant_a)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"supported_languages": ["en", "en"]}, "supported_languages"),
        ({"default_language": "bn", "supported_languages": ["en"]}, "default_language"),
        ({"supported_languages": ["en", "fr"]}, "supported_languages"),
        ({"contact_phone": "01712345678"}, "contact_phone"),
        ({"country": "bd"}, "country"),
    ],
)
def test_validation_rejects_inconsistent_configuration(
    tenant_a: Tenant, overrides: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_store_settings(tenant_a, **overrides)

    assert field in excinfo.value.message_dict


@pytest.mark.parametrize(
    "changes",
    [
        {"country": "bd"},
        {"default_currency": "bdt"},
        {"preset": "toys"},
        {"default_language": "bn", "supported_languages": ["en"]},
        {"supported_languages": []},
        {"supported_languages": ["en", "fr"], "default_language": "en"},
        {"contact_phone": "017123"},
    ],
)
def test_postgresql_enforces_the_configuration_rules(
    tenant_a: Tenant, changes: dict[str, Any]
) -> None:
    store = make_store_settings(tenant_a)

    with (
        tenant_context(tenant_a),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        StoreSettings.objects.filter(pk=store.pk).update(**changes)  # bypasses validation
