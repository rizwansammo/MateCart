import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from django.conf import settings
from django.db import close_old_connections, connections
from django.utils import timezone
from rest_framework.test import APIClient

from control.accounts import sessions
from control.accounts.models import AdminRole, AdminUser, SessionStatus
from control.platform.models import (
    BackupObservation,
    Client,
    ConsoleAuditEvent,
    ConsoleNotification,
    Deployment,
    DomainRecord,
    MonitoringCredential,
    MonitoringObservation,
    OperationalAlert,
    Server,
    StoreRegistration,
)
from control.platform.services import evaluate_alerts, ingest_observation
from control.platform.tasks import evaluate_monitoring, retention

pytestmark = pytest.mark.django_db


def signed_in(
    role: str = AdminRole.PLATFORM_OWNER, *, recent: bool = True
) -> tuple[APIClient, AdminUser]:
    user = AdminUser.objects.create_user(
        email=f"{uuid.uuid4()}@example.test",
        password="Not-used-password-928!",
        role=role,
        activated_at=timezone.now(),
        email_mfa_enabled_at=timezone.now(),
    )
    session, token = sessions.start(user, status=SessionStatus.ACTIVE)
    if recent:
        sessions.mark_reauthenticated(session)
    else:
        session.authenticated_at = timezone.now() - timedelta(days=1)
        session.save(update_fields=["authenticated_at"])
    client = APIClient()
    client.cookies[settings.MATECART_CONSOLE_COOKIE] = token
    return client, user


def infrastructure() -> tuple[Client, StoreRegistration, Server, Deployment]:
    client = Client.objects.create(
        display_name="River Trading",
        client_type="business",
        country="BD",
        time_zone="Asia/Dhaka",
        status="active",
    )
    store = StoreRegistration.objects.create(
        client=client,
        name="River Shop",
        handle=f"river-{uuid.uuid4().hex[:8]}",
        preset="general",
        country="BD",
        default_currency="BDT",
        time_zone="Asia/Dhaka",
        default_language="en",
        supported_languages=["en", "bn"],
        desired_theme="general-marketplace",
        desired_modules=[
            "catalog",
            "inventory",
            "content_pages",
            "customer_accounts",
            "cart_checkout",
        ],
        status="registered",
    )
    server = Server.objects.create(name="NetaMate VPS", ownership="netamate", country="BD")
    deployment = Deployment.objects.create(
        store=store,
        server=server,
        environment="production",
        reference=f"DEP-{uuid.uuid4().hex[:8]}",
    )
    return client, store, server, deployment


def credential(deployment: Deployment, suffix: str = "0001") -> MonitoringCredential:
    return MonitoringCredential.objects.create(
        deployment=deployment,
        token_hash=hashlib.sha256(f"token-{uuid.uuid4()}".encode()).hexdigest(),
        label="Test telemetry",
        last_four=suffix,
    )


def observation(
    deployment: Deployment,
    monitoring_credential: MonitoringCredential,
    *,
    observed_at: Any,
    store_status: str = "healthy",
    api_status: str = "healthy",
    application_version: str = "current",
) -> MonitoringObservation:
    return MonitoringObservation.objects.create(
        deployment=deployment,
        credential=monitoring_credential,
        report_id=uuid.uuid4(),
        observed_at=observed_at,
        store_status=store_status,
        api_status=api_status,
        application_version=application_version,
    )


def telemetry_payload(
    deployment: Deployment,
    *,
    observed_at: Any,
    status: str,
    version: str,
) -> dict[str, Any]:
    return {
        "deployment_id": str(deployment.pk),
        "store_id": str(deployment.store_id),
        "report_id": str(uuid.uuid4()),
        "observed_at": observed_at.isoformat(),
        "store_status": status,
        "api_status": status,
        "application_version": version,
    }


def test_fleet_health_uses_each_deployments_latest_observation() -> None:
    api, _ = signed_in()
    now = timezone.now()
    expected = ["healthy", "unavailable", "degraded", "unknown", "stale"]
    deployment_ids: list[str] = []
    for index, state in enumerate(expected):
        _, _, _, deployment = infrastructure()
        deployment_ids.append(str(deployment.pk))
        if state == "unknown":
            continue
        report_credential = credential(deployment, f"{index:04d}")
        if state in {"unavailable", "degraded"}:
            observation(
                deployment,
                report_credential,
                observed_at=now - timedelta(minutes=2),
            )
        observed_at = now - timedelta(minutes=8) if state == "stale" else now
        observation(
            deployment,
            report_credential,
            observed_at=observed_at,
            store_status="healthy" if state == "stale" else state,
            api_status="healthy" if state == "stale" else state,
        )
        deployment.last_observed_at = observed_at
        deployment.save(update_fields=["last_observed_at", "updated_at"])

    listing = api.get("/api/console/deployments/")
    states = {row["id"]: row["health_state"] for row in listing.data["results"]}
    assert [states[item] for item in deployment_ids] == expected
    for deployment_id, state in zip(deployment_ids, expected, strict=True):
        assert api.get(f"/api/console/deployments/{deployment_id}/").data["health_state"] == state

    overview = api.get("/api/console/overview/")
    assert overview.data["healthy"] == 1
    assert overview.data["unavailable"] == 1
    assert overview.data["degraded"] == 1
    assert overview.data["unknown"] == 1
    assert overview.data["stale"] == 1


