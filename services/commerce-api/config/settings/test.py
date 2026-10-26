"""
Test settings. Tests use the real PostgreSQL and Redis services (Compose locally, service
containers in CI) so integration paths are exercised; only Celery runs eagerly in-process.
"""

import os

# Tests never need a real secret; set it before base settings read the environment.
os.environ.setdefault("DJANGO_SECRET_KEY", "insecure-test-only-secret-key-never-used-outside-tests")
os.environ.setdefault(
    "MATECART_ENCRYPTION_KEY", "insecure-test-only-encryption-key-never-used-elsewhere"
)

from .base import *

CACHES["default"]["KEY_PREFIX"] = "commerce-test"
# Rate-limit counters stay in-process so each test starts from zero (conftest clears them).
CACHES["ratelimit"] = {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "rl"}

# Tests configure the deployment tenant explicitly (settings fixture), never from the environment:
# the Compose container sets MATECART_TENANT_ID for development, CI does not.
MATECART_TENANT_ID = None

# Fast hashing for tests only; deployments use Argon2 (base settings, checked in test_project).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MATECART_PUBLIC_BASE_URL = "http://testserver"
# Phone sign-in and Google stay disabled unless a test enables them with its own fakes.
MATECART_SMS_PROVIDER = ""
MATECART_PAYMENT_PROVIDER = ""
MATECART_COURIER_PROVIDER = ""
GOOGLE_OIDC_CLIENT_ID = ""
GOOGLE_OIDC_CLIENT_SECRET = ""

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

API_SCHEMA_ENABLED = True
