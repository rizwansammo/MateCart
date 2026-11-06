from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from control.core.models import TimestampedModel, UUIDModel


class ClientStatus(models.TextChoices):
    PROSPECT = "prospect", "Prospect"
    ONBOARDING = "onboarding", "Onboarding"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On hold"
    ARCHIVED = "archived", "Archived"


class Client(UUIDModel, TimestampedModel):
    display_name = models.CharField(max_length=160)
    legal_name = models.CharField(max_length=200, blank=True)
    client_type = models.CharField(max_length=32, default="business")
    primary_contact_name = models.CharField(max_length=160, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=2)
    time_zone = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True, max_length=1000)
    internal_reference = models.CharField(max_length=80, blank=True, unique=True, null=True)
    account_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="clients",
    )
    status = models.CharField(
        max_length=20, choices=ClientStatus.choices, default=ClientStatus.PROSPECT
    )
    onboarding_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, max_length=4000)

    class Meta:
        ordering = ["display_name", "id"]
        indexes = [models.Index(fields=["status", "display_name"], name="platform_client_search")]


class ClientContact(UUIDModel, TimestampedModel):
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="contacts")
    name = models.CharField(max_length=160)
    role = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    is_primary = models.BooleanField(default=False)
    notes = models.CharField(max_length=500, blank=True)


class StoreStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REGISTERED = "registered", "Registered"
    READY = "ready_for_deployment", "Ready for deployment"
    PENDING = "deployment_pending", "Deployment pending"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Administratively suspended"
    ARCHIVED = "archived", "Archived"


class StoreRegistration(UUIDModel, TimestampedModel):
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="stores")
    name = models.CharField(max_length=160)
    handle = models.SlugField(max_length=80, unique=True)
    preset = models.CharField(max_length=24)
    country = models.CharField(max_length=2)
    default_currency = models.CharField(max_length=3)
    time_zone = models.CharField(max_length=64)
    default_language = models.CharField(max_length=12)
    supported_languages = models.JSONField(default=list)
    desired_theme = models.CharField(max_length=80)
    desired_modules = models.JSONField(default=list)
    applied_theme = models.CharField(max_length=80, blank=True)
    applied_modules = models.JSONField(default=list)
    desired_revision = models.PositiveIntegerField(default=1)
    applied_revision = models.PositiveIntegerField(default=0)
    applied_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=StoreStatus.choices, default=StoreStatus.DRAFT)
    onboarding_status = models.CharField(max_length=32, default="draft")
    environment_designation = models.CharField(max_length=24, default="production")

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(applied_revision__lte=models.F("desired_revision")),
                name="platform_store_applied_not_ahead",
            )
        ]


class StoreConfigurationRevision(UUIDModel):
    store = models.ForeignKey(StoreRegistration, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    preset = models.CharField(max_length=24)
    theme = models.CharField(max_length=80)
    modules = models.JSONField(default=list)
    languages = models.JSONField(default=list)
    status = models.CharField(max_length=24, default="pending_application")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    evidence = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-revision"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "revision"], name="platform_store_revision_unique"
            )
        ]


class Server(UUIDModel, TimestampedModel):
    name = models.CharField(max_length=160)
    ownership = models.CharField(
        max_length=24, choices=[("netamate", "NetaMate"), ("client", "Client")]
    )
    owner_client = models.ForeignKey(
        Client, null=True, blank=True, on_delete=models.PROTECT, related_name="servers"
    )
    provider = models.CharField(max_length=100, blank=True)
    hostname = models.CharField(max_length=253, blank=True)
    public_ip = models.GenericIPAddressField(null=True, blank=True)
    region = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, blank=True)
    operating_system = models.CharField(max_length=120, blank=True)
    cpu_cores = models.PositiveSmallIntegerField(null=True, blank=True)
    ram_mb = models.PositiveIntegerField(null=True, blank=True)
    disk_gb = models.PositiveIntegerField(null=True, blank=True)
    environment = models.CharField(max_length=24, default="production")
    asset_reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=24, default="registered")
    notes = models.TextField(blank=True, max_length=2000)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ownership="netamate", owner_client__isnull=True)
                | Q(ownership="client", owner_client__isnull=False),
                name="platform_server_owner_valid",
            )
        ]


