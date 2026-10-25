from typing import Any

from django.core.management.base import BaseCommand, CommandError

from commerce.inventory.services import reconcile
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id


class Command(BaseCommand):
    help = (
        "Check that every stock level equals the sum of its ledger movements (and that batch and "
        "serial records agree). Read-only; exits with an error when anything differs."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        with tenant_context(tenant_id):
            problems = reconcile()
        if problems:
            for problem in problems[:50]:
                self.stderr.write(
                    f"{problem.kind}: variant {problem.variant_id} at {problem.location_id} "
                    f"expected {problem.expected}, found {problem.actual}"
                )
            raise CommandError(f"{len(problems)} inventory discrepancies found.")
        self.stdout.write(self.style.SUCCESS("Inventory levels match the movement ledger."))
