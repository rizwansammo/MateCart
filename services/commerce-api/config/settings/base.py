"""
Settings shared by every environment of the MateCart commerce API.

All configuration comes from environment variables. Environment modules (development, test,
production) import this module and override only what differs. Never put secrets in this file.
"""

import uuid
from pathlib import Path
from typing import Any

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
    # Installed for AbstractBaseUser, password hashing and validators. Its global Group and
    # Permission tables are not used: store roles are tenant-scoped (commerce.staff, D-044).
    # Django's admin and session apps are not used: realm sessions live in identity (D-040).
    "django.contrib.auth",
    "rest_framework",
    "drf_spectacular",
    "commerce.core",
    "commerce.tenancy",
    "commerce.stores",
    "commerce.identity",
    "commerce.staff",
    "commerce.audit",
    # Phase 04: product and inventory engine (docs/DATA_MODEL.md §2, D-050..D-058).
    "commerce.media",
    "commerce.catalog",
    "commerce.pricing",
    "commerce.inventory",
    # Phase 07: customer/guest carts and server-owned checkout sessions.
    "commerce.checkout",
    "commerce.orders",
    # Phase 09: physical shipments, courier tracking, COD reconciliation and fraud review.
    "commerce.fulfillment",
    # Phase 10: customer purchase-linked after sales records.
    "commerce.aftersales",
    # Phase 12: campaigns, flash sales, loyalty, SEO, consent, analytics and Meta tracking.
    "commerce.marketing",
    # Phase 13: notification engine, email connections (SMTP, IMAP, MateMail), SMS, templates.
    "commerce.notifications",
    # Phase 14: website live chat (WebSocket), Messenger, WhatsApp, Instagram, unified inbox.
    "commerce.communications",
    # Phase 05: store content (pages, homepage sections). Theme and modules live in stores.
    "commerce.content",
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
# Phase 14 (D-092): HTTP and WebSocket are served by one ASGI application (config.asgi).
ASGI_APPLICATION = "config.asgi.application"

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
# Any RFC 9562 version is accepted (ids are issued externally, D-036); nil/max are placeholders.
if MATECART_TENANT_ID is not None and MATECART_TENANT_ID.variant != uuid.RFC_4122:
    raise ImproperlyConfigured("MATECART_TENANT_ID must be an RFC 9562 UUID (not nil or max).")

# --- Identity (docs/DATA_MODEL.md) ---
AUTH_USER_MODEL = "identity.User"
# Django's authenticate() is never used: sign-in runs through realm-aware services
# (commerce.identity.passwords and friends), so this backend refuses everything.
AUTHENTICATION_BACKENDS = ["commerce.identity.backends.NoLoginBackend"]

# --- Authentication & security (docs/SECURITY.md, D-039..D-044) ---
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS: list[dict[str, Any]] = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ("email", "full_name")},
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# Encrypts secrets that must be read back (TOTP seeds) and keys code hashes. Separate from
# SECRET_KEY; rotating it invalidates authenticator-app enrolments and open codes.
MATECART_ENCRYPTION_KEY = env("MATECART_ENCRYPTION_KEY", default="")
# Cookies carry the Secure flag unless explicitly disabled for plain-HTTP local development.
MATECART_SECURE_COOKIES = env.bool("MATECART_SECURE_COOKIES", default=True)
MATECART_SESSIONS = {
    "customer": {
        "absolute_seconds": env.int("MATECART_CUSTOMER_SESSION_SECONDS", default=30 * 24 * 3600),
        "idle_seconds": env.int("MATECART_CUSTOMER_IDLE_SECONDS", default=14 * 24 * 3600),
    },
    "staff": {
        "absolute_seconds": env.int("MATECART_STAFF_SESSION_SECONDS", default=12 * 3600),
        "idle_seconds": env.int("MATECART_STAFF_IDLE_SECONDS", default=2 * 3600),
    },
}
# The staff cookie is only ever sent to the merchant API, never to storefront or customer routes.
MATECART_SESSION_COOKIES = {
    "customer": {"name": "mc_customer", "path": "/"},
    "staff": {"name": "mc_staff", "path": "/api/manage"},
}
CSRF_COOKIE_NAME = "mc_csrf"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # the web apps echo it in X-CSRFToken
CSRF_COOKIE_SECURE = MATECART_SECURE_COOKIES
CSRF_TRUSTED_ORIGINS: list[str] = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
# Number of reverse proxies in front of the API whose X-Forwarded-For entries are trusted.
MATECART_TRUSTED_PROXY_COUNT = env.int("MATECART_TRUSTED_PROXY_COUNT", default=0)
# The storefront's public origin (e.g. https://shop.example.com): links in emails, OAuth redirect.
MATECART_PUBLIC_BASE_URL = env("MATECART_PUBLIC_BASE_URL", default="").rstrip("/")

