from datetime import datetime, timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import (
    BackupObservation,
    ConsoleNotification,
    Deployment,
    DomainRecord,
    MonitoringObservation,
    OperationalAlert,
)
from .services import notify_alert


@shared_task(name="platform.evaluate_monitoring", ignore_result=True)
def evaluate_monitoring() -> None:
    """Idempotently open or recover missing-heartbeat alerts from authoritative timestamps."""
    now = timezone.now()
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
    key = f"deployment:{deployment_key}:{category}:{key_suffix}"
    evidence = details or {"source": "control_evaluation"}
    with transaction.atomic():
        alert = OperationalAlert.objects.select_for_update().filter(deduplication_key=key).first()
        if active and alert is None:
            alert = OperationalAlert.objects.create(
                deployment=deployment,
                category=category,
                severity=severity,
                deduplication_key=key,
                title=title,
                details=evidence,
            )
            notify_alert(alert)
        elif active and alert is not None and alert.status == "resolved":
            alert.status = "open"
            alert.resolved_at = None
            alert.resolved_by = None
            alert.acknowledged_at = None
            alert.acknowledged_by = None
            alert.silenced_until = None
            alert.details = {**evidence, "reactivated_at": now.isoformat()}
            alert.save(
                update_fields=[
                    "status",
                    "resolved_at",
                    "resolved_by",
                    "acknowledged_at",
                    "acknowledged_by",
                    "silenced_until",
                    "details",
                    "updated_at",
                ]
            )
            notify_alert(alert, incident_at=now)
        elif not active and alert is not None and alert.status in {"open", "acknowledged"}:
            alert.status = "resolved"
            alert.resolved_at = now
            alert.save(update_fields=["status", "resolved_at", "updated_at"])


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
