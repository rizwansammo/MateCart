"""Phase 04.1 corrections: idempotency request identity and expiry-aware sellable stock."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import connections

from commerce.audit.models import AuditEvent
from commerce.catalog import services as catalog
from commerce.catalog.models import ProductVariant
from commerce.core.domain import ConflictError
from commerce.core.ids import uuid7
from commerce.inventory import services as inventory
from commerce.inventory.models import Batch, StockLevel, StockLocation, StockMovement
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, set_preset, simple_product
from .factories import make_store_settings
from .helpers import Api
from .test_inventory_concurrency import run_concurrently

pytestmark = pytest.mark.usefixtures("store")


def req(key: str | None = None, **kwargs: Any) -> inventory.StockRequest:
    return inventory.StockRequest(reason="integrity", idempotency_key=key, **kwargs)


@pytest.fixture
def ctx(deployment_tenant: Tenant) -> Any:
    with tenant_context(deployment_tenant):
        yield deployment_tenant


@pytest.fixture
def tee(deployment_tenant: Tenant) -> ProductVariant:
    return first_variant(deployment_tenant, simple_product(deployment_tenant, publish=True))


@pytest.fixture
def phone(deployment_tenant: Tenant) -> ProductVariant:
    product = simple_product(deployment_tenant, sku="PHONE-1", name="Phone", tracking_mode="serial")
    return first_variant(deployment_tenant, product)


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
        publish=True,
    )
    return first_variant(deployment_tenant, product)


def main() -> StockLocation:
    return inventory.default_location()


def shop(code: str = "SHOP") -> StockLocation:
    existing = StockLocation.objects.filter(code=code).first()
    return existing or inventory.save_location(
        location=None, data={"code": code, "name": code.title()}, actor=None
    )


def batch(code: str, days: int = 5) -> inventory.BatchInput:
    return inventory.BatchInput(
        code=code, expires_on=inventory.store_today() + timedelta(days=days)
    )


def expire(*codes: str, days_ago: int = 1) -> None:
    Batch.objects.filter(code__in=codes).update(
        expires_on=inventory.store_today() - timedelta(days=days_ago)
    )


# --- Finding 01: idempotency request identity -----------------------------------------------------


def test_identical_retries_replay_without_new_effects(ctx: Tenant, tee: ProductVariant) -> None:
    first = inventory.receive(variant=tee, location=main(), quantity=10, request=req("rcpt-0001"))
    events = AuditEvent.objects.count()
    again = inventory.receive(
        variant=tee, location=main(), quantity=Decimal("10.000"), request=req("rcpt-0001")
    )

    assert (again.replayed, again.operation_id) == (True, first.operation_id)
    assert StockLevel.objects.get(variant=tee).on_hand == Decimal(10)
    assert StockMovement.objects.filter(variant=tee).count() == 1
    assert AuditEvent.objects.count() == events


def conflict(action: Callable[[], Any]) -> None:
    with pytest.raises(ConflictError) as excinfo:
        action()
    assert excinfo.value.get_codes() == "idempotency_conflict"


def test_a_different_receipt_quantity_is_a_conflict(ctx: Tenant, tee: ProductVariant) -> None:
    inventory.receive(variant=tee, location=main(), quantity=10, request=req("rcpt-0001"))
    conflict(
        lambda: inventory.receive(
            variant=tee, location=main(), quantity=25, request=req("rcpt-0001")
        )
    )
    assert StockLevel.objects.get(variant=tee).on_hand == Decimal(10)


def test_different_references_are_a_conflict(ctx: Tenant, tee: ProductVariant) -> None:
    key = "po-line-0001"
    inventory.receive(
        variant=tee, location=main(), quantity=1, request=req(key, reference_id="PO-1")
    )
    conflict(
        lambda: inventory.receive(
            variant=tee, location=main(), quantity=1, request=req(key, reference_id="PO-2")
        )
    )
    # The free-text reason is a description, not part of the request identity.
    retried = inventory.receive(
        variant=tee,
        location=main(),
        quantity=1,
        request=inventory.StockRequest(reason="reworded", idempotency_key=key, reference_id="PO-1"),
    )
    assert retried.replayed


def test_a_different_batch_is_a_conflict(ctx: Tenant, milk: ProductVariant) -> None:
    inventory.receive(
        variant=milk, location=main(), quantity=3, batch=batch("LOT-A"), request=req("milk-0001")
    )
    conflict(
        lambda: inventory.receive(
            variant=milk,
            location=main(),
            quantity=3,
            batch=batch("LOT-B"),
            request=req("milk-0001"),
        )
    )
    conflict(
        lambda: inventory.receive(
            variant=milk,
            location=main(),
            quantity=3,
            batch=batch("LOT-A", days=9),
            request=req("milk-0001"),
        )
    )
    assert inventory.receive(
        variant=milk, location=main(), quantity=3, batch=batch("LOT-A"), request=req("milk-0001")
    ).replayed


def test_serials_are_compared_as_a_normalised_set(ctx: Tenant, phone: ProductVariant) -> None:
    inventory.receive(
        variant=phone,
        location=main(),
        quantity=2,
        serials=["IMEI-1", "IMEI-2"],
        request=req("ph-0001"),
    )
    replay = inventory.receive(
        variant=phone,
        location=main(),
        quantity=2,
        serials=[" imei-2", "imei-1 "],
        request=req("ph-0001"),
    )
    assert replay.replayed
    conflict(
        lambda: inventory.receive(
            variant=phone,
            location=main(),
            quantity=2,
            serials=["IMEI-1", "IMEI-3"],
            request=req("ph-0001"),
        )
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"movement_type": "damaged", "quantity": 1}, {"movement_type": "damaged", "quantity": 2}),
        ({"movement_type": "damaged", "quantity": 1}, {"movement_type": "lost", "quantity": 1}),
        (
            {"movement_type": "adjustment", "quantity": -1},
            {"movement_type": "adjustment", "quantity": 1},
        ),
    ],
)
def test_different_adjustments_are_a_conflict(
    ctx: Tenant, tee: ProductVariant, first: dict[str, Any], second: dict[str, Any]
) -> None:
    inventory.receive(variant=tee, location=main(), quantity=5, request=req())
    inventory.adjust(variant=tee, location=main(), request=req("adj-0001"), **first)
    conflict(
        lambda: inventory.adjust(variant=tee, location=main(), request=req("adj-0001"), **second)
    )
    assert StockLevel.objects.get(variant=tee).on_hand == Decimal(5) + (
        first["quantity"] if first["movement_type"] == "adjustment" else -first["quantity"]
    )


@pytest.mark.parametrize("change", ["destination", "quantity"])
def test_different_transfers_are_a_conflict(ctx: Tenant, tee: ProductVariant, change: str) -> None:
    source, destination, other = main(), shop("SHOP"), shop("KIOSK")
    inventory.receive(variant=tee, location=source, quantity=5, request=req())
    inventory.transfer(
        variant=tee, source=source, destination=destination, quantity=2, request=req("move-0001")
    )
    conflict(
        lambda: inventory.transfer(
            variant=tee,
            source=source,
            destination=other if change == "destination" else destination,
            quantity=3 if change == "quantity" else 2,
            request=req("move-0001"),
        )
    )
    levels = dict(StockLevel.objects.values_list("location__code", "on_hand"))
    assert levels == {"MAIN": Decimal(3), "SHOP": Decimal(2)}


def test_a_different_reversal_target_is_a_conflict(ctx: Tenant, tee: ProductVariant) -> None:
    one = inventory.receive(variant=tee, location=main(), quantity=2, request=req()).movements[0]
    two = inventory.receive(variant=tee, location=main(), quantity=3, request=req()).movements[0]
    inventory.reverse(movement=one, request=req("undo-0001"))
    conflict(lambda: inventory.reverse(movement=two, request=req("undo-0001")))
    assert StockLevel.objects.get(variant=tee).on_hand == Decimal(3)


@pytest.mark.parametrize("operation", ["reserve", "release", "fulfil"])
def test_different_reservation_quantities_are_a_conflict(
    ctx: Tenant, tee: ProductVariant, operation: str
) -> None:
    inventory.receive(variant=tee, location=main(), quantity=10, request=req())
    inventory.reserve(variant=tee, location=main(), quantity=6, request=req())
    act = getattr(inventory, operation)
    act(variant=tee, location=main(), quantity=1, request=req("order-0001"))
    conflict(lambda: act(variant=tee, location=main(), quantity=2, request=req("order-0001")))


def test_keys_are_independent_per_store(
    deployment_tenant: Tenant, tenant_b: Tenant, tee: ProductVariant
) -> None:
    make_store_settings(tenant_b)
    other = first_variant(tenant_b, simple_product(tenant_b, sku="OTHER-1"))
    with tenant_context(deployment_tenant):
        mine = inventory.receive(
            variant=tee, location=main(), quantity=4, request=req("shared-key-1")
        )
    with tenant_context(tenant_b):
        theirs = inventory.receive(
            variant=other, location=main(), quantity=9, request=req("shared-key-1")
        )
        assert not theirs.replayed
        assert StockLevel.objects.get().on_hand == Decimal(9)
    assert mine.operation_id != theirs.operation_id


def test_a_failed_request_does_not_consume_its_key(ctx: Tenant, tee: ProductVariant) -> None:
    with pytest.raises(inventory.InsufficientStock):
        inventory.adjust(
            variant=tee, location=main(), quantity=2, movement_type="lost", request=req("loss-0001")
        )
    assert not StockMovement.objects.filter(idempotency_key="loss-0001").exists()
    inventory.receive(variant=tee, location=main(), quantity=5, request=req())
    applied = inventory.adjust(
        variant=tee, location=main(), quantity=2, movement_type="lost", request=req("loss-0001")
    )
    assert not applied.replayed
    assert StockLevel.objects.get(variant=tee).on_hand == Decimal(3)


def test_keys_recorded_before_fingerprints_are_verified_from_the_ledger(
    ctx: Tenant, tee: ProductVariant
) -> None:
    """A Phase 04 movement carries its key but no fingerprint: its recorded effects decide."""
    StockMovement.objects.create(
        operation_id=uuid7(),
        movement_type="receipt",
        variant=tee,
        location=main(),
        on_hand_delta=5,
        on_hand_after=5,
        reserved_after=0,
        idempotency_key="legacy-rcpt-1",
    )
    replay = inventory.receive(
        variant=tee, location=main(), quantity=5, request=req("legacy-rcpt-1")
    )
    assert replay.replayed
    conflict(
        lambda: inventory.receive(
            variant=tee, location=main(), quantity=6, request=req("legacy-rcpt-1")
        )
    )


def test_conflicting_keys_answer_409_over_the_api(
    api: Api, deployment_tenant: Tenant, tee: ProductVariant
) -> None:
    from commerce.staff.permissions import StaffRole

    from .catalog_helpers import sign_in_staff

    sign_in_staff(api, deployment_tenant, StaffRole.OWNER, "owner@example.com")
    body = {"variant": str(tee.pk), "quantity": "10", "idempotency_key": "api-receipt-1"}
    assert api.post("/api/manage/inventory/receipts/", body).status_code == 201
    assert api.post("/api/manage/inventory/receipts/", body).status_code == 200
    response = api.post("/api/manage/inventory/receipts/", {**body, "quantity": "25"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


# --- Finding 01: concurrency ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_conflicting_requests_apply_exactly_one(
    deployment_tenant: Tenant, tee: ProductVariant
) -> None:
    with tenant_context(deployment_tenant):
        location = main()

    results = run_concurrently(
        deployment_tenant,
        2,
        lambda index: inventory.receive(
            variant=tee,
            location=location,
            quantity=10 if index == 0 else 25,
            request=req("race-0001"),
        ),
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ConflictError)
    with tenant_context(deployment_tenant):
        assert (
            StockLevel.objects.get(variant=tee).on_hand == successes[0].movements[0].on_hand_delta
        )
        assert StockMovement.objects.filter(variant=tee).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_conflicting_transfers_move_stock_once(
    deployment_tenant: Tenant, tee: ProductVariant
) -> None:
    with tenant_context(deployment_tenant):
        source, first, second = main(), shop("SHOP"), shop("KIOSK")
        inventory.receive(variant=tee, location=source, quantity=10, request=req())

    results = run_concurrently(
        deployment_tenant,
        2,
        lambda index: inventory.transfer(
            variant=tee,
            source=source,
            destination=first if index == 0 else second,
            quantity=4,
            request=req("race-move-1"),
        ),
    )

    assert sum(1 for r in results if not isinstance(r, Exception)) == 1
    assert all(isinstance(r, ConflictError) for r in results if isinstance(r, Exception))
    with tenant_context(deployment_tenant):
        levels = {
            code: on_hand
            for code, on_hand in StockLevel.objects.values_list("location__code", "on_hand")
            if on_hand
        }
        assert levels in (
            {"MAIN": Decimal(6), "SHOP": Decimal(4)},
            {"MAIN": Decimal(6), "KIOSK": Decimal(4)},
        )
        assert inventory.reconcile() == []


@pytest.mark.django_db(transaction=True)
def test_concurrent_retries_on_different_variants_keep_one_owner_per_key(
    deployment_tenant: Tenant, tee: ProductVariant
) -> None:
    other = first_variant(
        deployment_tenant, simple_product(deployment_tenant, sku="OTHER-1", name="Other")
    )
    with tenant_context(deployment_tenant):
        location = main()
    variants = [tee, other]

    results = run_concurrently(
        deployment_tenant,
        2,
        lambda index: inventory.receive(
            variant=variants[index], location=location, quantity=1, request=req("cross-variant-1")
        ),
    )

    assert sum(1 for r in results if not isinstance(r, Exception)) == 1
    assert all(isinstance(r, ConflictError) for r in results if isinstance(r, Exception))
    with tenant_context(deployment_tenant):
        assert StockMovement.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_identical_requests_in_two_stores_each_apply_once(
    deployment_tenant: Tenant, tenant_b: Tenant, tee: ProductVariant
) -> None:
    make_store_settings(tenant_b)
    other = first_variant(tenant_b, simple_product(tenant_b, sku="OTHER-1"))
    with tenant_context(deployment_tenant):
        mine = main()
    with tenant_context(tenant_b):
        theirs = main()

    def receive(index: int) -> inventory.Operation:
        tenant, variant, location = (
            (deployment_tenant, tee, mine) if index % 2 else (tenant_b, other, theirs)
        )
        with tenant_context(tenant):
            return inventory.receive(
                variant=variant, location=location, quantity=2, request=req("same-key-0001")
            )

    barrier_results: list[Any] = []

    def worker(index: int) -> Any:
        connections.close_all()
        try:
            return receive(index)
        except Exception as exc:
            return exc
        finally:
            connections.close_all()

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        barrier_results = list(pool.map(worker, range(8)))

    assert not [r for r in barrier_results if isinstance(r, Exception)]
    for tenant in (deployment_tenant, tenant_b):
        with tenant_context(tenant):
            assert StockLevel.objects.get().on_hand == Decimal(2)
            assert StockMovement.objects.count() == 1


# --- Finding 02: expiry-aware sellable stock ------------------------------------------------------


def public_variant(api: Api, slug: str) -> dict[str, Any]:
    response = api.get(f"/api/catalog/products/{slug}/")
    assert response.status_code == 200, response.json()
    [variant] = response.json()["variants"]
    return dict(variant)


def listed_in_stock(api: Api) -> bool:
    [row] = api.get("/api/catalog/products/").json()["results"]
    return bool(row["in_stock"])


def test_only_expired_batches_are_not_in_stock(ctx: Tenant, api: Api, milk: ProductVariant) -> None:
    inventory.receive(variant=milk, location=main(), quantity=10, batch=batch("OLD"), request=req())
    expire("OLD")

    assert public_variant(api, "milk")["in_stock"] is False
    assert listed_in_stock(api) is False
    assert catalog.is_purchasable(ProductVariant.objects.get(pk=milk.pk)) is False
    assert StockLevel.objects.get(variant=milk).on_hand == Decimal(10)  # physical stock unchanged
    assert inventory.reconcile() == []


def test_reservations_cannot_take_expired_stock(ctx: Tenant, milk: ProductVariant) -> None:
    inventory.receive(variant=milk, location=main(), quantity=5, batch=batch("OLD"), request=req())
    inventory.receive(variant=milk, location=main(), quantity=4, batch=batch("NEW"), request=req())
    expire("OLD")

    with pytest.raises(inventory.InsufficientStock):
        inventory.reserve(variant=milk, location=main(), quantity=5, request=req())
    inventory.reserve(variant=milk, location=main(), quantity=4, request=req())
    with pytest.raises(inventory.InsufficientStock):
        inventory.reserve(variant=milk, location=main(), quantity=1, request=req())


def test_expiry_follows_the_store_calendar(
    ctx: Tenant, api: Api, milk: ProductVariant, monkeypatch: pytest.MonkeyPatch
) -> None:
    StoreSettings.objects.update(timezone="Asia/Dhaka")  # UTC+6
    inventory.receive(variant=milk, location=main(), quantity=3, batch=batch("EDGE"), request=req())
    Batch.objects.filter(code="EDGE").update(expires_on=datetime(2026, 10, 26, tzinfo=UTC).date())

    # 17:59 UTC is 23:59 in Dhaka on the expiry date: still sellable (inclusive).
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: datetime(2026, 10, 26, 17, 59, tzinfo=UTC)
    )
    assert public_variant(api, "milk")["in_stock"] is True
    # 18:00 UTC is already 27 October in Dhaka, although UTC is still on the 26th.
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: datetime(2026, 10, 26, 18, 0, tzinfo=UTC)
    )
    assert public_variant(api, "milk")["in_stock"] is False


def test_mixed_batches_count_only_unexpired_stock(ctx: Tenant, milk: ProductVariant) -> None:
    inventory.receive(variant=milk, location=main(), quantity=5, batch=batch("A"), request=req())
    inventory.receive(variant=milk, location=main(), quantity=4, batch=batch("B"), request=req())
    expire("A")
    assert inventory.sellable_quantity(milk) == Decimal(4)


def test_availability_spans_active_locations_only(
    ctx: Tenant, api: Api, milk: ProductVariant
) -> None:
    second = shop("SHOP")
    inventory.receive(
        variant=milk, location=main(), quantity=5, batch=batch("MAIN-OLD"), request=req()
    )
    inventory.receive(
        variant=milk, location=second, quantity=3, batch=batch("SHOP-NEW"), request=req()
    )
    expire("MAIN-OLD")
    assert inventory.sellable_quantity(milk) == Decimal(3)
    assert public_variant(api, "milk")["in_stock"] is True

    # An inactive location's stock is never sellable (set directly: the service refuses to
    # deactivate a location that still holds stock).
    StockLocation.objects.filter(pk=second.pk).update(is_active=False)
    assert inventory.sellable_quantity(milk) == Decimal(0)
    assert public_variant(api, "milk")["in_stock"] is False


def test_reservations_reduce_sellable_batch_stock(
    ctx: Tenant, api: Api, milk: ProductVariant
) -> None:
    inventory.receive(variant=milk, location=main(), quantity=5, batch=batch("OLD"), request=req())
    inventory.receive(variant=milk, location=main(), quantity=4, batch=batch("NEW"), request=req())
    inventory.reserve(variant=milk, location=main(), quantity=3, request=req())
    expire("OLD")
    assert inventory.sellable_quantity(milk) == Decimal(1)
    inventory.reserve(variant=milk, location=main(), quantity=1, request=req())
    assert inventory.sellable_quantity(milk) == Decimal(0)
    assert public_variant(api, "milk")["in_stock"] is False
    # Physical availability (on-hand minus reserved) still counts the expired units.
    assert StockLevel.objects.get(variant=milk).available == Decimal(5)


def test_empty_and_undated_batches(ctx: Tenant, deployment_tenant: Tenant) -> None:
    rice = first_variant(
        deployment_tenant,
        simple_product(deployment_tenant, sku="RICE-1", name="Rice", tracking_mode="batch"),
    )
    inventory.receive(
        variant=rice,
        location=main(),
        quantity=2,
        batch=inventory.BatchInput(code="R1"),
        request=req(),
    )
    assert inventory.sellable_quantity(rice) == Decimal(2)  # no expiry date: never expires
    inventory.adjust(
        variant=rice,
        location=main(),
        quantity=2,
        movement_type="damaged",
        batch=inventory.BatchInput(code="R1"),
        request=req(),
    )
    assert inventory.sellable_quantity(rice) == Decimal(0)  # a batch with nothing left


def test_preorders_and_unlimited_variants_stay_orderable(
    ctx: Tenant, api: Api, deployment_tenant: Tenant
) -> None:
    future = inventory.store_today() + timedelta(days=30)
    simple_product(
        deployment_tenant,
        sku="PRE-1",
        name="Preorder",
        variant={"release_date": future},
        publish=True,
    )
    simple_product(
        deployment_tenant,
        sku="EB-1",
        name="Ebook",
        variant={"kind": "digital", "digital_format": "epub"},
        publish=True,
    )
    simple_product(
        deployment_tenant, sku="SVC-1", name="Service", track_inventory=False, publish=True
    )
    for slug in ("preorder", "ebook", "service"):
        assert public_variant(api, slug)["in_stock"] is True
        assert catalog.is_purchasable(ProductVariant.objects.get(product__slug=slug)) is True
    preorder = ProductVariant.objects.get(sku="PRE-1")
    assert inventory.sellable_quantity(preorder) == Decimal(0)  # orderable, but no physical stock


def test_plain_and_serial_stock_are_unchanged(
    ctx: Tenant, api: Api, tee: ProductVariant, phone: ProductVariant
) -> None:
    assert public_variant(api, "plain-tee")["in_stock"] is False
    inventory.receive(variant=tee, location=main(), quantity=2, request=req())
    inventory.reserve(variant=tee, location=main(), quantity=1, request=req())
    inventory.receive(variant=phone, location=main(), quantity=1, serials=["SN-9"], request=req())
    sellable = inventory.sellable_quantities([tee, phone])
    assert sellable == {tee.pk: Decimal(1), phone.pk: Decimal(1)}
    assert public_variant(api, "plain-tee")["in_stock"] is True
    assert catalog.is_purchasable(ProductVariant.objects.get(pk=tee.pk)) is True


def test_list_and_detail_agree_after_expiry(ctx: Tenant, api: Api, milk: ProductVariant) -> None:
    inventory.receive(
        variant=milk, location=main(), quantity=3, batch=batch("L1", days=1), request=req()
    )
    assert listed_in_stock(api) is True
    assert public_variant(api, "milk")["in_stock"] is True
    expire("L1")
    assert listed_in_stock(api) is False
    assert public_variant(api, "milk")["in_stock"] is False
    assert inventory.reconcile() == []  # expiry never changes the ledger


def test_fingerprints_are_stored_once_per_keyed_operation(ctx: Tenant, tee: ProductVariant) -> None:
    source, destination = main(), shop("SHOP")
    inventory.receive(variant=tee, location=source, quantity=4, request=req())
    moved = inventory.transfer(
        variant=tee, source=source, destination=destination, quantity=2, request=req("fp-move-1")
    )
    keyed = StockMovement.objects.get(operation_id=moved.operation_id, idempotency_key="fp-move-1")
    assert re.fullmatch(r"[0-9a-f]{64}", keyed.request_fingerprint or "")
    other_leg = StockMovement.objects.get(
        operation_id=moved.operation_id, idempotency_key__isnull=True
    )
    assert other_leg.request_fingerprint is None
    unkeyed = StockMovement.objects.filter(idempotency_key__isnull=True)
    assert not unkeyed.exclude(request_fingerprint__isnull=True).exists()

    # Reversing a transfer through either leg is the same request.
    inventory.reverse(movement=moved.movements[0], request=req("fp-undo-1"))
    assert inventory.reverse(movement=moved.movements[1], request=req("fp-undo-1")).replayed


def test_reserved_unexpired_stock_cannot_be_moved_or_written_off(
    ctx: Tenant, milk: ProductVariant
) -> None:
    second = shop("SHOP")
    inventory.receive(variant=milk, location=main(), quantity=5, batch=batch("OLD"), request=req())
    inventory.receive(variant=milk, location=main(), quantity=4, batch=batch("NEW"), request=req())
    inventory.reserve(variant=milk, location=main(), quantity=4, request=req())
    expire("OLD")

    with pytest.raises(inventory.InsufficientStock):
        inventory.transfer(
            variant=milk,
            source=main(),
            destination=second,
            quantity=1,
            batch_code="NEW",
            request=req(),
        )
    with pytest.raises(inventory.InsufficientStock):
        inventory.adjust(
            variant=milk,
            location=main(),
            quantity=1,
            movement_type="lost",
            batch=inventory.BatchInput(code="NEW"),
            request=req(),
        )
    # Expired stock is not promised to anyone: it can be written off or moved.
    inventory.adjust(
        variant=milk,
        location=main(),
        quantity=5,
        movement_type="damaged",
        batch=inventory.BatchInput(code="OLD"),
        request=inventory.StockRequest(reason="expired"),
    )
    assert StockLevel.objects.get(variant=milk, location=main()).on_hand == Decimal(4)
    assert inventory.reconcile() == []
