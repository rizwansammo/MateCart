from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from commerce.identity.errors import AuthError
from commerce.staff.services import invite_owner
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id


class Command(BaseCommand):
    help = (
        "NetaMate provisioning: email the store owner a single-use invitation to create the owner "
        "account. Refused once the store has an owner. The link is never printed."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True, help="The owner's email address.")

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        with tenant_context(tenant_id):
            try:
                issued = invite_owner(email=options["email"])
            except AuthError as exc:
                raise CommandError(str(exc.detail)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Owner invitation sent to {issued.invitation.email}; it expires "
                f"{issued.invitation.expires_at:%Y-%m-%d %H:%M} UTC."
            )
        )
