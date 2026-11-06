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
    credential = models.ForeignKey(
        MonitoringCredential, on_delete=models.PROTECT, related_name="observations"
    )
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
            )
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
