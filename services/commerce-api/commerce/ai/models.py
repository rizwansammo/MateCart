"""AI assistant records (Phase 15, D-101 to D-104). Every table is tenant-owned.

- ``AIConfiguration``: one per store. The provider credential is encrypted with
  ``commerce.core.security.encrypt`` and is never returned by any API; only a four-character
  hint is shown. Models are the merchant's explicit choice and are never replaced silently.
- ``AIRequest``: one durable row per generation. It keeps operational metadata (feature, model,
  status, safe error code, provider-reported token usage, timings, the business record it is
  about) and the *validated* result while it is useful. Prompts are built inside the worker from
  authorised records and are never stored. Results are cleared after the retention period; the
  metadata stays for usage history.
- Review state lives on the request: a draft is applied only by an explicit staff action through
  the owning domain service (catalogue, media, marketing), which writes its own audit event.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q

from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel


class AIProviderKind(models.TextChoices):
    GROQ = "groq", "GroqCloud"
    # Deterministic fixtures for development and tests. Never calls a model; every output is
    # labelled as test output. Refused in production settings.
    TEST = "test", "Test provider (no AI)"


class AIStatus(models.TextChoices):
    NOT_CONFIGURED = "not_configured", "Not configured"
    UNVERIFIED = "unverified", "Configured but not verified"
    CONNECTED = "connected", "Connected"
    INVALID_CREDENTIALS = "invalid_credentials", "Invalid credentials"
    RATE_LIMITED = "rate_limited", "Rate limited"
    UNAVAILABLE = "unavailable", "Provider unavailable"
    UNSUPPORTED_MODEL = "unsupported_model", "Model unavailable or unsupported"
    DISABLED = "disabled", "Switched off"


class AIFeature(models.TextChoices):
    PRODUCT_CONTENT = "product_content", "Product content"
    IMAGE_ANALYSIS = "image_analysis", "Product image analysis"
    SUPPORT_REPLY = "support_reply", "Support reply suggestions"
    SUPPORT_SUMMARY = "support_summary", "Conversation summaries"
    MARKETING_COPY = "marketing_copy", "Marketing copy"
    BUSINESS_INSIGHTS = "business_insights", "Business insights"


class AIRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


FINISHED_STATUSES = (
    AIRequestStatus.COMPLETED,
    AIRequestStatus.FAILED,
    AIRequestStatus.CANCELLED,
    AIRequestStatus.EXPIRED,
)


class AIReviewStatus(models.TextChoices):
    NONE = "none", "Nothing to review"
    PENDING = "pending", "Waiting for review"
    APPLIED = "applied", "Applied"
    USED = "used", "Used by staff"
    REJECTED = "rejected", "Rejected"


def _choice(field: str, choices: type[models.TextChoices], name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=Q(**{f"{field}__in": choices.values}), name=name)


class AIConfiguration(TenantOwnedModel, TimestampedModel):
    provider = models.CharField(
        max_length=16, choices=AIProviderKind.choices, default=AIProviderKind.GROQ
    )
    api_key_encrypted = models.TextField(blank=True)
    api_key_hint = models.CharField(max_length=8, blank=True)
    # General and structured text generation; image understanding (may be empty: unavailable).
    text_model = models.CharField(max_length=120, blank=True)
    vision_model = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=24, choices=AIStatus.choices, default=AIStatus.NOT_CONFIGURED
    )
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=48, blank=True)
    # Models the provider listed at the last verification, with the capabilities MateCart knows
    # for them: [{"id", "context_window", "capabilities": [...], "preview": bool}].
    available_models = models.JSONField(default=list, blank=True)
    models_checked_at = models.DateTimeField(null=True, blank=True)
    enabled_features = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    monthly_request_limit = models.PositiveIntegerField(default=500)
    monthly_token_limit = models.PositiveIntegerField(default=1_000_000)
    max_output_tokens = models.PositiveIntegerField(default=1500)
    user_hourly_limit = models.PositiveIntegerField(default=40)
    # Support features send short, masked conversation excerpts to the provider. They stay off
    # until a settings manager confirms the store discloses this to customers (D-104).
    customer_data_confirmed_at = models.DateTimeField(null=True, blank=True)
    customer_data_confirmed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant"], name="ai_configuration_one_per_store"),
            TenantForeignKeyConstraint(
                field="customer_data_confirmed_by", name="ai_config_confirmed_by_tenant"
            ),
            _choice("provider", AIProviderKind, "ai_config_provider_valid"),
            _choice("status", AIStatus, "ai_config_status_valid"),
            models.CheckConstraint(
                condition=Q(monthly_request_limit__lte=100_000)
                & Q(monthly_token_limit__lte=500_000_000)
                & Q(max_output_tokens__gte=64, max_output_tokens__lte=8192)
                & Q(user_hourly_limit__gte=1, user_hourly_limit__lte=1000),
                name="ai_config_limits_bounded",
            ),
        ]


class AIRequest(TenantOwnedModel, TimestampedModel):
    feature = models.CharField(max_length=32, choices=AIFeature.choices)
    status = models.CharField(
        max_length=12, choices=AIRequestStatus.choices, default=AIRequestStatus.PENDING
    )
    review_status = models.CharField(
        max_length=12, choices=AIReviewStatus.choices, default=AIReviewStatus.NONE
    )
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    provider = models.CharField(max_length=16, choices=AIProviderKind.choices)
    model = models.CharField(max_length=120, blank=True)
    is_test = models.BooleanField(default=False)
    # The business record the request is about (product, media, conversation, campaign) or
    # empty for store-wide requests such as insights. Never a free-form URL.
    subject_type = models.CharField(max_length=24, blank=True)
    subject_id = models.UUIDField(null=True, blank=True)
    # Validated options (fields, language, tone, kind, period); never customer data.
    options = models.JSONField(default=dict, blank=True)
    # Names of the record types the worker used to build the context (e.g. "product",
    # "policy_pages", "order_status"); shown to staff so suggestions can be verified.
    context_sources = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    result = models.JSONField(null=True, blank=True)
    result_cleared_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=48, blank=True)
    provider_called = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    provider_request_id = models.CharField(max_length=120, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    client_key = models.CharField(max_length=64, blank=True)
    cancel_requested = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    applied_fields = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="requested_by", name="ai_request_user_tenant"),
            TenantForeignKeyConstraint(field="reviewed_by", name="ai_request_reviewer_tenant"),
            _choice("feature", AIFeature, "ai_request_feature_valid"),
            _choice("status", AIRequestStatus, "ai_request_status_valid"),
            _choice("review_status", AIReviewStatus, "ai_request_review_valid"),
            _choice("provider", AIProviderKind, "ai_request_provider_valid"),
            models.UniqueConstraint(
                fields=["tenant", "requested_by", "client_key"],
                condition=~Q(client_key=""),
                name="ai_request_client_key_unique",
            ),
            models.CheckConstraint(
                condition=Q(status=AIRequestStatus.COMPLETED) | Q(result__isnull=True),
                name="ai_request_result_only_completed",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="ai_request_recent"),
            models.Index(fields=["tenant", "status", "created_at"], name="ai_request_status"),
            models.Index(
                fields=["tenant", "requested_by", "-created_at"], name="ai_request_by_user"
            ),
            models.Index(fields=["tenant", "subject_id"], name="ai_request_subject"),
        ]
