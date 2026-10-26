"""Host-run migration round-trip against temporary databases in MateCart's own PostgreSQL.

Covers the authentication migrations (Phase 03), the product and inventory engine (Phase 04:
media, catalog, pricing, inventory, including the append-only ledger trigger) and Phase 05 store
appearance, modules and content, and Phase 07 cart and checkout.

Never resets development data or reads passwords. Existing container environment supplies the
credentials; only the database name is overridden in the temporary API process.
"""

import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(*args: str, stdin: str | None = None) -> None:
    subprocess.run(list(args), cwd=ROOT, input=stdin, text=True, check=True)


def verify(service: str, user: str, reversal: list[tuple[str, str]]) -> None:
    database = f"matecart_auth_verify_{uuid.uuid4().hex[:12]}"
    db_service = service.replace("api", "db")
    command("docker", "compose", "exec", "-T", db_service, "createdb", "-U", user, database)
    code = f"""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.test'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
settings.DATABASES['default']['NAME'] = {database!r}
call_command('migrate', interactive=False, verbosity=0)
print('Fresh migration forward: OK')
for app, target in {reversal!r}:
    call_command('migrate', app, target, interactive=False, verbosity=0)
print('Migration reverse: OK')
call_command('migrate', interactive=False, verbosity=0)
call_command('makemigrations', check=True, dry_run=True, verbosity=0)
call_command('check', verbosity=0)
print('Reapply and drift check: OK')
"""
    try:
        command("docker", "compose", "run", "--rm", "--no-deps", "-T", service,
                "python", "-", stdin=code)
    finally:
        command("docker", "compose", "exec", "-T", db_service, "dropdb", "-U", user, database)
    print(f"{service}: temporary database removed; development database unchanged")


if __name__ == "__main__":
    verify(
        "commerce-api",
        "matecart_commerce",
        [
            ("orders", "zero"),
            ("checkout", "zero"),
            ("content", "zero"),
            ("stores", "0002"),
            ("inventory", "zero"),
            ("pricing", "zero"),
            ("catalog", "zero"),
            ("media", "zero"),
            ("staff", "zero"),
            ("identity", "0002"),
        ],
    )
    verify("control-api", "matecart_control", [("accounts", "0001")])
