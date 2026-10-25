from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from commerce.stores.models import StorePreset
from commerce.tenancy.exceptions import TenancyError, TenantAlreadyProvisioned
from commerce.tenancy.provisioning import StoreProfile, provision_tenant


class Command(BaseCommand):
    help = (
        "Create this deployment's tenant (MATECART_TENANT_ID) with its store settings and an "
        "audit event, in one transaction."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--handle", required=True, help="Operations handle, e.g. rahim-fashion."
        )
        parser.add_argument("--name", required=True, help="Store display name.")
        parser.add_argument("--preset", required=True, choices=StorePreset.values)
        parser.add_argument("--country", required=True, help="ISO 3166-1 alpha-2, e.g. BD.")
        parser.add_argument("--currency", required=True, help="ISO 4217, e.g. BDT.")
        parser.add_argument("--timezone", required=True, help="IANA time zone, e.g. Asia/Dhaka.")
        parser.add_argument("--language", required=True, help="Default language, e.g. en.")
        parser.add_argument(
            "--languages",
            default="",
            help="Comma-separated supported languages (default: --language).",
        )
        parser.add_argument("--legal-name", default="")
        parser.add_argument("--contact-email", default="")
        parser.add_argument("--contact-phone", default="")
        parser.add_argument(
            "--if-missing",
            action="store_true",
            help="Succeed without changes when the tenant already exists.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        language = options["language"].strip().lower()
        languages = [
            code.strip().lower() for code in options["languages"].split(",") if code.strip()
        ]
        profile = StoreProfile(
            handle=options["handle"].strip(),
            name=options["name"].strip(),
            preset=options["preset"],
            country=options["country"].strip().upper(),
            default_currency=options["currency"].strip().upper(),
            timezone=options["timezone"].strip(),
            default_language=language,
            supported_languages=tuple(languages or [language]),
            legal_name=options["legal_name"].strip(),
            contact_email=options["contact_email"],
            contact_phone=options["contact_phone"],
        )
        try:
            tenant = provision_tenant(profile)
        except TenantAlreadyProvisioned as exc:
            if options["if_missing"]:
                self.stdout.write(f"{exc} Nothing changed.")
                return
            raise CommandError(str(exc)) from exc
        except TenancyError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Provisioned tenant {tenant.handle} ({tenant.pk})."))
