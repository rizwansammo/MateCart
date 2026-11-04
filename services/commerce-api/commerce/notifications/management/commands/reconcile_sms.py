"""NetaMate only: review SMS reservations whose outcome is unknown (D-091).

A notification SMS whose submission ended without a clear answer from the gateway (a timeout
after the request was sent, a dropped connection, a worker lost mid-send) is never resent
automatically, and its units stay held. After checking the gateway's own records, NetaMate
resolves each held reservation: ``--accepted`` spends the units, ``--not-accepted`` returns them.
Nothing is sent by this command, and no recipient or message is printed.
"""

from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from commerce.core.domain import ConflictError
from commerce.notifications.services import held_sms, reconcile_sms
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id


class Command(BaseCommand):
    help = "List held SMS reservations, or resolve one after checking the gateway's records."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("reservation", nargs="?", help="The held reservation's id.")
        outcome = parser.add_mutually_exclusive_group()
        outcome.add_argument("--accepted", action="store_true")
        outcome.add_argument("--not-accepted", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        with tenant_context(tenant_id):
            if options["reservation"] is None:
                for usage in held_sms():
                    self.stdout.write(
                        f"{usage.pk}  {usage.occurred_at:%Y-%m-%d %H:%M}  {usage.purpose}  "
                        f"segments={usage.segments}  notification={usage.notification_id}"
                    )
                return
            if not (options["accepted"] or options["not_accepted"]):
                raise CommandError("Choose --accepted or --not-accepted.")
            try:
                usage = reconcile_sms(
                    options["reservation"],
                    accepted=bool(options["accepted"]),
                    actor_label="netamate",
                )
            except ConflictError as error:
                raise CommandError(str(error)) from None
            except ValidationError:
                raise CommandError("That is not a reservation id.") from None
        self.stdout.write(f"Reservation {usage.pk}: {usage.state}")
