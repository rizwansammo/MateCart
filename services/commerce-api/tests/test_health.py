import pytest
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.core import health
from commerce.core.serializers import ReadinessSerializer
from commerce.tenancy.models import Tenant

ALL_OK = {"database": "ok", "cache": "ok", "broker": "ok", "tenant": "ok"}


def test_liveness_reports_ok_without_touching_dependencies(api_client: APIClient) -> None:
    # No django_db mark: any database access here would fail the test.
    response = api_client.get("/api/health/live/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "commerce-api"}
    assert "no-store" in response["Cache-Control"]


def test_readiness_reports_every_dependency_ok(
    api_client: APIClient, deployment_tenant: Tenant
) -> None:
    response = api_client.get("/api/health/ready/")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "commerce-api", "checks": ALL_OK}
    assert ReadinessSerializer(data=body).is_valid()


@pytest.mark.django_db
def test_readiness_fails_until_the_tenant_is_configured_and_provisioned(
    api_client: APIClient, settings: Settings
) -> None:
    settings.MATECART_TENANT_ID = None
    not_configured = api_client.get("/api/health/ready/")

    settings.MATECART_TENANT_ID = Tenant(handle="unprovisioned").pk  # an id with no row
    not_provisioned = api_client.get("/api/health/ready/")

    for response in (not_configured, not_provisioned):
        assert response.status_code == 503
        assert response.json()["checks"] == {**ALL_OK, "tenant": "error"}


def test_readiness_fails_when_the_database_holds_other_tenants(
    api_client: APIClient, deployment_tenant: Tenant, tenant_b: Tenant
) -> None:
    response = api_client.get("/api/health/ready/")

    assert response.status_code == 503
    assert response.json()["checks"]["tenant"] == "error"


def test_readiness_returns_503_without_leaking_failure_details(
    api_client: APIClient,
    deployment_tenant: Tenant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_broker() -> None:
        raise ConnectionError("redis://:s3cret@internal-host:6379 refused")

    monkeypatch.setitem(health.READINESS_CHECKS, "broker", broken_broker)

    response = api_client.get("/api/health/ready/")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "service": "commerce-api",
        "checks": {**ALL_OK, "broker": "error"},
    }
    assert "s3cret" not in response.content.decode()
    assert "Readiness check failed: broker" in caplog.text
