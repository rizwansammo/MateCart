"""Test settings. Tests use the real PostgreSQL service (Compose locally, CI service container)."""

import os

# Tests never need a real secret; set it before base settings read the environment.
os.environ.setdefault("DJANGO_SECRET_KEY", "insecure-test-only-secret-key-never-used-outside-tests")
os.environ.setdefault(
    "MATECART_ENCRYPTION_KEY", "insecure-test-only-encryption-key-never-used-elsewhere"
)
# Rate limits use an in-process cache in tests; Redis is never contacted.
os.environ.setdefault("REDIS_URL", "redis://unused-in-tests:6379/0")

from .base import *

API_SCHEMA_ENABLED = True

# Fast hashing for tests only; deployments use Argon2 (base settings, checked in test_project).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Rate-limit counters stay in-process so each test starts from zero (conftest clears them).
CACHES["ratelimit"] = {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "rl"}
MATECART_CONSOLE_PUBLIC_BASE_URL = "http://testserver"
