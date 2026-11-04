"""Marketing background work (D-083, D-084). Every task is idempotent and tenant-scoped."""

from typing import Any

from celery import shared_task

from commerce.tenancy.tasks import TenantTask

MAX_RETRIES = 5


@shared_task(
    base=TenantTask, bind=True, name="marketing.deliver_meta_event", max_retries=MAX_RETRIES
)
def deliver_meta_event(self: Any, delivery_id: str, *, tenant_id: str) -> str:
    """Send one Conversions API event; retried with backoff for temporary failures only."""
    from .tracking import deliver

    result = deliver(delivery_id)
    if result == "retry":
        raise self.retry(countdown=min(3600, 60 * 2**self.request.retries))
    return result


@shared_task(base=TenantTask, name="marketing.prune")
def prune_marketing_data(*, tenant_id: str) -> dict[str, int]:
    """Apply the analytics, consent and delivery retention policy."""
    from .analytics import prune

    return prune()