class Deployment(UUIDModel, TimestampedModel):
    store = models.ForeignKey(
        StoreRegistration, on_delete=models.PROTECT, related_name="deployments"
    )
    server = models.ForeignKey(Server, on_delete=models.PROTECT, related_name="deployments")
    environment = models.CharField(max_length=24)
    reference = models.CharField(max_length=100)
    desired_version = models.CharField(max_length=80, blank=True)
    observed_version = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=32, default="awaiting_deployment_integration")
    public_origin = models.URLField(blank=True)
    internal_reference = models.CharField(max_length=100, blank=True)
    last_observed_at = models.DateTimeField(null=True, blank=True)
    last_healthy_at = models.DateTimeField(null=True, blank=True)
    configuration_revision = models.PositiveIntegerField(default=0)
    # Phase 17 (D-120): the last version verified healthy after an update, which is the only
    # automatic rollback target. Empty until a verified installation exists.
    rollback_version = models.CharField(max_length=80, blank=True)
    verified_version = models.CharField(max_length=80, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["store__name", "environment"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "environment"], name="platform_deploy_env_unique"
            )
        ]


class DomainRecord(UUIDModel, TimestampedModel):
    store = models.ForeignKey(StoreRegistration, on_delete=models.PROTECT, related_name="domains")
    deployment = models.ForeignKey(
        Deployment, null=True, blank=True, on_delete=models.PROTECT, related_name="domains"
    )
    domain = models.CharField(max_length=253, unique=True)
    purpose = models.CharField(max_length=24, default="primary")
    configured = models.BooleanField(default=False)
    dns_state = models.CharField(max_length=24, default="unknown")
    tls_state = models.CharField(max_length=24, default="unknown")
    certificate_expires_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    # Phase 17 (D-122): ownership proof is a TXT record _matecart-verify.<domain> holding this
    # value. Only verified domains are ever sent to an Agent for proxy/TLS configuration.
    verification_token = models.CharField(max_length=64, blank=True)
    verification_state = models.CharField(max_length=24, default="pending")
    verification_checked_at = models.DateTimeField(null=True, blank=True)
    verification_detail = models.CharField(max_length=200, blank=True)
    tls_mode = models.CharField(max_length=16, default="acme")
    renewal_state = models.CharField(max_length=24, default="unknown")


class OperationalRequest(UUIDModel, TimestampedModel):
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=4000)
    status = models.CharField(max_length=24, default="draft")
    client = models.ForeignKey(
        Client, null=True, blank=True, on_delete=models.PROTECT, related_name="operations"
    )
    store = models.ForeignKey(
        StoreRegistration,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="operations",
    )
    deployment = models.ForeignKey(
        Deployment, null=True, blank=True, on_delete=models.PROTECT, related_name="operations"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requests_created"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="requests_assigned",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="requests_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    completed_evidence = models.CharField(max_length=500, blank=True)
    # Phase 17: validated parameters of an executable request (release version, domain id).
    parameters = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MonitoringCredential(UUIDModel, TimestampedModel):
    deployment = models.ForeignKey(
        Deployment, on_delete=models.PROTECT, related_name="monitoring_credentials"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=100)
    last_four = models.CharField(max_length=4)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class MonitoringObservation(UUIDModel):
    deployment = models.ForeignKey(
        Deployment, on_delete=models.PROTECT, related_name="observations"
    )
    # Exactly one source: a deployment monitoring credential (Phase 16) or the server's
    # enrolled Deployment Agent (Phase 17).
    credential = models.ForeignKey(
        MonitoringCredential,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    agent = models.ForeignKey(
        "DeploymentAgent",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    worker_status = models.CharField(max_length=24, blank=True)
    database_status = models.CharField(max_length=24, blank=True)
    configuration_revision = models.PositiveIntegerField(null=True, blank=True)
    report_id = models.UUIDField()
    observed_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=24, default="reported")
    store_status = models.CharField(max_length=24)
    api_status = models.CharField(max_length=24)
    response_ms = models.PositiveIntegerField(null=True, blank=True)
    cpu_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    ram_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    disk_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    visitor_count_24h = models.PositiveIntegerField(null=True, blank=True)
    application_version = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-observed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["deployment", "report_id"], name="platform_observation_replay"
            ),
            models.CheckConstraint(
                condition=Q(credential__isnull=False, agent__isnull=True)
                | Q(credential__isnull=True, agent__isnull=False),
                name="platform_observation_one_source",
            ),
        ]
        indexes = [
            models.Index(fields=["deployment", "-observed_at"], name="platform_observation_recent")
        ]


