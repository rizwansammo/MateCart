"""Tenant isolation at every implemented boundary, proven with two deliberately separate tenants.

The single-tenant runtime never serves two tenants; these tests create a second one directly so
the guarantees hold for the future hybrid model too.
"""

import uuid

import pytest
from celery import shared_task
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from pytest_django.fixtures import Settings
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from commerce.audit.models import ActorType, AuditEvent
from commerce.core.ids import uuid7
from commerce.identity.models import AccountType, User
from commerce.tenancy.context import (
    get_active_tenant_id,
    get_active_tenant_id_or_none,
    tenant_context,
    unscoped_access,
)
from commerce.tenancy.exceptions import (
    CrossTenantAccessError,
    TenancyError,
    TenantContextMissing,
    TenantImmutableError,
    TenantNotProvisioned,
)
from commerce.tenancy.keys import tenant_cache_key, tenant_storage_path
from commerce.tenancy.middleware import TenantMiddleware
from commerce.tenancy.models import Tenant
from commerce.tenancy.tasks import TenantTask
from tests.factories import make_user

pytestmark = pytest.mark.django_db


def _save_and_check_constraints(obj: AuditEvent) -> None:
    """Save, then check deferred constraints (such as tenant foreign keys) immediately."""
    obj.save()
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


# --- Reads -------------------------------------------------------------------------------------


def test_queries_without_a_tenant_context_fail_closed(tenant_a: Tenant) -> None:
    make_user(tenant_a, email="alice@example.com")

    with pytest.raises(TenantContextMissing):
        list(User.objects.all())
    with pytest.raises(TenantContextMissing):
        User.objects.count()


def test_each_tenant_sees_only_its_own_rows(tenant_a: Tenant, tenant_b: Tenant) -> None:
    make_user(tenant_a, email="alice@example.com")
    bob = make_user(tenant_b, email="bob@example.com")

    with tenant_context(tenant_a):
        assert list(User.objects.values_list("email", flat=True)) == ["alice@example.com"]
        assert not User.objects.filter(pk=bob.pk).exists()
        with pytest.raises(User.DoesNotExist):
            User.objects.get(pk=bob.pk)


# --- Writes ------------------------------------------------------------------------------------


def test_new_rows_take_the_active_tenant(tenant_a: Tenant) -> None:
    with tenant_context(tenant_a):
        user = User(account_type=AccountType.CUSTOMER, email="new@example.com")
        user.set_unusable_password()
        user.save()

    assert user.tenant_id == tenant_a.pk


def test_writes_without_a_tenant_context_are_refused(tenant_a: Tenant) -> None:
    user = User(tenant=tenant_a, account_type=AccountType.CUSTOMER, email="x@example.com")

    with pytest.raises(TenantContextMissing):
        user.save()


def test_saving_or_deleting_another_tenants_row_is_refused(
    tenant_a: Tenant, tenant_b: Tenant
) -> None:
    bob = make_user(tenant_b, email="bob@example.com", full_name="Bob")

    with tenant_context(tenant_a):
        bob.full_name = "Changed by tenant A"
        with pytest.raises(CrossTenantAccessError):
            bob.save()
        with pytest.raises(CrossTenantAccessError):
            bob.delete()
    with tenant_context(tenant_b):
        assert User.objects.get(pk=bob.pk).full_name == "Bob"


def test_a_rows_tenant_never_changes(tenant_a: Tenant, tenant_b: Tenant) -> None:
    alice = make_user(tenant_a, email="alice@example.com")

    with tenant_context(tenant_a):
        reloaded = User.objects.get(pk=alice.pk)
        reloaded.tenant = tenant_b
        with pytest.raises(TenantImmutableError):
            reloaded.save()
        with pytest.raises(TenantImmutableError):
            User.objects.filter(pk=alice.pk).update(tenant=tenant_b)
    with unscoped_access("test: privileged code still cannot move rows"):
        reloaded = User.objects.get(pk=alice.pk)
        reloaded.tenant = tenant_b
        with pytest.raises(TenantImmutableError):
            reloaded.save()


