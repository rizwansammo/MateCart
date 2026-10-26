"""Inventory operations (D-054). Every stock change goes through this module.

Concurrency: an operation runs in one transaction and locks, in this order, the variant row
(FOR NO KEY UPDATE), its stock levels (ordered by primary key), batch levels, then serials. Levels
are only changed while locked, so balances are never computed from stale reads, and the
database's CHECK constraints refuse negative stock or reservations beyond on-hand stock even if a
code path forgot a rule.

Idempotency: callers may pass an ``idempotency_key``. The first movement of the operation stores
it (unique per store) with the fingerprint of the request's canonical identity (``Intent``); a
retry with the same key and the same request returns the original operation, a request that
differs in anything that changes stock is refused with ``idempotency_conflict`` (D-059).

Sellable stock: expired batches stay in on-hand stock (expiry changes sellability, not the
ledger), but they are never offered or reserved. ``sellable_quantities`` is the one definition
used by the public catalogue, ``is_purchasable`` and ``reserve`` (D-060).

Policy: stock never goes negative (no backorders in Phase 04). Reservations (``reserve``,
``release``, ``fulfil``) are the extension points Phases 07/08 will call from checkout and
orders; they are complete and tested here but not exposed through the API.
"""

import hashlib
import json
import re
import uuid
import zoneinfo
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.catalog.models import (
    GroceryDetails,
    InventoryUnit,
    Product,
    ProductVariant,
    TrackingMode,
    VariantKind,
)
from commerce.core.domain import ConflictError, DomainError, save_unique, validated
from commerce.core.ids import uuid7
from commerce.identity.models import User
from commerce.stores.models import StoreSettings

from .models import (
    BATCH_CODE_PATTERN,
    IN_STOCK_SERIAL_STATUSES,
    SERIAL_PATTERN,
    ZERO,
    Batch,
    BatchLevel,
    MovementType,
    SerialNumber,
    SerialStatus,
    StockLevel,
    StockLocation,
    StockMovement,
)

MAX_QUANTITY = Decimal("1000000000")
MAX_SERIALS_PER_OPERATION = 500
DEFAULT_LOCATION_CODE = "MAIN"
_IDEMPOTENCY_CONSTRAINT = "inventory_movement_idempotency_unique"

# Manual adjustment types and the sign their quantity must have.
ADJUSTMENT_TYPES: dict[str, int] = {
    MovementType.ADJUSTMENT: 0,  # either sign: a stock-count correction
    MovementType.DAMAGED: -1,
    MovementType.LOST: -1,
    MovementType.RETURNED: 1,
}
REVERSIBLE_TYPES = frozenset(
    {
        MovementType.RECEIPT,
        MovementType.ADJUSTMENT,
        MovementType.DAMAGED,
        MovementType.LOST,
        MovementType.RETURNED,
        MovementType.TRANSFER_OUT,
        MovementType.TRANSFER_IN,
    }
)


class InsufficientStock(ConflictError):
    default_detail = "There isn't enough stock for this change."
    default_code = "insufficient_stock"


class NotTracked(DomainError):
    default_detail = "This variant's stock is not tracked."
    default_code = "inventory_not_tracked"


@dataclass(frozen=True)
class StockRequest:
    """Who asked for a stock change and why. Free text stays in the ledger, not in audit."""

    reason: str = ""
    reference_type: str = ""
    reference_id: str = ""
    idempotency_key: str | None = None
    actor: User | None = None
    actor_label: str = ""


@dataclass(frozen=True)
class BatchInput:
    code: str
    expires_on: date | None = None
    manufactured_on: date | None = None
    supplier_reference: str = ""


@dataclass
class Operation:
    operation_id: uuid.UUID
    movements: list[StockMovement] = field(default_factory=list)
    replayed: bool = False
    fingerprint: str | None = None


QUANTUM = Decimal("0.001")
FINGERPRINT_VERSION = 1


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else f"{value.quantize(QUANTUM):f}"


@dataclass(frozen=True)
class Intent:
    """What a stock request does: the identity an idempotency key is bound to (D-059).

    Included: the operation (first movement type), variant, locations, signed quantity, batch
    code and dates, the set of serials, the reversed operation and the business reference
    (``reference_type``/``reference_id``). Excluded: the free-text ``reason``, the actor and a
    batch's supplier reference, which describe a change without changing its effect.
    """

    movement_type: str
    variant: Any
    location: Any
    quantity: Decimal | None = None
    to_location: Any = None
    batch_code: str | None = None
    batch_expires_on: date | None = None
    batch_manufactured_on: date | None = None
    serials: tuple[str, ...] = ()
    reverses: Any = None
    reference: tuple[str, str] = ("", "")

    def canonical(self) -> dict[str, Any]:
        def text(value: Any) -> str | None:
            return None if value is None else str(value)

        return {
            "v": FINGERPRINT_VERSION,
            "type": self.movement_type,
            "variant": text(self.variant),
            "location": text(self.location),
            "to_location": text(self.to_location),
            "quantity": _decimal_text(self.quantity),
            "batch": None
            if self.batch_code is None
            else [
                self.batch_code,
                text(self.batch_expires_on and self.batch_expires_on.isoformat()),
                text(self.batch_manufactured_on and self.batch_manufactured_on.isoformat()),
            ],
            "serials": sorted(set(self.serials)),
            "reverses": text(self.reverses),
            "reference": list(self.reference),
        }

    def fingerprint(self) -> str:
        """SHA-256 of the canonical JSON: stable across processes, no floats, no reprs."""
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _reference(request: StockRequest) -> tuple[str, str]:
    return (request.reference_type[:50], request.reference_id[:100])


