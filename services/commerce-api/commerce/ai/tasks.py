"""AI background work (D-101). Tasks are idempotent: a request is claimed under its row lock
and a second run of the same task finds it no longer pending and does nothing."""

from __future__ import annotations

from celery import shared_task

from commerce.tenancy.tasks import TenantTask


@shared_task(base=TenantTask, name="ai.run_request", acks_late=True)
def run_request(request_id: str, *, tenant_id: str) -> str:
    from . import services

    return services.run(request_id)


@shared_task(base=TenantTask, name="ai.sweep_tenant")
def sweep_tenant(*, tenant_id: str) -> dict[str, int]:
    from . import services

    return services.sweep()


@shared_task(name="ai.sweep")
def sweep() -> dict[str, int]:
    """Beat entry point (every five minutes)."""
    from commerce.communications.tasks import _tenant

    tenant_id = _tenant()
    return sweep_tenant(tenant_id=tenant_id) if tenant_id else {}
