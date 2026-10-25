from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from control.accounts.errors import AuthError
from control.accounts.services import create_admin


class Command(BaseCommand):
    help = (
        "NetaMate personnel only: create a console administrator and email a single-use "
        "activation link (re-sends it if the administrator has not activated yet). The link is "
        "never printed; the administrator must set up two-step verification on first sign-in."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="", help="Full name.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            user = create_admin(email=options["email"], full_name=options["name"].strip())
        except AuthError as exc:
            raise CommandError(str(exc.detail)) from exc
        self.stdout.write(self.style.SUCCESS(f"Activation link emailed to {user.email}."))