# Email: smtp://, smtp+tls://, smtp+ssl:// in deployments; Mailpit locally. The default discards
# mail, and the console backend is never used, because messages contain sign-in codes.
_email = env.email_url("EMAIL_URL", default="dummymail://")
EMAIL_BACKEND: str = _email["EMAIL_BACKEND"]
EMAIL_HOST: str = _email.get("EMAIL_HOST") or "localhost"
EMAIL_PORT: int = _email.get("EMAIL_PORT") or 25
EMAIL_HOST_USER: str = _email.get("EMAIL_HOST_USER") or ""
EMAIL_HOST_PASSWORD: str = _email.get("EMAIL_HOST_PASSWORD") or ""
EMAIL_USE_TLS: bool = bool(_email.get("EMAIL_USE_TLS"))
EMAIL_USE_SSL: bool = bool(_email.get("EMAIL_USE_SSL"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MateCart <no-reply@matecart.invalid>")
EMAIL_TIMEOUT = 10
# Dotted path to an SMS provider class (commerce.core.delivery.SmsProvider). Empty disables phone
# sign-in. The managed gateway and per-store usage billing arrive in Phase 13.
MATECART_SMS_PROVIDER = env("MATECART_SMS_PROVIDER", default="")
# Dotted path to one online payment provider (commerce.orders.providers.PaymentProvider). Empty
# keeps online payment unavailable; no gateway is selected yet (D-072).
MATECART_PAYMENT_PROVIDER = env("MATECART_PAYMENT_PROVIDER", default="")
# Dotted path to one courier adapter. Empty keeps external booking/callbacks unavailable while the
# authenticated manual shipment workflow remains fully operational.
MATECART_COURIER_PROVIDER = env("MATECART_COURIER_PROVIDER", default="")
# Phase 12 (D-084): Meta Conversions API. The access token is a deployment secret: it is read
# only here, never stored in the database, returned by an API or logged. Empty = server-side
# delivery unavailable (the browser Pixel can still be configured by the merchant).
META_CAPI_ACCESS_TOKEN = env("META_CAPI_ACCESS_TOKEN", default="")
META_GRAPH_API_VERSION = env("META_GRAPH_API_VERSION", default="v26.0")
# Phase 13 (D-086): the MateMail instance a store may connect to (HTTPS, set by NetaMate; empty =
# the MateMail option is unavailable). Never merchant input, so it cannot be used for SSRF.
MATEMAIL_BASE_URL = env("MATEMAIL_BASE_URL", default="").rstrip("/")
# Mail hosts allowed to resolve to private addresses and to skip TLS. Development only (Mailpit);
# production leaves it empty, so every SMTP/IMAP host must be public and use TLS.
MATECART_MAIL_INTERNAL_HOSTS: list[str] = []
# "Continue with Google" (OpenID Connect). Both empty keeps the option hidden.
GOOGLE_OIDC_CLIENT_ID = env("GOOGLE_OIDC_CLIENT_ID", default="")
GOOGLE_OIDC_CLIENT_SECRET = env("GOOGLE_OIDC_CLIENT_SECRET", default="")

# --- Media (uploaded catalogue files; docs/SECURITY.md "Catalogue media") ---
# Files live under tenants/<tenant id>/... (tenant_storage_path), never in PostgreSQL. Nothing
# serves MEDIA_ROOT directly: API views stream files after checking publication or staff access.
MEDIA_ROOT = env("MATECART_MEDIA_ROOT", default=str(BASE_DIR / "media"))
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"file_permissions_mode": 0o640, "directory_permissions_mode": 0o750},
    },
}
# Largest accepted upload; checked before the image decoder runs (commerce.media.services).
MATECART_MEDIA_MAX_BYTES = env.int("MATECART_MEDIA_MAX_BYTES", default=10 * 1024 * 1024)

# --- Cache (Redis) ---
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "KEY_PREFIX": "commerce",
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    },
    # Sign-in attempt counters (commerce.core.ratelimit), kept apart from application caching.
    "ratelimit": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "KEY_PREFIX": "commerce-rl",
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    },
}

# --- Real-time events (Phase 14, D-092) ---
# Channels distributes WebSocket events between ASGI workers through the store's own Redis (a
# separate database number). It is a transport only: messages are persisted in PostgreSQL first.
CHANNEL_LAYER_URL = env("MATECART_CHANNEL_LAYER_URL", default="")
CHANNEL_LAYERS: dict[str, Any] = (
    {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [CHANNEL_LAYER_URL],
                "prefix": "commerce-ws",
                "capacity": 200,
                "expiry": 30,
                "group_expiry": 3600,
            },
        }
    }
    if CHANNEL_LAYER_URL
    else {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)