class BackupObservation(UUIDModel):
    deployment = models.ForeignKey(Deployment, on_delete=models.PROTECT, related_name="backups")
    backup_type = models.CharField(max_length=32)
    observed_at = models.DateTimeField()
    backup_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24)
    source = models.CharField(max_length=24, default="reported")
    recovery_tested_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    # Phase 17 (D-123): Agent-reported backups carry their idempotency id and integrity metadata.
    # The artifact itself never leaves the Server; no destination credentials are stored here.
    report_id = models.UUIDField(null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    artifact_name = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["deployment", "report_id"],
                condition=Q(report_id__isnull=False),
                name="platform_backup_report_unique",
            )
        ]


class OperationalAlert(UUIDModel, TimestampedModel):
    deployment = models.ForeignKey(
        Deployment, null=True, blank=True, on_delete=models.PROTECT, related_name="alerts"
    )
    server = models.ForeignKey(
        Server, null=True, blank=True, on_delete=models.PROTECT, related_name="alerts"
    )
    category = models.CharField(max_length=40)
    severity = models.CharField(max_length=16)
    status = models.CharField(max_length=20, default="open")
    deduplication_key = models.CharField(max_length=160, unique=True)
    title = models.CharField(max_length=200)
    details = models.JSONField(default=dict)
    source_observation = models.ForeignKey(
        MonitoringObservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="alerts",
    )
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="alerts_acknowledged",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="alerts_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    silenced_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ConsoleNotification(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="console_notifications"
    )
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500)
    link = models.CharField(max_length=300, blank=True)
    deduplication_key = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "deduplication_key"], name="platform_notification_dedupe"
            )
        ]


class ConsoleAuditEvent(UUIDModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.PROTECT,
        related_name="console_audit_events",
    )
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=80)
    resource_id = models.UUIDField(null=True)
    request_id = models.CharField(max_length=64, blank=True)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Console audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Console audit events are append-only.")


# ---------------------------------------------------------------- Phase 17: deployment automation


class AgentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REVOKED = "revoked", "Revoked"


class DeploymentAgent(UUIDModel, TimestampedModel):
    """One host Agent per Server (D-119). It acts only on Deployments registered to its Server."""

    server = models.ForeignKey(Server, on_delete=models.PROTECT, related_name="agents")
    status = models.CharField(
        max_length=16, choices=AgentStatus.choices, default=AgentStatus.ACTIVE
    )
    key_id = models.CharField(max_length=32, unique=True)
    # HMAC secrets are Fernet-encrypted (control.core.security); never returned after issue.
    secret_encrypted = models.TextField()
    pending_secret_encrypted = models.TextField(blank=True)
    credential_expires_at = models.DateTimeField()
    agent_version = models.CharField(max_length=40, blank=True)
    host_facts = models.JSONField(default=dict, blank=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        ordering = ["-enrolled_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["server"],
                condition=Q(status="active"),
                name="platform_agent_one_active_per_server",
            )
        ]


