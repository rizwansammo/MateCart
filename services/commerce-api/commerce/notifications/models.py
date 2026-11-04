"""Notification engine records (Phase 13, D-086 to D-090). Every table is tenant-owned.

``Notification`` is the durable outbox: rows are written in the business transaction that caused
them, so a committed event is never lost when the broker is down. ``DeliveryAttempt`` is
append-only. Rendered content is kept only as long as the retention policy allows; secrets
(authentication codes, reset links, credentials) never enter these tables.
"""

from __future__ import annotations

from typing import Any

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

from commerce.catalog.models import ProductVariant
from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

SHA256_HEX = r"^[0-9a-f]{64}$"


class AppendOnlyError(RuntimeError):
    pass


# --- Email connections ----------------------------------------------------------------------------


class MailProvider(models.TextChoices):
    SMTP = "smtp", "Standard SMTP"
    MATEMAIL = "matemail", "MateMail Connected App"


class ConnectionStatus(models.TextChoices):
    NOT_CONFIGURED = "not_configured", "Not configured"
    UNVERIFIED = "unverified", "Configured but not verified"
    AWAITING_APPROVAL = "awaiting_approval", "Waiting for approval in MateMail"
    CONNECTED = "connected", "Connected"
    FAILED = "failed", "Failed"
    REVOKED = "revoked", "Revoked by the provider"


class SmtpSecurity(models.TextChoices):
    STARTTLS = "starttls", "STARTTLS (port 587)"
    TLS = "tls", "TLS (port 465)"
    NONE = "none", "None (local development only)"


class MailConnection(TenantOwnedModel, TimestampedModel):
    """One per provider per store. ``is_enabled`` marks the store's email transport: at most one
    connection is enabled, and only a connected one is used for delivery. Secrets are encrypted
    with ``commerce.core.security.encrypt`` and never returned by an API."""

    provider = models.CharField(max_length=16, choices=MailProvider.choices)
    is_enabled = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=ConnectionStatus.choices, default=ConnectionStatus.NOT_CONFIGURED
    )
    display_name = models.CharField(max_length=80, blank=True)
    # Standard SMTP (outbound).
    smtp_host = models.CharField(max_length=253, blank=True)
    smtp_port = models.PositiveIntegerField(null=True, blank=True)
    smtp_security = models.CharField(
        max_length=10, choices=SmtpSecurity.choices, default=SmtpSecurity.STARTTLS
    )
    smtp_username = models.CharField(max_length=254, blank=True)
    smtp_password_encrypted = models.TextField(blank=True)
    from_email = models.EmailField(blank=True)
    from_name = models.CharField(max_length=80, blank=True)
    reply_to = models.EmailField(blank=True)
    sender_verified_at = models.DateTimeField(null=True, blank=True)
    sender_code_hash = models.CharField(max_length=64, blank=True)
    sender_code_expires_at = models.DateTimeField(null=True, blank=True)
    # IMAP (connection verification only; see MAIL_AND_NOTIFICATIONS.md for the boundary).
    imap_host = models.CharField(max_length=253, blank=True)
    imap_port = models.PositiveIntegerField(null=True, blank=True)
    imap_security = models.CharField(
        max_length=10, choices=SmtpSecurity.choices, default=SmtpSecurity.TLS
    )
    imap_username = models.CharField(max_length=254, blank=True)
    imap_password_encrypted = models.TextField(blank=True)
    imap_status = models.CharField(
        max_length=20, choices=ConnectionStatus.choices, default=ConnectionStatus.NOT_CONFIGURED
    )
    imap_verified_at = models.DateTimeField(null=True, blank=True)
    imap_error = models.CharField(max_length=60, blank=True)
    # MateMail Connected App.
    matemail_tenant_id = models.CharField(max_length=36, blank=True)
    matemail_poll_token_encrypted = models.TextField(blank=True)
    matemail_authorization_url = models.CharField(max_length=500, blank=True)
    matemail_request_expires_at = models.DateTimeField(null=True, blank=True)
    matemail_access_token_encrypted = models.TextField(blank=True)
    matemail_integration_id = models.CharField(max_length=36, blank=True)
    matemail_mailbox = models.EmailField(blank=True)
    matemail_scopes = models.CharField(max_length=200, blank=True)
    # Health.
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=60, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "provider"], name="notifications_connection_provider_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(is_enabled=True),
                name="notifications_connection_one_enabled",
            ),
            models.CheckConstraint(
                condition=Q(provider__in=MailProvider.values),
                name="notifications_connection_provider_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=ConnectionStatus.values),
                name="notifications_connection_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(smtp_port__isnull=True)
                | (Q(smtp_port__gte=1) & Q(smtp_port__lte=65535)),
                name="notifications_connection_port_valid",
            ),
        ]