def test_observation_alert_resolves_and_reactivates_without_duplicates_or_storms() -> None:
    _, user = signed_in()
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    failed = observation(
        deployment,
        report_credential,
        observed_at=timezone.now(),
        store_status="unavailable",
        api_status="unavailable",
    )
    evaluate_alerts(failed)
    evaluate_alerts(failed)
    assert OperationalAlert.objects.filter(category="store_unavailable").count() == 1
    assert ConsoleNotification.objects.filter(user=user).count() == 2

    recovered = observation(
        deployment,
        report_credential,
        observed_at=timezone.now() + timedelta(seconds=1),
    )
    evaluate_alerts(recovered)
    assert OperationalAlert.objects.get(category="store_unavailable").status == "resolved"

    recurrence = observation(
        deployment,
        report_credential,
        observed_at=timezone.now() + timedelta(seconds=2),
        store_status="unavailable",
        api_status="unavailable",
    )
    evaluate_alerts(recurrence)
    evaluate_alerts(recurrence)
    alert = OperationalAlert.objects.get(category="store_unavailable")
    assert alert.status == "open"
    assert alert.source_observation == recurrence
    assert OperationalAlert.objects.filter(category="store_unavailable").count() == 1
    assert ConsoleNotification.objects.filter(user=user).count() == 4

    metric_failure = observation(
        deployment,
        report_credential,
        observed_at=timezone.now() + timedelta(seconds=3),
    )
    metric_failure.cpu_percent = Decimal("95.00")
    metric_failure.save(update_fields=["cpu_percent"])
    evaluate_alerts(metric_failure)
    assert OperationalAlert.objects.get(category="cpu_high").status == "open"
    missing_metric = observation(
        deployment,
        report_credential,
        observed_at=timezone.now() + timedelta(seconds=4),
    )
    evaluate_alerts(missing_metric)
    assert OperationalAlert.objects.get(category="cpu_high").status == "open"
    metric_recovery = observation(
        deployment,
        report_credential,
        observed_at=timezone.now() + timedelta(seconds=5),
    )
    metric_recovery.cpu_percent = Decimal("20.00")
    metric_recovery.save(update_fields=["cpu_percent"])
    evaluate_alerts(metric_recovery)
    assert OperationalAlert.objects.get(category="cpu_high").status == "resolved"


def test_monitoring_credential_metadata_revocation_and_ingestion() -> None:
    api, _ = signed_in()
    _, store, _, deployment = infrastructure()
    first = api.post(
        f"/api/console/deployments/{deployment.pk}/monitoring-credentials/",
        {"label": "Primary"},
        format="json",
    )
    second = api.post(
        f"/api/console/deployments/{deployment.pk}/monitoring-credentials/",
        {"label": "Replacement"},
        format="json",
    )
    assert first.status_code == second.status_code == 201

    listing = api.get(f"/api/console/deployments/{deployment.pk}/monitoring-credentials/")
    assert listing.status_code == 200
    assert listing.data["count"] == 2
    assert all("token" not in row and "token_hash" not in row for row in listing.data["results"])

    revoked = api.patch(
        f"/api/console/deployments/{deployment.pk}/monitoring-credentials/{first.data['id']}/",
        {"action": "revoke"},
        format="json",
    )
    assert revoked.status_code == 200
    assert revoked.data["revoked_at"] is not None
    repeated = api.patch(
        f"/api/console/deployments/{deployment.pk}/monitoring-credentials/{first.data['id']}/",
        {"action": "revoke"},
        format="json",
    )
    assert repeated.status_code == 200
    assert ConsoleAuditEvent.objects.filter(action="monitoring.credential.revoked").count() == 1

    anonymous = APIClient()
    report = telemetry_payload(
        deployment,
        observed_at=timezone.now(),
        status="healthy",
        version="v1",
    )
    assert (
        anonymous.post(
            "/api/console/telemetry/observations/",
            report,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {first.data['token']}",
        ).status_code
        == 400
    )
    report["report_id"] = str(uuid.uuid4())
    assert (
        anonymous.post(
            "/api/console/telemetry/observations/",
            report,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {second.data['token']}",
        ).status_code
        == 202
    )
    assert str(deployment.store_id) == str(store.pk)


