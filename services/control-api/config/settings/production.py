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
if len(MATECART_ENCRYPTION_KEY) < 43 or MATECART_ENCRYPTION_KEY.startswith(
    ("__GENERATE__", "insecure")
):
    raise ImproperlyConfigured(
        "MATECART_ENCRYPTION_KEY must be a unique random value of 43+ characters."
    )
if MATECART_ENCRYPTION_KEY == SECRET_KEY:
    raise ImproperlyConfigured("MATECART_ENCRYPTION_KEY must differ from DJANGO_SECRET_KEY.")
if not MATECART_SECURE_COOKIES:
    raise ImproperlyConfigured("MATECART_SECURE_COOKIES cannot be disabled in production.")
if not MATECART_CONSOLE_PUBLIC_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured(
        "MATECART_CONSOLE_PUBLIC_BASE_URL must be the console's https:// origin."
    )
if EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
    raise ImproperlyConfigured("EMAIL_URL must point to a real SMTP service in production.")

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
