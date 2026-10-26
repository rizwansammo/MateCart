from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from rest_framework.exceptions import ValidationError

from commerce.core.domain import DomainError
from commerce.stores import services, themes
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id


class Command(BaseCommand):
    help = (
        "NetaMate administration: assign the store's storefront theme (D-069). The theme must "
        "belong to the store's business preset. Only the theme changes: products, prices, stock, "
        "content and the merchant's branding are kept. --default returns to the preset's default."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--theme", choices=themes.THEME_IDS)
        target.add_argument("--default", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        theme_id: str | None = None if options["default"] else options["theme"]
        with tenant_context(tenant_id):
            try:
                settings = services.assign_theme(
                    theme_id=theme_id, actor_label="assign_store_theme"
                )
            except (ValidationError, DomainError) as exc:
                detail = exc.detail if isinstance(exc, ValidationError) else exc
                raise CommandError(f"Theme not assigned: {detail}") from exc
            effective = themes.effective_theme(settings.active_theme, settings.preset)
        self.stdout.write(self.style.SUCCESS(f"The store now uses the {effective} theme."))
