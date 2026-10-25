"""
Settings shared by every environment of the MateCart commerce API.

All configuration comes from environment variables. Environment modules (development, test,
production) import this module and override only what differs. Never put secrets in this file.
"""

import uuid
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()

SERVICE_NAME = "commerce-api"

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    # Installed for AbstractBaseUser, password hashing and the auth framework. Its global Group and
    # Permission tables are not used for store roles (not tenant-scoped; RBAC arrives in Phase 03).
    # Admin and sessions stay out until Phase 03 designs login and session handling.
    "django.contrib.auth",
    "rest_framework",
    "drf_spectacular",
    "commerce.core",
    "commerce.tenancy",
    "commerce.stores",
    "commerce.identity",
    "commerce.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "commerce.core.middleware.RequestIdMiddleware",
    "commerce.tenancy.middleware.TenantMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# --- Database (one dedicated PostgreSQL database per store deployment) ---
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = env.int(
    "DJANGO_DB_CONNECT_TIMEOUT", default=5
)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Tenancy (docs/ARCHITECTURE.md §3) ---
# "single" is the only supported mode: this deployment serves exactly one tenant, configured here
# and never taken from requests. A pooled mode would be a new, documented decision (D-026).
MATECART_TENANCY_MODE = env("MATECART_TENANCY_MODE", default="single")
if MATECART_TENANCY_MODE != "single":
    raise ImproperlyConfigured("MATECART_TENANCY_MODE must be 'single'; no other mode exists yet.")
_tenant_id = env("MATECART_TENANT_ID", default="").strip()
try:
    MATECART_TENANT_ID: uuid.UUID | None = uuid.UUID(_tenant_id) if _tenant_id else None
except ValueError as exc:
    raise ImproperlyConfigured("MATECART_TENANT_ID must be a UUID.") from exc

# --- Identity (docs/DATA_MODEL.md) ---
AUTH_USER_MODEL = "identity.User"
# Nothing can authenticate until Phase 03 replaces this with tenant-aware login backends.
AUTHENTICATION_BACKENDS = ["commerce.identity.backends.NoLoginBackend"]

# --- Cache (Redis) ---
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "KEY_PREFIX": "commerce",
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    }
}

# --- Internationalisation (store-level language and time zone arrive with store configuration) ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Django REST Framework ---
# Secure by default: no authentication backends exist yet, so every endpoint that does not
# explicitly allow anonymous access is denied. Phase 03 adds authentication and RBAC.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "commerce.core.exceptions.api_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# --- OpenAPI schema (the API contract; exported to openapi.yaml and checked for drift in CI) ---
SPECTACULAR_SETTINGS = {
    "TITLE": "MateCart Commerce API",
    "DESCRIPTION": "Storefront, checkout and merchant API for a single MateCart store deployment.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {"CheckStateEnum": "commerce.core.serializers.CHECK_STATES"},
}
API_SCHEMA_ENABLED = env.bool("DJANGO_API_SCHEMA_ENABLED", default=False)

# --- Celery (Redis broker) ---
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=None)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_RESULT_EXPIRES = 3600
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
# Reliability: a task is acknowledged only after it finishes and is re-queued if its worker dies.
# Every task must therefore be idempotent (safe to run more than once).
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Must exceed the longest task runtime or ETA, otherwise Redis redelivers the task.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# --- Logging (stdout; JSON in deployed environments, readable text locally) ---
LOG_LEVEL = env("DJANGO_LOG_LEVEL", default="INFO")
LOG_FORMAT = env("DJANGO_LOG_FORMAT", default="json")
if LOG_FORMAT not in ("json", "console"):
    raise ImproperlyConfigured("DJANGO_LOG_FORMAT must be 'json' or 'console'.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "commerce.core.logging.RequestIdFilter"},
    },
    "formatters": {
        "json": {"()": "commerce.core.logging.JsonFormatter", "service": SERVICE_NAME},
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