def _batch_identity(batch: BatchInput | None) -> dict[str, Any]:
    if batch is None:
        return {}
    return {
        "batch_code": batch.code.strip(),
        "batch_expires_on": batch.expires_on,
        "batch_manufactured_on": batch.manufactured_on,
    }


# --- Helpers --------------------------------------------------------------------------------------


def is_tracked(variant: ProductVariant) -> bool:
    product = Product.objects.only("track_inventory").get(pk=variant.product_id)
    return product.track_inventory and variant.kind == VariantKind.PHYSICAL


def store_today() -> date:
    """Today's date in the store's time zone (expiry dates are calendar dates of the store)."""
    name = StoreSettings.objects.values_list("timezone", flat=True).first() or "UTC"
    return timezone.now().astimezone(zoneinfo.ZoneInfo(name)).date()


def clean_quantity(variant: ProductVariant, value: Any, *, signed: bool = False) -> Decimal:
    """A non-zero quantity in the variant's unit: whole numbers for ``each``, at most three
    decimal places otherwise. Negative only when ``signed``."""
    try:
        quantity = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationError({"quantity": ["Enter a number."]}) from exc
    if not quantity.is_finite() or quantity == 0 or abs(quantity) >= MAX_QUANTITY:
        raise ValidationError({"quantity": ["Enter a non-zero quantity."]})
    if quantity < 0 and not signed:
        raise ValidationError({"quantity": ["Enter a positive quantity."]})
    places = Decimal(1) if variant.inventory_unit == InventoryUnit.EACH else Decimal("0.001")
    if quantity != quantity.quantize(places):
        if variant.inventory_unit == InventoryUnit.EACH:
            raise ValidationError({"quantity": ["This variant is counted in whole units."]})
        raise ValidationError({"quantity": ["Use at most three decimal places."]})
    return quantity


_SERIAL = re.compile(SERIAL_PATTERN)
_BATCH_CODE = re.compile(BATCH_CODE_PATTERN)


def _clean_serials(raw: Sequence[str]) -> list[str]:
    serials = [value.strip().upper() for value in raw]
    if len(serials) > MAX_SERIALS_PER_OPERATION:
        raise ValidationError(
            {"serials": [f"Record at most {MAX_SERIALS_PER_OPERATION} serials at a time."]}
        )
    if len(set(serials)) != len(serials):
        raise ValidationError({"serials": ["Each serial number can appear only once."]})
    for serial in serials:
        if not _SERIAL.match(serial):
            raise ValidationError({"serials": [f"{serial or '(empty)'} is not a valid serial."]})
    return serials


def default_location() -> StockLocation:
    """The store's default stock location, created as MAIN on first use."""
    existing = StockLocation.objects.filter(is_default=True).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return StockLocation.objects.create(
                code=DEFAULT_LOCATION_CODE, name="Main location", is_default=True
            )
    except IntegrityError:
        created = StockLocation.objects.filter(is_default=True).first()
        if created is None:
            raise
        return created


def save_location(
    *, location: StockLocation | None, data: dict[str, Any], actor: User | None
) -> StockLocation:
    fields = ["code", "name", "is_active", "address_line", "city", "country", "notes"]
    with transaction.atomic():
        creating = location is None
        target = (
            StockLocation()
            if location is None
            else StockLocation.objects.select_for_update().get(pk=location.pk)
        )
        before = {name: getattr(target, name) for name in [*fields, "is_default"]}
        for name in fields:
            if name in data:
                setattr(target, name, data[name].upper() if name == "code" else data[name])
        if not creating and before["is_active"] and not target.is_active:
            if target.is_default:
                raise ConflictError("The default location stays active.", "default_location")
            held = StockLevel.objects.filter(location=target).filter(
                Q(on_hand__gt=0) | Q(reserved__gt=0)
            )
            if held.exists():
                raise ConflictError(
                    "Move or remove this location's stock before deactivating it.",
                    "location_has_stock",
                )
        if data.get("is_default") and not target.is_default:
            if not target.is_active:
                raise ValidationError({"is_default": ["Activate the location first."]})
            StockLocation.objects.filter(is_default=True).update(is_default=False)
            target.is_default = True
        validated(target, exclude=["tenant"])
        save_unique(
            target,
            {"inventory_location_code_unique": ("code", "Another location uses this code.")},
        )
        after = {name: getattr(target, name) for name in before}
        record_audit_event(
            action="inventory.location.created" if creating else "inventory.location.updated",
            resource=target,
            actor=actor,
            changes=field_changes(
                before, after, fields=["code", "name", "is_active", "is_default"]
            ),
        )
    return target


def _lock_variant(variant: ProductVariant) -> tuple[ProductVariant, Product]:
    """Lock the variant (FOR NO KEY UPDATE: foreign-key checks elsewhere are not blocked) and
    re-read its tracking configuration under the lock."""
    locked = ProductVariant.objects.select_for_update(no_key=True).get(pk=variant.pk)
    product = Product.objects.get(pk=locked.product_id)
    if not product.track_inventory or locked.kind != VariantKind.PHYSICAL:
        raise NotTracked()
    return locked, product


