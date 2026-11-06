"""Phase 17 production commands: runtime privileges, readiness and Console configuration."""

import json
import uuid
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from commerce.stores import modules
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

ROLE = "matecart_test_runtime"


@pytest.fixture
def runtime_role() -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {ROLE}")
        cursor.execute(f"CREATE ROLE {ROLE} NOLOGIN NOSUPERUSER NOCREATEROLE")
    return ROLE


def as_role(sql: str) -> None:
    """Run ``sql`` as the runtime role inside the caller's savepoint. On failure the savepoint
    rollback also undoes SET LOCAL ROLE, so only the success path resets it."""
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL ROLE {ROLE}")
        cursor.execute(sql)
        cursor.execute("RESET ROLE")


def test_runtime_role_gets_data_privileges_but_no_ddl(
    runtime_role: str, deployment_tenant: Tenant
) -> None:
    out = StringIO()
    call_command("apply_runtime_grants", "--role", runtime_role, stdout=out)
    call_command("apply_runtime_grants", "--role", runtime_role, stdout=StringIO())  # idempotent
    assert "Runtime database privileges applied" in out.getvalue()
    table = Tenant._meta.db_table
    with transaction.atomic():
        as_role(f"SELECT count(*) FROM {table}")  # noqa: S608
    for statement in (
        f"ALTER TABLE {table} ADD COLUMN injected integer",
        f"DROP TABLE {table}",
        "CREATE TABLE matecart_runtime_probe (id integer)",
    ):
        with pytest.raises(ProgrammingError), transaction.atomic():
            as_role(statement)


def test_runtime_grants_refuse_unsafe_role_names() -> None:
    with pytest.raises(CommandError):
        call_command("apply_runtime_grants", "--role", "app; DROP TABLE x")
    with pytest.raises(CommandError, match="does not exist"):
        call_command("apply_runtime_grants", "--role", "matecart_missing_role")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        owner = cursor.fetchone()[0]
    with pytest.raises(CommandError, match=r"superuser|differ"):
        call_command("apply_runtime_grants", "--role", owner)


def test_check_deployment_ready_reports_only_check_names(
    deployment_tenant: Tenant, settings: Any
) -> None:
    out = StringIO()
    call_command("check_deployment_ready", stdout=out)
    assert "database=ok" in out.getvalue()
    assert "tenant=ok" in out.getvalue()
    # A deployment pointed at an unprovisioned tenant never falls back to another tenant.
    settings.MATECART_TENANT_ID = uuid.uuid4()
    with pytest.raises(CommandError) as failure:
        call_command("check_deployment_ready")
    message = str(failure.value)
    assert "tenant=error" in message
    assert "postgres://" not in message
    assert "password" not in message.lower()


def test_platform_configuration_applies_theme_and_modules_atomically(
    store: StoreSettings,
) -> None:
    preset = store.preset
    theme = {
        "fashion": "fashion-editorial",
        "general": "general-marketplace",
        "grocery": "grocery-quickshop",
        "electronics": "electronics-spec-first",
        "books": "books-literary",
    }[preset]
    candidates = [
        spec.key
        for spec in modules.REGISTRY
        if spec.available
        and not spec.core
        and preset in spec.presets
        and not spec.requires
        and spec.key != "online_payments"
    ]
    target = candidates[0]
    out = StringIO()
    call_command(
        "apply_platform_configuration",
        "--revision",
        "4",
        "--preset",
        preset,
        "--theme",
        theme,
        "--modules",
        f"catalog,inventory,{target}",
        stdout=out,
    )
    result = json.loads(out.getvalue())
    assert result["applied_revision"] == 4
    with tenant_context(store.tenant_id):
        assert target in modules.enabled_modules()
        assert StoreSettings.objects.get().active_theme == theme
    with pytest.raises(CommandError, match="Unknown modules"):
        call_command(
            "apply_platform_configuration",
            "--revision",
            "5",
            "--preset",
            preset,
            "--theme",
            theme,
            "--modules",
            "catalog,not_a_module",
        )
    with pytest.raises(CommandError):
        call_command(
            "apply_platform_configuration",
            "--revision",
            "0",
            "--preset",
            preset,
            "--theme",
            theme,
            "--modules",
            "catalog",
        )
