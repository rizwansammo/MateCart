"""Merchant roles and permissions (D-044).

Roles come from the approved prototype. Permissions are fixed in code, so a role can never be
widened through the database or an API payload. Custom roles are a Phase 11 decision.
"""

from django.db import models


class StaffRole(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    ORDER_HANDLER = "order_handler", "Order handler"
    CONTENT_EDITOR = "content_editor", "Content editor"


class Permission(models.TextChoices):
    STAFF_MANAGE = "staff.manage", "Invite and manage staff"
    STORE_SETTINGS = "store.settings", "Change store settings"
    PAYOUTS_MANAGE = "payouts.manage", "Manage payouts"
    ORDERS_MANAGE = "orders.manage", "Manage orders"
    COURIERS_MANAGE = "couriers.manage", "Book and track couriers"
    MESSAGES_MANAGE = "messages.manage", "Answer customer messages"
    CUSTOMERS_VIEW = "customers.view", "View customers"
    CATALOG_MANAGE = "catalog.manage", "Manage products and inventory"
    # Internal cost prices (margins): Owner and Manager only, never Content editor (D-053).
    CATALOG_COSTS = "catalog.costs", "See and change cost prices"
    CONTENT_MANAGE = "content.manage", "Manage website content"
    PROMOTIONS_MANAGE = "promotions.manage", "Manage promotions"
    REPORTS_VIEW = "reports.view", "View reports"


ROLE_DESCRIPTIONS: dict[str, str] = {
    StaffRole.OWNER: "Full access, including staff and payouts",
    StaffRole.MANAGER: "Everything except payouts and staff",
    StaffRole.ORDER_HANDLER: "Orders, couriers and customer messages",
    StaffRole.CONTENT_EDITOR: "Products, photos and website content",
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    StaffRole.OWNER: frozenset(Permission.values),
    StaffRole.MANAGER: frozenset(Permission.values)
    - {Permission.STAFF_MANAGE, Permission.PAYOUTS_MANAGE},
    StaffRole.ORDER_HANDLER: frozenset(
        {
            Permission.ORDERS_MANAGE,
            Permission.COURIERS_MANAGE,
            Permission.MESSAGES_MANAGE,
            Permission.CUSTOMERS_VIEW,
        }
    ),
    StaffRole.CONTENT_EDITOR: frozenset({Permission.CATALOG_MANAGE, Permission.CONTENT_MANAGE}),
}

# Roles an owner may give through invitations or role changes. Ownership itself is only created
# by NetaMate provisioning (invite_owner) and is never assignable from the dashboard.
ASSIGNABLE_ROLES = (StaffRole.MANAGER, StaffRole.ORDER_HANDLER, StaffRole.CONTENT_EDITOR)


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())
