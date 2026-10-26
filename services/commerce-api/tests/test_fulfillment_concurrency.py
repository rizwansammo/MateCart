from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.core.domain import ConflictError
from commerce.fulfillment import services
from commerce.fulfillment.models import CourierBooking, CourierCodRecord, Shipment
from commerce.fulfillment.providers import reset_provider_cache
from commerce.inventory.models import StockLevel, StockMovement
from commerce.orders import services as order_services
from commerce.orders.models import Order
from commerce.staff.models import StaffMembership
from commerce.stores.modules import set_module
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .fakes import FakeCourierProvider
from .test_fulfillment import confirmed_order, move, prepare
from .test_inventory_concurrency import run_concurrently

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("store")]


@pytest.fixture
def fakecourier(settings: Settings, store: Any) -> type[FakeCourierProvider]:
    settings.MATECART_COURIER_PROVIDER = "tests.fakes.FakeCourierProvider"
    reset_provider_cache()
    FakeCourierProvider.reset()
    with tenant_context(store.tenant):
        set_module(key="courier_booking", enabled=True, actor=None)
    return FakeCourierProvider


def ready_shipment(store: Any) -> tuple[APIClient, Any, str]:
    _customer, staff, variant, placed = confirmed_order(store, quantity=2)
    shipment = prepare(staff, placed.data["id"])
    for target in ("picking", "packed", "ready_for_dispatch"):
        assert move(staff, shipment.data["id"], target).status_code == 200
    return staff, variant, shipment.data["id"]


def test_two_staff_dispatches_deduct_stock_once(deployment_tenant: Tenant, store: Any) -> None:
    _staff, variant, shipment_id = ready_shipment(store)
    results = run_concurrently(
        deployment_tenant,
        2,
        lambda _index: services.transition_shipment(
            shipment_id=shipment_id, to_status="dispatched", actor=None
        ),
    )

    assert all(isinstance(result, Shipment) for result in results)
    with tenant_context(deployment_tenant):
        level = StockLevel.objects.get(variant=variant)
        assert level.on_hand == Decimal(1)
        assert level.reserved == 0
        assert StockMovement.objects.filter(movement_type="fulfilled").count() == 1


def test_order_cancellation_and_dispatch_cannot_both_win(
    deployment_tenant: Tenant, store: Any
) -> None:
    _staff, variant, shipment_id = ready_shipment(store)
    with tenant_context(deployment_tenant):
        shipment = Shipment.objects.get(pk=shipment_id)
        order_id = shipment.order_id
        actor = StaffMembership.objects.select_related("user").get().user
    results = run_concurrently(
        deployment_tenant,
        2,
        lambda index: (
            services.transition_shipment(
                shipment_id=shipment_id, to_status="dispatched", actor=actor
            )
            if index == 0
            else order_services.merchant_transition(
                order_id=order_id, to_status="cancelled", actor=actor, reason="merchant"
            )
        ),
    )

    assert any(
        isinstance(result, (ConflictError, services.InvalidShipmentTransition))
        for result in results
    )
    with tenant_context(deployment_tenant):
        order = Order.objects.get(pk=order_id)
        shipment = Shipment.objects.get(pk=shipment_id)
        level = StockLevel.objects.get(variant=variant)
        assert (order.status, shipment.status) in {
            ("cancelled", "cancelled"),
            ("processing", "dispatched"),
        }
        if shipment.status == "dispatched":
            assert level.on_hand == Decimal(1)
            assert StockMovement.objects.filter(movement_type="fulfilled").count() == 1
        else:
            assert level.on_hand == Decimal(2)
            assert StockMovement.objects.filter(movement_type="fulfilled").count() == 0


def test_concurrent_booking_retries_create_one_external_booking(
    deployment_tenant: Tenant,
    store: Any,
    fakecourier: type[FakeCourierProvider],
) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    with tenant_context(deployment_tenant):
        actor = StaffMembership.objects.select_related("user").get().user
    shipment = prepare(staff, placed.data["id"])
    results = run_concurrently(
        deployment_tenant,
        2,
        lambda _index: services.book_courier(
            shipment_id=shipment.data["id"],
            idempotency_key="concurrent-booking-key",
            actor=actor,
        ),
    )

    assert all(isinstance(result, CourierBooking) for result in results)
    with tenant_context(deployment_tenant):
        assert CourierBooking.objects.count() == 1
        assert CourierBooking.objects.get().status == "booked"
    assert len(fakecourier.booked) == 1


def test_concurrent_different_remittances_record_one(deployment_tenant: Tenant, store: Any) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    for target in ("picking", "packed", "ready_for_dispatch", "dispatched", "delivered"):
        assert move(staff, shipment.data["id"], target).status_code == 200
    amount = Decimal(placed.data["grand_total"])
    collection = staff.post(
        f"/api/manage/fulfillment/shipments/{shipment.data['id']}/cod-collection/",
        {
            "collected_amount": str(amount),
            "courier_fees": "5.0000",
            "collection_reference": "CONCURRENT-COLLECTION",
        },
        format="json",
    )
    with tenant_context(deployment_tenant):
        actor = StaffMembership.objects.select_related("user").get().user
    results = run_concurrently(
        deployment_tenant,
        2,
        lambda index: services.reconcile_cod(
            record_id=collection.data["id"],
            actual_remittance=amount - Decimal(5 + index),
            remittance_reference=f"CONCURRENT-REM-{index}",
            remitted_at=timezone.now(),
            actor=actor,
        ),
    )

    assert sum(isinstance(result, CourierCodRecord) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    with tenant_context(deployment_tenant):
        assert CourierCodRecord.objects.get().actual_remittance is not None
