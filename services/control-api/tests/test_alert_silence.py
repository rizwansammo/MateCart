"""Phase 16 carry-over: alert silence expiry, recovery during silence and recurrence (D-116)."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from control.accounts.models import AdminRole
from control.platform.models import ConsoleAuditEvent, ConsoleNotification, OperationalAlert
from control.platform.services import evaluate_alerts
from control.platform.tasks import evaluate_monitoring, expire_silences

from .test_platform_integrity import credential, infrastructure, observation, signed_in

pytestmark = pytest.mark.django_db


def _failing(deployment: Any, report_credential: Any, offset: int) -> Any:
    return observation(
        deployment,
        report_credential,
        observed_at=timezone.now() + timedelta(seconds=offset),
        store_status="unavailable",
        api_status="unavailable",
    )


def _healthy(deployment: Any, report_credential: Any, offset: int) -> Any:
    return observation(
        deployment, report_credential, observed_at=timezone.now() + timedelta(seconds=offset)
    )


def _silence(api: Any, alert: OperationalAlert, minutes: int = 30) -> None:
    until = (timezone.now() + timedelta(minutes=minutes)).isoformat()
    response = api.patch(
        f"/api/console/alerts/{alert.pk}/", {"action": "silence", "until": until}, format="json"
    )
    assert response.status_code == 200, response.data


def _expire(alert: OperationalAlert) -> None:
    OperationalAlert.objects.filter(pk=alert.pk).update(
        silenced_until=timezone.now() - timedelta(seconds=1)
    )


def _store_alert() -> OperationalAlert:
    return OperationalAlert.objects.get(category="store_unavailable")


def test_silence_holds_while_valid_and_failure_persists() -> None:
    api, user = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    evaluate_alerts(_failing(deployment, report_credential, 0))
    _silence(api, _store_alert())
    notifications = ConsoleNotification.objects.filter(user=user).count()

    evaluate_alerts(_failing(deployment, report_credential, 1))
    evaluate_monitoring()
    alert = _store_alert()
    assert alert.status == "silenced"
    assert alert.silenced_until is not None
    assert ConsoleNotification.objects.filter(user=user).count() == notifications


def test_expired_silence_with_persistent_failure_reopens_once() -> None:
    api, user = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    evaluate_alerts(_failing(deployment, report_credential, 0))
    _silence(api, _store_alert())
    _expire(_store_alert())
    before = ConsoleNotification.objects.filter(user=user).count()

    # Failure evidence after expiry makes the alert visible again.
    evaluate_alerts(_failing(deployment, report_credential, 1))
    evaluate_alerts(_failing(deployment, report_credential, 2))
    evaluate_monitoring()
    evaluate_monitoring()
    alert = _store_alert()
    assert alert.status == "open"
    assert alert.silenced_until is None
    assert "silence_ended_at" in alert.details
    assert OperationalAlert.objects.filter(category="store_unavailable").count() == 1
    assert ConsoleNotification.objects.filter(user=user).count() == before + 1
    assert (
        ConsoleAuditEvent.objects.filter(
            action="alert.silence_expired", resource_id=alert.pk, actor__isnull=True
        ).count()
        == 1
    )


def test_scheduled_sweep_reopens_lapsed_silence_without_new_report() -> None:
    api, _ = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    evaluate_alerts(_failing(deployment, report_credential, 0))
    _silence(api, _store_alert())
    _expire(_store_alert())
    # No new evidence: recovery is never invented, so the unresolved incident becomes visible.
    assert expire_silences(timezone.now()) == 1
    assert expire_silences(timezone.now()) == 0
    assert _store_alert().status == "open"


def test_recovery_during_silence_resolves_and_expiry_after_recovery_is_quiet() -> None:
    api, user = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    evaluate_alerts(_failing(deployment, report_credential, 0))
    _silence(api, _store_alert())

    evaluate_alerts(_healthy(deployment, report_credential, 1))
    alert = _store_alert()
    assert alert.status == "resolved"
    assert alert.silenced_until is None
    assert ConsoleAuditEvent.objects.filter(
        action="alert.resolved_during_silence", resource_id=alert.pk
    ).exists()

    before = ConsoleNotification.objects.filter(user=user).count()
    evaluate_monitoring()
    assert expire_silences(timezone.now() + timedelta(days=31)) == 0
    assert _store_alert().status == "resolved"
    assert ConsoleNotification.objects.filter(user=user).count() == before


def test_recurring_failure_after_silenced_recovery_reactivates_once() -> None:
    api, user = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    evaluate_alerts(_failing(deployment, report_credential, 0))
    _silence(api, _store_alert())
    evaluate_alerts(_healthy(deployment, report_credential, 1))
    alert_notifications = ConsoleNotification.objects.filter(
        user=user, deduplication_key__startswith=f"alert:{_store_alert().pk}"
    )
    before = alert_notifications.count()

    recurrence = _failing(deployment, report_credential, 2)
    evaluate_alerts(recurrence)
    evaluate_alerts(recurrence)
    alert = _store_alert()
    assert alert.status == "open"
    assert alert.source_observation == recurrence
    assert OperationalAlert.objects.filter(category="store_unavailable").count() == 1
    assert alert_notifications.count() == before + 1


def test_missing_metric_does_not_resolve_a_silenced_alert() -> None:
    api, _ = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    metric = _healthy(deployment, report_credential, 0)
    metric.cpu_percent = 97
    metric.save(update_fields=["cpu_percent"])
    evaluate_alerts(metric)
    alert = OperationalAlert.objects.get(category="cpu_high")
    _silence(api, alert)
    evaluate_alerts(_healthy(deployment, report_credential, 1))  # cpu not reported
    assert OperationalAlert.objects.get(category="cpu_high").status == "silenced"


def test_scheduled_alert_silence_lifecycle_is_idempotent() -> None:
    api, user = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    deployment.last_observed_at = timezone.now() - timedelta(minutes=30)
    deployment.save(update_fields=["last_observed_at", "updated_at"])
    evaluate_monitoring()
    alert = OperationalAlert.objects.get(category="missing_heartbeat")
    _silence(api, alert)
    for _ in range(3):
        evaluate_monitoring()
    assert OperationalAlert.objects.get(pk=alert.pk).status == "silenced"

    _expire(alert)
    before = ConsoleNotification.objects.filter(user=user).count()
    for _ in range(3):
        evaluate_monitoring()
    assert OperationalAlert.objects.get(pk=alert.pk).status == "open"
    assert ConsoleNotification.objects.filter(user=user).count() == before + 1

    _silence(api, alert)
    deployment.last_observed_at = timezone.now()
    deployment.save(update_fields=["last_observed_at", "updated_at"])
    evaluate_monitoring()
    assert OperationalAlert.objects.get(pk=alert.pk).status == "resolved"


def test_resolved_alert_cannot_be_silenced_or_acknowledged() -> None:
    api, _ = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, _, deployment = infrastructure()
    report_credential = credential(deployment)
    evaluate_alerts(_failing(deployment, report_credential, 0))
    evaluate_alerts(_healthy(deployment, report_credential, 1))
    alert = _store_alert()
    until = (timezone.now() + timedelta(hours=1)).isoformat()
    silence = api.patch(
        f"/api/console/alerts/{alert.pk}/", {"action": "silence", "until": until}, format="json"
    )
    acknowledge = api.patch(
        f"/api/console/alerts/{alert.pk}/", {"action": "acknowledge"}, format="json"
    )
    assert silence.status_code == 400
    assert acknowledge.status_code == 400
    assert _store_alert().status == "resolved"
