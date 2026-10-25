from typing import TYPE_CHECKING, Any

from celery import Task

from .context import tenant_context
from .exceptions import TenantContextMissing
from .resolution import verify_task_tenant

if TYPE_CHECKING:
    _TaskBase = Task[Any, Any]
else:
    _TaskBase = Task


class TenantTask(_TaskBase):
    """Base class for Celery tasks that touch tenant data.

    The task function takes a keyword-only ``tenant_id`` and callers pass it explicitly, e.g.
    ``send_receipt.delay(order_id, tenant_id=str(tenant.id))``. A worker never infers the tenant:
    before running, it checks that the id is valid, is the tenant this deployment serves and is
    provisioned, then runs the task inside that tenant's context.

        @shared_task(base=TenantTask)
        def send_receipt(order_id: str, *, tenant_id: str) -> None: ...
    """

    abstract = True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("tenant_id") is None:
            raise TenantContextMissing(f"Task {self.name} needs an explicit tenant_id argument.")
        with tenant_context(verify_task_tenant(kwargs["tenant_id"])):
            return super().__call__(*args, **kwargs)
