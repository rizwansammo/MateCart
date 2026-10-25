"""
Settings shared by every environment of the NetaMate control API (Central Platform Console).

All configuration comes from environment variables. Environment modules (development, test,
production) import this module and override only what differs. Never put secrets in this file.

Boundary: this service owns NetaMate's operational data (clients, servers, deployments). It must
never hold copies of client stores' business data, and client stores must keep working when it
is unavailable.
"""

from pathlib import Path
from typing import Any

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()

SERVICE_NAME = "control-api"

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    # Installed for AbstractBaseUser, password hashing and validators. Django's admin and session
    # apps are not used: console sessions live in control.accounts (D-045).
    "django.contrib.auth",
    "rest_framework",
    "drf_spectacular",
    "control.core",
    "control.accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "control.core.middleware.RequestIdMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# --- Database (the console's own PostgreSQL database, never a client store's) ---
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = env.int(
    "DJANGO_DB_CONNECT_TIMEOUT", default=5
)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Identity: NetaMate console administrators, never store accounts (docs/DATA_MODEL.md) ---
AUTH_USER_MODEL = "accounts.AdminUser"
# Django's authenticate() is never used: console sign-in runs through control.accounts.services.
AUTHENTICATION_BACKENDS = ["control.accounts.backends.NoLoginBackend"]

# --- Authentication & security (docs/SECURITY.md, D-045) ---
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS: list[dict[str, Any]] = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ("email", "full_name")},
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# Encrypts authenticator seeds and keys code hashes; separate from SECRET_KEY.
MATECART_ENCRYPTION_KEY = env("MATECART_ENCRYPTION_KEY", default="")
MATECART_SECURE_COOKIES = env.bool("MATECART_SECURE_COOKIES", default=True)
MATECART_CONSOLE_SESSION_SECONDS = env.int("MATECART_CONSOLE_SESSION_SECONDS", default=8 * 3600)
MATECART_CONSOLE_IDLE_SECONDS = env.int("MATECART_CONSOLE_IDLE_SECONDS", default=30 * 60)
MATECART_CONSOLE_COOKIE = "mc_console"
MATECART_CONSOLE_COOKIE_PATH = "/api/console"
# Distinct from the storefront's CSRF cookie: both apps share a host name in local development.
CSRF_COOKIE_NAME = "mc_console_csrf"
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_HTTPONLY = False  # the console app echoes it in X-CSRFToken
CSRF_COOKIE_SECURE = MATECART_SECURE_COOKIES
CSRF_TRUSTED_ORIGINS: list[str] = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
MATECART_TRUSTED_PROXY_COUNT = env.int("MATECART_TRUSTED_PROXY_COUNT", default=0)
# The console's public origin, used in activation and reset links.
MATECART_CONSOLE_PUBLIC_BASE_URL = env("MATECART_CONSOLE_PUBLIC_BASE_URL", default="").rstrip("/")

# Email: SMTP in deployments, Mailpit locally. The default discards mail; the console backend is
# never used because messages contain sign-in codes and activation links.
_email = env.email_url("EMAIL_URL", default="dummymail://")
EMAIL_BACKEND: str = _email["EMAIL_BACKEND"]
EMAIL_HOST: str = _email.get("EMAIL_HOST") or "localhost"
EMAIL_PORT: int = _email.get("EMAIL_PORT") or 25
EMAIL_HOST_USER: str = _email.get("EMAIL_HOST_USER") or ""
EMAIL_HOST_PASSWORD: str = _email.get("EMAIL_HOST_PASSWORD") or ""
EMAIL_USE_TLS: bool = bool(_email.get("EMAIL_USE_TLS"))
EMAIL_USE_SSL: bool = bool(_email.get("EMAIL_USE_SSL"))
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="NetaMate Console <no-reply@matecart.invalid>"
)
EMAIL_TIMEOUT = 10

# --- Cache: sign-in rate limits live in the console's own Redis ---
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "ratelimit": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "KEY_PREFIX": "control-rl",
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    },
}

# --- Templates (email bodies only; the console UI is the Next.js app) ---
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Django REST Framework ---
# Secure by default: views choose their session authentication explicitly (control.accounts.auth);
# with none, every endpoint that does not explicitly allow anonymous access is denied.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "control.core.exceptions.api_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# --- OpenAPI schema (the API contract; exported to openapi.yaml and checked for drift in tests) ---
SPECTACULAR_SETTINGS = {
    "TITLE": "NetaMate Control API",
    "DESCRIPTION": "Internal API behind the NetaMate Central Platform Console.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "CheckStateEnum": "control.core.serializers.CHECK_STATES",
        "SecondStepMethodEnum": "control.accounts.views.METHODS",
    },
}
API_SCHEMA_ENABLED = env.bool("DJANGO_API_SCHEMA_ENABLED", default=False)

# --- Logging (stdout; JSON in deployed environments, readable text locally) ---
LOG_LEVEL = env("DJANGO_LOG_LEVEL", default="INFO")
LOG_FORMAT = env("DJANGO_LOG_FORMAT", default="json")
if LOG_FORMAT not in ("json", "console"):
    raise ImproperlyConfigured("DJANGO_LOG_FORMAT must be 'json' or 'console'.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "control.core.logging.RequestIdFilter"},
    },
    "formatters": {
        "json": {"()": "control.core.logging.JsonFormatter", "service": SERVICE_NAME},
        "console": {
            "format": "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": LOG_FORMAT,
        },
    },
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL},
    "loggers": {
        # Route Django's own loggers through the same handler instead of its defaults.
        "django": {"handlers": ["stdout"], "level": LOG_LEVEL, "propagate": False},
        "django.server": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}
