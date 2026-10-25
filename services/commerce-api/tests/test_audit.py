import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from commerce.audit.models import ActorType, AuditEvent, AuditLogImmutableError
from commerce.audit.services import REDACTED, field_changes, record_audit_event
from commerce.core.request_context import request_id_var
from commerce.identity.models import AccountType
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from tests.factories import make_store_settings, make_user

pytestmark = pytest.mark.django_db


def _record(tenant: Tenant) -> AuditEvent:
    with tenant_context(tenant), transaction.atomic():
        return record_audit_event(action="tenant.checked", resource=tenant, actor_label="test")


def test_events_capture_actor_resource_changes_and_request(tenant_a: Tenant) -> None:
    manager = make_user(tenant_a, account_type=AccountType.STAFF, email="manager@example.com")
    store = make_store_settings(tenant_a, name="Old name")
    token = request_id_var.set("req-audit-0001")
    try:
        with tenant_context(tenant_a), transaction.atomic():
            event = record_audit_event(
                action="store.settings.updated",
                resource=store,
                actor=manager,
                changes=field_changes({"name": "Old name"}, {"name": "New name"}, fields=["name"]),
            )
    finally:
        request_id_var.reset(token)

    assert event.tenant_id == tenant_a.pk
    assert (event.actor_type, event.actor_user) == (ActorType.USER, manager)
    assert (event.resource_type, event.resource_id) == ("stores.storesettings", str(store.pk))
    assert event.changes == {"name": {"from": "Old name", "to": "New name"}}
    assert event.request_id == "req-audit-0001"


def test_sensitive_values_are_never_stored(tenant_a: Tenant) -> None:
    with tenant_context(tenant_a), transaction.atomic():
        event = record_audit_event(
            action="integration.credentials.updated",
            resource=tenant_a,
            changes={
                "api_key": "sk-live-123",
                "Password": "hunter2",
                "otp_code": "123456",
                "mode": "live",
            },
        )

    assert event.changes == {
        "api_key": REDACTED,
        "Password": REDACTED,
        "otp_code": REDACTED,
        "mode": "live",
    }


def test_events_roll_back_with_the_change_they_describe(tenant_a: Tenant) -> None:
    def failing_change() -> None:
        record_audit_event(action="tenant.checked", resource=tenant_a)
        raise RuntimeError("change failed")

    with tenant_context(tenant_a):
        with pytest.raises(RuntimeError, match="change failed"), transaction.atomic():
            failing_change()
        assert not AuditEvent.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_events_must_be_recorded_inside_a_transaction(tenant_a: Tenant) -> None:
    with tenant_context(tenant_a), pytest.raises(RuntimeError, match="inside the transaction"):
        record_audit_event(action="tenant.checked", resource=tenant_a)


def test_action_names_and_actor_consistency_are_validated(tenant_a: Tenant) -> None:
    with tenant_context(tenant_a), transaction.atomic():
        with pytest.raises(ValidationError):
            record_audit_event(action="Not A Dotted Verb", resource=tenant_a)
        with pytest.raises(ValidationError):
            AuditEvent(
                action="tenant.checked", actor_type=ActorType.USER, resource_type="tenancy.tenant"
            ).full_clean()


def test_events_are_scoped_to_their_tenant(tenant_a: Tenant, tenant_b: Tenant) -> None:
    _record(tenant_a)

    with tenant_context(tenant_b):
        assert not AuditEvent.objects.exists()
    with tenant_context(tenant_a):
        assert AuditEvent.objects.count() == 1


def test_the_orm_cannot_change_or_delete_events(tenant_a: Tenant) -> None:
    event = _record(tenant_a)

    with tenant_context(tenant_a):
        event.action = "tenant.rewritten"
        with pytest.raises(AuditLogImmutableError):
            event.save()
        with pytest.raises(AuditLogImmutableError):
            event.delete()
        with pytest.raises(AuditLogImmutableError):
            AuditEvent.objects.update(action="tenant.rewritten")
        with pytest.raises(AuditLogImmutableError):
            AuditEvent.objects.all().delete()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_auditevent SET action = 'tenant.rewritten' WHERE id = %s",
        "DELETE FROM audit_auditevent WHERE id = %s",
    ],
)
def test_postgresql_refuses_to_change_or_delete_events(tenant_a: Tenant, statement: str) -> None:
    event = _record(tenant_a)

    with (
        pytest.raises(IntegrityError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement, [event.pk])