def _lock_levels(
    variant: ProductVariant, locations: Sequence[StockLocation]
) -> dict[Any, StockLevel]:
    """Create missing levels, then lock them all in primary-key order (deadlock-free)."""
    StockLevel.objects.bulk_create(
        [StockLevel(variant=variant, location=location) for location in locations],
        ignore_conflicts=True,
    )
    rows = (
        StockLevel.objects.select_for_update()
        .filter(variant=variant, location__in=[location.pk for location in locations])
        .order_by("pk")
    )
    return {row.location_id: row for row in rows}


def _lock_batch_level(batch: Batch, location: StockLocation) -> BatchLevel:
    BatchLevel.objects.bulk_create(
        [BatchLevel(batch=batch, location=location)], ignore_conflicts=True
    )
    return BatchLevel.objects.select_for_update().get(batch=batch, location=location)


def _record(
    level: StockLevel,
    *,
    operation: Operation,
    movement_type: str,
    on_hand_delta: Decimal,
    reserved_delta: Decimal = ZERO,
    request: StockRequest,
    first: bool,
    batch: Batch | None = None,
    serial: SerialNumber | None = None,
    reversal_of: StockMovement | None = None,
) -> StockMovement:
    """Apply deltas to a locked level and append the matching ledger entry."""
    on_hand = level.on_hand + on_hand_delta
    reserved = level.reserved + reserved_delta
    if on_hand < 0 or reserved > on_hand:
        raise InsufficientStock()
    if reserved < 0:
        raise ConflictError("Less stock is reserved than that.", "insufficient_reservation")
    if batch is not None and on_hand_delta < 0 and reserved > 0:
        # Reservations are only ever backed by unexpired batch stock (D-060): removing such stock
        # must leave enough for them, as for plain stock. Expired stock is promised to no one.
        today = store_today()
        if batch.expires_on is None or batch.expires_on >= today:
            key = (level.variant_id, level.location_id)
            eligible = _eligible_batch_stock([level.variant_id], today).get(key, ZERO)
            if eligible + on_hand_delta < reserved:
                raise InsufficientStock("That unexpired stock is reserved.")
    level.on_hand, level.reserved = on_hand, reserved
    level.save(update_fields=["on_hand", "reserved", "updated_at"])
    if batch is not None:
        batch_level = _lock_batch_level(batch, level.location)
        batch_level.on_hand += on_hand_delta
        if batch_level.on_hand < 0:
            raise InsufficientStock(f"Batch {batch.code} doesn't have enough stock here.")
        batch_level.save(update_fields=["on_hand"])
    movement = StockMovement(
        operation_id=operation.operation_id,
        movement_type=movement_type,
        variant_id=level.variant_id,
        location_id=level.location_id,
        on_hand_delta=on_hand_delta,
        reserved_delta=reserved_delta,
        on_hand_after=on_hand,
        reserved_after=reserved,
        batch=batch,
        serial=serial,
        reversal_of=reversal_of,
        idempotency_key=request.idempotency_key if first else None,
        request_fingerprint=operation.fingerprint if first else None,
        reference_type=request.reference_type[:50],
        reference_id=request.reference_id[:100],
        reason=request.reason[:500],
        actor_user=request.actor,
        actor_label=request.actor_label[:100],
    )
    save_unique(movement, {})
    operation.movements.append(movement)
    return movement


def _recorded_intent(movements: Sequence[StockMovement]) -> Intent:
    """The identity of an operation recorded before fingerprints existed, rebuilt from its ledger
    entries. Batch dates are taken from the batch itself; the manufacture date is not compared."""
    first = movements[0]
    kind = first.movement_type
    if kind in (MovementType.TRANSFER_OUT, MovementType.TRANSFER_IN):
        inbound = [m for m in movements if m.movement_type == MovementType.TRANSFER_IN]
        quantity: Decimal | None = sum((m.on_hand_delta for m in inbound), ZERO)
        to_location = inbound[0].location_id if inbound else None
    elif kind in (MovementType.RESERVED, MovementType.RELEASED):
        quantity, to_location = abs(sum((m.reserved_delta for m in movements), ZERO)), None
    elif kind == MovementType.FULFILLED:
        quantity, to_location = -sum((m.on_hand_delta for m in movements), ZERO), None
    elif kind == MovementType.REVERSAL:
        quantity, to_location = None, None
    else:
        quantity, to_location = sum((m.on_hand_delta for m in movements), ZERO), None
    batch = Batch.objects.filter(pk=first.batch_id).first() if first.batch_id else None
    reverses = (
        StockMovement.objects.filter(pk=first.reversal_of_id)
        .values_list("operation_id", flat=True)
        .first()
        if first.reversal_of_id
        else None
    )
    serials = tuple(
        SerialNumber.objects.filter(
            pk__in=[m.serial_id for m in movements if m.serial_id]
        ).values_list("serial", flat=True)
    )
    return Intent(
        movement_type=kind,
        variant=first.variant_id,
        location=first.location_id,
        quantity=quantity,
        to_location=to_location,
        batch_code=batch.code if batch else None,
        batch_expires_on=batch.expires_on if batch else None,
        serials=serials,
        reverses=reverses,
        reference=(first.reference_type, first.reference_id),
    )


def _matches(first: StockMovement, intent: Intent) -> bool:
    if first.request_fingerprint is not None:
        return first.request_fingerprint == intent.fingerprint()
    # Recorded before Phase 04.1: compare everything the ledger proves; a request that omits a
    # batch expiry is compared without it, never against a guess.
    movements = list(
        StockMovement.objects.filter(operation_id=first.operation_id).order_by("occurred_at", "pk")
    )
    recorded = _recorded_intent(movements)
    if intent.batch_expires_on is None:
        recorded = replace(recorded, batch_expires_on=None)
    return recorded.canonical() == replace(intent, batch_manufactured_on=None).canonical()


