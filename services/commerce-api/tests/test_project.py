"""Project-level guarantees: migrations, the committed API contract and production settings."""

import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command


@pytest.mark.django_db
def test_models_and_migrations_are_in_sync() -> None:
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


def test_committed_openapi_schema_is_up_to_date(tmp_path: Path) -> None:
    generated = tmp_path / "openapi.yaml"
    call_command("spectacular", "--file", str(generated), "--validate", "--fail-on-warn")

    committed = Path(settings.BASE_DIR, "openapi.yaml").read_text(encoding="utf-8")
    assert committed == generated.read_text(encoding="utf-8"), (
        "openapi.yaml is stale: run `pnpm api:schema` and commit the result."
    )


def _manage_with_production_settings(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    # A complete, valid production configuration; each test overrides what it checks.
    production = {
        "MATECART_TENANT_ID": str(uuid.uuid4()),
        "MATECART_ENCRYPTION_KEY": secrets.token_urlsafe(48),
        "MATECART_SECURE_COOKIES": "true",
        "MATECART_PUBLIC_BASE_URL": "https://store.example.com",
        "EMAIL_URL": "smtp+tls://mailer:unused@smtp.example.com:587",
        "MATECART_SMS_PROVIDER": "",
        "GOOGLE_OIDC_CLIENT_ID": "",
        "GOOGLE_OIDC_CLIENT_SECRET": "",
    }
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=settings.BASE_DIR,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            **production,
            **env,
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_production_settings_pass_django_deploy_checks() -> None:
    result = _manage_with_production_settings(
        "check",
        "--deploy",
        "--fail-level",
        "WARNING",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
        DJANGO_ALLOWED_HOSTS="store.example.com",
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("weak_key", ["__GENERATE__", "too-short"])
def test_production_settings_refuse_weak_secret_keys(weak_key: str) -> None:
    result = _manage_with_production_settings(
        "check", DJANGO_SECRET_KEY=weak_key, DJANGO_ALLOWED_HOSTS="store.example.com"
    )

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY must be" in result.stderr


def test_production_settings_require_allowed_hosts() -> None:
    result = _manage_with_production_settings(
        "check", DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_ALLOWED_HOSTS=""
    )

    assert result.returncode != 0
    assert "DJANGO_ALLOWED_HOSTS must list" in result.stderr


def test_production_settings_require_the_served_tenant() -> None:
    result = _manage_with_production_settings(
        "check",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
        DJANGO_ALLOWED_HOSTS="store.example.com",
        MATECART_TENANT_ID="",
    )

    assert result.returncode != 0
    assert "MATECART_TENANT_ID must identify" in result.stderr


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("MATECART_TENANT_ID", "not-a-uuid", "MATECART_TENANT_ID must be a UUID"),
        ("MATECART_TENANT_ID", str(uuid.UUID(int=0)), "must be an RFC 9562 UUID"),
        ("MATECART_TENANT_ID", "ffffffff-ffff-ffff-ffff-ffffffffffff", "must be an RFC 9562 UUID"),
        ("MATECART_TENANCY_MODE", "pooled", "MATECART_TENANCY_MODE must be 'single'"),
    ],
)
def test_invalid_tenancy_configuration_stops_startup(
    variable: str, value: str, message: str
) -> None:
    result = _manage_with_production_settings(
        "check",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
        DJANGO_ALLOWED_HOSTS="store.example.com",
        **{variable: value},
    )

    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("MATECART_ENCRYPTION_KEY", "", "MATECART_ENCRYPTION_KEY must be"),
        ("MATECART_ENCRYPTION_KEY", "__GENERATE__", "MATECART_ENCRYPTION_KEY must be"),
        ("MATECART_SECURE_COOKIES", "false", "MATECART_SECURE_COOKIES cannot be disabled"),
        ("MATECART_PUBLIC_BASE_URL", "http://store.example.com", "must be the store's https://"),
        ("EMAIL_URL", "consolemail://", "EMAIL_URL must point to a real SMTP service"),
        ("EMAIL_URL", "dummymail://", "EMAIL_URL must point to a real SMTP service"),
        ("MATECART_SMS_PROVIDER", "tests.fakes.FakeSmsProvider", "cannot be a test fake"),
        ("GOOGLE_OIDC_CLIENT_ID", "client-id-only", "Set both GOOGLE_OIDC_CLIENT_ID"),
    ],
)
def test_unsafe_authentication_configuration_stops_startup(
    variable: str, value: str, message: str
) -> None:
    result = _manage_with_production_settings(
        "check",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
        DJANGO_ALLOWED_HOSTS="store.example.com",
        **{variable: value},
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_encryption_key_must_differ_from_the_secret_key() -> None:
    shared = secrets.token_urlsafe(64)
    result = _manage_with_production_settings(
        "check",
        DJANGO_SECRET_KEY=shared,
        DJANGO_ALLOWED_HOSTS="store.example.com",
        MATECART_ENCRYPTION_KEY=shared,
    )

    assert result.returncode != 0
    assert "must differ from DJANGO_SECRET_KEY" in result.stderr


def test_deployments_hash_passwords_with_argon2() -> None:
    # Test settings swap in a fast hasher; the shared base settings are what deployments run.
    from config.settings import base

    assert base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    assert any(
        rule["NAME"].endswith("MinimumLengthValidator") and rule["OPTIONS"]["min_length"] >= 10
        for rule in base.AUTH_PASSWORD_VALIDATORS
    )
