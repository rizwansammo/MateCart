"""Notification background work (D-087). Delivery tasks are tenant tasks with an explicit,
verified tenant id; the beat entries resolve the deployment's one configured tenant and hand off.
Every task is idempotent: delivery claims its row, jobs deduplicate by event key."""

from typing import Any

from celery import shared_task

from commerce.tenancy.tasks import TenantTask


@shared_task(base=TenantTask, name="notifications.deliver")
def deliver_notification(notification_id: str, *, tenant_id: str) -> str:
    from .services import deliver

    return deliver(notification_id)


@shared_task(base=TenantTask, name="notifications.sweep_tenant")
def sweep_tenant(*, tenant_id: str) -> int:
    """Queue every due notification (recovers from broker outages and finished retry delays)."""
    from .services import due_ids

    ids = due_ids()
    for pk in ids:
        deliver_notification.delay(str(pk), tenant_id=tenant_id)
    return len(ids)


@shared_task(base=TenantTask, name="notifications.jobs_tenant")
def jobs_tenant(*, tenant_id: str, job: str) -> Any:
    from . import jobs, services

    if job == "stock_alerts":
        return jobs.stock_alerts()
    if job == "daily_summary":
        return jobs.daily_summary()
    if job == "points_reminders":
        return jobs.points_reminders()
    if job == "cleanup":
        return services.cleanup()
    raise ValueError("Unknown job.")


def _tenant() -> str | None:
    from commerce.tenancy.models import Tenant
    from commerce.tenancy.resolution import configured_tenant_id

    tenant_id = configured_tenant_id()
    if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
        return None
    return str(tenant_id)


@shared_task(name="notifications.sweep")
def sweep() -> int:
    """Beat entry point (every minute)."""
    tenant_id = _tenant()
    if tenant_id is None:
        return 0
    result: int = sweep_tenant(tenant_id=tenant_id)
    return result


@shared_task(name="notifications.run_job")
def run_job(job: str) -> Any:
    """Beat entry point for scheduled jobs (see CELERY_BEAT_SCHEDULE)."""
    tenant_id = _tenant()
    if tenant_id is None:
        return None
    return jobs_tenant(tenant_id=tenant_id, job=job)