def _replay(request: StockRequest, intent: Intent) -> Operation | None:
    """The committed operation for this key, if any. A different request is a conflict."""
    if request.idempotency_key is None:
        return None
    first = StockMovement.objects.filter(idempotency_key=request.idempotency_key).first()
    if first is None:
        return None
    if not _matches(first, intent):
        raise ConflictError(
            "This idempotency key was already used for a different stock change.",
            "idempotency_conflict",
        )
    movements = list(
        StockMovement.objects.filter(operation_id=first.operation_id).order_by("occurred_at", "pk")
    )
    return Operation(first.operation_id, movements, replayed=True)


def _execute(
    request: StockRequest,
    *,
    intent: Intent,
    variant: ProductVariant,
    location: StockLocation,
    body: Callable[[Operation, ProductVariant, Product], None],
    audit_action: str,
    audit: dict[str, Any],
) -> Operation:
    """Run ``body`` as one idempotent, audited operation under the variant lock."""
    movement_type = intent.movement_type
    replayed = _replay(request, intent)
    if replayed is not None:
        return replayed
    fingerprint = intent.fingerprint() if request.idempotency_key is not None else None
    operation = Operation(uuid7(), fingerprint=fingerprint)
    try:
        with transaction.atomic():
            locked, product = _lock_variant(variant)
            # A concurrent retry of the same request has committed while we waited for the lock.
            replayed = _replay(request, intent)
            if replayed is not None:
                return replayed
            body(operation, locked, product)
            record_audit_event(
                action=audit_action,
                resource=operation.movements[0],
                actor=request.actor,
                actor_label=request.actor_label if request.actor is None else "",
                changes={
                    "sku": locked.sku,
                    "location": location.code,
                    "movement_type": movement_type,
                    "entries": len(operation.movements),
                    **audit,
                },
            )
    except IntegrityError as exc:
        diag = getattr(exc.__cause__, "diag", None)
        if getattr(diag, "constraint_name", None) != _IDEMPOTENCY_CONSTRAINT:
            raise
        # The key was taken concurrently by an operation on another variant: replay or refuse.
        replayed = _replay(request, intent)
        if replayed is None:
            raise
        return replayed
    return operation


def _check_location(location: StockLocation) -> None:
    if not location.is_active:
        raise ConflictError("This stock location is inactive.", "location_inactive")


# --- Batches and serials --------------------------------------------------------------------------


def _batch_for_inbound(variant: ProductVariant, product: Product, data: BatchInput) -> Batch:
    code = data.code.strip()
    if not _BATCH_CODE.match(code):
        raise ValidationError({"batch": ["Enter a valid batch code."]})
    today = store_today()
    if data.expires_on is not None and data.expires_on < today:
        raise ValidationError({"batch": ["This batch has already expired."]})
    if data.manufactured_on is not None and data.manufactured_on > today:
        raise ValidationError({"batch": ["The manufacture date is in the future."]})
    batch = Batch.objects.filter(variant=variant, code=code).first()
    if batch is not None:
        if data.expires_on is not None and data.expires_on != batch.expires_on:
            raise ValidationError(
                {"batch": [f"Batch {code} already exists with a different expiry date."]}
            )
        if batch.expires_on is not None and batch.expires_on < today:
            raise ValidationError({"batch": [f"Batch {code} has expired."]})
        return batch
    perishable = GroceryDetails.objects.filter(product=product, is_perishable=True).exists()
    if perishable and data.expires_on is None:
        raise ValidationError({"batch": ["Perishable products need the batch's expiry date."]})
    batch = Batch(
        variant=variant,
        code=code,
        expires_on=data.expires_on,
        manufactured_on=data.manufactured_on,
        received_on=today,
        supplier_reference=data.supplier_reference.strip()[:100],
    )
    validated(batch, exclude=["tenant"])
    save_unique(batch, {"inventory_batch_code_unique": ("batch", "This batch already exists.")})
    return batch


def _existing_batch(variant: ProductVariant, code: str | None) -> Batch:
    batch = Batch.objects.filter(variant=variant, code=(code or "").strip()).first()
    if batch is None:
        raise ValidationError({"batch": ["Choose one of this variant's batches."]})
    return batch


def _serials_in_stock(
    variant: ProductVariant, location: StockLocation, serials: Sequence[str]
) -> list[SerialNumber]:
    rows = list(SerialNumber.objects.select_for_update().filter(serial__in=serials).order_by("pk"))
    found = {row.serial: row for row in rows}
    for serial in serials:
        row = found.get(serial)
        if (
            row is None
            or row.variant_id != variant.pk
            or row.location_id != location.pk
            or row.status != SerialStatus.AVAILABLE
        ):
            raise ValidationError(
                {"serials": [f"{serial} is not available at {location.code} for this variant."]}
            )
    return [found[serial] for serial in serials]