# --- Templates ------------------------------------------------------------------------------------


class NotificationTemplate(TenantOwnedModel, TimestampedModel):
    """A merchant's override of a system template (``registry.KINDS``) for one language. Text
    only: the branded HTML layout is built by the server and every variable is escaped."""

    key = models.CharField(max_length=60)
    locale = models.CharField(max_length=8)
    is_enabled = models.BooleanField(default=True)
    subject = models.CharField(max_length=160, blank=True)
    heading = models.CharField(max_length=120, blank=True)
    body = models.TextField(max_length=4000, blank=True)
    sms = models.CharField(max_length=480, blank=True)
    in_app_title = models.CharField(max_length=120, blank=True)
    in_app_body = models.CharField(max_length=300, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="updated_by", name="notifications_template_by_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "key", "locale"], name="notifications_template_unique"
            ),
        ]


# --- Notifications and attempts -------------------------------------------------------------------


class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    IN_APP = "in_app", "In app"


class Category(models.TextChoices):
    TRANSACTIONAL = "transactional", "Order and account updates"
    SECURITY = "security", "Security"
    OPERATIONAL = "operational", "Store operations"
    MARKETING = "marketing", "Marketing"


class Audience(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    STAFF = "staff", "Staff"


class NotificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    SENT = "sent", "Accepted by the provider"
    DELIVERED = "delivered", "Delivery confirmed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    SUPPRESSED = "suppressed", "Not sent (preferences or limits)"


class Notification(TenantOwnedModel, TimestampedModel):
    """One message on one channel for one recipient. ``event_key`` names the business event
    (for example ``order.placed:<order id>``); with the channel and recipient it is unique, so
    retries and replays can never create a second notification for the same event."""

    kind = models.CharField(max_length=60)
    category = models.CharField(max_length=16, choices=Category.choices)
    channel = models.CharField(max_length=8, choices=Channel.choices)
    audience = models.CharField(max_length=10, choices=Audience.choices)
    event_key = models.CharField(max_length=160)
    recipient_key = models.CharField(max_length=64, validators=[RegexValidator(SHA256_HEX)])
    recipient_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    recipient_address = models.CharField(max_length=254, blank=True)
    required_permission = models.CharField(max_length=40, blank=True)
    locale = models.CharField(max_length=8, default="en")
    template_version = models.PositiveIntegerField(default=0)
    related_type = models.CharField(max_length=40, blank=True)
    related_id = models.CharField(max_length=64, blank=True)
    link_path = models.CharField(max_length=200, blank=True)
    # Rendered at creation (D-088): a later template edit never changes a committed message.
    # Cleared by the retention policy; never contains authentication secrets.
    subject = models.CharField(max_length=200, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    status = models.CharField(
        max_length=12, choices=NotificationStatus.choices, default=NotificationStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    scheduled_at = models.DateTimeField()
    queued_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=40, blank=True)
    provider = models.CharField(max_length=20, blank=True)
    provider_reference = models.CharField(max_length=200, blank=True)
    content_cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="recipient_user", name="notifications_recipient_user_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "event_key", "channel", "recipient_key"],
                name="notifications_event_unique",
            ),
            models.CheckConstraint(
                condition=Q(channel__in=Channel.values), name="notifications_channel_valid"
            ),
            models.CheckConstraint(
                condition=Q(category__in=Category.values), name="notifications_category_valid"
            ),
            models.CheckConstraint(
                condition=Q(audience__in=Audience.values), name="notifications_audience_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=NotificationStatus.values),
                name="notifications_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(channel=Channel.IN_APP) | Q(recipient_user__isnull=False),
                name="notifications_in_app_has_user",
            ),
            models.CheckConstraint(
                condition=Q(channel=Channel.IN_APP) | ~Q(recipient_address=""),
                name="notifications_transport_has_address",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "scheduled_at"], name="notifications_due"),
            models.Index(
                fields=["tenant", "recipient_user", "channel", "read_at"],
                name="notifications_inbox",
            ),
            models.Index(fields=["tenant", "created_at"], name="notifications_created"),
        ]


