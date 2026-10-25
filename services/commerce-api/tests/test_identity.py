"""The custom user model: auth framework compatibility, normalisation and uniqueness rules."""

from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from commerce.identity.models import AccountType, User
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from tests.factories import make_user

pytestmark = pytest.mark.django_db


def test_the_custom_user_model_is_swapped_in() -> None:
    assert settings.AUTH_USER_MODEL == "identity.User"
    assert get_user_model() is User
    assert User.USERNAME_FIELD == "id"


def test_users_have_uuid7_ids_and_hashed_passwords(tenant_a: Tenant) -> None:
    user = make_user(tenant_a, email="alice@example.com", password="correct horse battery")

    assert user.pk.version == 7
    assert user.password != "correct horse battery"
    assert user.check_password("correct horse battery")
    assert user.is_authenticated


def test_accounts_without_a_password_cannot_sign_in_with_one(tenant_a: Tenant) -> None:
    user = make_user(tenant_a, phone="+8801712345678")

    assert not user.has_usable_password()


def test_nothing_authenticates_until_login_backends_exist(tenant_a: Tenant) -> None:
    user = make_user(tenant_a, email="alice@example.com", password="pw-123456789")

    with tenant_context(tenant_a):
        assert authenticate(username=str(user.pk), password="pw-123456789") is None
        assert authenticate(email="alice@example.com", password="pw-123456789") is None
    assert settings.AUTHENTICATION_BACKENDS == ["commerce.identity.backends.NoLoginBackend"]


def test_natural_key_is_the_opaque_id_within_the_tenant(tenant_a: Tenant, tenant_b: Tenant) -> None:
    user = make_user(tenant_a, email="alice@example.com")

    with tenant_context(tenant_a):
        assert User.objects.get_by_natural_key(str(user.pk)) == user
    with tenant_context(tenant_b), pytest.raises(User.DoesNotExist):
        User.objects.get_by_natural_key(str(user.pk))


def test_login_identifiers_are_normalized(tenant_a: Tenant) -> None:
    by_email = make_user(tenant_a, email="  Alice@Example.COM ")
    by_phone = make_user(tenant_a, phone="01712345678", phone_region="BD")

    assert by_email.email == "alice@example.com"
    assert by_phone.phone == "+8801712345678"


def test_an_account_needs_an_email_or_a_phone(tenant_a: Tenant) -> None:
    with pytest.raises(ValidationError):
        make_user(tenant_a)


def test_invalid_phone_numbers_are_rejected(tenant_a: Tenant) -> None:
    with pytest.raises(ValidationError):
        make_user(tenant_a, phone="12345", phone_region="BD")


def test_identifiers_are_unique_per_tenant_and_realm(tenant_a: Tenant, tenant_b: Tenant) -> None:
    make_user(tenant_a, email="alice@example.com", phone="+8801712345678")

    with pytest.raises(ValidationError):
        make_user(tenant_a, email="ALICE@example.com")
    with pytest.raises(ValidationError):
        make_user(tenant_a, phone="+8801712345678")

    # Separate realm in the same store, and the same person in another store, are separate accounts.
    staff = make_user(tenant_a, account_type=AccountType.STAFF, email="alice@example.com")
    elsewhere = make_user(tenant_b, email="alice@example.com")
    assert staff.account_type == AccountType.STAFF
    assert elsewhere.tenant_id == tenant_b.pk


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "Upper@Example.com"},
        {"email": ""},
        {"email": None, "phone": None},
        {"phone": "12345"},
        {"account_type": "admin"},
        {"email": None, "email_verified_at": timezone.now()},
    ],
)
def test_postgresql_enforces_identity_rules(tenant_a: Tenant, changes: dict[str, Any]) -> None:
    user = make_user(tenant_a, email="alice@example.com", phone="+8801712345678")

    with tenant_context(tenant_a), pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(**changes)  # bypasses validation


def test_postgresql_enforces_uniqueness_even_without_validation(tenant_a: Tenant) -> None:
    make_user(tenant_a, email="alice@example.com")
    duplicate = User(account_type=AccountType.CUSTOMER, email="alice@example.com", password="!")

    with tenant_context(tenant_a), pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([duplicate])
