"""Apply a Central Console configuration revision to this Store (Phase 17, D-120).

Run only by the Deployment Agent through ``docker compose exec`` after an approved
``apply_configuration`` operation. Everything goes through the existing domain services
(change_store_preset safeguards, ``assign_theme``, ``set_module``) inside one transaction, so a
revision is applied completely or not at all. Prints one JSON line the Agent reports back.
"""

import json
import re
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from rest_framework.exceptions import ValidationError

from commerce.core.domain import DomainError
from commerce.stores import modules, services, themes
from commerce.stores.models import StorePreset, StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.tenancy.resolution import configured_tenant_id

MODULE_KEY = re.compile(r"^[a-z_]{2,40}$")


class Command(BaseCommand):
    help = (
        "NetaMate administration: apply a Console configuration revision (preset, theme, modules)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--revision", type=int, required=True)
        parser.add_argument("--preset", required=True, choices=StorePreset.values)
        parser.add_argument("--theme", required=True, choices=themes.THEME_IDS)
        parser.add_argument("--modules", required=True, help="Comma-separated enabled modules.")

    def handle(self, *args: Any, **options: Any) -> None:
        revision: int = options["revision"]
        if revision < 1:
            raise CommandError("The revision must be positive.")
        desired = [item for item in options["modules"].split(",") if item]
        if not all(MODULE_KEY.fullmatch(item) for item in desired):
            raise CommandError("Module keys are lower-case identifiers.")
        unknown = sorted(set(desired) - set(modules.BY_KEY))
        if unknown:
            raise CommandError(f"Unknown modules: {', '.join(unknown)}.")
        tenant_id = configured_tenant_id()
        if tenant_id is None or not Tenant.objects.filter(pk=tenant_id).exists():
            raise CommandError("Provision the store first (provision_tenant).")
        try:
            with tenant_context(tenant_id), transaction.atomic():
                settings = StoreSettings.objects.select_for_update().first()
                if settings is None:
                    raise CommandError("Store settings are missing.")
                if settings.preset != options["preset"]:
                    # Refuses when existing products do not fit the new preset (no silent loss).
                    call_command("change_store_preset", preset=options["preset"])
                services.assign_theme(
                    theme_id=options["theme"], actor_label="apply_platform_configuration"
                )
                wanted = set(desired)
                # Turn modules off first, then on in dependency order.
                for spec in modules.REGISTRY:
                    if spec.core or not spec.available or spec.key in wanted:
                        continue
                    if spec.key in modules.enabled_modules():
                        modules.set_module(key=spec.key, enabled=False, actor=None)
                pending = [key for key in desired if not modules.BY_KEY[key].core]
                for _ in range(len(pending) + 1):
                    progressed = False
                    for key in list(pending):
                        spec = modules.BY_KEY[key]
                        enabled = set(modules.enabled_modules())
                        if all(dep in enabled for dep in spec.requires):
                            if key not in enabled:
                                modules.set_module(key=key, enabled=True, actor=None)
                            pending.remove(key)
                            progressed = True
                    if not pending or not progressed:
                        break
                if pending:
                    raise CommandError(f"Module prerequisites missing for {', '.join(pending)}.")
                applied = sorted(modules.enabled_modules())
        except (ValidationError, DomainError) as exc:
            detail = exc.detail if isinstance(exc, ValidationError) else exc
            raise CommandError(f"Configuration not applied: {detail}") from exc
        self.stdout.write(
            json.dumps(
                {"applied_revision": revision, "modules": applied, "theme": options["theme"]}
            )
        )
