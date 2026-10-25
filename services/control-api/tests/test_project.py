"""Project-level guarantees: migrations, the committed API contract and production settings."""

import os
import secrets
import subprocess
import sys
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
        "MATECART_ENCRYPTION_KEY": secrets.token_urlsafe(48),
        "MATECART_SECURE_COOKIES": "true",
        "MATECART_CONSOLE_PUBLIC_BASE_URL": "https://console.example.com",
        "EMAIL_URL": "smtp+tls://mailer:unused@smtp.example.com:587",
        "REDIS_URL": "redis://:unused@redis.example.com:6379/0",
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
        DJANGO_ALLOWED_HOSTS="console.example.com",
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("weak_key", ["__GENERATE__", "too-short"])
def test_production_settings_refuse_weak_secret_keys(weak_key: str) -> None:
    result = _manage_with_production_settings(
        "check", DJANGO_SECRET_KEY=weak_key, DJANGO_ALLOWED_HOSTS="console.example.com"
    )

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY must be" in result.stderr


def test_production_settings_require_allowed_hosts() -> None:
    result = _manage_with_production_settings(
        "check", DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_ALLOWED_HOSTS=""
    )

    assert result.returncode != 0
    assert "DJANGO_ALLOWED_HOSTS must list the console" in result.stderr


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("MATECART_ENCRYPTION_KEY", "", "MATECART_ENCRYPTION_KEY must be"),
        ("MATECART_SECURE_COOKIES", "false", "MATECART_SECURE_COOKIES cannot be disabled"),
        ("MATECART_CONSOLE_PUBLIC_BASE_URL", "http://console.example.com", "https:// origin"),
        ("EMAIL_URL", "consolemail://", "EMAIL_URL must point to a real SMTP service"),
    ],
)
def test_unsafe_authentication_configuration_stops_startup(
    variable: str, value: str, message: str
) -> None:
    result = _manage_with_production_settings(
        "check",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
        DJANGO_ALLOWED_HOSTS="console.example.com",
        **{variable: value},
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_deployments_hash_passwords_with_argon2() -> None:
    from config.settings import base

    assert base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
