"""
Test settings. Tests use the real PostgreSQL and Redis services (Compose locally, service
containers in CI) so integration paths are exercised; only Celery runs eagerly in-process.
"""

import os

# Tests never need a real secret; set it before base settings read the environment.
os.environ.setdefault("DJANGO_SECRET_KEY", "insecure-test-only-secret-key-never-used-outside-tests")

from .base import *

CACHES["default"]["KEY_PREFIX"] = "commerce-test"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

API_SCHEMA_ENABLED = True
