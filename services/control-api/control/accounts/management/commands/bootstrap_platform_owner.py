from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from control.accounts.errors import AuthError
from control.accounts.models import AdminRole, AdminUser
from control.accounts.services import create_admin


class Command(BaseCommand):
    help = (
        "Bootstrap the first Platform Owner and email an activation link. Refuses after one exists."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="", help="Full name.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            with transaction.atomic():
                if (
                    AdminUser.objects.select_for_update()
                    .filter(role=AdminRole.PLATFORM_OWNER)
                    .exists()
                ):
                    raise CommandError("A Platform Owner already exists.")
                user = create_admin(email=options["email"], full_name=options["name"].strip())
                user.role = AdminRole.PLATFORM_OWNER
                user.save(update_fields=["role", "updated_at"])
        except AuthError as exc:
            raise CommandError(str(exc.detail)) from exc
        self.stdout.write(self.style.SUCCESS(f"Platform Owner activation emailed to {user.email}."))
