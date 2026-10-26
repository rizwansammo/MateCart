from celery import shared_task

from commerce.tenancy.tasks import TenantTask


@shared_task(base=TenantTask, name="orders.expire_payment")
def expire_order_payment(order_id: str, *, tenant_id: str) -> None:
    """Idempotently cancel an online order whose payment window passed unpaid."""
    from .models import Order
    from .services import expire_unpaid

    order = Order.objects.filter(pk=order_id).first()
    if order is not None:
        expire_unpaid(order)
