"""
Production settings skeleton. Deployment specifics (proxy, TLS, HSTS policy, static and media
storage) are finalised and verified in Phase 17; until then this module only enforces safe defaults.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("__GENERATE__", "insecure")):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be a unique random value of 50+ characters.")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the store's domains.")
if MATECART_TENANT_ID is None:
    raise ImproperlyConfigured("MATECART_TENANT_ID must identify the store this deployment serves.")
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
if not MATECART_PUBLIC_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured("MATECART_PUBLIC_BASE_URL must be the store's https:// origin.")
if EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
    raise ImproperlyConfigured("EMAIL_URL must point to a real SMTP service in production.")
# Test doubles must never answer real customers (a fake provider would "send" codes nowhere).
if MATECART_SMS_PROVIDER.startswith("tests."):
    raise ImproperlyConfigured("MATECART_SMS_PROVIDER cannot be a test fake in production.")
if MATECART_PAYMENT_PROVIDER.startswith("tests."):
    raise ImproperlyConfigured("MATECART_PAYMENT_PROVIDER cannot be a test fake in production.")
if bool(GOOGLE_OIDC_CLIENT_ID) != bool(GOOGLE_OIDC_CLIENT_SECRET):
    raise ImproperlyConfigured(
        "Set both GOOGLE_OIDC_CLIENT_ID and GOOGLE_OIDC_CLIENT_SECRET, or neither."
    )

# The reverse proxy terminates TLS and must overwrite (never pass through) X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Container health checks call the app directly over HTTP.
SECURE_REDIRECT_EXEMPT = [r"^api/health/"]
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=3600)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS includeSubDomains/preload depend on each client's domain setup and are decided per
# deployment in Phase 17, so these two deploy-check warnings are acknowledged here.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
