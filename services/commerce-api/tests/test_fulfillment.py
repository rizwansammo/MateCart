import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.fulfillment import services as fulfillment_services
from commerce.fulfillment.models import (
    CourierBooking,
    CourierCallback,
    CourierCodRecord,
    FraudAssessment,
    FraudFlag,
    FraudReview,
    Shipment,
    ShipmentItem,
    TrackingEvent,
)
from commerce.fulfillment.providers import reset_provider_cache
from commerce.inventory.models import StockLevel, StockMovement
from commerce.orders.models import Order, OrderAllocation, PaymentIntent, PaymentTransaction
from commerce.orders.providers import reset_provider_cache as reset_payment_provider_cache
from commerce.stores.modules import set_module
from commerce.tenancy.context import tenant_context

from .fakes import FakeCourierProvider, FakePaymentProvider
from .test_checkout import add, product
from .test_orders import callback, digital, digital_checkout, merchant, place, reviewed, stock

pytestmark = pytest.mark.django_db


def confirmed_order(store: Any, *, quantity: int = 4) -> tuple[APIClient, APIClient, Any, Any]:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, quantity)
    customer = APIClient()
    placed = place(customer, reviewed(customer, variant), key=f"place-{uuid.uuid4().hex}")
    assert placed.status_code == 201, placed.data
    staff = merchant(store.tenant)
    confirmed = staff.post(
        f"/api/manage/orders/{placed.data['id']}/transition/",
        {"status": "confirmed"},
        format="json",
    )
    assert confirmed.status_code == 200, confirmed.data
    return customer, staff, variant, placed


def prepare(staff: APIClient, order_id: str) -> Any:
    response = staff.post(f"/api/manage/fulfillment/orders/{order_id}/shipment/", {}, format="json")
    assert response.status_code in (200, 201), response.data
    return response


def move(staff: APIClient, shipment_id: str, target: str, **extra: Any) -> Any:
    return staff.post(
        f"/api/manage/fulfillment/shipments/{shipment_id}/transition/",
        {"status": target, **extra},
        format="json",
    )


@pytest.fixture
def fakecourier(settings: Settings, store: Any) -> type[FakeCourierProvider]:
    settings.MATECART_COURIER_PROVIDER = "tests.fakes.FakeCourierProvider"
    reset_provider_cache()
    FakeCourierProvider.reset()
    with tenant_context(store.tenant):
        set_module(key="courier_booking", enabled=True, actor=None)
    return FakeCourierProvider


@pytest.fixture
def fakepay(settings: Settings, store: Any) -> type[FakePaymentProvider]:
    settings.MATECART_PAYMENT_PROVIDER = "tests.fakes.FakePaymentProvider"
    reset_payment_provider_cache()
    FakePaymentProvider.reset()
    with tenant_context(store.tenant):
        set_module(key="online_payments", enabled=True, actor=None)
    return FakePaymentProvider


def test_manual_shipment_dispatches_stock_once_and_is_customer_visible(store: Any) -> None:
    customer, staff, variant, placed = confirmed_order(store)
    created = prepare(staff, placed.data["id"])
    shipment_id = created.data["id"]

    details = staff.post(
        f"/api/manage/fulfillment/shipments/{shipment_id}/courier-details/",
        {
            "courier_name": "Dhaka Local Delivery",
            "tracking_number": "LOCAL-2026-001",
        },
        format="json",
    )
    invalid = move(staff, shipment_id, "packed")
    assert details.status_code == 200
    assert invalid.status_code == 409
    for target in ("picking", "packed", "ready_for_dispatch"):
        assert move(staff, shipment_id, target).status_code == 200
    dispatched = move(staff, shipment_id, "dispatched")
    replay = move(staff, shipment_id, "dispatched")

    assert dispatched.status_code == 200
    assert replay.status_code == 200
    public = customer.get(f"/api/orders/{placed.data['id']}/").data["shipment"]
    assert {key: value for key, value in public.items() if key != "events"} == {
        "reference": created.data["reference"],
        "status": "dispatched",
        "courier_name": "Dhaka Local Delivery",
        "tracking_number": "LOCAL-2026-001",
        "tracking_url": "",
        "dispatched_at": dispatched.data["dispatched_at"],
        "delivered_at": None,
        "failed_at": None,
    }
    assert [event["status"] for event in public["events"]] == [
        "ready",
        "picking",
        "packed",
        "ready_for_dispatch",
        "dispatched",
    ]
    with tenant_context(store.tenant):
        level = StockLevel.objects.get(variant=variant)
        assert level.on_hand == Decimal(3)
        assert level.reserved == 0
        assert StockMovement.objects.filter(movement_type="fulfilled").count() == 1


