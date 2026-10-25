"""Inventory: locations, materialised stock levels and the append-only movement ledger (D-054).

Quantity semantics (per variant and location):
- ``on_hand``: units physically held;
- ``reserved``: units promised to open orders (reservations arrive with Phases 07/08);
- ``available`` = on_hand - reserved, computed by PostgreSQL (a generated column), never stored
  independently.

Every change is a ``StockMovement`` written in the same transaction as the level it changes, under
a row lock on that level, with the balances after the change. The database refuses negative
on-hand or reserved quantities and reservations beyond on-hand stock, and refuses UPDATE/DELETE of
movements (trigger). Corrections are compensating movements. ``services.reconcile`` proves the
levels equal the ledger.
"""

from decimal import Decimal
from typing import Any, ClassVar, NoReturn

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from commerce.catalog.models import ProductVariant
from commerce.core.models import TimestampedModel
from commerce.core.standards import validate_country_code
from commerce.identity.models import User
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.managers import TenantQuerySet, TenantScopedManager
from commerce.tenancy.models import TenantOwnedModel

LOCATION_CODE_PATTERN = r"^[A-Z0-9](?:[A-Z0-9_-]{0,30}[A-Z0-9])?$"
BATCH_CODE_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,62}[A-Za-z0-9])?$"
SERIAL_PATTERN = r"^[A-Z0-9](?:[A-Z0-9._/:-]{0,98}[A-Z0-9])?$"
ZERO = Decimal("0")


class StockLocation(TenantOwnedModel, TimestampedModel):
    """A place stock is held: warehouse, shop or storeroom. Exactly one default location is
    created on first use (``services.default_location``)."""

    code = models.CharField(max_length=32, validators=[RegexValidator(LOCATION_CODE_PATTERN)])
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    address_line = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, blank=True, validators=[validate_country_code])
    notes = models.CharField(max_length=1000, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "code"], name="inventory_location_code_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(is_default=True),
                name="inventory_location_one_default",
            ),
            models.CheckConstraint(
                condition=Q(code__regex=LOCATION_CODE_PATTERN),
                name="inventory_location_code_format",
            ),
            models.CheckConstraint(
                condition=Q(is_default=False) | Q(is_active=True),
                name="inventory_location_default_is_active",
            ),
            models.CheckConstraint(
                condition=Q(country="") | Q(country__regex=r"^[A-Z]{2}$"),
                name="inventory_location_country_format",
            ),
        ]

    def __str__(self) -> str:
        return self.code


class StockLevel(TenantOwnedModel):
    """Current quantities of one variant at one location, materialised from the ledger."""

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="+")
    on_hand = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO)
    reserved = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO)
    available = models.GeneratedField(
        expression=F("on_hand") - F("reserved"),
        output_field=models.DecimalField(max_digits=14, decimal_places=3),
        db_persist=True,
    )
    low_stock_threshold = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="variant", name="inventory_stocklevel_variant_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="location", name="inventory_stocklevel_location_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "variant", "location"], name="inventory_stocklevel_unique"
            ),
            models.CheckConstraint(
                condition=Q(on_hand__gte=0), name="inventory_stocklevel_on_hand_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(reserved__gte=0), name="inventory_stocklevel_reserved_gte_0"
            ),
            # No overselling: reservations never exceed the stock that exists.
            models.CheckConstraint(
                condition=Q(reserved__lte=F("on_hand")),
                name="inventory_stocklevel_reserved_within_on_hand",
            ),
            models.CheckConstraint(
                condition=Q(low_stock_threshold__isnull=True) | Q(low_stock_threshold__gte=0),
                name="inventory_stocklevel_threshold_gte_0",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "location"], name="inventory_level_location"),
        ]


class Batch(TenantOwnedModel, TimestampedModel):
    """A lot of one variant received together (grocery, perishables): its expiry drives
    first-expired-first-out allocation later (``services.batches_by_expiry``)."""

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=64, validators=[RegexValidator(BATCH_CODE_PATTERN)])
    manufactured_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    received_on = models.DateField()
    supplier_reference = models.CharField(max_length=100, blank=True)

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "batches"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="variant", name="inventory_batch_variant_same_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "variant", "code"], name="inventory_batch_code_unique"
            ),
            models.CheckConstraint(
                condition=Q(code__regex=BATCH_CODE_PATTERN), name="inventory_batch_code_format"
            ),
            models.CheckConstraint(
                condition=Q(manufactured_on__isnull=True)
                | Q(expires_on__isnull=True)
                | Q(manufactured_on__lte=F("expires_on")),
                name="inventory_batch_dates_ordered",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "variant", "expires_on"], name="inventory_batch_expiry"),
        ]

    def __str__(self) -> str:
        return self.code


