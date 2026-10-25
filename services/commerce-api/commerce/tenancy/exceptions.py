class TenancyError(Exception):
    """A tenant boundary would be crossed, or the tenant cannot be established. Never caught to
    "continue anyway": callers fail the operation (the API answers 503 tenant_unavailable)."""


class TenantContextMissing(TenancyError):
    """Tenant-owned data was accessed without an active tenant context."""


class TenantContextConflict(TenancyError):
    """A different tenant, or unscoped access, was requested inside an active tenant context."""


class CrossTenantAccessError(TenancyError):
    """An object or task belongs to a tenant other than the active one."""


class TenantImmutableError(TenancyError):
    """Code tried to move a row to another tenant. Ownership never changes after creation."""


class TenantNotConfigured(TenancyError):
    """MATECART_TENANT_ID is not set for this deployment."""


class TenantNotProvisioned(TenancyError):
    """The configured tenant has no row in this database yet (run provision_tenant)."""


class TenantConfigurationError(TenancyError):
    """The database does not match the single-tenant deployment configuration."""


class TenantProvisioningError(TenancyError):
    """Provisioning was refused: invalid input, duplicate, or not allowed in this mode."""


class TenantAlreadyProvisioned(TenantProvisioningError):
    """The configured tenant already exists; nothing was changed."""
