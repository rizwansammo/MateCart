"""Console administrator identity: its own user model, in its own database boundary."""

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from control.accounts.models import AdminUser
from control.core.ids import uuid7

pytestmark = pytest.mark.django_db


def test_the_console_uses_its_own_admin_user_model() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.AdminUser"
    assert get_user_model() is AdminUser


def test_admins_have_uuid7_ids_and_hashed_passwords() -> None:
    admin = AdminUser.objects.create_user("ops@netamate.example", password="correct horse battery")

    assert admin.pk.version == 7
    assert admin.password != "correct horse battery"
    assert admin.check_password("correct horse battery")


def test_admin_emails_are_normalized_and_unique_case_insensitively() -> None:
    admin = AdminUser.objects.create_user("  Ops@NetaMate.Example ")

    assert admin.email == "ops@netamate.example"
    assert not admin.has_usable_password()
    with pytest.raises(ValidationError):
        AdminUser.objects.create_user("OPS@netamate.example")
    with pytest.raises(ValueError, match="email"):
        AdminUser.objects.create_user("   ")


def test_postgresql_enforces_the_email_rules() -> None:
    admin = AdminUser.objects.create_user("ops@netamate.example")

    with pytest.raises(IntegrityError), transaction.atomic():
        AdminUser.objects.filter(pk=admin.pk).update(email="Ops@NetaMate.example")
    with pytest.raises(IntegrityError), transaction.atomic():
        AdminUser.objects.bulk_create([AdminUser(email="ops@netamate.example", password="!")])


def test_nothing_authenticates_until_console_sign_in_exists() -> None:
    AdminUser.objects.create_user("ops@netamate.example", password="pw-123456789")

    assert authenticate(email="ops@netamate.example", password="pw-123456789") is None
    assert authenticate(username="ops@netamate.example", password="pw-123456789") is None


def test_the_control_database_holds_no_store_data() -> None:
    store_apps = ("tenancy", "stores", "identity", "audit")

    assert not any(apps.is_installed(f"commerce.{label}") for label in store_apps)
    assert not [t for t in connection.introspection.table_names() if t.split("_")[0] in store_apps]


def test_uuid7_sets_version_and_variant() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == "specified in RFC 4122"