class BatchLevel(TenantOwnedModel):
    """On-hand quantity of one batch at one location. For batch-tracked variants the batch
    levels at a location add up to that location's StockLevel.on_hand (``reconcile``)."""

    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="levels")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="+")
    on_hand = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="batch", name="inventory_batchlevel_batch_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="location", name="inventory_batchlevel_location_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "batch", "location"], name="inventory_batchlevel_unique"
            ),
            models.CheckConstraint(
                condition=Q(on_hand__gte=0), name="inventory_batchlevel_on_hand_gte_0"
            ),
        ]


class SerialStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    RESERVED = "reserved", "Reserved"
    SOLD = "sold", "Sold"
    DAMAGED = "damaged", "Damaged"
    LOST = "lost", "Lost"


IN_STOCK_SERIAL_STATUSES = (SerialStatus.AVAILABLE, SerialStatus.RESERVED)


class SerialNumber(TenantOwnedModel, TimestampedModel):
    """One individually identified unit (electronics: serial number or IMEI).

    A serial is unique in the store and is in at most one place: in stock (available or
    reserved) it has exactly one location; sold, damaged or lost it has none. For
    serial-tracked variants the in-stock serials at a location equal that location's on-hand.
    """

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    serial = models.CharField(max_length=100, validators=[RegexValidator(SERIAL_PATTERN)])
    status = models.CharField(max_length=16, choices=SerialStatus.choices)
    location = models.ForeignKey(
        StockLocation, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="variant", name="inventory_serial_variant_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="location", name="inventory_serial_location_same_tenant"
            ),
            models.UniqueConstraint(fields=["tenant", "serial"], name="inventory_serial_unique"),
            models.CheckConstraint(
                condition=Q(serial__regex=SERIAL_PATTERN), name="inventory_serial_format"
            ),
            models.CheckConstraint(
                condition=Q(status__in=SerialStatus.values), name="inventory_serial_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=IN_STOCK_SERIAL_STATUSES, location__isnull=False)
                | (~Q(status__in=IN_STOCK_SERIAL_STATUSES) & Q(location__isnull=True)),
                name="inventory_serial_location_matches_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "variant", "location", "status"], name="inventory_serial_stock"
            ),
        ]

    def __str__(self) -> str:
        return self.serial


class MovementType(models.TextChoices):
    RECEIPT = "receipt", "Stock received"
    ADJUSTMENT = "adjustment", "Stock count adjustment"
    DAMAGED = "damaged", "Damaged"
    LOST = "lost", "Lost"
    RETURNED = "returned", "Returned to stock"
    TRANSFER_OUT = "transfer_out", "Transferred out"
    TRANSFER_IN = "transfer_in", "Transferred in"
    RESERVED = "reserved", "Reserved"
    RELEASED = "released", "Reservation released"
    FULFILLED = "fulfilled", "Reserved stock dispatched"
    REVERSAL = "reversal", "Reversal"


# Signs every movement type must have, enforced by the database (pos: > 0, neg: < 0, zero: = 0).
_ON_HAND_POSITIVE = (MovementType.RECEIPT, MovementType.RETURNED, MovementType.TRANSFER_IN)
_ON_HAND_NEGATIVE = (MovementType.DAMAGED, MovementType.LOST, MovementType.TRANSFER_OUT)


class StockLedgerImmutableError(Exception):
    """The ledger is append-only: corrections are compensating movements."""