def _serials_inbound(
    variant: ProductVariant, location: StockLocation, serials: Sequence[str], *, returning: bool
) -> list[SerialNumber]:
    """New serials (receipts, found stock) or, for returns, serials that left the stock."""
    existing = {
        row.serial: row
        for row in SerialNumber.objects.select_for_update()
        .filter(serial__in=serials)
        .order_by("pk")
    }
    result: list[SerialNumber] = []
    for serial in serials:
        row = existing.get(serial)
        if row is None:
            row = SerialNumber(
                variant=variant,
                serial=serial,
                status=SerialStatus.AVAILABLE,
                location=location,
            )
            save_unique(
                row,
                {"inventory_serial_unique": ("serials", f"{serial} is already registered.")},
            )
        elif (
            returning
            and row.variant_id == variant.pk
            and row.status not in IN_STOCK_SERIAL_STATUSES
        ):
            row.status, row.location = SerialStatus.AVAILABLE, location
            row.save(update_fields=["status", "location", "updated_at"])
        else:
            raise ValidationError({"serials": [f"{serial} is already registered."]})
        result.append(row)
    return result


def _require_unit_inputs(
    product: Product,
    quantity: Decimal,
    *,
    batch: object | None,
    serials: Sequence[str],
) -> None:
    if product.tracking_mode == TrackingMode.BATCH and batch is None:
        raise ValidationError({"batch": ["This product is tracked by batch; choose a batch."]})
    if product.tracking_mode != TrackingMode.BATCH and batch is not None:
        raise ValidationError({"batch": ["This product is not tracked by batch."]})
    if product.tracking_mode == TrackingMode.SERIAL:
        if len(serials) != abs(quantity):
            raise ValidationError({"serials": ["Give one serial number per unit."]})
    elif serials:
        raise ValidationError({"serials": ["This product is not tracked by serial number."]})


# --- Operations -----------------------------------------------------------------------------------


def receive(
    *,
    variant: ProductVariant,
    location: StockLocation,
    quantity: Any,
    request: StockRequest,
    batch: BatchInput | None = None,
    serials: Sequence[str] = (),
) -> Operation:
    """Stock arriving at a location (purchase, opening balance)."""
    amount = clean_quantity(variant, quantity)
    serial_list = _clean_serials(serials)
    _check_location(location)

    def body(operation: Operation, locked: ProductVariant, product: Product) -> None:
        _require_unit_inputs(product, amount, batch=batch, serials=serial_list)
        level = _lock_levels(locked, [location])[location.pk]
        if product.tracking_mode == TrackingMode.SERIAL:
            for index, row in enumerate(
                _serials_inbound(locked, location, serial_list, returning=False)
            ):
                _record(
                    level,
                    operation=operation,
                    movement_type=MovementType.RECEIPT,
                    on_hand_delta=Decimal(1),
                    request=request,
                    first=index == 0,
                    serial=row,
                )
            return
        lot = _batch_for_inbound(locked, product, batch) if batch is not None else None
        _record(
            level,
            operation=operation,
            movement_type=MovementType.RECEIPT,
            on_hand_delta=amount,
            request=request,
            first=True,
            batch=lot,
        )

    intent = Intent(
        movement_type=MovementType.RECEIPT,
        variant=variant.pk,
        location=location.pk,
        quantity=amount,
        serials=tuple(serial_list),
        reference=_reference(request),
        **_batch_identity(batch),
    )
    return _execute(
        request,
        intent=intent,
        variant=variant,
        location=location,
        body=body,
        audit_action="inventory.stock.received",
        audit={"quantity": amount, "batch": batch.code if batch else None},
    )


def adjust(
    *,
    variant: ProductVariant,
    location: StockLocation,
    quantity: Any,
    movement_type: str,
    request: StockRequest,
    batch: BatchInput | None = None,
    serials: Sequence[str] = (),
) -> Operation:
    """A recorded correction: count adjustment (either sign), damaged, lost or returned stock.
    Damaged and lost take positive quantities and remove stock; returns add it."""
    if movement_type not in ADJUSTMENT_TYPES:
        raise ValidationError({"movement_type": ["Choose a valid adjustment type."]})
    sign = ADJUSTMENT_TYPES[movement_type]
    amount = clean_quantity(variant, quantity, signed=sign == 0)
    delta = amount if sign >= 0 else -amount
    serial_list = _clean_serials(serials)
    _check_location(location)
    if not request.reason.strip():
        raise ValidationError({"reason": ["Say why the stock is being adjusted."]})

    def body(operation: Operation, locked: ProductVariant, product: Product) -> None:
        _require_unit_inputs(product, delta, batch=batch, serials=serial_list)
        level = _lock_levels(locked, [location])[location.pk]
        if product.tracking_mode == TrackingMode.SERIAL:
            if delta > 0:
                rows = _serials_inbound(
                    locked, location, serial_list, returning=movement_type == MovementType.RETURNED
                )
            else:
                rows = _serials_in_stock(locked, location, serial_list)
                status = (
                    SerialStatus.DAMAGED
                    if movement_type == MovementType.DAMAGED
                    else SerialStatus.LOST
                )
                for row in rows:
                    row.status, row.location = status, None
                    row.save(update_fields=["status", "location", "updated_at"])
            step = Decimal(1) if delta > 0 else Decimal(-1)
            for index, row in enumerate(rows):
                _record(
                    level,
                    operation=operation,
                    movement_type=movement_type,
                    on_hand_delta=step,
                    request=request,
                    first=index == 0,
                    serial=row,
                )
            return
        lot: Batch | None = None
        if batch is not None:
            lot = (
                _batch_for_inbound(locked, product, batch)
                if delta > 0
                else _existing_batch(locked, batch.code)
            )
        _record(
            level,
            operation=operation,
            movement_type=movement_type,
            on_hand_delta=delta,
            request=request,
            first=True,
            batch=lot,
        )

    intent = Intent(
        movement_type=movement_type,
        variant=variant.pk,
        location=location.pk,
        quantity=delta,
        serials=tuple(serial_list),
        reference=_reference(request),
        **_batch_identity(batch),
    )
    return _execute(
        request,
        intent=intent,
        variant=variant,
        location=location,
        body=body,
        audit_action="inventory.stock.adjusted",
        audit={"quantity": delta, "batch": batch.code if batch else None},
    )


