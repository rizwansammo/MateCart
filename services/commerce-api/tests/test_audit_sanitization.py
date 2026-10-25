"""Phase 02.1, finding 02: audit history must never hold secrets or unnecessary personal data."""

import copy
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from pytest_django.fixtures import Settings

from commerce.audit.models import AuditEvent
from commerce.audit.services import (
    REDACTED,
    field_changes,
    is_sensitive_field,
    record_audit_event,
    sanitize_audit_value,
)
from commerce.core.ids import uuid7
from commerce.tenancy.context import tenant_context
from commerce.tenancy.exceptions import TenantProvisioningError
from commerce.tenancy.models import Tenant
from commerce.tenancy.provisioning import StoreProfile, provision_tenant

SECRET = "sk-live-9f8e7d6c5b4a"

NESTED_PAYLOAD: dict[str, Any] = {
    "mode": "live",
    "integration": {
        "provider": "courier",
        "credentials": {"api_key": SECRET, "access_token": SECRET},
        "webhooks": [
            {"url": "https://example.test/hook", "signing_secret": SECRET},
            {"url": "https://example.test/other", "headers": {"Authorization": f"Bearer {SECRET}"}},
        ],
    },
    "levels": {"one": {"two": {"three": {"refreshToken": SECRET, "note": "kept"}}}},
}


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "Password",
        "PASSWORD_HASH",
        "new_password",
        "passphrase",
        "api_key",
        "apiKey",
        "X-API-Key",
        "APIKEY",
        "client_secret",
        "secretKey",
        "access_token",
        "refreshToken",
        "session_id",
        "otp",
        "otp_code",
        "recovery_codes",
        "backupCodes",
        "private_key",
        "card_number",
        "cardNumber",
        "cvv",
        "CVC",
        "pin",
        "credentials",
        "Authorization",
        "cookie",
    ],
)
def test_sensitive_field_names_are_recognised_in_any_style(name: str) -> None:
    assert is_sensitive_field(name)


@pytest.mark.parametrize(
    "name",
    ["name", "email", "shipping_address", "footprint", "company", "author", "preset", "mode"],
)
def test_ordinary_field_names_are_kept(name: str) -> None:
    assert not is_sensitive_field(name)


def test_matching_errs_towards_redaction() -> None:
    # Unambiguous fragments match inside longer names; over-redaction is the safe failure mode.
    assert is_sensitive_field("tokenless_note")
    assert is_sensitive_field("legacyPasswordFormat")


def test_nested_and_listed_secrets_are_redacted_at_every_level() -> None:
    original = copy.deepcopy(NESTED_PAYLOAD)

    cleaned = sanitize_audit_value(NESTED_PAYLOAD)

    assert cleaned == {
        "mode": "live",
        "integration": {
            "provider": "courier",
            "credentials": REDACTED,
            "webhooks": [
                {"url": "https://example.test/hook", "signing_secret": REDACTED},
                {"url": "https://example.test/other", "headers": {"Authorization": REDACTED}},
            ],
        },
        "levels": {"one": {"two": {"three": {"refreshToken": REDACTED, "note": "kept"}}}},
    }
    assert SECRET not in repr(cleaned)
    assert original == NESTED_PAYLOAD  # the caller's data is never mutated


@pytest.mark.parametrize(
    "value",
    [
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----",
        "pbkdf2_sha256$870000$salt$hash=",
        "argon2$argon2id$v=19$m=102400,t=2,p=8$c2FsdA$aGFzaA",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
        "Bearer abc.def.ghi",
        "4111 1111 1111 1111",
        "4242-4242-4242-4242",
    ],
)
def test_secret_looking_values_are_redacted_under_innocent_keys(value: str) -> None:
    assert sanitize_audit_value({"note": value}) == {"note": REDACTED}


