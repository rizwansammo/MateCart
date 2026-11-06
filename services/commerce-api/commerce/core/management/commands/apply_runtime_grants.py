"""Production database privilege separation (D-118).

The migrate job connects as the schema-owning migration role and runs this command after
``migrate``. It gives the runtime role exactly the data privileges the application needs
(SELECT/INSERT/UPDATE/DELETE on tables, USAGE/SELECT on sequences) and nothing that changes the
schema. Triggers that protect audit and stock history stay owned by the migration role, so the
runtime role cannot disable or drop them. Re-running is safe.
"""

import re
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, transaction
from psycopg import sql

ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


def runtime_grant_statements(role: str, owner: str, schema: str = "public") -> list[sql.Composed]:
    if not ROLE_PATTERN.fullmatch(role):
        raise CommandError("The runtime role name must be a lower-case PostgreSQL identifier.")
    if role == owner:
        raise CommandError("The runtime role must differ from the migration (owner) role.")
    ident_role = sql.Identifier(role)
    ident_schema = sql.Identifier(schema)
    ident_owner = sql.Identifier(owner)
    return [
        sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(ident_schema, ident_role),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident_schema, ident_role),
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
            ident_schema, ident_role
        ),
        sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
            ident_schema, ident_role
        ),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(ident_owner, ident_schema, ident_role),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT USAGE, SELECT ON SEQUENCES TO {}"
        ).format(ident_owner, ident_schema, ident_role),
    ]


class Command(BaseCommand):
    help = (
        "Grant the runtime database role data-only privileges on the current schema (no DDL). "
        "Run by the production migrate job as the migration role."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--role", required=True, help="Runtime role, e.g. matecart_app.")

    def handle(self, *args: Any, **options: Any) -> None:
        role: str = options["role"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user, current_schema()")
            row = cursor.fetchone()
            if row is None:
                raise CommandError("Could not determine the current database role.")
            owner, schema = str(row[0]), str(row[1])
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            if cursor.fetchone() is None:
                raise CommandError(
                    "The runtime role does not exist. The database init script creates it."
                )
            cursor.execute(
                "SELECT rolsuper OR rolcreaterole OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                [role],
            )
            elevated = cursor.fetchone()
            if elevated is not None and elevated[0]:
                raise CommandError("The runtime role must not be a superuser or manage roles.")
            with transaction.atomic():
                for statement in runtime_grant_statements(role, owner, schema):
                    cursor.execute(statement)
        self.stdout.write(self.style.SUCCESS("Runtime database privileges applied."))