class StockMovementQuerySet(TenantQuerySet["StockMovement"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise StockLedgerImmutableError("Stock movements cannot be updated.")

    def delete(self) -> NoReturn:
        raise StockLedgerImmutableError("Stock movements cannot be deleted.")

    def bulk_update(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise StockLedgerImmutableError("Stock movements cannot be updated.")


class StockMovementManager(TenantScopedManager["StockMovement"]):
    _queryset_class = StockMovementQuerySet


class StockMovement(TenantOwnedModel):
    """One entry in the append-only inventory ledger.

    ``on_hand_delta``/``reserved_delta`` are signed changes; ``*_after`` are the level's balances
    once applied (the level row is locked, so they form a gap-free sequence per level).
    ``operation_id`` groups the entries of one operation (a transfer's two legs, one entry per
    serial); the first entry carries the caller's ``idempotency_key``, which is unique, so a
    retried request returns the original operation instead of applying it twice.
    """

    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    operation_id = models.UUIDField(editable=False)
    movement_type = models.CharField(max_length=16, choices=MovementType.choices)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="+")
    on_hand_delta = models.DecimalField(max_digits=14, decimal_places=3)
    reserved_delta = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO)
    on_hand_after = models.DecimalField(max_digits=14, decimal_places=3)
    reserved_after = models.DecimalField(max_digits=14, decimal_places=3)
    batch = models.ForeignKey(
        Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    serial = models.ForeignKey(
        SerialNumber, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reversal_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    idempotency_key = models.CharField(max_length=100, null=True, blank=True)  # noqa: DJ001
    reference_type = models.CharField(max_length=50, blank=True)
    reference_id = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    actor_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    actor_label = models.CharField(max_length=100, blank=True)

    objects: ClassVar[StockMovementManager] = StockMovementManager()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="variant", name="inventory_movement_variant_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="location", name="inventory_movement_location_same_tenant"
            ),
            TenantForeignKeyConstraint(field="batch", name="inventory_movement_batch_same_tenant"),
            TenantForeignKeyConstraint(
                field="serial", name="inventory_movement_serial_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="reversal_of", name="inventory_movement_reversal_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="actor_user", name="inventory_movement_actor_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="inventory_movement_idempotency_unique",
            ),
            # A movement is reversed at most once.
            models.UniqueConstraint(
                fields=["tenant", "reversal_of"],
                condition=Q(reversal_of__isnull=False),
                name="inventory_movement_reversed_once",
            ),
            models.CheckConstraint(
                condition=Q(movement_type__in=MovementType.values),
                name="inventory_movement_type_valid",
            ),
            models.CheckConstraint(
                condition=~Q(on_hand_delta=0) | ~Q(reserved_delta=0),
                name="inventory_movement_changes_something",
            ),
            models.CheckConstraint(
                condition=Q(on_hand_after__gte=0, reserved_after__gte=0)
                & Q(reserved_after__lte=F("on_hand_after")),
                name="inventory_movement_balances_valid",
            ),
            models.CheckConstraint(
                condition=~Q(movement_type__in=_ON_HAND_POSITIVE)
                | Q(on_hand_delta__gt=0, reserved_delta=0),
                name="inventory_movement_inbound_signs",
            ),
            models.CheckConstraint(
                condition=~Q(movement_type__in=_ON_HAND_NEGATIVE)
                | Q(on_hand_delta__lt=0, reserved_delta=0),
                name="inventory_movement_outbound_signs",
            ),
            models.CheckConstraint(
                condition=~Q(movement_type=MovementType.ADJUSTMENT) | Q(reserved_delta=0),
                name="inventory_movement_adjustment_signs",
            ),
            models.CheckConstraint(
                condition=~Q(movement_type=MovementType.RESERVED)
                | Q(on_hand_delta=0, reserved_delta__gt=0),
                name="inventory_movement_reserved_signs",
            ),
            models.CheckConstraint(
                condition=~Q(movement_type=MovementType.RELEASED)
                | Q(on_hand_delta=0, reserved_delta__lt=0),
                name="inventory_movement_released_signs",
            ),
            models.CheckConstraint(
                condition=~Q(movement_type=MovementType.FULFILLED)
                | Q(on_hand_delta__lt=0, reserved_delta=F("on_hand_delta")),
                name="inventory_movement_fulfilled_signs",
            ),
            models.CheckConstraint(
                condition=Q(movement_type=MovementType.REVERSAL, reversal_of__isnull=False)
                | (~Q(movement_type=MovementType.REVERSAL) & Q(reversal_of__isnull=True)),
                name="inventory_movement_reversal_link",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "variant", "location", "occurred_at"],
                name="inventory_movement_level",
            ),
            models.Index(fields=["tenant", "-occurred_at"], name="inventory_movement_time"),
            models.Index(fields=["tenant", "operation_id"], name="inventory_movement_operation"),
        ]

    def __str__(self) -> str:
        return f"{self.movement_type} {self.on_hand_delta}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise StockLedgerImmutableError("Stock movements cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise StockLedgerImmutableError("Stock movements cannot be deleted.")