# --- Communications (Phase 14, D-092 to D-098; docs/COMMUNICATIONS.md) ---
# Meta app used for Messenger, Instagram and WhatsApp. Deployment secrets: read only here, never
# stored in the database, returned by an API or logged. Empty = those channels show
# "configuration required"; website chat needs none of them.
META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
META_WEBHOOK_VERIFY_TOKEN = env("META_WEBHOOK_VERIFY_TOKEN", default="")
# Facebook Login for Business / WhatsApp Embedded Signup configuration ids (optional).
META_LOGIN_CONFIG_ID = env("META_LOGIN_CONFIG_ID", default="")
META_WHATSAPP_CONFIG_ID = env("META_WHATSAPP_CONFIG_ID", default="")
# Set only after Meta approves the app's Human Agent feature: allows Messenger and Instagram
# replies with the HUMAN_AGENT tag up to 7 days after the customer's last message.
META_HUMAN_AGENT_APPROVED = env.bool("META_HUMAN_AGENT_APPROVED", default=False)
# Development simulator (D-097): a labelled fake connection for local acceptance. Never in
# production (production settings force it off).
MATECART_CHANNEL_SIMULATOR = env.bool("MATECART_CHANNEL_SIMULATOR", default=False)
# Browser origins allowed to open WebSockets (besides MATECART_PUBLIC_BASE_URL). Defaults to the
# CSRF trusted origins, which already list the storefront origins.
MATECART_WEBSOCKET_ORIGINS: list[str] = env.list("MATECART_WEBSOCKET_ORIGINS", default=[])
# Retention (D-096): message bodies and files of inactive closed conversations.
MATECART_CONVERSATION_RETENTION_DAYS = env.int("MATECART_CONVERSATION_RETENTION_DAYS", default=365)
MATECART_GUEST_CHAT_RETENTION_DAYS = env.int("MATECART_GUEST_CHAT_RETENTION_DAYS", default=180)

# --- Templates (email bodies only; the web UI is the Next.js apps) ---
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

