"""Concurrent stock operations against real PostgreSQL: committed transactions in parallel threads.

These prove the locking design: no lost updates, no overselling, retries applied once, and
opposite transfers that cannot deadlock.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from django.db import connections

from commerce.catalog.models import ProductVariant
from commerce.inventory import services as inventory
from commerce.inventory.models import StockLevel, StockLocation, StockMovement
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, simple_product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("store")]

WORKERS = 8


def run_concurrently(tenant: Tenant, count: int, action: Callable[[int], Any]) -> list[Any]:
    """Start ``count`` calls of ``action`` together; each returns its result or exception."""
    barrier = threading.Barrier(count)

    def worker(index: int) -> Any:
        connections.close_all()
        try:
            with tenant_context(tenant):
                barrier.wait(timeout=10)
                return action(index)
        except Exception as exc:  # collected and asserted by the test
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(worker, range(count)))


@pytest.fixture
def variant(deployment_tenant: Tenant) -> ProductVariant:
    return first_variant(deployment_tenant, simple_product(deployment_tenant))


@pytest.fixture
def main(deployment_tenant: Tenant) -> StockLocation:
    with tenant_context(deployment_tenant):
        return inventory.default_location()


def request(**kwargs: Any) -> inventory.StockRequest:
    return inventory.StockRequest(reason="concurrency", **kwargs)


def test_concurrent_receipts_lose_no_updates(
    deployment_tenant: Tenant, variant: ProductVariant, main: StockLocation
) -> None:
    results = run_concurrently(
        deployment_tenant,
        WORKERS,
        lambda _: inventory.receive(variant=variant, location=main, quantity=1, request=request()),
    )

    assert not [r for r in results if isinstance(r, Exception)]
    with tenant_context(deployment_tenant):
        assert StockLevel.objects.get(variant=variant).on_hand == WORKERS
        balances = sorted(
            StockMovement.objects.filter(variant=variant).values_list("on_hand_after", flat=True)
        )
        assert balances == list(range(1, WORKERS + 1))  # a gap-free sequence: no lost update
        assert inventory.reconcile() == []


def test_concurrent_reservations_never_oversell(
    deployment_tenant: Tenant, variant: ProductVariant, main: StockLocation
) -> None:
    with tenant_context(deployment_tenant):
        inventory.receive(variant=variant, location=main, quantity=5, request=request())

    results = run_concurrently(
        deployment_tenant,
        WORKERS,
        lambda _: inventory.reserve(variant=variant, location=main, quantity=1, request=request()),
    )

    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == WORKERS - 5
    assert all(isinstance(r, inventory.InsufficientStock) for r in failures)
    with tenant_context(deployment_tenant):
        stock = StockLevel.objects.get(variant=variant)
        assert (stock.on_hand, stock.reserved, stock.available) == (
            Decimal(5),
            Decimal(5),
            Decimal(0),
        )
        assert inventory.reconcile() == []


def test_concurrent_decrements_stop_at_zero(
    deployment_tenant: Tenant, variant: ProductVariant, main: StockLocation
) -> None:
    with tenant_context(deployment_tenant):
        inventory.receive(variant=variant, location=main, quantity=3, request=request())

    results = run_concurrently(
        deployment_tenant,
        WORKERS,
        lambda _: inventory.adjust(
            variant=variant, location=main, quantity=1, movement_type="damaged", request=request()
        ),
    )

    assert sum(1 for r in results if not isinstance(r, Exception)) == 3
    with tenant_context(deployment_tenant):
        assert StockLevel.objects.get(variant=variant).on_hand == 0


def test_concurrent_retries_of_one_request_apply_once(
    deployment_tenant: Tenant, variant: ProductVariant, main: StockLocation
) -> None:
    results = run_concurrently(
        deployment_tenant,
        WORKERS,
        lambda _: inventory.receive(
            variant=variant,
            location=main,
            quantity=4,
            request=request(idempotency_key="supplier-delivery-0042"),
        ),
    )

    assert not [r for r in results if isinstance(r, Exception)]
    assert len({r.operation_id for r in results}) == 1
    assert sum(1 for r in results if not r.replayed) == 1
    with tenant_context(deployment_tenant):
        assert StockLevel.objects.get(variant=variant).on_hand == 4
        assert StockMovement.objects.filter(variant=variant).count() == 1


def test_opposite_transfers_do_not_deadlock(
    deployment_tenant: Tenant, variant: ProductVariant, main: StockLocation
) -> None:
    with tenant_context(deployment_tenant):
        shop = inventory.save_location(
            location=None, data={"code": "SHOP", "name": "Shop"}, actor=None
        )
        for location in (main, shop):
            inventory.receive(variant=variant, location=location, quantity=10, request=request())

    def move(index: int) -> inventory.Operation:
        source, destination = (main, shop) if index % 2 else (shop, main)
        return inventory.transfer(
            variant=variant, source=source, destination=destination, quantity=1, request=request()
        )

    results = run_concurrently(deployment_tenant, WORKERS, move)

    assert not [r for r in results if isinstance(r, Exception)]
    with tenant_context(deployment_tenant):
        levels = dict(StockLevel.objects.values_list("location__code", "on_hand"))
        assert levels == {"MAIN": 10, "SHOP": 10}
        assert inventory.reconcile() == []
