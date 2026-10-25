"""Phase 02.1, finding 01: forged model instances must not bypass tenant ownership.

A "forged" instance is constructed in code (not loaded from the database) with another tenant's
primary key and the active tenant's id. The tenant id on such an instance proves nothing about the
row it targets, so every write path must be qualified by the tenant stored in the database.

Each test asserts the rejection *and* that tenant B's row is byte-for-byte unchanged.
"""

from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from commerce.identity.models import AccountType, User
from commerce.tenancy.context import tenant_context, unscoped_access
from commerce.tenancy.exceptions import CrossTenantAccessError, TenancyError
from commerce.tenancy.models import Tenant
from tests.factories import make_user

pytestmark = pytest.mark.django_db


def _snapshot(user: User) -> dict[str, Any]:
    """Tenant B's row as stored, read with privileged access."""
    with unscoped_access("test: compare stored row"):
        return dict(User.objects.filter(pk=user.pk).values().get())


@pytest.fixture
def bob(tenant_b: Tenant) -> User:
    return make_user(tenant_b, email="bob@example.com", full_name="Bob", password="bob-secret-1")


def _forged(bob: User, tenant: Tenant, **fields: Any) -> User:
    """Tenant A's code builds a complete instance carrying Bob's primary key."""
    now = timezone.now()
    return User(
        pk=bob.pk,
        tenant=tenant,
        account_type=AccountType.CUSTOMER,
        email="attacker@example.com",
        full_name="Forged",
        password="!",
        created_at=now,
        updated_at=now,
        **fields,
    )


def test_forged_force_update_cannot_overwrite_or_move_another_tenants_row(
    tenant_a: Tenant, bob: User
) -> None:
    before = _snapshot(bob)

    with tenant_context(tenant_a):
        forged = _forged(bob, tenant_a)
        with pytest.raises(CrossTenantAccessError), transaction.atomic():
            forged.save(force_update=True)

    assert _snapshot(bob) == before


def test_forged_update_fields_cannot_change_another_tenants_row(
    tenant_a: Tenant, bob: User
) -> None:
    before = _snapshot(bob)

    with tenant_context(tenant_a):
        forged = _forged(bob, tenant_a)
        # Django turns this into an INSERT (the primary key has a default): duplicate key.
        with pytest.raises(IntegrityError), transaction.atomic():
            forged.save(update_fields=["full_name"])

    assert _snapshot(bob) == before


def test_forged_instance_marked_as_stored_cannot_update_another_tenants_row(
    tenant_a: Tenant, bob: User
) -> None:
    before = _snapshot(bob)

    with tenant_context(tenant_a):
        for kwargs in ({"update_fields": ["full_name"]}, {}):
            forged = _forged(bob, tenant_a)
            forged._state.adding = False  # as if it had been loaded
            with pytest.raises(CrossTenantAccessError), transaction.atomic():
                forged.save(**kwargs)

    assert _snapshot(bob) == before


def test_forged_instance_with_plain_save_is_rejected(tenant_a: Tenant, bob: User) -> None:
    before = _snapshot(bob)

    with (
        tenant_context(tenant_a),
        pytest.raises(IntegrityError),  # a new instance is inserted: duplicate key
        transaction.atomic(),
    ):
        _forged(bob, tenant_a).save()

    assert _snapshot(bob) == before


def test_forged_delete_cannot_delete_another_tenants_row(tenant_a: Tenant, bob: User) -> None:
    before = _snapshot(bob)

    with tenant_context(tenant_a), pytest.raises(CrossTenantAccessError):
        _forged(bob, tenant_a).delete()

    assert _snapshot(bob) == before


def test_forged_refresh_cannot_read_another_tenants_row(tenant_a: Tenant, bob: User) -> None:
    with tenant_context(tenant_a):
        forged = _forged(bob, tenant_a)
        with pytest.raises(User.DoesNotExist):
            forged.refresh_from_db()

    assert forged.email == "attacker@example.com"  # nothing of Bob's was copied in


def test_upserts_cannot_overwrite_another_tenants_row(tenant_a: Tenant, bob: User) -> None:
    before = _snapshot(bob)

    with tenant_context(tenant_a):
        forged = _forged(bob, tenant_a)
        with pytest.raises(TenancyError), transaction.atomic():
            User.objects.bulk_create(
                [forged],
                update_conflicts=True,
                unique_fields=["id"],
                update_fields=["full_name", "email"],
            )

    assert _snapshot(bob) == before


def test_privileged_bulk_update_is_qualified_by_each_rows_stored_tenant(
    tenant_a: Tenant, bob: User
) -> None:
    before = _snapshot(bob)
    forged = _forged(bob, tenant_a)

    with unscoped_access("test: maintenance bulk update with a forged instance"):
        updated = User.objects.bulk_update([forged], ["full_name"])

    assert updated == 0
    assert _snapshot(bob) == before


def test_privileged_delete_still_checks_the_stored_tenant(tenant_a: Tenant, bob: User) -> None:
    before = _snapshot(bob)

    with (
        unscoped_access("test: maintenance delete with a forged instance"),
        pytest.raises(CrossTenantAccessError),
    ):
        _forged(bob, tenant_a).delete()

    assert _snapshot(bob) == before


def test_get_or_create_and_update_or_create_cannot_reach_another_tenants_row(
    tenant_a: Tenant, bob: User
) -> None:
    before = _snapshot(bob)

    with tenant_context(tenant_a):
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.update_or_create(
                pk=bob.pk,
                defaults={
                    "full_name": "Taken over",
                    "account_type": AccountType.CUSTOMER,
                    "email": "x@example.com",
                },
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.get_or_create(
                pk=bob.pk,
                defaults={"account_type": AccountType.CUSTOMER, "email": "y@example.com"},
            )

    assert _snapshot(bob) == before


def test_legitimate_updates_and_deletes_keep_working(tenant_a: Tenant) -> None:
    alice = make_user(tenant_a, email="alice@example.com", full_name="Alice")

    with tenant_context(tenant_a):
        alice.full_name = "Alice A."
        alice.save(update_fields=["full_name"])
        loaded = User.objects.get(pk=alice.pk)
        loaded.full_name = "Alice B."
        loaded.save()
        loaded.save(force_update=True)
        loaded.refresh_from_db()
        assert loaded.full_name == "Alice B."

        # Updating one's own row without loading it first is legitimate too.
        own = User(pk=alice.pk, tenant=tenant_a, full_name="Alice C.")
        own._state.adding = False
        own.save(update_fields=["full_name"])
        assert User.objects.get(pk=alice.pk).full_name == "Alice C."

        deferred = User.objects.only("full_name").get(pk=alice.pk)
        deferred.full_name = "Alice D."
        deferred.save()  # loads the deferred tenant safely
        assert User.objects.get(pk=alice.pk).full_name == "Alice D."

        assert own.delete()[0] == 1
        assert not User.objects.filter(pk=alice.pk).exists()
