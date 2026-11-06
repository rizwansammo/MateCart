from rest_framework.exceptions import PermissionDenied

from .models import AdminRole, AdminUser

READ = {
    "clients.read",
    "stores.read",
    "servers.read",
    "deployments.read",
    "monitoring.read",
    "alerts.read",
    "operations.read",
    "administrators.read",
    "audit.read",
}
MUTATE = {
    "clients.manage",
    "stores.manage",
    "store_configuration.manage",
    "servers.manage",
    "deployments.manage",
    "monitoring.manage",
    "alerts.manage",
    "operations.manage",
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    AdminRole.PLATFORM_OWNER: frozenset(
        READ | MUTATE | {"administrators.manage", "security.manage"}
    ),
    AdminRole.PLATFORM_ADMIN: frozenset(READ | MUTATE | {"administrators.read"}),
    AdminRole.OPERATIONS_MANAGER: frozenset(READ | (MUTATE - {"clients.manage", "stores.manage"})),
    AdminRole.SUPPORT_OPERATOR: frozenset(
        READ | {"clients.manage", "operations.manage", "alerts.manage"}
    ),
    AdminRole.READ_ONLY_AUDITOR: frozenset(READ),
}


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def require_permission(user: AdminUser, permission: str) -> None:
    if permission not in permissions_for(user.role):
        raise PermissionDenied("Your console role does not allow this action.")