def transfer(
    *,
    variant: ProductVariant,
    source: StockLocation,
    destination: StockLocation,
    quantity: Any,
    request: StockRequest,
    batch_code: str | None = None,
    serials: Sequence[str] = (),
) -> Operation:
    """Move stock between locations: two linked movements in one transaction."""
    if source.pk == destination.pk:
        raise ValidationError({"to_location": ["Choose a different location."]})
    amount = clean_quantity(variant, quantity)
    serial_list = _clean_serials(serials)
    _check_location(source)
    _check_location(destination)

    def body(operation: Operation, locked: ProductVariant, product: Product) -> None:
        _require_unit_inputs(product, amount, batch=batch_code, serials=serial_list)
        levels = _lock_levels(locked, [source, destination])
        out_level, in_level = levels[source.pk], levels[destination.pk]
        if product.tracking_mode == TrackingMode.SERIAL:
            rows = _serials_in_stock(locked, source, serial_list)
            for index, row in enumerate(rows):
                _record(
                    out_level,
                    operation=operation,
                    movement_type=MovementType.TRANSFER_OUT,
                    on_hand_delta=Decimal(-1),
                    request=request,
                    first=index == 0,
                    serial=row,
                )
                row.location = destination
                row.save(update_fields=["location", "updated_at"])
                _record(
                    in_level,
                    operation=operation,
                    movement_type=MovementType.TRANSFER_IN,
                    on_hand_delta=Decimal(1),
                    request=request,
                    first=False,
                    serial=row,
                )
            return
        lot = _existing_batch(locked, batch_code) if batch_code is not None else None
        for level, movement_type, delta in (
            (out_level, MovementType.TRANSFER_OUT, -amount),
            (in_level, MovementType.TRANSFER_IN, amount),
        ):
            _record(
                level,
                operation=operation,
                movement_type=movement_type,
                on_hand_delta=delta,
                request=request,
                first=movement_type == MovementType.TRANSFER_OUT,
                batch=lot,
            )

    intent = Intent(
        movement_type=MovementType.TRANSFER_OUT,
        variant=variant.pk,
        location=source.pk,
        to_location=destination.pk,
        quantity=amount,
        batch_code=batch_code.strip() if batch_code is not None else None,
        serials=tuple(serial_list),
        reference=_reference(request),
    )
    return _execute(
        request,
        intent=intent,
        variant=variant,
        location=source,
        body=body,
        audit_action="inventory.stock.transferred",
        audit={"quantity": amount, "destination": destination.code, "batch": batch_code},
    )


def reverse(*, movement: StockMovement, request: StockRequest) -> Operation:
    """Undo a manual operation with compensating movements (both legs of a transfer).

    Reservations belong to orders (Phases 07/08), and serial-number movements change unit
    identities, so both are corrected with explicit adjustments instead.
    """
    if not request.reason.strip():
        raise ValidationError({"reason": ["Say why the change is being reversed."]})
    originals = list(
        StockMovement.objects.filter(operation_id=movement.operation_id).order_by(
            "occurred_at", "pk"
        )
    )
    if any(row.movement_type not in REVERSIBLE_TYPES for row in originals):
        raise ConflictError("This kind of stock movement cannot be reversed.", "not_reversible")
    if any(row.serial_id is not None for row in originals):
        raise ConflictError(
            "Correct serial-number stock with an adjustment instead.", "not_reversible"
        )
    # Remove stock before adding it back, so a transfer reversal never needs extra stock.
    ordered = sorted(originals, key=lambda row: row.on_hand_delta < 0)
    variant = ProductVariant.objects.get(pk=movement.variant_id)
    # The operation's first entry (which carries the idempotency key) is at this location.
    location = StockLocation.objects.get(pk=ordered[0].location_id)

    def body(operation: Operation, locked: ProductVariant, product: Product) -> None:
        if StockMovement.objects.filter(reversal_of__in=originals).exists():
            raise ConflictError("This change was already reversed.", "already_reversed")
        locations = list(
            StockLocation.objects.filter(pk__in={row.location_id for row in originals})
        )
        levels = _lock_levels(locked, locations)
        batches = {
            row.pk: row
            for row in Batch.objects.filter(pk__in=[r.batch_id for r in originals if r.batch_id])
        }
        for index, original in enumerate(ordered):
            _record(
                levels[original.location_id],
                operation=operation,
                movement_type=MovementType.REVERSAL,
                on_hand_delta=-original.on_hand_delta,
                request=request,
                first=index == 0,
                batch=batches.get(original.batch_id) if original.batch_id else None,
                reversal_of=original,
            )

    try:
        intent = Intent(
            movement_type=MovementType.REVERSAL,
            variant=variant.pk,
            location=location.pk,
            reverses=movement.operation_id,
            reference=_reference(request),
        )
        return _execute(
            request,
            intent=intent,
            variant=variant,
            location=location,
            body=body,
            audit_action="inventory.stock.reversed",
            audit={"reversed_operation": str(movement.operation_id)},
        )
    except IntegrityError as exc:
        diag = getattr(exc.__cause__, "diag", None)
        if getattr(diag, "constraint_name", None) == "inventory_movement_reversed_once":
            raise ConflictError("This change was already reversed.", "already_reversed") from exc
        raise