class AttemptOutcome(models.TextChoices):
    ACCEPTED = "accepted", "Accepted by the provider"
    RETRYABLE = "retryable", "Temporary failure; will retry"
    PERMANENT = "permanent", "Permanent failure"
    UNKNOWN = "unknown", "Outcome unknown; not resent"


class DeliveryAttempt(TenantOwnedModel):
    """Append-only (ORM guard and PostgreSQL trigger)."""

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="delivery_attempts"
    )
    number = models.PositiveSmallIntegerField()
    provider = models.CharField(max_length=20)
    outcome = models.CharField(max_length=12, choices=AttemptOutcome.choices)
    error_code = models.CharField(max_length=40, blank=True)
    provider_reference = models.CharField(max_length=200, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    occurred_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="notification", name="notifications_attempt_notification_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "notification", "number"], name="notifications_attempt_unique"
            ),
            models.CheckConstraint(
                condition=Q(outcome__in=AttemptOutcome.values),
                name="notifications_attempt_outcome_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.number}:{self.outcome}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Delivery attempts are append-only.")
        super().save(*args, **kwargs)


# --- Preferences and consent ----------------------------------------------------------------------


class NotificationPreference(TenantOwnedModel, TimestampedModel):
    """A user's choice for one topic and channel. Absent row = the topic's default."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    topic = models.CharField(max_length=40)
    channel = models.CharField(max_length=8, choices=Channel.choices)
    enabled = models.BooleanField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="notifications_preference_user_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "user", "topic", "channel"],
                name="notifications_preference_unique",
            ),
        ]


class CommunicationConsent(TenantOwnedModel):
    """Append-only evidence for optional marketing messages (opt-in, withdrawal, unsubscribe).
    Separate from Phase 12 tracking consent: accepting cookies never subscribes anyone."""

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    channel = models.CharField(max_length=8, choices=Channel.choices)
    granted = models.BooleanField()
    source = models.CharField(max_length=20)
    recorded_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="notifications_consent_user_tenant"),
            models.CheckConstraint(
                condition=Q(source__in=["account", "unsubscribe_link", "staff"]),
                name="notifications_consent_source_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "user", "recorded_at"], name="notifications_consent"),
        ]

    def __str__(self) -> str:
        return f"{self.channel}:{self.granted}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Consent evidence is append-only.")
        super().save(*args, **kwargs)


# --- SMS ------------------------------------------------------------------------------------------


class SenderStatus(models.TextChoices):
    NONE = "none", "No sender requested"
    PENDING = "pending", "Waiting for provider approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class SmsSettings(TenantOwnedModel, TimestampedModel):
    """The store's SMS configuration. The merchant requests a sender id; only NetaMate approves
    it (``manage.py approve_sms_sender``), after the provider registered it. Allowance and
    prepaid credits are provisioned administratively; nothing here is purchased."""

    is_enabled = models.BooleanField(default=False)
    requested_sender = models.CharField(max_length=11, blank=True)
    approved_sender = models.CharField(max_length=11, blank=True)
    sender_status = models.CharField(
        max_length=10, choices=SenderStatus.choices, default=SenderStatus.NONE
    )
    order_updates = models.BooleanField(default=True)
    shipping_updates = models.BooleanField(default=True)
    monthly_allowance = models.PositiveIntegerField(default=0)
    prepaid_credits = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant"], name="notifications_sms_settings_one"),
            models.CheckConstraint(
                condition=Q(sender_status__in=SenderStatus.values),
                name="notifications_sms_sender_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(sender_status=SenderStatus.APPROVED) | ~Q(approved_sender=""),
                name="notifications_sms_approved_has_sender",
            ),
        ]


class SmsUsageState(models.TextChoices):
    """Reservation life cycle (D-091). Units count against the store while reserved, accepted or
    held; only a definite non-acceptance releases them."""

    RESERVED = "reserved", "Reserved before sending"
    ACCEPTED = "accepted", "Accepted by the gateway"
    RELEASED = "released", "Not accepted; units returned"
    HELD = "held", "Outcome unknown; units held until reconciled"


SMS_FUNDING = ("", "allowance", "prepaid", "mixed", "auth", "overage")
"""``funded_by`` values. ``auth``: authentication codes, exempt from the store's notification
units (D-091). ``overage`` exists only in records written before D-091; it is never written now."""


class SmsUsage(TenantOwnedModel):
    """One SMS reservation and its outcome, for accounting. Segments follow GSM-7/UCS-2 rules
    (D-089). Notification SMS reserve their units atomically before the provider call (D-091):
    ``allowance_segments`` from the store-local month's allowance, ``prepaid_segments`` from
    prepaid credits (already deducted from ``SmsSettings.prepaid_credits``)."""

    notification = models.ForeignKey(
        Notification, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    attempt = models.PositiveSmallIntegerField(
        default=0, help_text="The notification's delivery attempt; 0 for authentication SMS."
    )
    purpose = models.CharField(max_length=40)
    period = models.CharField(max_length=7, help_text="YYYY-MM in the store's time zone.")
    segments = models.PositiveSmallIntegerField()
    encoding = models.CharField(max_length=8)
    accepted = models.BooleanField()
    state = models.CharField(
        max_length=10, choices=SmsUsageState.choices, default=SmsUsageState.RESERVED
    )
    funded_by = models.CharField(max_length=10, blank=True)
    allowance_segments = models.PositiveSmallIntegerField(default=0)
    prepaid_segments = models.PositiveSmallIntegerField(default=0)
    occurred_at = models.DateTimeField(help_text="When the units were reserved (or recorded).")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=40, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="notification", name="notifications_sms_usage_notification_tenant"
            ),
            models.CheckConstraint(
                condition=Q(segments__gte=1), name="notifications_sms_usage_segments"
            ),
            models.CheckConstraint(
                condition=Q(state__in=SmsUsageState.values),
                name="notifications_sms_usage_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(funded_by__in=SMS_FUNDING),
                name="notifications_sms_usage_funding_valid",
            ),
            models.CheckConstraint(
                condition=Q(allowance_segments__lte=F("segments") - F("prepaid_segments")),
                name="notifications_sms_usage_units_within_segments",
            ),
            models.CheckConstraint(
                condition=Q(accepted=True, state=SmsUsageState.ACCEPTED)
                | (Q(accepted=False) & ~Q(state=SmsUsageState.ACCEPTED)),
                name="notifications_sms_usage_accepted_matches_state",
            ),
            # One reservation per notification attempt: a duplicate delivery cannot reserve twice.
            models.UniqueConstraint(
                fields=["tenant", "notification", "attempt"],
                condition=Q(attempt__gt=0),
                name="notifications_sms_usage_one_per_attempt",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "period"], name="notifications_sms_period")]


# --- Stock alert state ----------------------------------------------------------------------------


class StockAlertState(TenantOwnedModel, TimestampedModel):
    """Last alert level per variant, so alerts fire on threshold transitions, not every check."""

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="+")
    level = models.CharField(max_length=8)
    changed_at = models.DateTimeField()
    sequence = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="variant", name="notifications_stock_variant_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "variant"], name="notifications_stock_state_unique"
            ),
            models.CheckConstraint(
                condition=Q(level__in=["ok", "low", "out"]),
                name="notifications_stock_state_level_valid",
            ),
        ]
