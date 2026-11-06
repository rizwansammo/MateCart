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
        key = f"deployment:{deployment.pk}:missing_heartbeat"
        alert = OperationalAlert.objects.filter(deduplication_key=key).first()
        last_observed_at = deployment.last_observed_at
        stale = last_observed_at is not None and last_observed_at < stale_before
        if stale and alert is None:
            with transaction.atomic():
                created = OperationalAlert.objects.create(
                    deployment=deployment,
                    category="missing_heartbeat",
                    severity="critical",
                    deduplication_key=key,
                    title="Monitoring heartbeat is stale",
                    details={
                        "source": "control_evaluation",
                        "last_observed_at": (
                            last_observed_at.isoformat() if last_observed_at is not None else ""
                        ),
                    },
                )
                notify_alert(created)
        elif not stale and alert is not None and alert.status in {"open", "acknowledged"}:
            OperationalAlert.objects.filter(pk=alert.pk).update(status="resolved", resolved_at=now)
        backup = (
            BackupObservation.objects.filter(deployment=deployment).order_by("-observed_at").first()
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
) -> None:
    deployment_key = str(deployment.pk) if deployment else "unassigned"
    key = f"deployment:{deployment_key}:{category}:{key_suffix}"
    alert = OperationalAlert.objects.filter(deduplication_key=key).first()
    if active and alert is None:
        created = OperationalAlert.objects.create(
            deployment=deployment,
            category=category,
            severity=severity,
            deduplication_key=key,
            title=title,
            details={"source": "control_evaluation"},
        )
        notify_alert(created)
    elif not active and alert is not None and alert.status in {"open", "acknowledged"}:
        OperationalAlert.objects.filter(pk=alert.pk).update(status="resolved", resolved_at=now)


@shared_task(name="platform.retention", ignore_result=True)
def retention() -> None:
    """Bound operational data while retaining audit history indefinitely."""
    now = timezone.now()
    MonitoringObservation.objects.filter(observed_at__lt=now - timedelta(days=90)).delete()
    ConsoleNotification.objects.filter(
        read_at__isnull=False, created_at__lt=now - timedelta(days=90)
    ).delete()
    OperationalAlert.objects.filter(
        status="resolved", resolved_at__lt=now - timedelta(days=400)
    ).delete()
