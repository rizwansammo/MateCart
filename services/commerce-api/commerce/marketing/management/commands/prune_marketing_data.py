"""Apply the marketing data retention policy now (D-083). Safe to run at any time."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from commerce.marketing.analytics import prune
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id


class Command(BaseCommand):
    help = "Delete analytics, consent and delivery records past their retention period."

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        with tenant_context(tenant_id):
            removed = prune()
        self.stdout.write(", ".join(f"{name}: {count}" for name, count in removed.items()))