def test_cancelled_and_digital_orders_do_not_create_shipments(
    store: Any, api_client: APIClient, fakepay: type[Any]
) -> None:
    physical = product(store.tenant, tracked=True)
    stock(store.tenant, physical, 1)
    placed = place(api_client, reviewed(api_client, physical))
    cancelled_order = api_client.post(f"/api/orders/{placed.data['id']}/cancel/", {}, format="json")
    assert cancelled_order.status_code == 200
    staff = merchant(store.tenant)
    cancelled = staff.post(
        f"/api/manage/fulfillment/orders/{placed.data['id']}/shipment/", {}, format="json"
    )

    digital_variant = digital(store.tenant)
    digital_client = APIClient()
    digital_placed = place(
        digital_client, digital_checkout(digital_client, digital_variant), "online"
    )
    digital_client.post(f"/api/orders/{digital_placed.data['id']}/payment/", {}, format="json")
    with tenant_context(store.tenant):
        intent = PaymentIntent.objects.get(order_id=digital_placed.data["id"])
    callback(APIClient(), intent)
    no_physical = staff.post(
        f"/api/manage/fulfillment/orders/{digital_placed.data['id']}/shipment/",
        {},
        format="json",
    )

    assert cancelled.status_code == 409
    assert no_physical.status_code == 409
    assert no_physical.data["error"]["code"] == "shipment_not_required"
    with tenant_context(store.tenant):
        assert Shipment.objects.count() == 0


def test_shipment_cancellation_requires_full_order_cancellation_and_releases_once(
    store: Any,
) -> None:
    _customer, staff, variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])

    standalone = move(staff, shipment.data["id"], "cancelled")
    assert standalone.status_code == 409
    assert standalone.data["error"]["code"] == "cancel_order_instead"
    with tenant_context(store.tenant):
        assert Order.objects.get(pk=placed.data["id"]).status == "confirmed"
        assert Shipment.objects.get(pk=shipment.data["id"]).status == "ready"
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(1)
        assert not StockMovement.objects.filter(movement_type="released").exists()

    order_url = f"/api/manage/orders/{placed.data['id']}/transition/"
    cancelled = staff.post(order_url, {"status": "cancelled"}, format="json")
    replay = staff.post(order_url, {"status": "cancelled"}, format="json")

    assert cancelled.status_code == replay.status_code == 200
    with tenant_context(store.tenant):
        assert Order.objects.get(pk=placed.data["id"]).status == "cancelled"
        assert Shipment.objects.get(pk=shipment.data["id"]).status == "cancelled"
        assert StockLevel.objects.get(variant=variant).reserved == 0
        assert OrderAllocation.objects.get(order_id=placed.data["id"]).status == "released"
        assert StockMovement.objects.filter(movement_type="released").count() == 1
        assert TrackingEvent.objects.filter(status="cancelled").count() == 1