def test_monitoring_credential_management_requires_permission_and_recent_authentication() -> None:
    owner, _ = signed_in()
    _, _, _, deployment = infrastructure()
    created = owner.post(
        f"/api/console/deployments/{deployment.pk}/monitoring-credentials/",
        {"label": "Primary"},
        format="json",
    )
    auditor, _ = signed_in(AdminRole.READ_ONLY_AUDITOR)
    endpoint = (
        f"/api/console/deployments/{deployment.pk}/monitoring-credentials/{created.data['id']}/"
    )
    assert auditor.patch(endpoint, {"action": "revoke"}, format="json").status_code == 403
    old_session, _ = signed_in(recent=False)
    assert old_session.patch(endpoint, {"action": "revoke"}, format="json").status_code == 403
    assert (
        old_session.get(
            f"/api/console/deployments/{deployment.pk}/monitoring-credentials/"
        ).status_code
        == 403
    )


def test_scheduled_alerts_recover_recur_and_remain_idempotent() -> None:
    _, user = signed_in()
    _, store, _, deployment = infrastructure()
    now = timezone.now()

    deployment.last_observed_at = now - timedelta(minutes=6)
    deployment.save(update_fields=["last_observed_at", "updated_at"])
    evaluate_monitoring()
    evaluate_monitoring()
    heartbeat = OperationalAlert.objects.get(category="missing_heartbeat")
    assert heartbeat.status == "open"

    deployment.last_observed_at = now
    deployment.save(update_fields=["last_observed_at", "updated_at"])
    evaluate_monitoring()
    heartbeat.refresh_from_db()
    assert heartbeat.status == "resolved"

    deployment.last_observed_at = now - timedelta(minutes=6)
    deployment.save(update_fields=["last_observed_at", "updated_at"])
    evaluate_monitoring()
    evaluate_monitoring()
    heartbeat.refresh_from_db()
    assert heartbeat.status == "open"
    assert OperationalAlert.objects.filter(category="missing_heartbeat").count() == 1

    BackupObservation.objects.create(
        deployment=deployment,
        backup_type="database",
        observed_at=now,
        backup_at=now - timedelta(days=2),
        status="failed",
    )
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="backup_failure").status == "open"
    assert OperationalAlert.objects.get(category="backup_overdue").status == "open"
    BackupObservation.objects.create(
        deployment=deployment,
        backup_type="database",
        observed_at=now + timedelta(seconds=1),
        backup_at=now,
        status="successful",
    )
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="backup_failure").status == "resolved"
    assert OperationalAlert.objects.get(category="backup_overdue").status == "resolved"
    BackupObservation.objects.create(
        deployment=deployment,
        backup_type="database",
        observed_at=now + timedelta(seconds=2),
        backup_at=now - timedelta(days=2),
        status="failed",
    )
    evaluate_monitoring()
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="backup_failure").status == "open"
    assert OperationalAlert.objects.get(category="backup_overdue").status == "open"

    domain = DomainRecord.objects.create(
        store=store,
        deployment=deployment,
        domain=f"{uuid.uuid4().hex}.example.test",
        certificate_expires_at=now - timedelta(days=1),
    )
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="ssl_expired").status == "open"
    domain.certificate_expires_at = now + timedelta(days=60)
    domain.save(update_fields=["certificate_expires_at", "updated_at"])
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="ssl_expired").status == "resolved"
    domain.certificate_expires_at = now - timedelta(days=1)
    domain.save(update_fields=["certificate_expires_at", "updated_at"])
    evaluate_monitoring()
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="ssl_expired").status == "open"

    domain.certificate_expires_at = now + timedelta(days=10)
    domain.save(update_fields=["certificate_expires_at", "updated_at"])
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="ssl_expiring").status == "open"
    domain.certificate_expires_at = now + timedelta(days=60)
    domain.save(update_fields=["certificate_expires_at", "updated_at"])
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="ssl_expiring").status == "resolved"
    domain.certificate_expires_at = now + timedelta(days=10)
    domain.save(update_fields=["certificate_expires_at", "updated_at"])
    evaluate_monitoring()
    evaluate_monitoring()
    assert OperationalAlert.objects.get(category="ssl_expiring").status == "open"

    assert OperationalAlert.objects.filter(category="backup_failure").count() == 1
    assert OperationalAlert.objects.filter(category="backup_overdue").count() == 1
    assert OperationalAlert.objects.filter(category="ssl_expired").count() == 1
    assert OperationalAlert.objects.filter(category="ssl_expiring").count() == 1
    assert ConsoleNotification.objects.filter(user=user).count() == 10


