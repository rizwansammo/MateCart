import uuid
from dataclasses import replace
from io import StringIO
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from pytest_django.fixtures import Settings

from commerce.audit.models import ActorType, AuditEvent
from commerce.core.ids import uuid7
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context, unscoped_access
from commerce.tenancy.exceptions import TenantAlreadyProvisioned, TenantProvisioningError
from commerce.tenancy.models import Tenant, TenantStatus
from commerce.tenancy.provisioning import StoreProfile, provision_tenant
from commerce.tenancy.resolution import check_deployment_tenant

pytestmark = pytest.mark.django_db

PROFILE = StoreProfile(
    handle="rahim-fashion",
    name="Rahim Fashion",
    preset="fashion",
    country="BD",
    default_currency="BDT",
    timezone="Asia/Dhaka",
    default_language="bn",
    supported_languages=("bn", "en"),
    contact_email="Owner@Example.com",
    contact_phone="01712345678",
)


@pytest.fixture
def configured_id(settings: Settings) -> uuid.UUID:
    settings.MATECART_TENANT_ID = uuid7()
    tenant_id: uuid.UUID = settings.MATECART_TENANT_ID
    return tenant_id


def test_provisioning_creates_tenant_store_settings_and_audit_event(
    configured_id: uuid.UUID,
) -> None:
    tenant = provision_tenant(PROFILE)

    assert tenant.pk == configured_id
    assert tenant.handle == "rahim-fashion"
    assert tenant.status == TenantStatus.ACTIVE
    with tenant_context(tenant):
        store = StoreSettings.objects.get()
        event = AuditEvent.objects.get()
    assert store.preset == "fashion"
    assert store.contact_email == "owner@example.com"
    assert store.contact_phone == "+8801712345678"
    assert store.supported_languages == ["bn", "en"]
    assert event.action == "tenant.provisioned"
    assert event.actor_type == ActorType.SYSTEM
    assert event.actor_label == "provision_tenant"
    assert (event.resource_type, event.resource_id) == ("tenancy.tenant", str(tenant.pk))
    assert event.changes["handle"] == {"from": None, "to": "rahim-fashion"}
    check_deployment_tenant()  # readiness is satisfied


def test_provisioning_requires_a_configured_tenant(settings: Settings) -> None:
    settings.MATECART_TENANT_ID = None

    with pytest.raises(TenantProvisioningError, match="MATECART_TENANT_ID"):
        provision_tenant(PROFILE)
    assert not Tenant.objects.exists()


def test_provisioning_refuses_an_id_other_than_the_configured_one(configured_id: uuid.UUID) -> None:
    with pytest.raises(TenantProvisioningError, match="does not match"):
        provision_tenant(PROFILE, tenant_id=uuid7())


def test_provisioning_twice_is_refused(configured_id: uuid.UUID) -> None:
    provision_tenant(PROFILE)

    with pytest.raises(TenantAlreadyProvisioned):
        provision_tenant(PROFILE)
    assert Tenant.objects.count() == 1


def test_a_single_tenant_database_refuses_a_second_tenant(
    configured_id: uuid.UUID, tenant_b: Tenant
) -> None:
    with pytest.raises(TenantProvisioningError, match="different tenant"):
        provision_tenant(PROFILE)
    assert list(Tenant.objects.values_list("handle", flat=True)) == ["store-b"]


@pytest.mark.parametrize(
    ("changes", "reported"),
    [
        ({"handle": "Bad Handle!"}, "handle"),
        ({"preset": "toys"}, "preset"),
        ({"country": "XX"}, "country"),
        ({"default_currency": "ABC"}, "default_currency"),
        ({"timezone": "Mars/Olympus_Mons"}, "timezone"),
        ({"default_language": "fr", "supported_languages": ("fr",)}, "default_language"),
        ({"default_language": "en", "supported_languages": ("bn",)}, "default_language"),
        ({"contact_phone": "12345"}, "phone"),
    ],
)
def test_invalid_input_is_rejected_and_leaves_nothing_behind(
    configured_id: uuid.UUID, changes: dict[str, Any], reported: str
) -> None:
    with pytest.raises(TenantProvisioningError, match=reported):
        provision_tenant(replace(PROFILE, **changes))

    assert not Tenant.objects.exists()
    with unscoped_access("test: verify provisioning rolled back"):
        assert not StoreSettings.objects.exists()
        assert not AuditEvent.objects.exists()


def test_command_provisions_and_is_safe_to_rerun_with_if_missing(configured_id: uuid.UUID) -> None:
    arguments = [
        "provision_tenant",
        "--handle=dev-store",
        "--name=Dev Store",
        "--preset=general",
        "--country=bd",
        "--currency=bdt",
        "--timezone=Asia/Dhaka",
        "--language=EN",
        "--languages=en,bn",
    ]
    out = StringIO()

    call_command(*arguments, stdout=out)
    call_command(*arguments, "--if-missing", stdout=out)
    with pytest.raises(CommandError, match="already provisioned"):
        call_command(*arguments, stdout=out)

    assert f"Provisioned tenant dev-store ({configured_id})" in out.getvalue()
    assert "Nothing changed." in out.getvalue()
    with tenant_context(configured_id):
        store = StoreSettings.objects.get()
    assert (store.country, store.default_currency, store.default_language) == ("BD", "BDT", "en")