def test_unexpected_types_and_depth_are_handled_safely() -> None:
    class Opaque:
        def __repr__(self) -> str:
            return SECRET

    deep: dict[str, Any] = {"v": "bottom"}
    for _ in range(20):
        deep = {"next": deep}
    moment = datetime(2026, 10, 25, tzinfo=UTC)
    identifier = uuid7()

    cleaned = sanitize_audit_value(
        {
            "object": Opaque(),
            "bytes": b"raw",
            "tags": {"b", "a"},
            "pair": ("x", 1),
            "nan": float("nan"),
            "amount": Decimal("12.50"),
            "when": moment,
            "id": identifier,
            "deep": deep,
            7: "numeric key",
            "long": "x" * 5000,
        }
    )

    assert cleaned["object"] == "[Opaque]"
    assert cleaned["bytes"] == "[bytes]"
    assert cleaned["tags"] == ["a", "b"]
    assert cleaned["pair"] == ["x", 1]
    assert cleaned["nan"] == "nan"
    assert (cleaned["amount"], cleaned["when"], cleaned["id"]) == (
        Decimal("12.50"),
        moment,
        identifier,
    )
    assert cleaned["7"] == "numeric key"
    assert "[TRUNCATED]" in repr(cleaned["deep"])
    assert len(cleaned["long"]) < 1100
    assert SECRET not in repr(cleaned)


def test_field_changes_records_only_allowlisted_fields() -> None:
    before = {"name": "Old", "contact_email": "a@example.com", "price": "10"}
    after = {"name": "New", "contact_email": "b@example.com", "price": "10"}

    assert field_changes(before, after, fields=("name", "price")) == {
        "name": {"from": "Old", "to": "New"}
    }


@pytest.mark.django_db
def test_recorded_events_store_only_sanitized_payloads(tenant_a: Tenant) -> None:
    with tenant_context(tenant_a), transaction.atomic():
        event = record_audit_event(
            action="integration.connection.updated", resource=tenant_a, changes=NESTED_PAYLOAD
        )

    with tenant_context(tenant_a):
        stored = AuditEvent.objects.get(pk=event.pk)
    assert SECRET not in repr(stored.changes)
    assert stored.changes["integration"]["provider"] == "courier"
    assert stored.changes["levels"]["one"]["two"]["three"]["note"] == "kept"


@pytest.mark.django_db
def test_failed_audit_writes_leak_no_secrets(
    tenant_a: Tenant, caplog: pytest.LogCaptureFixture
) -> None:
    with tenant_context(tenant_a):
        with pytest.raises(ValidationError) as excinfo, transaction.atomic():
            record_audit_event(action="Not A Verb", resource=tenant_a, changes=NESTED_PAYLOAD)
        assert not AuditEvent.objects.exists()

    assert SECRET not in str(excinfo.value)
    assert SECRET not in caplog.text


# --- Tenant provisioning ---------------------------------------------------------------------

PROFILE = StoreProfile(
    handle="rahim-fashion",
    name="Rahim Fashion",
    preset="fashion",
    country="BD",
    default_currency="BDT",
    timezone="Asia/Dhaka",
    default_language="bn",
    supported_languages=("bn", "en"),
    legal_name="Rahim Fashion House Ltd",
    contact_email="owner.private@example.com",
    contact_phone="01712345678",
)


@pytest.mark.django_db
def test_provisioning_audit_keeps_operational_facts_and_drops_contact_details(
    settings: Settings,
) -> None:
    settings.MATECART_TENANT_ID = uuid.uuid4()  # externally issued ids may be any UUID version

    tenant = provision_tenant(PROFILE)

    with tenant_context(tenant):
        event = AuditEvent.objects.get()
    recorded = repr(event.changes)
    for private in ("owner.private@example.com", "1712345678", "Rahim Fashion House Ltd"):
        assert private not in recorded
    assert set(event.changes) == {
        "handle",
        "name",
        "preset",
        "country",
        "default_currency",
        "timezone",
        "default_language",
        "supported_languages",
    }
    assert (event.action, event.actor_label) == ("tenant.provisioned", "provision_tenant")
    assert (event.resource_type, event.resource_id) == ("tenancy.tenant", str(tenant.pk))
    assert event.occurred_at is not None


@pytest.mark.django_db
def test_failed_provisioning_does_not_echo_contact_details(settings: Settings) -> None:
    settings.MATECART_TENANT_ID = uuid7()
    bad = replace(PROFILE, contact_email="owner.private@")

    with pytest.raises(TenantProvisioningError) as excinfo:
        provision_tenant(bad)

    assert "owner.private" not in str(excinfo.value)
    assert "contact_email" in str(excinfo.value)
    assert not Tenant.objects.exists()
