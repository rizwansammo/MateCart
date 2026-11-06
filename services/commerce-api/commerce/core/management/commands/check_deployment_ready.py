from typing import Any

from django.core.management.base import BaseCommand, CommandError

from commerce.core.health import run_readiness_checks


class Command(BaseCommand):
    help = (
        "Exit non-zero unless the database, cache, broker and the configured tenant are ready. "
        "Used by the production migrate job and deployment verification; prints check names "
        "and ok/error only, never connection details."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        results = run_readiness_checks()
        summary = ", ".join(f"{name}={state}" for name, state in results.items())
        if any(state != "ok" for state in results.values()):
            raise CommandError(f"Deployment is not ready: {summary}.")
        self.stdout.write(self.style.SUCCESS(f"Deployment ready: {summary}."))
