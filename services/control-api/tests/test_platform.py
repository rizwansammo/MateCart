import uuid
from datetime import timedelta
from typing import Any, cast

import pytest
from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from control.accounts import sessions
from control.accounts.models import AdminRole, AdminUser, SessionStatus
from control.platform.models import ConsoleAuditEvent, OperationalAlert
from control.platform.tasks import evaluate_monitoring

pytestmark = pytest.mark.django_db


def signed_in(role: str = AdminRole.PLATFORM_OWNER) -> tuple[APIClient, AdminUser]:
    user = AdminUser.objects.create_user(
        email=f"{uuid.uuid4()}@example.test",
        password="Not-used-password-928!",
        role=role,
        activated_at=timezone.now(),
        email_mfa_enabled_at=timezone.now(),
    )
    session, token = sessions.start(user, status=SessionStatus.ACTIVE)
    sessions.mark_reauthenticated(session)
    client = APIClient()
    client.cookies[settings.MATECART_CONSOLE_COOKIE] = token
    return client, user


def client_payload() -> dict[str, Any]:
    return {
        "display_name": "River Trading",
        "client_type": "business",
        "country": "BD",
        "time_zone": "Asia/Dhaka",
        "status": "onboarding",
    }


def create_client(api: APIClient) -> dict[str, Any]:
    response = api.post("/api/console/clients/", client_payload(), format="json")
    assert response.status_code == 201, response.data
    return cast(dict[str, Any], response.data)


def store_payload(client_id: str) -> dict[str, Any]:
    return {
        "client": client_id,
        "name": "River Shop",
        "handle": f"river-{uuid.uuid4().hex[:8]}",
        "preset": "general",
        "country": "BD",
        "default_currency": "BDT",
        "time_zone": "Asia/Dhaka",
        "default_language": "en",
        "supported_languages": ["en", "bn"],
        "desired_theme": "general-marketplace",
        "desired_modules": [
            "catalog",
            "inventory",
            "content_pages",
            "customer_accounts",
            "cart_checkout",
        ],
        "status": "registered",
    }


def infrastructure(api: APIClient) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    client = create_client(api)
    store_response = api.post("/api/console/stores/", store_payload(client["id"]), format="json")
    assert store_response.status_code == 201, store_response.data
    server_response = api.post(
        "/api/console/servers/",
        {"name": "NetaMate VPS 1", "ownership": "netamate", "country": "BD"},
        format="json",
    )
    assert server_response.status_code == 201, server_response.data
    deployment_response = api.post(
        "/api/console/deployments/",
        {
            "store": store_response.data["id"],
            "server": server_response.data["id"],
            "environment": "production",
            "reference": "DEP-001",
            "desired_version": "2a9e448",
            "state": "awaiting_deployment_integration",
        },
        format="json",
    )
    assert deployment_response.status_code == 201, deployment_response.data
    return store_response.data, server_response.data, deployment_response.data


def test_client_registry_search_contacts_and_history() -> None:
    api, _ = signed_in()
    created = create_client(api)
    response = api.post(
        f"/api/console/clients/{created['id']}/contacts/",
        {"name": "Rina", "email": "rina@example.test", "is_primary": True},
        format="json",
    )
    assert response.status_code == 201
    response = api.patch(
        f"/api/console/clients/{created['id']}/", {"status": "active"}, format="json"
    )
    assert response.status_code == 200
    response = api.get("/api/console/clients/?q=River&status=active")
    assert response.status_code == 200
    assert response.data["count"] == 1
    detail = api.get(f"/api/console/clients/{created['id']}/")
    assert len(detail.data["contacts"]) == 1
    assert ConsoleAuditEvent.objects.filter(action="client.updated").exists()


def test_store_uuid7_configuration_and_manifest_are_truthful() -> None:
    api, _ = signed_in()
    client = create_client(api)
    response = api.post("/api/console/stores/", store_payload(client["id"]), format="json")
    assert response.status_code == 201, response.data
    store_id = uuid.UUID(response.data["id"])
    assert store_id.version == 7
    assert response.data["configuration_state"] == "pending_application"
    detail = api.get(f"/api/console/stores/{store_id}/")
    assert detail.data["provisioning_manifest"]["store_id"] == str(store_id)
    assert "password" not in detail.data["provisioning_manifest"]
    assert detail.data["applied_revision"] == 0
    changed = api.patch(
        f"/api/console/stores/{store_id}/",
        {"desired_modules": response.data["desired_modules"] + ["live_chat"]},
        format="json",
    )
    assert changed.status_code == 200, changed.data
    assert changed.data["desired_revision"] == 2
    assert changed.data["applied_revision"] == 0


def test_store_rejects_unknown_modules_and_incompatible_theme() -> None:
    api, _ = signed_in()
    client = create_client(api)
    payload = store_payload(client["id"])
    payload["desired_modules"] = ["catalog", "not_real"]
    response = api.post("/api/console/stores/", payload, format="json")
    assert response.status_code == 400
    payload = store_payload(client["id"])
    payload["desired_theme"] = "books-literary"
    response = api.post("/api/console/stores/", payload, format="json")
    assert response.status_code == 400


def test_restricted_roles_are_enforced_by_backend() -> None:
    auditor, _ = signed_in(AdminRole.READ_ONLY_AUDITOR)
    response = auditor.post("/api/console/clients/", client_payload(), format="json")
    assert response.status_code == 403
    assert auditor.get("/api/console/clients/").status_code == 200
    support, _ = signed_in(AdminRole.SUPPORT_OPERATOR)
    assert support.get("/api/console/administrators/").status_code == 200
    assert (
        support.post(
            "/api/console/administrators/",
            {"email": "new@example.test", "full_name": "New"},
            format="json",
        ).status_code
        == 403
    )


