from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Immutable, tenant-scoped history of important changes."""

    name = "commerce.audit"
    label = "audit"
    verbose_name = "Audit"