def test_retention_keeps_referenced_incident_evidence_and_deletes_unreferenced_history() -> None:
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    old = timezone.now() - timedelta(days=91)
    referenced = observation(deployment, report_credential, observed_at=old)
    unreferenced = observation(
        deployment, report_credential, observed_at=old - timedelta(seconds=1)
    )
    recent = observation(deployment, report_credential, observed_at=timezone.now())
    alert = OperationalAlert.objects.create(
        deployment=deployment,
        category="store_unavailable",
        severity="critical",
        deduplication_key=f"deployment:{deployment.pk}:retention-test",
        title="Incident evidence",
        source_observation=referenced,
    )

    retention()
    retention()

    assert MonitoringObservation.objects.filter(pk=referenced.pk).exists()
    assert not MonitoringObservation.objects.filter(pk=unreferenced.pk).exists()
    assert MonitoringObservation.objects.filter(pk=recent.pk).exists()
    assert OperationalAlert.objects.filter(pk=alert.pk).exists()


def test_out_of_order_telemetry_is_historical_not_authoritative() -> None:
    api, _ = signed_in()
    _, _, _, deployment = infrastructure()
    created = api.post(
        f"/api/console/deployments/{deployment.pk}/monitoring-credentials/",
        {"label": "Primary"},
        format="json",
    )
    token = cast(str, created.data["token"])
    anonymous = APIClient()
    now = timezone.now()
    newer_failure = telemetry_payload(
        deployment,
        observed_at=now,
        status="unavailable",
        version="v2",
    )
    older_healthy = telemetry_payload(
        deployment,
        observed_at=now - timedelta(minutes=1),
        status="healthy",
        version="v1",
    )
    for payload in (newer_failure, older_healthy):
        response = anonymous.post(
            "/api/console/telemetry/observations/",
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 202, response.data

    deployment.refresh_from_db()
    assert deployment.last_observed_at == now
    assert deployment.observed_version == "v2"
    assert OperationalAlert.objects.get(category="store_unavailable").status == "open"
    assert deployment.observations.count() == 2

    equal_healthy = telemetry_payload(
        deployment,
        observed_at=now,
        status="healthy",
        version="v2-equal",
    )
    response = anonymous.post(
        "/api/console/telemetry/observations/",
        equal_healthy,
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 202, response.data
    deployment.refresh_from_db()
    assert deployment.observed_version == "v2"
    assert OperationalAlert.objects.get(category="store_unavailable").status == "open"

    newer_recovery = telemetry_payload(
        deployment,
        observed_at=now + timedelta(seconds=1),
        status="healthy",
        version="v3",
    )
    older_failure = telemetry_payload(
        deployment,
        observed_at=now - timedelta(seconds=30),
        status="unavailable",
        version="v2-old",
    )
    for payload in (newer_recovery, older_failure):
        response = anonymous.post(
            "/api/console/telemetry/observations/",
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert response.status_code == 202, response.data

    deployment.refresh_from_db()
    assert deployment.last_observed_at == now + timedelta(seconds=1)
    assert deployment.last_healthy_at == now + timedelta(seconds=1)
    assert deployment.observed_version == "v3"
    assert OperationalAlert.objects.get(category="store_unavailable").status == "resolved"


@pytest.mark.django_db(transaction=True)
def test_concurrent_telemetry_keeps_the_newest_state() -> None:
    _, _, _, deployment = infrastructure()
    raw_token = f"concurrent-{uuid.uuid4()}"
    MonitoringCredential.objects.create(
        deployment=deployment,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        label="Concurrent telemetry",
        last_four=raw_token[-4:],
    )
    now = timezone.now()
    reports = [
        {
            **telemetry_payload(
                deployment,
                observed_at=now - timedelta(seconds=1),
                status="healthy",
                version="v1",
            ),
            "deployment_id": deployment.pk,
            "store_id": deployment.store_id,
            "report_id": uuid.uuid4(),
            "observed_at": now - timedelta(seconds=1),
        },
        {
            **telemetry_payload(
                deployment,
                observed_at=now,
                status="unavailable",
                version="v2",
            ),
            "deployment_id": deployment.pk,
            "store_id": deployment.store_id,
            "report_id": uuid.uuid4(),
            "observed_at": now,
        },
    ]

    def submit(payload: dict[str, Any]) -> None:
        close_old_connections()
        try:
            ingest_observation(raw_token, payload)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(submit, reports))

    deployment.refresh_from_db()
    assert deployment.last_observed_at == now
    assert deployment.observed_version == "v2"
    assert deployment.observations.count() == 2
    assert OperationalAlert.objects.get(category="store_unavailable").status == "open"


def test_configuration_permissions_prerequisites_and_pending_application() -> None:
    owner, _ = signed_in()
    client, _, _, _ = infrastructure()
    payload = {
        "client": str(client.pk),
        "name": "Configuration Store",
        "handle": f"configuration-{uuid.uuid4().hex[:8]}",
        "preset": "grocery",
        "country": "BD",
        "default_currency": "BDT",
        "time_zone": "Asia/Dhaka",
        "default_language": "en",
        "supported_languages": ["en"],
        "desired_theme": "grocery-quickshop",
        "desired_modules": [
            "catalog",
            "inventory",
            "content_pages",
            "customer_accounts",
        ],
        "status": "registered",
    }
    created = owner.post("/api/console/stores/", payload, format="json")
    assert created.status_code == 201, created.data
    endpoint = f"/api/console/stores/{created.data['id']}/"
    invalid = owner.patch(
        endpoint,
        {"desired_modules": created.data["desired_modules"] + ["delivery_slots"]},
        format="json",
    )
    assert invalid.status_code == 400
    changed = owner.patch(
        endpoint,
        {"desired_modules": created.data["desired_modules"] + ["cart_checkout"]},
        format="json",
    )
    assert changed.status_code == 200
    assert changed.data["desired_revision"] == 2
    assert changed.data["applied_revision"] == 0
    assert changed.data["applied_modules"] == []
    assert changed.data["configuration_state"] == "pending_application"
    auditor, _ = signed_in(AdminRole.READ_ONLY_AUDITOR)
    assert auditor.patch(endpoint, {"desired_modules": []}, format="json").status_code == 403


def test_monitoring_latest_fields_preserve_missing_and_stale_values() -> None:
    api, _ = signed_in()
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    report = observation(
        deployment,
        report_credential,
        observed_at=timezone.now() - timedelta(minutes=6),
        application_version="v1",
    )
    response = api.get("/api/console/monitoring/?latest=true")
    assert response.status_code == 200
    row = response.data["results"][0]
    assert str(row["id"]) == str(report.pk)
    assert row["health_state"] == "stale"
    assert row["freshness"] == "stale"
    assert row["response_ms"] is None
    assert row["cpu_percent"] is None
    assert row["ram_percent"] is None
    assert row["disk_percent"] is None

    _, _, _, current_deployment = infrastructure()
    current_credential = credential(current_deployment, "0002")
    current = observation(
        current_deployment,
        current_credential,
        observed_at=timezone.now(),
        application_version="v2",
    )
    current.response_ms = 123
    current.cpu_percent = Decimal("41.00")
    current.ram_percent = Decimal("52.00")
    current.disk_percent = Decimal("63.00")
    current.visitor_count_24h = 74
    current.save(
        update_fields=[
            "response_ms",
            "cpu_percent",
            "ram_percent",
            "disk_percent",
            "visitor_count_24h",
        ]
    )
    response = api.get("/api/console/monitoring/?latest=true")
    rows = {str(item["deployment_id"]): item for item in response.data["results"]}
    current_row = rows[str(current_deployment.pk)]
    assert current_row["health_state"] == "healthy"
    assert current_row["freshness"] == "current"
    assert current_row["response_ms"] == 123
    assert str(current_row["cpu_percent"]) == "41.00"
    assert str(current_row["ram_percent"]) == "52.00"
    assert str(current_row["disk_percent"]) == "63.00"
    assert current_row["visitor_count_24h"] == 74
    assert current_row["application_version"] == "v2"


def test_server_ownership_is_validated_before_database_constraints() -> None:
    api, _ = signed_in()
    response = api.post(
        "/api/console/servers/",
        {"name": "Client VPS", "ownership": "client", "country": "BD"},
        format="json",
    )
    assert response.status_code == 400
    client, _, _, _ = infrastructure()
    response = api.post(
        "/api/console/servers/",
        {
            "name": "Client VPS",
            "ownership": "client",
            "owner_client": str(client.pk),
            "country": "BD",
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert str(response.data["owner_client"]) == str(client.pk)
    auditor, _ = signed_in(AdminRole.READ_ONLY_AUDITOR)
    assert (
        auditor.post(
            "/api/console/servers/",
            {"name": "Forbidden VPS", "ownership": "netamate", "country": "BD"},
            format="json",
        ).status_code
        == 403
    )