def test_deployment_starts_unknown_and_does_not_claim_execution() -> None:
    api, _ = signed_in()
    store, _, deployment = infrastructure(api)
    assert deployment["state"] == "awaiting_deployment_integration"
    domain = api.post(
        "/api/console/domains/",
        {
            "store": store["id"],
            "deployment": deployment["id"],
            "domain": f"{uuid.uuid4().hex}.example.test",
        },
        format="json",
    )
    assert domain.status_code == 201, domain.data
    backup = api.post(
        "/api/console/backups/",
        {
            "deployment": deployment["id"],
            "backup_type": "database",
            "observed_at": timezone.now().isoformat(),
            "status": "successful",
        },
        format="json",
    )
    assert backup.status_code == 201, backup.data
    detail = api.get(f"/api/console/deployments/{deployment['id']}/")
    assert detail.data["health_state"] == "unknown"
    assert detail.data["observed_version"] == ""
    assert len(detail.data["domains"]) == 1
    assert len(detail.data["backups"]) == 1
    store_detail = api.get(f"/api/console/stores/{store['id']}/")
    assert len(store_detail.data["deployments"]) == 1
    assert len(store_detail.data["domains"]) == 1


def test_telemetry_auth_binding_replay_and_alert_recovery() -> None:
    api, _ = signed_in()
    store, _, deployment = infrastructure(api)
    credential = api.post(
        f"/api/console/deployments/{deployment['id']}/monitoring-credentials/",
        {"label": "test agent"},
        format="json",
    )
    assert credential.status_code == 201
    token = credential.data["token"]
    report = {
        "deployment_id": deployment["id"],
        "store_id": store["id"],
        "report_id": str(uuid.uuid4()),
        "observed_at": timezone.now().isoformat(),
        "store_status": "unavailable",
        "api_status": "unavailable",
        "cpu_percent": "95.00",
        "application_version": "2a9e448",
        "backup_status": "failed",
        "backup_type": "database",
        "backup_at": (timezone.now() - timedelta(days=2)).isoformat(),
    }
    anonymous = APIClient()
    response = anonymous.post(
        "/api/console/telemetry/observations/",
        report,
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 202, response.data
    assert OperationalAlert.objects.filter(status="open", category="store_unavailable").exists()
    evaluate_monitoring()
    evaluate_monitoring()
    assert OperationalAlert.objects.filter(category="backup_failure").count() == 1
    assert OperationalAlert.objects.filter(category="backup_overdue").count() == 1
    replay = anonymous.post(
        "/api/console/telemetry/observations/",
        report,
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert replay.status_code == 400
    wrong = dict(report, report_id=str(uuid.uuid4()), deployment_id=str(uuid.uuid4()))
    assert (
        anonymous.post(
            "/api/console/telemetry/observations/",
            wrong,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 400
    )
    healthy = dict(
        report,
        report_id=str(uuid.uuid4()),
        observed_at=(timezone.now() + timedelta(seconds=1)).isoformat(),
        store_status="healthy",
        api_status="healthy",
        cpu_percent="20.00",
    )
    assert (
        anonymous.post(
            "/api/console/telemetry/observations/",
            healthy,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 202
    )
    assert OperationalAlert.objects.get(category="store_unavailable").status == "resolved"


def test_operation_needs_separate_approver_and_verified_completion() -> None:
    first, _ = signed_in()
    response = first.post(
        "/api/console/operations/",
        {"kind": "version_update", "title": "Update River", "status": "submitted"},
        format="json",
    )
    assert response.status_code == 201
    operation_id = response.data["id"]
    assert (
        first.patch(
            f"/api/console/operations/{operation_id}/",
            {"status": "approved"},
            format="json",
        ).status_code
        == 400
    )
    second, _ = signed_in()
    assert (
        second.patch(
            f"/api/console/operations/{operation_id}/",
            {"status": "approved"},
            format="json",
        ).status_code
        == 200
    )
    second.patch(
        f"/api/console/operations/{operation_id}/",
        {"status": "pending_execution"},
        format="json",
    )
    assert (
        second.patch(
            f"/api/console/operations/{operation_id}/",
            {"status": "completed"},
            format="json",
        ).status_code
        == 400
    )


def test_last_owner_and_self_promotion_protection() -> None:
    api, owner = signed_in()
    response = api.patch(
        f"/api/console/administrators/{owner.pk}/",
        {"role": AdminRole.PLATFORM_ADMIN},
        format="json",
    )
    assert response.status_code == 400
    second = AdminUser.objects.create_user(
        email="second-owner@example.test",
        role=AdminRole.PLATFORM_OWNER,
        is_active=True,
        activated_at=timezone.now(),
    )
    response = api.patch(
        f"/api/console/administrators/{second.pk}/",
        {"role": AdminRole.PLATFORM_ADMIN},
        format="json",
    )
    assert response.status_code == 200
    response = api.patch(
        f"/api/console/administrators/{owner.pk}/",
        {"is_active": False},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_audit_rows_are_database_immutable() -> None:
    api, _ = signed_in()
    create_client(api)
    event = ConsoleAuditEvent.objects.first()
    assert event is not None
    with pytest.raises(DatabaseError), transaction.atomic():
        ConsoleAuditEvent.objects.filter(pk=event.pk).update(action="tampered")
