from typing import Any

from celery.exceptions import TimeoutError as CeleryTimeoutError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from commerce.core.tasks import ping


class Command(BaseCommand):
    help = "Send a ping task through the broker and wait for a Celery worker to answer."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait.")

    def handle(self, *args: Any, **options: Any) -> None:
        timeout: float = options["timeout"]
        result = ping.delay()
        try:
            reply = result.get(timeout=timeout)
        except CeleryTimeoutError as exc:
            raise CommandError(f"No worker answered within {timeout}s (task {result.id}).") from exc
        if reply != "pong":
            raise CommandError(f"Unexpected reply {reply!r} from task {result.id}.")
        self.stdout.write(
            self.style.SUCCESS(f"Celery round-trip OK: task {result.id} returned {reply!r}.")
        )
