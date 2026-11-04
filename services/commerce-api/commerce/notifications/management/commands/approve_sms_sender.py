"""NetaMate only: record a provider's decision on the store's requested SMS sender id (D-089).

The merchant can only *request* a sender id. After the SMS provider has registered (or refused)
it, NetaMate runs this command on the store's deployment. Nothing in the dashboard can approve.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from commerce.audit.services import record_audit_event
from commerce.notifications.models import SenderStatus
from commerce.notifications.services import sms_settings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id


class Command(BaseCommand):
    help = "Approve or reject the store's requested SMS sender id after provider registration."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("sender", help="The exact sender id the provider registered.")
        parser.add_argument("--reject", action="store_true")
        parser.add_argument("--allowance", type=int, default=None, help="Monthly segments.")
        parser.add_argument("--prepaid", type=int, default=None, help="Set prepaid credits.")

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        with tenant_context(tenant_id), transaction.atomic():
            row = sms_settings()
            sender = options["sender"].strip()
            if sender != row.requested_sender:
                raise CommandError("This is not the sender id the store requested.")
            row.sender_status = (
                SenderStatus.REJECTED if options["reject"] else SenderStatus.APPROVED
            )
            row.approved_sender = "" if options["reject"] else sender
            if options["allowance"] is not None:
                row.monthly_allowance = max(0, options["allowance"])
            if options["prepaid"] is not None:
                row.prepaid_credits = max(0, options["prepaid"])
            row.save()
            record_audit_event(
                action="notifications.sms.sender_decided",
                resource=row,
                actor_label="netamate",
                changes={
                    "sender": sender,
                    "status": row.sender_status,
                    "monthly_allowance": row.monthly_allowance,
                    "prepaid_credits": row.prepaid_credits,
                },
            )
        self.stdout.write(f"Sender {sender}: {row.sender_status}")
