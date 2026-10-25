"""Local development settings (Docker Compose project "matecart-dev")."""

from .base import *

DEBUG = True
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "localhost", "127.0.0.1", "[::1]"]

API_SCHEMA_ENABLED = env.bool("DJANGO_API_SCHEMA_ENABLED", default=True)
