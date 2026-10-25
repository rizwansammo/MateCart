"""Which tenant does this deployment serve?

Single-tenant mode (the only supported mode) answers from configuration alone:
``settings.MATECART_TENANT_ID``. Request data (host, headers, query parameters, body, cookies) is
never consulted, so no client can choose or switch the tenant. A future pooled mode would add a
verified domain-to-tenant lookup here; it is a separate, documented decision (DECISIONS D-026).
"""

import uuid

from django.conf import settings
from django.http import HttpRequest

from .exceptions import (
    CrossTenantAccessError,
    TenancyError,
    TenantConfigurationError,
    TenantNotConfigured,
    TenantNotProvisioned,
)
from .models import Tenant


def configured_tenant_id() -> uuid.UUID | None:
    tenant_id: uuid.UUID | None = settings.MATECART_TENANT_ID
    return tenant_id


def resolve_request_tenant_id(request: HttpRequest) -> uuid.UUID | None:
    """The tenant a request runs as. ``request`` is accepted, and deliberately ignored."""
    return configured_tenant_id()


def get_deployment_tenant() -> Tenant:
    tenant_id = configured_tenant_id()
    if tenant_id is None:
        raise TenantNotConfigured("MATECART_TENANT_ID is not set for this deployment.")
    try:
        return Tenant.objects.get(pk=tenant_id)
    except Tenant.DoesNotExist as exc:
        raise TenantNotProvisioned(
            f"Tenant {tenant_id} is not provisioned. Run `manage.py provision_tenant`."
        ) from exc


def check_deployment_tenant() -> None:
    """Readiness: the configured tenant exists and is the only tenant in this database."""
    tenant = get_deployment_tenant()
    if Tenant.objects.exclude(pk=tenant.pk).exists():
        raise TenantConfigurationError(
            "This single-tenant database contains tenants other than the configured one."
        )


def verify_task_tenant(raw_tenant_id: object) -> uuid.UUID:
    """Check a task's explicit tenant id before the task touches any data."""
    try:
        tenant_id = uuid.UUID(str(raw_tenant_id))
    except ValueError as exc:
        raise TenancyError(f"{raw_tenant_id!r} is not a valid tenant id.") from exc
    configured = configured_tenant_id()
    if configured is None:
        raise TenantNotConfigured("MATECART_TENANT_ID is not set for this deployment.")
    if tenant_id != configured:
        raise CrossTenantAccessError(
            f"Task targets tenant {tenant_id}, but this deployment serves {configured}."
        )
    if not Tenant.objects.filter(pk=tenant_id).exists():
        raise TenantNotProvisioned(f"Tenant {tenant_id} is not provisioned.")
    return tenant_id
