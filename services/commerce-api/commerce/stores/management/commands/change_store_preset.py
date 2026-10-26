from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from commerce.audit.services import record_audit_event
from commerce.catalog.models import Product
from commerce.catalog.services import ALLOWED_PRODUCT_TYPES
from commerce.stores.models import StorePreset, StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id


class Command(BaseCommand):
    help = (
        "NetaMate administration: change the store's business preset. Refused while any product "
        "(draft, active or archived) uses a product type the new preset does not offer, so no "
        "product is silently invalidated. Appearance overrides and content are kept."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--preset", required=True, choices=StorePreset.values)

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        preset: str = options["preset"]
        with tenant_context(tenant_id), transaction.atomic():
            settings = StoreSettings.objects.select_for_update().first()
            if settings is None:
                raise CommandError("Store settings are missing.")
            if settings.preset == preset:
                self.stdout.write(f"The store already uses the {preset} preset.")
                return
            allowed = ALLOWED_PRODUCT_TYPES[preset]
            blocking = (
                Product.objects.exclude(product_type__in=allowed)
                .values_list("product_type", flat=True)
                .distinct()
            )
            if blocking:
                raise CommandError(
                    f"Products of type {', '.join(sorted(blocking))} exist; the {preset} preset "
                    "does not offer them. Change or archive and convert those products first."
                )
            previous, settings.preset = settings.preset, preset
            settings.save(update_fields=["preset", "updated_at"])
            record_audit_event(
                action="store.preset.changed",
                resource=settings,
                actor_label="change_store_preset",
                changes={"preset": {"from": previous, "to": preset}},
            )
        self.stdout.write(self.style.SUCCESS(f"Store preset changed from {previous} to {preset}."))