# --- Internationalisation (store-level language and time zone arrive with store configuration) ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Django REST Framework ---
# Secure by default: views choose their realm's authentication explicitly (commerce.identity.auth);
# with none, every endpoint that does not explicitly allow anonymous access is denied.
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
    "ENUM_NAME_OVERRIDES": {
        "CheckStateEnum": "commerce.core.serializers.CHECK_STATES",
        "StaffRoleEnum": "commerce.staff.permissions.StaffRole",
        "AssignableRoleEnum": "commerce.staff.permissions.ASSIGNABLE_ROLES",
        "MfaMethodEnum": "commerce.staff.serializers.MFA_METHODS",
        "ProductStatusEnum": "commerce.catalog.models.ProductStatus",
        "ProductTypeEnum": "commerce.catalog.models.ProductType",
        "TrackingModeEnum": "commerce.catalog.models.TrackingMode",
        "VariantKindEnum": "commerce.catalog.models.VariantKind",
        "InventoryUnitEnum": "commerce.catalog.models.InventoryUnit",
        "NetUnitEnum": "commerce.catalog.models.NetUnit",
        "AttributeValueTypeEnum": "commerce.catalog.models.AttributeValueType",
        "AttributeScopeEnum": "commerce.catalog.models.AttributeScope",
        "MovementTypeEnum": "commerce.inventory.models.MovementType",
        "SerialStatusEnum": "commerce.inventory.models.SerialStatus",
        "MediaKindEnum": "commerce.media.models.MediaKind",
        "PageStatusEnum": "commerce.content.models.PageStatus",
        "PageTypeEnum": "commerce.content.models.PageType",
        "SectionTypeEnum": "commerce.content.models.SectionType",
        "CartStatusEnum": "commerce.checkout.models.CartStatus",
        "CheckoutStatusEnum": "commerce.checkout.models.CheckoutStatus",
        "CouponDiscountTypeEnum": "commerce.checkout.models.CouponDiscountType",
        "OrderStatusEnum": "commerce.orders.models.OrderStatus",
        "OrderSourceEnum": "commerce.orders.models.OrderSource",
        "AssistedOrderSourceEnum": "commerce.orders.serializers.ASSISTED_SOURCE_CHOICES",
        "OrderPaymentStatusEnum": "commerce.orders.models.OrderPaymentStatus",
        "PaymentMethodEnum": "commerce.orders.models.PaymentMethod",
        "RefundStatusEnum": "commerce.orders.models.RefundStatus",
        "RefundMethodEnum": "commerce.orders.models.RefundMethod",
        "MerchantOrderTargetEnum": "commerce.orders.serializers.MERCHANT_TARGETS",
        "ShipmentStatusEnum": "commerce.fulfillment.models.ShipmentStatus",
        "ShipmentTransitionTargetEnum": "commerce.fulfillment.serializers.TRANSITION_TARGETS",
        "BookingStatusEnum": "commerce.fulfillment.models.BookingStatus",
        "CodReconciliationStatusEnum": "commerce.fulfillment.models.CodReconciliationStatus",
        "FraudReviewStatusEnum": "commerce.fulfillment.models.FraudReviewStatus",
        "FraudDecisionEnum": "commerce.fulfillment.models.FraudDecision",
        "AfterSalesCaseKindEnum": "commerce.aftersales.models.CaseKind",
        "AfterSalesCaseStatusEnum": "commerce.aftersales.models.CaseStatus",
        "PurchaseReviewStatusEnum": "commerce.aftersales.models.ReviewStatus",
        "DigitalEntitlementStatusEnum": "commerce.aftersales.models.EntitlementStatus",
        "PromotionKindEnum": "commerce.marketing.promotions.PUBLIC_KINDS",
        "CampaignKindEnum": "commerce.marketing.models.CampaignKind",
        "PromotionStatusEnum": "commerce.marketing.models.PromotionStatus",
        "PromotionStateEnum": "commerce.marketing.serializers.PROMOTION_STATES",
        "PromotionActionEnum": "commerce.marketing.serializers.PROMOTION_ACTIONS",
        "CampaignTargetKindEnum": "commerce.marketing.serializers.TARGET_KINDS",
        "MarketingLookupKindEnum": "commerce.marketing.serializers.LOOKUP_KINDS",
        "SitemapEntryTypeEnum": "commerce.marketing.serializers.SITEMAP_TYPES",
        "ConsentOriginEnum": "commerce.marketing.serializers.CONSENT_ORIGINS",
        "BrowserEventEnum": "commerce.marketing.serializers.BROWSER_EVENTS",
        "PixelStateEnum": "commerce.marketing.serializers.PIXEL_STATES",
        "ServerEventStateEnum": "commerce.marketing.serializers.SERVER_EVENT_STATES",
        "AnalyticsRangeEnum": "commerce.marketing.serializers.ANALYTICS_RANGES",
        "PurchaseTimingEnum": "commerce.marketing.models.PurchaseTiming",
        "NotificationStatusEnum": "commerce.notifications.models.NotificationStatus",
        "NotificationChannelEnum": "commerce.notifications.models.Channel",
        "NotificationCategoryEnum": "commerce.notifications.models.Category",
        "MailProviderEnum": "commerce.notifications.models.MailProvider",
        "MailConnectionStatusEnum": "commerce.notifications.models.ConnectionStatus",
        "MailSecurityEnum": "commerce.notifications.models.SmtpSecurity",
        "SmsSenderStatusEnum": "commerce.notifications.models.SenderStatus",
        "OtpChannelEnum": "commerce.identity.models.OtpChannel",
        "WarrantyProviderEnum": "commerce.catalog.models.WarrantyProvider",
        "ChannelTypeEnum": "commerce.communications.models.ChannelType",
        "ChannelConnectionStatusEnum": "commerce.communications.serializers.CONNECTION_STATES",
        "ConversationStatusEnum": "commerce.communications.models.ConversationStatus",
        "MessageDirectionEnum": "commerce.communications.models.MessageDirection",
        "MessageKindEnum": "commerce.communications.models.MessageKind",
        "MessageDeliveryStatusEnum": "commerce.communications.models.DeliveryStatus",
        "AttachmentStatusEnum": "commerce.communications.models.AttachmentStatus",
        "ConversationEventKindEnum": "commerce.communications.models.EventKind",
        "OrderLinkKindEnum": "commerce.communications.models.OrderLinkKind",
        "ChatLanguagesEnum": "commerce.communications.models.ChatLanguages",
        "LauncherPositionEnum": "commerce.communications.models.LauncherPosition",
    },
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
# Phase 13 (D-087): the MateCart beat service (compose: commerce-beat) runs these. Every entry is
# idempotent, so a missed or doubled run is harmless.
CELERY_BEAT_SCHEDULE = {
    "notifications-sweep": {"task": "notifications.sweep", "schedule": 60.0},
    "notifications-stock-alerts": {
        "task": "notifications.run_job",
        "schedule": 900.0,
        "args": ("stock_alerts",),
    },
    "notifications-daily-summary": {
        "task": "notifications.run_job",
        "schedule": 3600.0,
        "args": ("daily_summary",),
    },
    "notifications-points-reminders": {
        "task": "notifications.run_job",
        "schedule": 86400.0,
        "args": ("points_reminders",),
    },
    "notifications-cleanup": {
        "task": "notifications.run_job",
        "schedule": 86400.0,
        "args": ("cleanup",),
    },
    # Phase 14: re-queue stuck outbound messages and media downloads; daily housekeeping.
    "communications-sweep": {"task": "communications.sweep", "schedule": 60.0},
    "communications-housekeeping": {"task": "communications.housekeeping", "schedule": 3600.0},
}

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
