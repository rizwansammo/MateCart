"""
Production settings skeleton. Deployment specifics (proxy, TLS, HSTS policy, network exposure)
are finalised and verified in Phases 16-17; until then this module only enforces safe defaults.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("__GENERATE__", "insecure")):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be a unique random value of 50+ characters.")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the console's domains.")

# The reverse proxy terminates TLS and must overwrite (never pass through) X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Container health checks call the app directly over HTTP.
SECURE_REDIRECT_EXEMPT = [r"^api/health/"]
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=3600)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS includeSubDomains/preload are decided with the console's domain in Phase 17, so these two
# deploy-check warnings are acknowledged here.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