class AgentEnrollment(UUIDModel):
    """Single-use, short-lived enrollment token for one Server (stored as SHA-256 only)."""

    server = models.ForeignKey(Server, on_delete=models.PROTECT, related_name="enrollments")
    token_hash = models.CharField(max_length=64, unique=True)
    last_four = models.CharField(max_length=4)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    agent = models.ForeignKey(
        DeploymentAgent, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]


class ReleaseRecord(UUIDModel, TimestampedModel):
    """A validated release manifest (D-121). Images are referenced only by immutable digest."""

    version = models.CharField(max_length=80, unique=True)
    manifest = models.JSONField()
    manifest_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="available")
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    notes = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]


class CommandKind(models.TextChoices):
    DEPLOY_RELEASE = "deploy_release", "Install release"
    UPDATE_RELEASE = "update_release", "Update release"
    ROLLBACK_RELEASE = "rollback_release", "Roll back release"
    APPLY_CONFIGURATION = "apply_configuration", "Apply store configuration"
    CONFIGURE_DOMAINS = "configure_domains", "Configure domains and TLS"
    RUN_BACKUP = "run_backup", "Run backup"


class CommandStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    DELIVERED = "delivered", "Delivered"
    RUNNING = "running", "Running"
    AWAITING_VERIFICATION = "awaiting_verification", "Awaiting verification"
    SUCCEEDED = "succeeded", "Succeeded (verified)"
    FAILED = "failed", "Failed"
    VERIFICATION_FAILED = "verification_failed", "Verification failed"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


ACTIVE_COMMAND_STATUSES = (
    CommandStatus.QUEUED,
    CommandStatus.DELIVERED,
    CommandStatus.RUNNING,
    CommandStatus.AWAITING_VERIFICATION,
)


class AgentCommand(UUIDModel, TimestampedModel):
    """A scoped, allowlisted deployment operation derived from one approved request (D-120)."""

    agent = models.ForeignKey(DeploymentAgent, on_delete=models.PROTECT, related_name="commands")
    deployment = models.ForeignKey(Deployment, on_delete=models.PROTECT, related_name="commands")
    operation = models.OneToOneField(
        OperationalRequest, on_delete=models.PROTECT, related_name="command"
    )
    kind = models.CharField(max_length=32, choices=CommandKind.choices)
    payload = models.JSONField()
    payload_sha256 = models.CharField(max_length=64)
    status = models.CharField(
        max_length=24, choices=CommandStatus.choices, default=CommandStatus.QUEUED
    )
    target_version = models.CharField(max_length=80, blank=True)
    previous_version = models.CharField(max_length=80, blank=True)
    target_revision = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_count = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_event_sequence = models.PositiveIntegerField(default=0)
    progress = models.JSONField(default=list, blank=True)
    result = models.JSONField(default=dict, blank=True)
    failure_category = models.CharField(max_length=40, blank=True)
    failure_detail = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["deployment"],
                condition=Q(status__in=["queued", "delivered", "running", "awaiting_verification"]),
                name="platform_command_one_active_per_deployment",
            )
        ]
        indexes = [models.Index(fields=["agent", "status"], name="platform_command_queue")]


class BackupPolicy(UUIDModel, TimestampedModel):
    """Desired backup schedule for one Deployment, executed locally by the Agent (D-123)."""

    deployment = models.OneToOneField(
        Deployment, on_delete=models.PROTECT, related_name="backup_policy"
    )
    enabled = models.BooleanField(default=True)
    schedule_hour_utc = models.PositiveSmallIntegerField(default=2)
    schedule_minute = models.PositiveSmallIntegerField(default=30)
    retention_days = models.PositiveSmallIntegerField(default=14)
    include_media = models.BooleanField(default=True)
    destination_kind = models.CharField(max_length=24, default="local")
    destination_label = models.CharField(max_length=100, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(schedule_hour_utc__lte=23) & Q(schedule_minute__lte=59),
                name="platform_backup_schedule_valid",
            ),
            models.CheckConstraint(
                condition=Q(retention_days__gte=1) & Q(retention_days__lte=365),
                name="platform_backup_retention_valid",
            ),
        ]
