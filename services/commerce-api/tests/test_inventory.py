"""Inventory ledger, levels, batches and serials against real PostgreSQL."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from rest_framework.exceptions import ValidationError

from commerce.audit.models import AuditEvent
from commerce.catalog.models import Product, ProductVariant
from commerce.core.domain import ConflictError
from commerce.inventory import services as inventory
from commerce.inventory.models import (
    Batch,
    BatchLevel,
    SerialNumber,
    StockLedgerImmutableError,
    StockLevel,
    StockLocation,
    StockMovement,
)
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, set_preset, simple_product

pytestmark = pytest.mark.usefixtures("store")

REQ = inventory.StockRequest(reason="test")


def qty(*values: int) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) for value in values)


@pytest.fixture
def tee(deployment_tenant: Tenant) -> ProductVariant:
    return first_variant(deployment_tenant, simple_product(deployment_tenant))


@pytest.fixture
def ctx(deployment_tenant: Tenant) -> Any:
    with tenant_context(deployment_tenant):
        yield deployment_tenant


def level(variant: ProductVariant, location: StockLocation | None = None) -> StockLevel:
    return StockLevel.objects.get(
        variant=variant, location=location or inventory.default_location()
    )


def receive(variant: ProductVariant, quantity: Any, **kwargs: Any) -> inventory.Operation:
    return inventory.receive(
        variant=variant,
        location=kwargs.pop("location", None) or inventory.default_location(),
        quantity=quantity,
        request=kwargs.pop("request", REQ),
        **kwargs,
    )


def second_location() -> StockLocation:
    return inventory.save_location(location=None, data={"code": "shop", "name": "Shop"}, actor=None)


# --- Locations and basic operations ---------------------------------------------------------------


def test_one_default_location_is_created(ctx: Tenant) -> None:
    first = inventory.default_location()
    assert inventory.default_location().pk == first.pk
    assert (first.code, first.is_default) == ("MAIN", True)
    shop = second_location()
    assert shop.code == "SHOP"
    with pytest.raises(ValidationError):
        inventory.save_location(location=None, data={"code": "SHOP", "name": "Dup"}, actor=None)


def test_receipts_update_levels_and_append_to_the_ledger(ctx: Tenant, tee: ProductVariant) -> None:
    operation = receive(tee, 5)
    receive(tee, "2")

    stock = level(tee)
    assert (stock.on_hand, stock.reserved, stock.available) == qty(7, 0, 7)
    movements = list(StockMovement.objects.filter(variant=tee).order_by("occurred_at", "pk"))
    assert [(m.movement_type, m.on_hand_delta, m.on_hand_after) for m in movements] == [
        ("receipt", Decimal(5), Decimal(5)),
        ("receipt", Decimal(2), Decimal(7)),
    ]
    assert operation.movements[0].reason == "test"
    event = AuditEvent.objects.get(
        action="inventory.stock.received", resource_id=str(movements[0].pk)
    )
    assert event.changes["quantity"] == "5"
    assert "reason" not in event.changes


@pytest.mark.parametrize(
    ("movement_type", "quantity", "expected"),
    [
        ("adjustment", -3, 7),
        ("adjustment", 2, 12),
        ("damaged", 1, 9),
        ("lost", 4, 6),
        ("returned", 1, 11),
    ],
)
def test_adjustments_are_signed_by_type(
    ctx: Tenant, tee: ProductVariant, movement_type: str, quantity: int, expected: int
) -> None:
    receive(tee, 10)
    inventory.adjust(
        variant=tee,
        location=inventory.default_location(),
        quantity=quantity,
        movement_type=movement_type,
        request=inventory.StockRequest(reason="count"),
    )
    assert level(tee).on_hand == expected


def test_stock_never_goes_negative(ctx: Tenant, tee: ProductVariant) -> None:
    receive(tee, 2)
    with pytest.raises(inventory.InsufficientStock):
        inventory.adjust(
            variant=tee,
            location=inventory.default_location(),
            quantity=3,
            movement_type="damaged",
            request=REQ,
        )
    with pytest.raises(ValidationError, match="positive"):
        inventory.adjust(
            variant=tee,
            location=inventory.default_location(),
            quantity=-1,
            movement_type="damaged",
            request=REQ,
        )
    assert level(tee).on_hand == 2
    assert StockMovement.objects.filter(variant=tee).count() == 1


def test_adjustments_need_a_reason(ctx: Tenant, tee: ProductVariant) -> None:
    with pytest.raises(ValidationError, match="why"):
        inventory.adjust(
            variant=tee,
            location=inventory.default_location(),
            quantity=1,
            movement_type="returned",
            request=inventory.StockRequest(),
        )


@pytest.mark.parametrize(
    ("unit", "quantity", "ok"),
    [
        ("each", "3", True),
        ("each", "1.5", False),
        ("kg", "1.255", True),
        ("kg", "1.2555", False),
        ("kg", "0", False),
    ],
)
def test_quantities_follow_the_inventory_unit(
    deployment_tenant: Tenant, ctx: Tenant, unit: str, quantity: str, ok: bool
) -> None:
    variant = first_variant(
        deployment_tenant, simple_product(deployment_tenant, variant={"inventory_unit": unit})
    )
    if ok:
        receive(variant, quantity)
        assert level(variant).on_hand == Decimal(quantity)
    else:
        with pytest.raises(ValidationError):
            receive(variant, quantity)


def test_untracked_and_digital_variants_have_no_stock(
    deployment_tenant: Tenant, ctx: Tenant
) -> None:
    untracked = first_variant(
        deployment_tenant, simple_product(deployment_tenant, sku="U-1", track_inventory=False)
    )
    digital = first_variant(
        deployment_tenant,
        simple_product(
            deployment_tenant,
            sku="D-1",
            name="Ebook",
            variant={"kind": "digital", "digital_format": "pdf"},
        ),
    )
    for variant in (untracked, digital):
        with pytest.raises(inventory.NotTracked):
            receive(variant, 1)


# --- Transfers and reversals ----------------------------------------------------------------------


def test_transfers_move_stock_atomically(ctx: Tenant, tee: ProductVariant) -> None:
    main, shop = inventory.default_location(), second_location()
    receive(tee, 5)
    operation = inventory.transfer(
        variant=tee, source=main, destination=shop, quantity=3, request=REQ
    )

    assert (level(tee, main).on_hand, level(tee, shop).on_hand) == qty(2, 3)
    assert {m.operation_id for m in operation.movements} == {operation.operation_id}
    assert [m.movement_type for m in operation.movements] == ["transfer_out", "transfer_in"]
    with pytest.raises(inventory.InsufficientStock):
        inventory.transfer(variant=tee, source=main, destination=shop, quantity=3, request=REQ)
    with pytest.raises(ValidationError):
        inventory.transfer(variant=tee, source=main, destination=main, quantity=1, request=REQ)
    assert (level(tee, main).on_hand, level(tee, shop).on_hand) == qty(2, 3)


def test_reversals_compensate_and_happen_once(ctx: Tenant, tee: ProductVariant) -> None:
    main, shop = inventory.default_location(), second_location()
    receipt = receive(tee, 5).movements[0]
    moved = inventory.transfer(variant=tee, source=main, destination=shop, quantity=2, request=REQ)

    inventory.reverse(movement=moved.movements[0], request=REQ)
    assert (level(tee, main).on_hand, level(tee, shop).on_hand) == qty(5, 0)
    with pytest.raises(ConflictError, match="already reversed"):
        inventory.reverse(movement=moved.movements[1], request=REQ)

    inventory.adjust(variant=tee, location=main, quantity=4, movement_type="lost", request=REQ)
    with pytest.raises(inventory.InsufficientStock):
        inventory.reverse(movement=receipt, request=REQ)  # 4 of the 5 received are gone
    with pytest.raises(ValidationError):
        inventory.reverse(movement=receipt, request=inventory.StockRequest())
    assert inventory.reconcile() == []


# --- Idempotency ----------------------------------------------------------------------------------


def test_retried_requests_apply_once(ctx: Tenant, tee: ProductVariant) -> None:
    request = inventory.StockRequest(reason="PO 17", idempotency_key="po-17-line-1")
    first = receive(tee, 4, request=request)
    again = receive(tee, 4, request=request)

    assert (first.replayed, again.replayed) == (False, True)
    assert again.operation_id == first.operation_id
    assert level(tee).on_hand == 4
    assert StockMovement.objects.filter(variant=tee).count() == 1
    with pytest.raises(ConflictError, match="different stock change"):
        inventory.transfer(
            variant=tee,
            source=inventory.default_location(),
            destination=second_location(),
            quantity=1,
            request=request,
        )


# --- Reservations (Phase 07/08 extension points) --------------------------------------------------


def test_reservations_never_exceed_available_stock(ctx: Tenant, tee: ProductVariant) -> None:
    main = inventory.default_location()
    receive(tee, 3)
    inventory.reserve(variant=tee, location=main, quantity=2, request=REQ)
    assert (level(tee).reserved, level(tee).available) == qty(2, 1)
    with pytest.raises(inventory.InsufficientStock):
        inventory.reserve(variant=tee, location=main, quantity=2, request=REQ)
    with pytest.raises(inventory.InsufficientStock):
        inventory.adjust(variant=tee, location=main, quantity=2, movement_type="lost", request=REQ)
    inventory.fulfil(variant=tee, location=main, quantity=1, request=REQ)
    inventory.release(variant=tee, location=main, quantity=1, request=REQ)
    with pytest.raises(ConflictError):
        inventory.release(variant=tee, location=main, quantity=1, request=REQ)
    stock = level(tee)
    assert (stock.on_hand, stock.reserved, stock.available) == qty(2, 0, 2)
    assert inventory.reconcile() == []


# --- Ledger and database guarantees ---------------------------------------------------------------


def test_the_ledger_is_append_only(ctx: Tenant, tee: ProductVariant) -> None:
    movement = receive(tee, 1).movements[0]
    with pytest.raises(StockLedgerImmutableError):
        movement.save()
    with pytest.raises(StockLedgerImmutableError):
        movement.delete()
    with pytest.raises(StockLedgerImmutableError):
        StockMovement.objects.filter(pk=movement.pk).update(reason="rewritten")
    for statement in (
        "UPDATE inventory_stockmovement SET reason = 'x'",
        "DELETE FROM inventory_stockmovement",
    ):
        with (
            pytest.raises(IntegrityError, match="append-only"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"on_hand": -1}, "on_hand_gte_0"),
        ({"reserved": 5}, "reserved_within_on_hand"),
    ],
)
def test_the_database_refuses_impossible_levels(
    ctx: Tenant, tee: ProductVariant, values: dict[str, int], constraint: str
) -> None:
    receive(tee, 2)
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        StockLevel.objects.filter(variant=tee).update(**values)


def test_reconciliation_detects_levels_that_disagree_with_the_ledger(
    ctx: Tenant, tee: ProductVariant
) -> None:
    receive(tee, 5)
    assert inventory.reconcile() == []
    call_command("reconcile_inventory")

    StockLevel.objects.filter(variant=tee).update(on_hand=9)  # bypasses the service on purpose
    [problem] = inventory.reconcile()
    assert (problem.kind, problem.expected, problem.actual) == ("on_hand", Decimal(5), Decimal(9))


def test_low_stock_thresholds_are_configurable(ctx: Tenant, tee: ProductVariant) -> None:
    receive(tee, 3)
    stock = inventory.update_level_settings(
        level=level(tee), low_stock_threshold=Decimal("5"), actor=None
    )
    assert stock.low_stock_threshold == 5
    assert inventory.available_quantity(tee) == 3


# --- Batches and expiry ---------------------------------------------------------------------------


@pytest.fixture
def milk(deployment_tenant: Tenant) -> ProductVariant:
    set_preset(deployment_tenant, "grocery")
    product = simple_product(
        deployment_tenant,
        sku="MILK-1L",
        name="Milk",
        product_type="grocery",
        tracking_mode="batch",
        details={"is_perishable": True},
        variant={"net_quantity": Decimal("1"), "net_unit": "l"},
    )
    return first_variant(deployment_tenant, product)


def batch(code: str, days: int | None) -> inventory.BatchInput:
    return inventory.BatchInput(
        code=code,
        expires_on=inventory.store_today() + timedelta(days=days) if days is not None else None,
    )


def test_batch_tracked_stock_needs_valid_batches(ctx: Tenant, milk: ProductVariant) -> None:
    with pytest.raises(ValidationError, match="tracked by batch"):
        receive(milk, 5)
    with pytest.raises(ValidationError, match="expired"):
        receive(milk, 5, batch=batch("OLD", -1))
    with pytest.raises(ValidationError, match="expiry"):
        receive(milk, 5, batch=batch("NODATE", None))
    receive(milk, 5, batch=batch("M-1", 3))
    receive(milk, 2, batch=batch("M-1", 3))
    with pytest.raises(ValidationError, match="different expiry"):
        receive(milk, 1, batch=batch("M-1", 9))
    assert BatchLevel.objects.get(batch__code="M-1").on_hand == 7
    assert inventory.reconcile() == []


def test_batches_are_listed_first_expired_first_out(ctx: Tenant, milk: ProductVariant) -> None:
    receive(milk, 1, batch=batch("LATE", 30))
    receive(milk, 1, batch=batch("SOON", 2))
    receive(milk, 1, batch=batch("MID", 10))
    Batch.objects.filter(code="MID").update(expires_on=inventory.store_today() - timedelta(days=1))

    order = [
        row.batch.code for row in inventory.batches_by_expiry(milk, inventory.default_location())
    ]
    assert order == ["SOON", "LATE"]  # the expired batch is not offered


def test_batch_stock_moves_and_leaves_by_batch(ctx: Tenant, milk: ProductVariant) -> None:
    main, shop = inventory.default_location(), second_location()
    receive(milk, 4, batch=batch("M-2", 5))
    receive(milk, 5, batch=batch("M-3", 6))
    inventory.transfer(
        variant=milk, source=main, destination=shop, quantity=3, batch_code="M-2", request=REQ
    )
    # MAIN holds 6 in total, but only 1 of batch M-2.
    with pytest.raises(inventory.InsufficientStock, match="M-2"):
        inventory.adjust(
            variant=milk,
            location=main,
            quantity=2,
            movement_type="damaged",
            batch=batch("M-2", None),
            request=REQ,
        )
    with pytest.raises(ValidationError, match="batches"):
        inventory.adjust(
            variant=milk,
            location=main,
            quantity=1,
            movement_type="damaged",
            batch=batch("UNKNOWN", None),
            request=REQ,
        )
    levels = sorted(BatchLevel.objects.values_list("batch__code", "location__code", "on_hand"))
    assert levels == [
        ("M-2", "MAIN", Decimal(1)),
        ("M-2", "SHOP", Decimal(3)),
        ("M-3", "MAIN", Decimal(5)),
    ]
    assert inventory.reconcile() == []


# --- Serial numbers -------------------------------------------------------------------------------


@pytest.fixture
def phone(deployment_tenant: Tenant) -> ProductVariant:
    product = simple_product(
        deployment_tenant, sku="PHONE-A5", name="Phone", tracking_mode="serial"
    )
    return first_variant(deployment_tenant, product)


def test_serials_are_received_once_per_unit(ctx: Tenant, phone: ProductVariant) -> None:
    with pytest.raises(ValidationError, match="one serial"):
        receive(phone, 2, serials=["SN-1"])
    with pytest.raises(ValidationError, match="only once"):
        receive(phone, 2, serials=["SN-1", "sn-1"])
    receive(phone, 2, serials=["sn-1", "SN-2"])
    with pytest.raises(ValidationError, match="already registered"):
        receive(phone, 1, serials=["SN-2"])
    assert level(phone).on_hand == 2
    assert StockMovement.objects.filter(variant=phone).count() == 2
    assert set(SerialNumber.objects.values_list("serial", flat=True)) == {"SN-1", "SN-2"}


def test_serials_have_one_place_and_status(ctx: Tenant, phone: ProductVariant) -> None:
    main, shop = inventory.default_location(), second_location()
    receive(phone, 3, serials=["A1", "A2", "A3"])
    inventory.transfer(
        variant=phone, source=main, destination=shop, quantity=1, serials=["A1"], request=REQ
    )
    with pytest.raises(ValidationError, match="not available at MAIN"):
        inventory.transfer(
            variant=phone, source=main, destination=shop, quantity=1, serials=["A1"], request=REQ
        )
    inventory.adjust(
        variant=phone,
        location=main,
        quantity=1,
        movement_type="damaged",
        serials=["A2"],
        request=REQ,
    )

    serials = {row.serial: (row.status, row.location_id) for row in SerialNumber.objects.all()}
    assert serials == {
        "A1": ("available", shop.pk),
        "A2": ("damaged", None),
        "A3": ("available", main.pk),
    }
    assert (level(phone, main).on_hand, level(phone, shop).on_hand) == qty(1, 1)
    assert inventory.reconcile() == []
    with pytest.raises(IntegrityError, match="location_matches_status"), transaction.atomic():
        SerialNumber.objects.filter(serial="A2").update(location=main)


def test_serials_are_unique_per_store_only(
    deployment_tenant: Tenant, tenant_b: Tenant, phone: ProductVariant
) -> None:
    from .factories import make_store_settings

    make_store_settings(tenant_b)
    with tenant_context(deployment_tenant):
        receive(phone, 1, serials=["IMEI-1"])
    other = first_variant(tenant_b, simple_product(tenant_b, sku="PHONE-B", tracking_mode="serial"))
    with tenant_context(tenant_b):
        receive(other, 1, serials=["IMEI-1"])
        assert SerialNumber.objects.get().serial == "IMEI-1"


def test_unused_variants_count_as_history_free(ctx: Tenant, tee: ProductVariant) -> None:
    from commerce.catalog.services import has_inventory_history

    assert not has_inventory_history([tee.pk])
    receive(tee, 1)
    assert has_inventory_history([tee.pk])
    assert Product.objects.count() == 1
