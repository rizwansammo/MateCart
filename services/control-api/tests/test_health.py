import pytest
from rest_framework.test import APIClient

from control.core import health
from control.core.serializers import ReadinessSerializer


def test_liveness_reports_ok_without_touching_dependencies(api_client: APIClient) -> None:
    # No django_db mark: any database access here would fail the test.
    response = api_client.get("/api/health/live/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "control-api"}
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_readiness_reports_the_database_ok(api_client: APIClient) -> None:
    response = api_client.get("/api/health/ready/")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "control-api", "checks": {"database": "ok"}}
    assert ReadinessSerializer(data=body).is_valid()


def test_readiness_returns_503_without_leaking_failure_details(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken_database() -> None:
        raise ConnectionError("postgres://matecart_control:s3cret@control-db refused")

    monkeypatch.setitem(health.READINESS_CHECKS, "database", broken_database)

    response = api_client.get("/api/health/ready/")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "service": "control-api",
        "checks": {"database": "error"},
    }
    assert "s3cret" not in response.content.decode()
    assert "Readiness check failed: database" in caplog.text
