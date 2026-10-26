from celery import shared_task
from django.utils import timezone

from commerce.tenancy.tasks import TenantTask


@shared_task(base=TenantTask, name="checkout.expire")
def expire_checkout(session_id: str, *, tenant_id: str) -> None:
    """Idempotently release an abandoned checkout's temporary stock holds."""
    from .models import CheckoutSession, CheckoutStatus
    from .services import release_checkout

    session = CheckoutSession.objects.filter(pk=session_id).first()
    if (
        session is None
        or session.status != CheckoutStatus.REVIEWED
        or session.expires_at > timezone.now()
    ):
        return
    release_checkout(session=session, expired=True)
