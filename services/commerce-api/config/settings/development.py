"""Local development settings (Docker Compose project "matecart-dev")."""

from .base import *

DEBUG = True
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "localhost", "127.0.0.1", "[::1]"]

API_SCHEMA_ENABLED = env.bool("DJANGO_API_SCHEMA_ENABLED", default=True)

# Phase 13: the local Mailpit catcher may be used as a merchant SMTP/IMAP test server.
MATECART_MAIL_INTERNAL_HOSTS = ["mailpit"]
