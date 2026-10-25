from django.apps import AppConfig
from django.core import checks


class TenancyConfig(AppConfig):
    """Tenant identity, context, scoped data access and provisioning (docs/ARCHITECTURE.md §3)."""

    name = "commerce.tenancy"
    label = "tenancy"
    verbose_name = "Tenancy"

    def ready(self) -> None:
        from .checks import check_tenant_owned_models

        checks.register(check_tenant_owned_models, checks.Tags.models)