def test_bulk_queryset_operations_stay_inside_the_tenant(
    tenant_a: Tenant, tenant_b: Tenant
) -> None:
    make_user(tenant_a, email="alice@example.com")
    bob = make_user(tenant_b, email="bob@example.com", full_name="Bob")

    with tenant_context(tenant_a):
        assert User.objects.update(full_name="Bulk renamed") == 1
        assert User.objects.all().delete()[0] == 1

    with tenant_context(tenant_b):
        assert User.objects.get(pk=bob.pk).full_name == "Bob"


def test_bulk_create_and_update_respect_ownership(tenant_a: Tenant, tenant_b: Tenant) -> None:
    with tenant_context(tenant_a):
        created = User.objects.bulk_create(
            [
                User(account_type=AccountType.CUSTOMER, email="one@example.com", password="!"),
                User(account_type=AccountType.CUSTOMER, email="two@example.com", password="!"),
            ]
        )
        assert {user.tenant_id for user in created} == {tenant_a.pk}

        foreign = User(
            tenant=tenant_b, account_type=AccountType.CUSTOMER, email="x@example.com", password="!"
        )
        with pytest.raises(CrossTenantAccessError):
            User.objects.bulk_create([foreign])
        with pytest.raises(TenantImmutableError):
            User.objects.bulk_update(created, ["tenant"])


# --- Relationships -----------------------------------------------------------------------------


def test_postgresql_rejects_cross_tenant_foreign_keys(tenant_a: Tenant, tenant_b: Tenant) -> None:
    bob = make_user(tenant_b, email="bob@example.com")

    with tenant_context(tenant_a):
        event = AuditEvent(
            action="test.cross_tenant",
            actor_type=ActorType.USER,
            actor_user_id=bob.pk,  # raw id, bypassing every Python-level check
            resource_type="identity.user",
        )
        with (
            pytest.raises(IntegrityError, match="audit_auditevent_actor_same_tenant"),
            transaction.atomic(),
        ):
            _save_and_check_constraints(event)


def test_same_tenant_foreign_keys_are_accepted(tenant_a: Tenant) -> None:
    alice = make_user(tenant_a, email="alice@example.com")

    with tenant_context(tenant_a), transaction.atomic():
        _save_and_check_constraints(
            AuditEvent(
                action="test.same_tenant",
                actor_type=ActorType.USER,
                actor_user=alice,
                resource_type="identity.user",
            )
        )


def test_validation_reports_cross_tenant_references(tenant_a: Tenant, tenant_b: Tenant) -> None:
    bob = make_user(tenant_b, email="bob@example.com")

    with tenant_context(tenant_a):
        event = AuditEvent(
            action="test.cross_tenant",
            actor_type=ActorType.USER,
            actor_user_id=bob.pk,
            resource_type="identity.user",
        )
        with pytest.raises(ValidationError) as excinfo:
            event.full_clean()

    assert "actor_user" in excinfo.value.message_dict


# --- Privileged access -------------------------------------------------------------------------


def test_unscoped_access_is_explicit_and_logged(
    tenant_a: Tenant, tenant_b: Tenant, caplog: pytest.LogCaptureFixture
) -> None:
    make_user(tenant_a, email="alice@example.com")
    make_user(tenant_b, email="bob@example.com")

    with unscoped_access("integrity report across all tenants"):
        assert User.objects.count() == 2

    assert "Unscoped tenant access started: integrity report across all tenants" in caplog.text


# --- Requests ----------------------------------------------------------------------------------


def _capturing_middleware(seen: list[uuid.UUID | None]) -> TenantMiddleware:
    def view(request: HttpRequest) -> HttpResponse:
        seen.append(get_active_tenant_id_or_none())
        return HttpResponse()

    return TenantMiddleware(view)


