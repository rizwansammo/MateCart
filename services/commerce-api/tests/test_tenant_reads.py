"""Phase 02.2: reads of tenant-owned rows must be governed by the active tenant context.

The tenant id on a Python instance is untrusted input: it may come from a forged instance, an
instance loaded under another tenant, or a caller-supplied queryset. Refreshes, deferred-field
loads and relation traversals must only ever reach rows of the active tenant; without a context
they fail closed unless explicit privileged maintenance access (``unscoped_access``) is active.

Each test also checks that nothing of tenant B's row was copied into the instance.
"""

from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import QuerySet

from commerce.audit.models import ActorType, AuditEvent
from commerce.identity.models import AccountType, User
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context, unscoped_access
from commerce.tenancy.exceptions import CrossTenantAccessError, TenantContextMissing
from commerce.tenancy.models import Tenant
from tests.factories import make_store_settings, make_user

pytestmark = pytest.mark.django_db

REFUSED = (CrossTenantAccessError, ObjectDoesNotExist)


@pytest.fixture
def bob(tenant_b: Tenant) -> User:
    return make_user(tenant_b, email="bob@example.com", full_name="Bob B")


def _blank(pk: Any, tenant: Tenant) -> User:
    """An instance built in code: real primary key, chosen tenant, placeholder values."""
    return User(pk=pk, tenant=tenant, account_type=AccountType.CUSTOMER, email="placeholder@x.test")


def _assert_nothing_copied(instance: User) -> None:
    assert instance.email == "placeholder@x.test"
    assert instance.full_name == ""


# --- A. Forged instance carrying the other tenant's id ----------------------------------------


def test_forged_instance_with_foreign_tenant_cannot_refresh(
    tenant_a: Tenant, tenant_b: Tenant, bob: User
) -> None:
    forged = _blank(bob.pk, tenant_b)

    with tenant_context(tenant_a), pytest.raises(REFUSED):
        forged.refresh_from_db()

    _assert_nothing_copied(forged)


# --- B. Instance legitimately loaded under the other tenant -----------------------------------


def test_instance_loaded_under_another_tenant_cannot_refresh(
    tenant_a: Tenant, tenant_b: Tenant, bob: User
) -> None:
    with tenant_context(tenant_b):
        loaded = User.objects.get(pk=bob.pk)
        User.objects.filter(pk=bob.pk).update(full_name="Bob changed")

    with tenant_context(tenant_a), pytest.raises(REFUSED):
        loaded.refresh_from_db()

    assert loaded.full_name == "Bob B"  # the newer value was not read


# --- C. Caller-supplied querysets cannot widen the scope --------------------------------------


@pytest.mark.parametrize("source", ["raw_queryset", "unscoped", "other_tenant"])
def test_from_queryset_cannot_bypass_the_active_tenant(
    tenant_a: Tenant, tenant_b: Tenant, bob: User, source: str
) -> None:
    queryset: QuerySet[User]
    if source == "raw_queryset":
        queryset = QuerySet[User](model=User)  # no manager at all, so no scoping
    elif source == "unscoped":
        with unscoped_access("test: build an unscoped queryset"):
            queryset = User.objects.all()  # evaluated later, outside the unscoped block
    else:
        queryset = QuerySet[User](model=User).filter(tenant=tenant_b)
    forged = _blank(bob.pk, tenant_a)

    with tenant_context(tenant_a), pytest.raises(REFUSED):
        forged.refresh_from_db(from_queryset=queryset)

    _assert_nothing_copied(forged)


# --- D. No tenant context ---------------------------------------------------------------------


def test_refresh_without_a_tenant_context_fails_closed(bob: User) -> None:
    for instance in (_blank(bob.pk, bob.tenant), User(pk=bob.pk)):
        with pytest.raises(TenantContextMissing):
            instance.refresh_from_db()


# --- E. Deferred fields -----------------------------------------------------------------------


