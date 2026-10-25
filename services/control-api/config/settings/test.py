"""Test settings. Tests use the real PostgreSQL service (Compose locally, CI service container)."""

import os

# Tests never need a real secret; set it before base settings read the environment.
os.environ.setdefault("DJANGO_SECRET_KEY", "insecure-test-only-secret-key-never-used-outside-tests")

from .base import *

API_SCHEMA_ENABLED = True

# Fast hashing for tests only; real hashers are chosen in Phase 03.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