def test_request_data_can_never_choose_the_tenant(
    rf: RequestFactory, deployment_tenant: Tenant, tenant_b: Tenant
) -> None:
    seen: list[uuid.UUID | None] = []
    request = rf.get(
        f"/api/anything/?tenant={tenant_b.pk}&tenant_id={tenant_b.pk}",
        HTTP_X_TENANT_ID=str(tenant_b.pk),
        HTTP_HOST="store-b.example.com",
    )

    _capturing_middleware(seen)(request)

    assert seen == [deployment_tenant.pk]
    assert get_active_tenant_id_or_none() is None  # nothing leaks past the request


def test_requests_without_a_configured_tenant_get_no_tenant(
    rf: RequestFactory, settings: Settings
) -> None:
    settings.MATECART_TENANT_ID = None
    seen: list[uuid.UUID | None] = []

    _capturing_middleware(seen)(rf.get("/api/anything/"))

    assert seen == [None]


class _CountUsersView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request: Request) -> Response:
        return Response({"users": User.objects.count()})


def test_tenancy_failures_answer_503_without_details(tenant_a: Tenant) -> None:
    response = _CountUsersView.as_view()(APIRequestFactory().get("/anything/"))

    assert response.status_code == 503
    assert response.data == {
        "error": {"code": "tenant_unavailable", "message": "This store is not available right now."}
    }


# --- Background tasks --------------------------------------------------------------------------


@shared_task(base=TenantTask, name="tests.count_users")
def count_users(*, tenant_id: str) -> int:
    return User.objects.count()


def test_tasks_run_inside_their_verified_tenant(
    deployment_tenant: Tenant, tenant_b: Tenant
) -> None:
    make_user(deployment_tenant, email="one@example.com")
    make_user(deployment_tenant, email="two@example.com")
    make_user(tenant_b, email="other@example.com")

    assert count_users.delay(tenant_id=str(deployment_tenant.pk)).get(timeout=5) == 2


def test_tasks_require_an_explicit_tenant(deployment_tenant: Tenant) -> None:
    with pytest.raises(TenantContextMissing):
        count_users()


def test_tasks_cannot_target_another_tenant(deployment_tenant: Tenant, tenant_b: Tenant) -> None:
    with pytest.raises(CrossTenantAccessError):
        count_users.delay(tenant_id=str(tenant_b.pk))


def test_tasks_reject_unknown_or_malformed_tenants(settings: Settings, db: None) -> None:
    settings.MATECART_TENANT_ID = uuid7()
    with pytest.raises(TenantNotProvisioned):
        count_users.delay(tenant_id=str(settings.MATECART_TENANT_ID))
    with pytest.raises(TenancyError):
        count_users.delay(tenant_id="not-a-uuid")


# --- Cache and storage namespaces --------------------------------------------------------------


def test_cache_keys_are_namespaced_by_tenant(tenant_a: Tenant) -> None:
    with tenant_context(tenant_a):
        assert tenant_cache_key("catalog", "menu") == f"tenant:{tenant_a.pk}:catalog:menu"
        with pytest.raises(ValueError, match="key part"):
            tenant_cache_key()
    with pytest.raises(TenantContextMissing):
        tenant_cache_key("catalog")


def test_storage_paths_are_namespaced_and_ignore_client_paths(tenant_a: Tenant) -> None:
    user = make_user(tenant_a, email="alice@example.com")

    path = tenant_storage_path(user, "..\\..\\etc/Passwd.JPG")
    odd = tenant_storage_path(user, "archive.tar.gz;rm -rf")

    assert path.startswith(f"tenants/{tenant_a.pk}/identity/user/")
    assert path.endswith(".jpg")
    assert ".." not in path
    assert odd.rsplit("/", 1)[-1].isalnum()


def test_active_tenant_helpers(tenant_a: Tenant) -> None:
    assert get_active_tenant_id_or_none() is None
    with tenant_context(tenant_a):
        assert get_active_tenant_id() == tenant_a.pk
