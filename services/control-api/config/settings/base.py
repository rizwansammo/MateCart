"""
Settings shared by every environment of the NetaMate control API (Central Platform Console).

All configuration comes from environment variables. Environment modules (development, test,
production) import this module and override only what differs. Never put secrets in this file.

Boundary: this service owns NetaMate's operational data (clients, servers, deployments). It must
never hold copies of client stores' business data, and client stores must keep working when it
is unavailable.
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()

SERVICE_NAME = "control-api"

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    # django.contrib.auth, admin and sessions are deliberately not installed yet. Central
    # administrator authentication is designed in Phase 03 (separate from merchant accounts).
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
    "control.core",
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

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Django REST Framework ---
# Secure by default: no authentication backends exist yet, so every endpoint that does not
# explicitly allow anonymous access is denied. Phase 03 adds administrator authentication.
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
    "ENUM_NAME_OVERRIDES": {"CheckStateEnum": "control.core.serializers.CHECK_STATES"},
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