def _plain_tracking_only(product: Product) -> None:
    if product.tracking_mode != TrackingMode.NONE:
        raise DomainError(
            "Batch and serial allocation for orders arrives with order fulfilment.",
            "allocation_required",
        )


def reserve(
    *, variant: ProductVariant, location: StockLocation, quantity: Any, request: StockRequest
) -> Operation:
    """Hold stock for an order (Phase 07 checkout). Refused unless enough is available."""
    amount = clean_quantity(variant, quantity)
    _check_location(location)

    def body(operation: Operation, locked: ProductVariant, product: Product) -> None:
        level = _lock_levels(locked, [location])[location.pk]
        if product.tracking_mode == TrackingMode.BATCH:
            # Reservations are not tied to batches, so all of them must fit in unexpired stock.
            # Batch levels only change under this variant lock, so this read is stable.
            eligible = _eligible_batch_stock([locked.pk], store_today()).get(
                (locked.pk, location.pk), ZERO
            )
            if level.reserved + amount > eligible:
                raise InsufficientStock("There isn't enough unexpired stock to reserve.")
        _record(
            level,
            operation=operation,
            movement_type=MovementType.RESERVED,
            on_hand_delta=ZERO,
            reserved_delta=amount,
            request=request,
            first=True,
        )

    intent = Intent(
        movement_type=MovementType.RESERVED,
        variant=variant.pk,
        location=location.pk,
        quantity=amount,
        reference=_reference(request),
    )
    return _execute(
        request,
        intent=intent,
        variant=variant,
        location=location,
        body=body,
        audit_action="inventory.stock.reserved",
        audit={"quantity": amount},
    )


def release(
    *, variant: ProductVariant, location: StockLocation, quantity: Any, request: StockRequest
) -> Operation:
    """Return reserved stock to available (cancelled or expired checkout, Phases 07/08)."""
    amount = clean_quantity(variant, quantity)

    def body(operation: Operation, locked: ProductVariant, product: Product) -> None:
        level = _lock_levels(locked, [location])[location.pk]
        _record(
            level,
            operation=operation,
            movement_type=MovementType.RELEASED,
            on_hand_delta=ZERO,
            reserved_delta=-amount,
            request=request,
            first=True,
        )

    intent = Intent(
        movement_type=MovementType.RELEASED,
        variant=variant.pk,
        location=location.pk,
        quantity=amount,
        reference=_reference(request),
    )
    return _execute(
        request,
        intent=intent,
        variant=variant,
        location=location,
        body=body,
        audit_action="inventory.stock.released",
        audit={"quantity": amount},
    )


def fulfil(
    *, variant: ProductVariant, location: StockLocation, quantity: Any, request: StockRequest
) -> Operation:
    """Dispatch reserved stock (Phase 08): on-hand and reserved fall together."""
    amount = clean_quantity(variant, quantity)

    def body(operation: Operation, locked: ProductVariant, product: Product) -> None:
        _plain_tracking_only(product)
        level = _lock_levels(locked, [location])[location.pk]
        _record(
            level,
            operation=operation,
            movement_type=MovementType.FULFILLED,
            on_hand_delta=-amount,
            reserved_delta=-amount,
            request=request,
            first=True,
        )

    intent = Intent(
        movement_type=MovementType.FULFILLED,
        variant=variant.pk,
        location=location.pk,
        quantity=amount,
        reference=_reference(request),
    )
    return _execute(
        request,
        intent=intent,
        variant=variant,
        location=location,
        body=body,
        audit_action="inventory.stock.fulfilled",
        audit={"quantity": amount},
    )


# --- Queries --------------------------------------------------------------------------------------


def _eligible_batch_stock(
    variant_ids: Sequence[Any], today: date
) -> dict[tuple[Any, Any], Decimal]:
    """Unexpired batch on-hand per (variant, location) at active locations. A batch is sellable
    through its expiry date (store calendar, inclusive); batches without a date never expire."""
    rows = (
        BatchLevel.objects.filter(
            batch__variant_id__in=variant_ids, location__is_active=True, on_hand__gt=0
        )
        .filter(Q(batch__expires_on__isnull=True) | Q(batch__expires_on__gte=today))
        .values("batch__variant_id", "location_id")
        .annotate(total=Sum("on_hand"))
    )
    return {(row["batch__variant_id"], row["location_id"]): row["total"] for row in rows}


def sellable_quantities(variants: Sequence[ProductVariant]) -> dict[Any, Decimal]:
    """Stock that can be promised to customers now, per variant (three queries for any number).

    Plain and serial-tracked variants: ``available`` (on-hand minus reserved) at active locations.
    Batch-tracked variants: unexpired batch stock at active locations minus that location's whole
    reservation, because reservations are not assigned to batches; this never overstates (D-060).
    Untracked and digital variants are not stock-limited; callers decide for them.
    """
    ids = [variant.pk for variant in variants]
    result: dict[Any, Decimal] = dict.fromkeys(ids, ZERO)
    if not ids:
        return result
    batch_tracked = set(
        ProductVariant.objects.filter(
            pk__in=ids, product__tracking_mode=TrackingMode.BATCH
        ).values_list("pk", flat=True)
    )
    eligible = _eligible_batch_stock(list(batch_tracked), store_today()) if batch_tracked else {}
    levels = StockLevel.objects.filter(variant_id__in=ids, location__is_active=True).values(
        "variant_id", "location_id", "available", "reserved"
    )
    for level in levels:
        key = (level["variant_id"], level["location_id"])
        usable = (
            eligible.get(key, ZERO) - level["reserved"]
            if level["variant_id"] in batch_tracked
            else level["available"]
        )
        if usable > 0:
            result[level["variant_id"]] += usable
    return result