def test_order_cancellation_failure_rolls_back_shipment_and_reservation(
    store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _customer, staff, variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])

    def fail_tracking(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("injected tracking failure")

    with monkeypatch.context() as patch:
        patch.setattr(fulfillment_services, "_tracking_event", fail_tracking)
        with pytest.raises(RuntimeError, match="injected tracking failure"):
            staff.post(
                f"/api/manage/orders/{placed.data['id']}/transition/",
                {"status": "cancelled"},
                format="json",
            )

    with tenant_context(store.tenant):
        assert Order.objects.get(pk=placed.data["id"]).status == "confirmed"
        assert Shipment.objects.get(pk=shipment.data["id"]).status == "ready"
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(1)
        assert OrderAllocation.objects.get(order_id=placed.data["id"]).status == "allocated"
        assert not StockMovement.objects.filter(movement_type="released").exists()


def test_mixed_order_ships_only_physical_items(
    store: Any, fakepay: type[FakePaymentProvider]
) -> None:
    client = APIClient()
    physical = product(store.tenant, tracked=True)
    stock(store.tenant, physical, 1)
    digital_variant = digital(store.tenant)
    assert add(client, physical).status_code == 200
    assert add(client, digital_variant).status_code == 200
    created = client.post("/api/checkout/", {}, format="json")
    checkout_id = created.data["id"]
    details = client.patch(
        f"/api/checkout/{checkout_id}/",
        {
            "contact_email": "mixed@example.test",
            "recipient_name": "Mixed Buyer",
            "address_line_1": "1 Mixed Street",
            "city": "Dhaka",
            "country": "BD",
        },
        format="json",
    )
    selected = client.patch(
        f"/api/checkout/{checkout_id}/",
        {"shipping_method": details.data["shipping_methods"][0]["id"]},
        format="json",
    )
    assert selected.status_code == 200
    assert (
        client.post(
            f"/api/checkout/{checkout_id}/review/",
            {"idempotency_key": "mixed-review-key"},
            format="json",
        ).status_code
        == 200
    )
    placed = place(client, checkout_id, "online", "mixed-place-key")
    client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    with tenant_context(store.tenant):
        intent = PaymentIntent.objects.get(order_id=placed.data["id"])
    callback(APIClient(), intent)
    staff = merchant(store.tenant)
    shipment = prepare(staff, placed.data["id"])

    assert len(shipment.data["items"]) == 1
    assert shipment.data["items"][0]["sku"] == physical.sku
    with tenant_context(store.tenant):
        assert ShipmentItem.objects.count() == 1


def test_unconfigured_provider_cannot_create_a_booking(store: Any) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    response = staff.post(
        f"/api/manage/fulfillment/shipments/{shipment.data['id']}/courier-booking/",
        {"idempotency_key": "booking-unconfigured"},
        format="json",
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "courier_unavailable"
    with tenant_context(store.tenant):
        assert CourierBooking.objects.count() == 0


def test_courier_booking_and_signed_tracking_callback_are_idempotent(
    store: Any, fakecourier: type[FakeCourierProvider]
) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    booking_url = f"/api/manage/fulfillment/shipments/{shipment.data['id']}/courier-booking/"
    body = {"idempotency_key": "booking-idempotent-001"}
    booked = staff.post(booking_url, body, format="json")
    replay = staff.post(booking_url, body, format="json")
    assert booked.status_code == replay.status_code == 200
    assert booked.data["id"] == replay.data["id"]
    assert len(fakecourier.booked) == 1

    for target in ("picking", "packed", "ready_for_dispatch", "dispatched"):
        assert move(staff, shipment.data["id"], target).status_code == 200
    payload = {
        "event_id": "tracking-event-001",
        "booking_reference": booked.data["provider_reference"],
        "status": "in_transit",
        "occurred_at": timezone.now().isoformat(),
        "note": "At delivery hub",
    }
    raw, signature = fakecourier.signed(payload)
    url = "/api/couriers/callbacks/fakecourier/tracking/"
    applied = APIClient().post(
        url,
        raw,
        content_type="application/json",
        HTTP_X_FAKE_COURIER_SIGNATURE=signature,
    )
    duplicate = APIClient().post(
        url,
        raw,
        content_type="application/json",
        HTTP_X_FAKE_COURIER_SIGNATURE=signature,
    )
    forged = APIClient().post(
        url,
        raw,
        content_type="application/json",
        HTTP_X_FAKE_COURIER_SIGNATURE="forged",
    )

    assert applied.data == {"outcome": "applied"}
    assert duplicate.data == {"outcome": "duplicate"}
    assert forged.status_code == 403
    with tenant_context(store.tenant):
        assert Shipment.objects.get().status == "in_transit"
        assert CourierCallback.objects.count() == 1
        assert TrackingEvent.objects.filter(status="in_transit").count() == 1


def test_tracking_callback_failure_rolls_back_deduplication_and_retries(
    store: Any,
    fakecourier: type[FakeCourierProvider],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    booked = staff.post(
        f"/api/manage/fulfillment/shipments/{shipment.data['id']}/courier-booking/",
        {"idempotency_key": "callback-rollback-booking"},
        format="json",
    )
    for target in ("picking", "packed", "ready_for_dispatch", "dispatched"):
        assert move(staff, shipment.data["id"], target).status_code == 200
    payload = {
        "event_id": "tracking-event-retryable",
        "booking_reference": booked.data["provider_reference"],
        "status": "in_transit",
        "occurred_at": timezone.now().isoformat(),
        "note": "Retry after injected failure",
    }
    raw, signature = fakecourier.signed(payload)
    headers = {"X-Fake-Courier-Signature": signature}

    def fail_transition(**_kwargs: Any) -> Any:
        raise RuntimeError("injected callback failure")

    with tenant_context(store.tenant):
        with monkeypatch.context() as patch:
            patch.setattr(fulfillment_services, "transition_shipment", fail_transition)
            with pytest.raises(RuntimeError, match="injected callback failure"):
                fulfillment_services.process_tracking_callback(
                    provider_code="fakecourier", body=raw, headers=headers
                )
        assert not CourierCallback.objects.filter(event_id=payload["event_id"]).exists()
        assert Shipment.objects.get(pk=shipment.data["id"]).status == "dispatched"

        assert (
            fulfillment_services.process_tracking_callback(
                provider_code="fakecourier", body=raw, headers=headers
            )
            == "applied"
        )
        assert CourierCallback.objects.get(event_id=payload["event_id"]).outcome == "applied"
        assert Shipment.objects.get(pk=shipment.data["id"]).status == "in_transit"
        assert TrackingEvent.objects.filter(provider_event_id=payload["event_id"]).count() == 1


def test_tracking_callback_records_stale_invalid_and_unknown_outcomes(
    store: Any, fakecourier: type[FakeCourierProvider]
) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    booked = staff.post(
        f"/api/manage/fulfillment/shipments/{shipment.data['id']}/courier-booking/",
        {"idempotency_key": "callback-outcomes-booking"},
        format="json",
    )
    for target in ("picking", "packed", "ready_for_dispatch", "dispatched"):
        assert move(staff, shipment.data["id"], target).status_code == 200

    def callback(event_id: str, reference: str, status: str, occurred_at: Any) -> str:
        raw, signature = fakecourier.signed(
            {
                "event_id": event_id,
                "booking_reference": reference,
                "status": status,
                "occurred_at": occurred_at.isoformat(),
            }
        )
        with tenant_context(store.tenant):
            return fulfillment_services.process_tracking_callback(
                provider_code="fakecourier",
                body=raw,
                headers={"X-Fake-Courier-Signature": signature},
            )

    now = timezone.now()
    assert (
        callback(
            "tracking-stale",
            booked.data["provider_reference"],
            "in_transit",
            now - timedelta(days=1),
        )
        == "stale"
    )
    assert (
        callback("tracking-delivered", booked.data["provider_reference"], "delivered", now)
        == "applied"
    )
    assert (
        callback(
            "tracking-invalid",
            booked.data["provider_reference"],
            "in_transit",
            now + timedelta(seconds=1),
        )
        == "invalid_transition"
    )
    assert (
        callback("tracking-unknown", "missing-reference", "in_transit", now) == "unknown_reference"
    )
    with tenant_context(store.tenant):
        assert Shipment.objects.get(pk=shipment.data["id"]).status == "delivered"
        assert set(CourierCallback.objects.values_list("outcome", flat=True)) == {
            "stale",
            "applied",
            "invalid_transition",
            "unknown_reference",
        }


def test_courier_failure_never_claims_booking_and_safe_retry_recovers(
    store: Any, fakecourier: type[FakeCourierProvider]
) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    url = f"/api/manage/fulfillment/shipments/{shipment.data['id']}/courier-booking/"
    body = {"idempotency_key": "provider-timeout-booking"}
    fakecourier.unavailable = True
    unavailable = staff.post(url, body, format="json")
    assert unavailable.status_code == 503
    with tenant_context(store.tenant):
        booking = CourierBooking.objects.get()
        current = Shipment.objects.get()
        assert booking.status == "pending"
        assert current.tracking_number == ""
        assert current.courier_code == ""

    fakecourier.unavailable = False
    recovered = staff.post(url, body, format="json")
    assert recovered.status_code == 200
    assert recovered.data["status"] == "booked"


def test_booked_courier_can_be_cancelled_before_dispatch(
    store: Any, fakecourier: type[FakeCourierProvider]
) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    base = f"/api/manage/fulfillment/shipments/{shipment.data['id']}"
    booked = staff.post(
        f"{base}/courier-booking/",
        {"idempotency_key": "booking-to-cancel"},
        format="json",
    )
    blocked_order = staff.post(
        f"/api/manage/orders/{placed.data['id']}/transition/",
        {"status": "cancelled"},
        format="json",
    )
    cancelled = staff.post(f"{base}/courier-booking/cancel/", {}, format="json")
    replay = staff.post(f"{base}/courier-booking/cancel/", {}, format="json")
    details = staff.get(f"{base}/")

    assert booked.data["status"] == "booked"
    assert blocked_order.status_code == 409
    assert blocked_order.data["error"]["code"] == "courier_booking_active"
    assert cancelled.data["status"] == "cancelled"
    assert replay.data["id"] == cancelled.data["id"]
    assert details.data["courier_name"] == ""
    assert details.data["tracking_number"] == ""


def test_delivered_cod_stays_unpaid_until_collection_then_reconciles(store: Any) -> None:
    customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    for target in ("picking", "packed", "ready_for_dispatch", "dispatched", "delivered"):
        assert move(staff, shipment.data["id"], target).status_code == 200

    delivered = customer.get(f"/api/orders/{placed.data['id']}/")
    assert delivered.data["payment_status"] == "unpaid"
    amount = Decimal(delivered.data["grand_total"])
    collection = staff.post(
        f"/api/manage/fulfillment/shipments/{shipment.data['id']}/cod-collection/",
        {
            "collected_amount": str(amount),
            "courier_fees": "10.0000",
            "deductions": "5.0000",
            "deduction_note": "Documented handling deduction",
            "collection_reference": "COLL-001",
        },
        format="json",
    )
    assert collection.status_code == 201, collection.data
    expected = amount - Decimal(15)
    assert Decimal(collection.data["expected_remittance"]) == expected
    remittance = staff.post(
        f"/api/manage/fulfillment/cod-records/{collection.data['id']}/remittance/",
        {
            "actual_remittance": str(expected - Decimal(1)),
            "remittance_reference": "REM-001",
            "remitted_at": timezone.now().isoformat(),
        },
        format="json",
    )
    repeated = staff.post(
        f"/api/manage/fulfillment/cod-records/{collection.data['id']}/remittance/",
        {
            "actual_remittance": str(expected - Decimal(1)),
            "remittance_reference": "REM-001",
            "remitted_at": timezone.now().isoformat(),
        },
        format="json",
    )
    assert remittance.data["status"] == "disputed"
    assert Decimal(remittance.data["outstanding_amount"]) == Decimal(1)
    assert repeated.status_code == 200
    with tenant_context(store.tenant):
        assert PaymentTransaction.objects.filter(kind="cod_collection").count() == 1
        assert Order.objects.get(pk=placed.data["id"]).payment_status == "paid"
        assert CourierCodRecord.objects.count() == 1


def test_fraud_flags_are_explainable_and_review_is_auditable(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 5)
    staff = merchant(store.tenant)
    order_ids: list[str] = []
    for _ in range(4):
        client = APIClient()
        placed = place(client, reviewed(client, variant), key=f"place-{uuid.uuid4().hex}")
        order_ids.append(placed.data["id"])
    assessed = staff.post(
        f"/api/manage/fulfillment/orders/{order_ids[-1]}/fraud-assessment/",
        {},
        format="json",
    )
    assert assessed.status_code == 201
    assert assessed.data["status"] == "pending"
    assert {flag["code"] for flag in assessed.data["flags"]} == {
        "repeated_recent_cod_contact",
        "repeated_same_item_cod",
    }
    assert all("email" not in str(flag["evidence"]) for flag in assessed.data["flags"])

    review_response = staff.post(
        f"/api/manage/fulfillment/fraud-assessments/{assessed.data['id']}/review/",
        {"decision": "clear", "reason": "Customer confirmed the orders by phone."},
        format="json",
    )
    assert review_response.data["status"] == "cleared"
    with tenant_context(store.tenant):
        assert FraudAssessment.objects.count() == 1
        assert FraudFlag.objects.count() == 2
        assert FraudReview.objects.count() == 1
        assert Order.objects.get(pk=order_ids[-1]).status == "placed"


def test_tracking_and_fraud_history_are_database_append_only(store: Any) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    assessed = staff.post(
        f"/api/manage/fulfillment/orders/{placed.data['id']}/fraud-assessment/",
        {},
        format="json",
    )
    review_response = staff.post(
        f"/api/manage/fulfillment/fraud-assessments/{assessed.data['id']}/review/",
        {"decision": "monitor", "reason": "Verify before dispatch."},
        format="json",
    )
    assert review_response.status_code == 200
    with tenant_context(store.tenant):
        checks = (
            TrackingEvent.objects.filter(shipment_id=shipment.data["id"]),
            FraudReview.objects.all(),
        )
        for queryset in checks:
            with pytest.raises(DatabaseError), transaction.atomic():
                queryset.update(occurred_at=timezone.now())


def test_shipment_records_are_tenant_scoped(store: Any, tenant_b: Any) -> None:
    _customer, staff, _variant, placed = confirmed_order(store)
    shipment = prepare(staff, placed.data["id"])
    with tenant_context(tenant_b):
        assert not Shipment.objects.filter(pk=shipment.data["id"]).exists()
