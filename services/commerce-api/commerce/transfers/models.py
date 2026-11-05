"""Data transfer records (Phase 15, D-105 to D-109). Every table is tenant-owned.

- ``SourceConnection``: a WooCommerce or Shopify store to migrate from. Credentials are
  encrypted and never returned; MateCart only ever *reads* from the source.
- ``DataTransferJob``: one import, export or migration with durable state, counters, a
  checkpoint for resumable extraction and the people who created and approved it.
- ``DataTransferFile``: an uploaded import file, a generated export or an error report, stored
  privately under the tenant's folder and served only through an authorised, expiring download.
- ``ImportItem``: one staged source record with its planned action, validation messages and
  outcome. Items are processed one by one and are idempotent: a processed item is never
  processed again, so a job can be resumed after a worker or broker failure.
- ``ExternalSourceMapping``: the durable link between a source record and the MateCart record it
  became or was matched to. A second run updates or skips instead of duplicating.
- ``HistoricalCustomer`` and ``HistoricalOrder``: read-only copies of a source store's customers
  and past orders with their provenance. They are deliberately *not* store accounts or orders:
  importing them never creates a login, payment, shipment, reservation, notification, coupon
  redemption or loyalty points, and they never enter sales reports.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q

from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.keys import tenant_storage_path
from commerce.tenancy.models import TenantOwnedModel


def _choice(field: str, choices: type[models.TextChoices], name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=Q(**{f"{field}__in": choices.values}), name=name)


class SourcePlatform(models.TextChoices):
    WOOCOMMERCE = "woocommerce", "WooCommerce"
    SHOPIFY = "shopify", "Shopify"


class SourceStatus(models.TextChoices):
    UNVERIFIED = "unverified", "Not verified"
    CONNECTED = "connected", "Connected"
    INVALID_CREDENTIALS = "invalid_credentials", "Invalid credentials"
    PERMISSION_MISSING = "permission_missing", "Missing read permission"
    UNREACHABLE = "unreachable", "Store unreachable"
    REFUSED = "refused", "Address refused"


class SourceConnection(TenantOwnedModel, TimestampedModel):
    platform = models.CharField(max_length=16, choices=SourcePlatform.choices)
    name = models.CharField(max_length=120, blank=True)
    # https://host (WooCommerce) or https://<shop>.myshopify.com; validated, never a private host.
    store_url = models.CharField(max_length=255)
    store_identity = models.CharField(max_length=253)
    credentials_encrypted = models.TextField(blank=True)
    credential_hint = models.CharField(max_length=12, blank=True)
    credential_kind = models.CharField(max_length=24, blank=True)
    status = models.CharField(
        max_length=24, choices=SourceStatus.choices, default=SourceStatus.UNVERIFIED
    )
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=48, blank=True)
    source_currency = models.CharField(max_length=3, blank=True)
    source_name = models.CharField(max_length=200, blank=True)
    # Readable object types found at verification: {"products": true, "customers": false, ...}.
    capabilities = models.JSONField(default=dict, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    is_simulated = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="created_by", name="transfers_source_creator_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "platform", "store_identity"], name="transfers_source_unique"
            ),
            _choice("platform", SourcePlatform, "transfers_source_platform_valid"),
            _choice("status", SourceStatus, "transfers_source_status_valid"),
        ]


class FilePurpose(models.TextChoices):
    UPLOAD = "upload", "Uploaded import file"
    EXPORT = "export", "Generated export"
    ERROR_REPORT = "error_report", "Error report"


class DataTransferFile(TenantOwnedModel, TimestampedModel):
    purpose = models.CharField(max_length=16, choices=FilePurpose.choices)
    file = models.FileField(upload_to=tenant_storage_path, max_length=255)
    display_name = models.CharField(max_length=120)
    content_type = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    expires_at = models.DateTimeField()
    downloads = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="created_by", name="transfers_file_creator_tenant"),
            _choice("purpose", FilePurpose, "transfers_file_purpose_valid"),
        ]


class JobKind(models.TextChoices):
    IMPORT = "import", "File import"
    EXPORT = "export", "Export"
    MIGRATION = "migration", "Store migration"


class JobSource(models.TextChoices):
    CSV = "csv", "CSV file"
    JSONL = "jsonl", "JSON Lines file"
    WOOCOMMERCE = "woocommerce", "WooCommerce"
    SHOPIFY = "shopify", "Shopify"
    STORE = "store", "This store"


class JobStatus(models.TextChoices):
    ANALYSING = "analysing", "Reading and validating"
    REVIEW = "review", "Ready for review"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Importing"
    COMPLETED = "completed", "Completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors", "Completed with errors"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


ACTIVE_JOB_STATUSES = (JobStatus.ANALYSING, JobStatus.QUEUED, JobStatus.RUNNING)


class ConflictStrategy(models.TextChoices):
    SKIP = "skip", "Create new records, skip existing ones"
    UPDATE = "update", "Create new records, update existing ones"
    MANUAL = "manual", "Create new records, decide each existing one"


class DataTransferJob(TenantOwnedModel, TimestampedModel):
    kind = models.CharField(max_length=12, choices=JobKind.choices)
    source = models.CharField(max_length=16, choices=JobSource.choices)
    status = models.CharField(max_length=24, choices=JobStatus.choices)
    # Export: products, categories, inventory, customers, orders. Import: catalog.
    entity = models.CharField(max_length=24)
    file_format = models.CharField(max_length=8, default="csv")
    scopes = ArrayField(models.CharField(max_length=24), default=list, blank=True)
    connection = models.ForeignKey(
        SourceConnection, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    conflict_strategy = models.CharField(
        max_length=8, choices=ConflictStrategy.choices, default=ConflictStrategy.SKIP
    )
    options = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    checkpoint = models.JSONField(default=dict, blank=True)
    total_items = models.PositiveIntegerField(default=0)
    processed_items = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=48, blank=True)
    sensitive = models.BooleanField(default=False)
    cancel_requested = models.BooleanField(default=False)
    input_file = models.ForeignKey(
        DataTransferFile, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    output_file = models.ForeignKey(
        DataTransferFile, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    report_file = models.ForeignKey(
        DataTransferFile, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    compensated_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="connection", name="transfers_job_source_tenant"),
            TenantForeignKeyConstraint(field="input_file", name="transfers_job_input_tenant"),
            TenantForeignKeyConstraint(field="output_file", name="transfers_job_output_tenant"),
            TenantForeignKeyConstraint(field="report_file", name="transfers_job_report_tenant"),
            TenantForeignKeyConstraint(field="created_by", name="transfers_job_creator_tenant"),
            TenantForeignKeyConstraint(field="approved_by", name="transfers_job_approver_tenant"),
            _choice("kind", JobKind, "transfers_job_kind_valid"),
            _choice("source", JobSource, "transfers_job_source_valid"),
            _choice("status", JobStatus, "transfers_job_status_valid"),
            _choice("conflict_strategy", ConflictStrategy, "transfers_job_strategy_valid"),
        ]
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="transfers_job_recent"),
            models.Index(fields=["tenant", "status"], name="transfers_job_status"),
        ]


class ItemAction(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    SKIP = "skip", "Skip"
    CONFLICT = "conflict", "Needs a decision"
    INVALID = "invalid", "Invalid"


class ItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DONE = "done", "Imported"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class ImportItem(TenantOwnedModel):
    job = models.ForeignKey(DataTransferJob, on_delete=models.CASCADE, related_name="+")
    position = models.PositiveIntegerField()
    object_type = models.CharField(max_length=16)
    external_id = models.CharField(max_length=128, blank=True)
    key = models.CharField(max_length=128, blank=True)
    title = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=12, choices=ItemAction.choices)
    status = models.CharField(max_length=12, choices=ItemStatus.choices, default=ItemStatus.PENDING)
    decision = models.CharField(max_length=8, blank=True)
    messages = models.JSONField(default=list, blank=True)
    # The normalised source record. Customer and order payloads hold personal data: they are
    # cleared once the job finishes (the historical record keeps what is needed).
    payload = models.JSONField(default=dict, blank=True)
    existing_id = models.UUIDField(null=True, blank=True)
    local_id = models.UUIDField(null=True, blank=True)
    created_local = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="job", name="transfers_item_job_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "job", "position"], name="transfers_item_position_unique"
            ),
            _choice("action", ItemAction, "transfers_item_action_valid"),
            _choice("status", ItemStatus, "transfers_item_status_valid"),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "job", "status", "position"], name="transfers_item_queue"
            ),
            models.Index(fields=["tenant", "job", "action"], name="transfers_item_action"),
        ]


class ExternalSourceMapping(TenantOwnedModel, TimestampedModel):
    platform = models.CharField(max_length=16)
    source_identity = models.CharField(max_length=253)
    object_type = models.CharField(max_length=16)
    external_id = models.CharField(max_length=128)
    local_id = models.UUIDField()
    # True when the job created the local record; False when it matched an existing one.
    created_by_job = models.BooleanField(default=False)
    first_job = models.ForeignKey(DataTransferJob, on_delete=models.PROTECT, related_name="+")
    last_job = models.ForeignKey(DataTransferJob, on_delete=models.PROTECT, related_name="+")

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="first_job", name="transfers_map_first_job_tenant"),
            TenantForeignKeyConstraint(field="last_job", name="transfers_map_last_job_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "platform", "source_identity", "object_type", "external_id"],
                name="transfers_map_external_unique",
            ),
            # One source record per local record: a forged or reused external id cannot be
            # pointed at an unrelated product that another source record already owns.
            models.UniqueConstraint(
                fields=["tenant", "platform", "source_identity", "object_type", "local_id"],
                name="transfers_map_local_unique",
            ),
        ]


class HistoricalCustomer(TenantOwnedModel, TimestampedModel):
    platform = models.CharField(max_length=16)
    source_identity = models.CharField(max_length=253)
    external_id = models.CharField(max_length=128)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.CharField(max_length=254, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, blank=True)
    # As reported by the source. Never treated as MateCart marketing consent (D-090).
    source_accepts_marketing = models.BooleanField(null=True, blank=True)
    source_orders_count = models.PositiveIntegerField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    job = models.ForeignKey(DataTransferJob, on_delete=models.PROTECT, related_name="+")

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="job", name="transfers_hcustomer_job_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "platform", "source_identity", "external_id"],
                name="transfers_hcustomer_unique",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "email"], name="transfers_hcustomer_email")]


class HistoricalOrder(TenantOwnedModel, TimestampedModel):
    platform = models.CharField(max_length=16)
    source_identity = models.CharField(max_length=253)
    external_id = models.CharField(max_length=128)
    number = models.CharField(max_length=64)
    placed_at = models.DateTimeField(null=True, blank=True)
    # Source values, kept as the source reported them (not MateCart order states).
    source_status = models.CharField(max_length=64, blank=True)
    source_financial_status = models.CharField(max_length=64, blank=True)
    source_fulfillment_status = models.CharField(max_length=64, blank=True)
    currency = models.CharField(max_length=3)
    subtotal = models.DecimalField(max_digits=19, decimal_places=4, null=True, blank=True)
    shipping_total = models.DecimalField(max_digits=19, decimal_places=4, null=True, blank=True)
    tax_total = models.DecimalField(max_digits=19, decimal_places=4, null=True, blank=True)
    discount_total = models.DecimalField(max_digits=19, decimal_places=4, null=True, blank=True)
    grand_total = models.DecimalField(max_digits=19, decimal_places=4)
    # [{"name", "sku", "quantity", "unit_price", "total"}] as recorded by the source.
    lines = models.JSONField(default=list, blank=True)
    customer = models.ForeignKey(
        HistoricalCustomer, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    customer_name = models.CharField(max_length=200, blank=True)
    job = models.ForeignKey(DataTransferJob, on_delete=models.PROTECT, related_name="+")

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="customer", name="transfers_horder_customer_tenant"),
            TenantForeignKeyConstraint(field="job", name="transfers_horder_job_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "platform", "source_identity", "external_id"],
                name="transfers_horder_unique",
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"), name="transfers_horder_currency"
            ),
        ]
        indexes = [models.Index(fields=["tenant", "-placed_at"], name="transfers_horder_recent")]