def sellable_quantity(variant: ProductVariant) -> Decimal:
    return sellable_quantities([variant])[variant.pk]


def batches_by_expiry(variant: ProductVariant, location: StockLocation) -> list[BatchLevel]:
    """Unexpired batch stock at ``location``, first-expiring first (FEFO order, with undated
    batches last). Allocation of orders to batches is Phase 08; this is its foundation."""
    today = store_today()
    return list(
        BatchLevel.objects.filter(batch__variant=variant, location=location, on_hand__gt=0)
        .filter(Q(batch__expires_on__isnull=True) | Q(batch__expires_on__gte=today))
        .select_related("batch")
        .order_by("batch__expires_on", "batch__received_on", "batch__code")
    )


@dataclass(frozen=True)
class Discrepancy:
    kind: str
    variant_id: Any
    location_id: Any
    expected: Decimal
    actual: Decimal
    batch_id: Any = None


def reconcile() -> list[Discrepancy]:
    """Compare materialised quantities with the ledger (and batch and serial records). An empty
    list means every level equals the sum of its movements."""
    problems: list[Discrepancy] = []
    ledger = {
        (row["variant_id"], row["location_id"]): (row["on_hand"], row["reserved"])
        for row in StockMovement.objects.values("variant_id", "location_id").annotate(
            on_hand=Sum("on_hand_delta"), reserved=Sum("reserved_delta")
        )
    }
    levels = {
        (row.variant_id, row.location_id): row
        for row in StockLevel.objects.only("variant_id", "location_id", "on_hand", "reserved")
    }
    for key in ledger.keys() | levels.keys():
        expected_on_hand, expected_reserved = ledger.get(key, (ZERO, ZERO))
        level = levels.get(key)
        actual_on_hand = level.on_hand if level else ZERO
        actual_reserved = level.reserved if level else ZERO
        if expected_on_hand != actual_on_hand:
            problems.append(
                Discrepancy("on_hand", key[0], key[1], expected_on_hand, actual_on_hand)
            )
        if expected_reserved != actual_reserved:
            problems.append(
                Discrepancy("reserved", key[0], key[1], expected_reserved, actual_reserved)
            )

    batch_ledger = {
        (row["batch_id"], row["location_id"]): row["on_hand"]
        for row in StockMovement.objects.filter(batch__isnull=False)
        .values("batch_id", "location_id")
        .annotate(on_hand=Sum("on_hand_delta"))
    }
    batch_levels = BatchLevel.objects.select_related("batch").only(
        "batch_id", "location_id", "on_hand", "batch__variant_id"
    )
    by_location: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: ZERO)
    for row in batch_levels:
        expected = batch_ledger.get((row.batch_id, row.location_id), ZERO)
        if expected != row.on_hand:
            problems.append(
                Discrepancy(
                    "batch",
                    row.batch.variant_id,
                    row.location_id,
                    expected,
                    row.on_hand,
                    row.batch_id,
                )
            )
        by_location[(row.batch.variant_id, row.location_id)] += row.on_hand
    batch_tracked = set(
        ProductVariant.objects.filter(product__tracking_mode=TrackingMode.BATCH).values_list(
            "pk", flat=True
        )
    )
    serial_tracked = set(
        ProductVariant.objects.filter(product__tracking_mode=TrackingMode.SERIAL).values_list(
            "pk", flat=True
        )
    )
    serial_counts = {
        (row["variant_id"], row["location_id"]): Decimal(row["count"])
        for row in SerialNumber.objects.filter(status__in=IN_STOCK_SERIAL_STATUSES)
        .values("variant_id", "location_id")
        .annotate(count=Count("pk"))
    }
    for key, level in levels.items():
        if key[0] in batch_tracked and by_location.get(key, ZERO) != level.on_hand:
            problems.append(
                Discrepancy(
                    "batch_total", key[0], key[1], level.on_hand, by_location.get(key, ZERO)
                )
            )
        if key[0] in serial_tracked and serial_counts.get(key, ZERO) != level.on_hand:
            problems.append(
                Discrepancy("serials", key[0], key[1], level.on_hand, serial_counts.get(key, ZERO))
            )
    return problems


def update_level_settings(
    *, level: StockLevel, low_stock_threshold: Decimal | None, actor: User | None
) -> StockLevel:
    with transaction.atomic():
        locked = StockLevel.objects.select_for_update().get(pk=level.pk)
        before = {"low_stock_threshold": locked.low_stock_threshold}
        locked.low_stock_threshold = low_stock_threshold
        locked.save(update_fields=["low_stock_threshold", "updated_at"])
        record_audit_event(
            action="inventory.level.updated",
            resource=locked,
            actor=actor,
            changes=field_changes(
                before,
                {"low_stock_threshold": low_stock_threshold},
                fields=["low_stock_threshold"],
            ),
        )
    return locked
