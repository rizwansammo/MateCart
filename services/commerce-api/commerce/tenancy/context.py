"""The active tenant for the current request, task or command.

Tenant-owned data is reachable only inside ``tenant_context(...)``. There is no implicit default:
code that runs without a context fails with TenantContextMissing instead of seeing every tenant.
The one deliberate exception is ``unscoped_access(reason)``, which is logged with its reason.

Context variables follow the current thread and asyncio task, so concurrent requests never share
a tenant.
"""

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from .exceptions import TenantContextConflict, TenantContextMissing

if TYPE_CHECKING:
    from .models import Tenant

logger = logging.getLogger(__name__)

_active_tenant_id: ContextVar[uuid.UUID | None] = ContextVar("tenant_id", default=None)
_unscoped_reason: ContextVar[str | None] = ContextVar("unscoped_reason", default=None)


@contextmanager
def tenant_context(tenant: "Tenant | uuid.UUID") -> Iterator[uuid.UUID]:
    """Run the block as ``tenant``. Re-entering the same tenant is allowed; switching is not."""
    tenant_id = tenant if isinstance(tenant, uuid.UUID) else tenant.pk
    if _unscoped_reason.get() is not None:
        raise TenantContextConflict("Cannot enter a tenant context inside unscoped access.")
    current = _active_tenant_id.get()
    if current is not None and current != tenant_id:
        raise TenantContextConflict(
            f"Tenant {tenant_id} requested inside the context of tenant {current}."
        )
    token = _active_tenant_id.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _active_tenant_id.reset(token)


def get_active_tenant_id() -> uuid.UUID:
    tenant_id = _active_tenant_id.get()
    if tenant_id is None:
        raise TenantContextMissing("No active tenant. Wrap the operation in tenant_context().")
    return tenant_id


def get_active_tenant_id_or_none() -> uuid.UUID | None:
    return _active_tenant_id.get()


def in_unscoped_access() -> bool:
    return _unscoped_reason.get() is not None


@contextmanager
def unscoped_access(reason: str) -> Iterator[None]:
    """Privileged, cross-tenant access for platform operations (migrations of data, support
    tooling, integrity reports). Never use it to serve a request. Every use is logged."""
    if not reason.strip():
        raise ValueError("unscoped_access() requires a reason.")
    if _active_tenant_id.get() is not None:
        raise TenantContextConflict("Unscoped access cannot be entered inside a tenant context.")
    logger.warning("Unscoped tenant access started: %s", reason)
    token = _unscoped_reason.set(reason)
    try:
        yield
    finally:
        _unscoped_reason.reset(token)
        logger.info("Unscoped tenant access ended: %s", reason)
