from datetime import datetime, timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    BackupObservation,
    ConsoleNotification,
    Deployment,
    DomainRecord,
    MonitoringObservation,
    OperationalAlert,
)
from .services import apply_alert_condition, end_expired_silence


@shared_task(name="platform.evaluate_monitoring", ignore_result=True)
def evaluate_monitoring() -> None:
    """Idempotently open or recover missing-heartbeat alerts from authoritative timestamps."""
    now = timezone.now()
    expire_silences(now)
    stale_before = now - timedelta(minutes=5)
    for deployment in Deployment.objects.all().iterator():
        last_observed_at = deployment.last_observed_at
        stale = last_observed_at is not None and last_observed_at < stale_before
        _set_alert(
            deployment,
            "missing_heartbeat",
            stale,
            "critical",
            "Monitoring heartbeat is stale",
            now,
            details={
                "source": "control_evaluation",
                "last_observed_at": (
                    last_observed_at.isoformat() if last_observed_at is not None else ""
                ),
            },
        )
        backup = (
            BackupObservation.objects.filter(deployment=deployment)
            .order_by("-observed_at", "id")
            .first()
        )
        backup_failed = backup is not None and backup.status == "failed"
        backup_overdue = (
            backup is not None
            and backup.backup_at is not None
            and backup.backup_at < now - timedelta(hours=24)
        )
        _set_alert(
            deployment,
            "backup_failure",
            backup_failed,
            "critical",
            "A backup failure was reported",
            now,
        )
        _set_alert(
            deployment,
            "backup_overdue",
            backup_overdue,
            "warning",
            "The last reported backup is overdue",
            now,
        )
    for domain in DomainRecord.objects.exclude(certificate_expires_at__isnull=True).iterator():
        expires = domain.certificate_expires_at
        if expires is None:
            continue
        _set_alert(
            domain.deployment,
            "ssl_expired",
            expires <= now,
            "critical",
            f"TLS certificate expired for {domain.domain}",
            now,
            key_suffix=str(domain.pk),
        )
        _set_alert(
            domain.deployment,
            "ssl_expiring",
            now < expires <= now + timedelta(days=30),
            "warning",
            f"TLS certificate is approaching expiry for {domain.domain}",
            now,
            key_suffix=str(domain.pk),
        )


def _set_alert(
    deployment: Deployment | None,
    category: str,
    active: bool,
    severity: str,
    title: str,
    now: datetime,
    *,
    key_suffix: str = "",
    details: dict[str, str] | None = None,
) -> None:
    deployment_key = str(deployment.pk) if deployment else "unassigned"
    apply_alert_condition(
        key=f"deployment:{deployment_key}:{category}:{key_suffix}",
        active=active,
        now=now,
        deployment=deployment,
        category=category,
        severity=severity,
        title=title,
        evidence=details or {"source": "control_evaluation"},
    )


def expire_silences(now: datetime) -> int:
    """End every lapsed silence, whether or not a fresh report arrived (D-116)."""
    ended = 0
    lapsed = OperationalAlert.objects.filter(status="silenced").filter(
        Q(silenced_until__lte=now) | Q(silenced_until__isnull=True)
    )
    for alert_id in lapsed.values_list("pk", flat=True):
        with transaction.atomic():
            alert = OperationalAlert.objects.select_for_update().filter(pk=alert_id).first()
            if alert is not None and end_expired_silence(alert, now):
                ended += 1
    return ended


@shared_task(name="platform.retention", ignore_result=True)
def retention() -> None:
    """Bound operational data while retaining audit history indefinitely."""
    now = timezone.now()
    MonitoringObservation.objects.filter(
        observed_at__lt=now - timedelta(days=90), alerts__isnull=True
    ).delete()
    ConsoleNotification.objects.filter(
        read_at__isnull=False, created_at__lt=now - timedelta(days=90)
    ).delete()
    OperationalAlert.objects.filter(
        status="resolved", resolved_at__lt=now - timedelta(days=400)
    ).delete()


@shared_task(name="platform.reconcile_deployments", ignore_result=True)
def reconcile_deployments() -> None:
    """Expire undelivered commands, time out silent ones and raise Agent connectivity alerts."""
    from .deployment import reconcile_commands

    reconcile_commands()
