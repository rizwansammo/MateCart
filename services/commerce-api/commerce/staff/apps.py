from django.apps import AppConfig


class StaffConfig(AppConfig):
    """Merchant staff: memberships, roles and permissions, invitations."""

    name = "commerce.staff"
    label = "staff"
    verbose_name = "Staff"