def test_deferred_fields_load_within_the_active_tenant(tenant_a: Tenant, tenant_b: Tenant) -> None:
    alice = make_user(tenant_a, email="alice@example.com", full_name="Alice")

    with tenant_context(tenant_a):
        deferred = User.objects.only("full_name").get(pk=alice.pk)
        assert deferred.email == "alice@example.com"  # deferred load works
        without_tenant = User.objects.only("full_name").get(pk=alice.pk)

    with tenant_context(tenant_b), pytest.raises(REFUSED):
        _ = without_tenant.email  # loaded under A, read under B
    with pytest.raises(TenantContextMissing):
        _ = without_tenant.email  # no context at all


# --- F. Legitimate same-tenant refresh ---------------------------------------------------------


def test_same_tenant_refresh_including_selected_fields(tenant_a: Tenant) -> None:
    alice = make_user(tenant_a, email="alice@example.com", full_name="Alice")

    with tenant_context(tenant_a):
        User.objects.filter(pk=alice.pk).update(full_name="Alice v2", email="alice2@example.com")
        alice.refresh_from_db(fields=["full_name"])
        assert (alice.full_name, alice.email) == ("Alice v2", "alice@example.com")
        alice.refresh_from_db()
        assert alice.email == "alice2@example.com"
        alice.refresh_from_db(from_queryset=User.objects.all())
        built = _blank(alice.pk, tenant_a)
        built.refresh_from_db()  # a built instance for one's own row is fine
        assert built.full_name == "Alice v2"


# --- G. Privileged maintenance ----------------------------------------------------------------


def test_privileged_refresh_is_explicit_and_respects_the_stored_tenant(
    tenant_a: Tenant, bob: User
) -> None:
    with unscoped_access("test: maintenance refresh"):
        loaded = User.objects.get(pk=bob.pk)
        loaded.refresh_from_db()
        assert loaded.full_name == "Bob B"
        mismatched = _blank(bob.pk, tenant_a)  # claims tenant A, row is stored under B
        with pytest.raises(ObjectDoesNotExist):
            mismatched.refresh_from_db()
        _assert_nothing_copied(mismatched)


# --- H. Async refresh -------------------------------------------------------------------------


def test_async_refresh_enforces_the_same_rules(
    tenant_a: Tenant, tenant_b: Tenant, bob: User
) -> None:
    alice = make_user(tenant_a, email="alice@example.com", full_name="Alice")

    with tenant_context(tenant_a):
        async_to_sync(alice.arefresh_from_db)()
        forged = _blank(bob.pk, tenant_b)
        with pytest.raises(REFUSED):
            async_to_sync(forged.arefresh_from_db)()
    _assert_nothing_copied(forged)
    with pytest.raises(TenantContextMissing):
        async_to_sync(_blank(bob.pk, tenant_b).arefresh_from_db)()


# --- I. Another tenant-owned model ------------------------------------------------------------


def test_protection_applies_to_every_tenant_owned_model(tenant_a: Tenant, tenant_b: Tenant) -> None:
    make_store_settings(tenant_a, name="Store A")
    other = make_store_settings(tenant_b, name="Store B")

    with tenant_context(tenant_b):
        loaded = StoreSettings.objects.get(pk=other.pk)
    with tenant_context(tenant_a), pytest.raises(REFUSED):
        loaded.refresh_from_db()
    forged = StoreSettings(pk=other.pk, tenant=tenant_a, name="placeholder")
    with tenant_context(tenant_a), pytest.raises(REFUSED):
        forged.refresh_from_db()
    assert forged.name == "placeholder"


# --- J. Relation traversal from an instance built in code -------------------------------------


def test_relation_traversal_cannot_read_another_tenants_row(tenant_a: Tenant, bob: User) -> None:
    with tenant_context(tenant_a):
        event = AuditEvent(
            action="test.read", actor_type=ActorType.USER, actor_user_id=bob.pk, resource_type="x"
        )
        with pytest.raises(ObjectDoesNotExist):
            _ = event.actor_user


def test_relation_traversal_within_the_tenant_still_works(tenant_a: Tenant) -> None:
    alice = make_user(tenant_a, email="alice@example.com", full_name="Alice")

    with tenant_context(tenant_a):
        event = AuditEvent(
            action="test.read", actor_type=ActorType.USER, actor_user_id=alice.pk, resource_type="x"
        )
        assert event.actor_user is not None
        assert event.actor_user.full_name == "Alice"
