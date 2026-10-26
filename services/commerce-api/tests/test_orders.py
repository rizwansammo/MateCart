import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.catalog.models import Product, ProductVariant
from commerce.checkout import services as checkout_services
from commerce.checkout.models import (
    Cart,
    CartStatus,
    CheckoutSession,
    CheckoutStatus,
    Coupon,
    CouponDiscountType,
    DeliverySlot,
    ShippingMethod,
)
from commerce.checkout.tasks import expire_checkout
from commerce.core.domain import ConflictError
from commerce.identity import sessions
from commerce.identity.models import AccountType, AuthSession
from commerce.inventory import services as inventory
from commerce.inventory.models import (
    MovementType,
    SerialNumber,
    SerialStatus,
    StockLevel,
    StockMovement,
)
from commerce.orders import services
from commerce.orders.models import (
    AllocationStatus,
    CouponRedemption,
    ImmutableRecordError,
    IntentStatus,
    Invoice,
    Order,
    OrderAllocation,
    OrderItem,
    OrderStatusEvent,
    PaymentIntent,
    PaymentTransaction,
    ProviderCallback,
    RedemptionStatus,
)
from commerce.orders.providers import reset_provider_cache
from commerce.staff.models import StaffMembership
from commerce.staff.permissions import StaffRole
from commerce.stores.models import StoreModule
from commerce.stores.modules import set_module
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, set_preset, simple_product
from .factories import make_user
from .fakes import FakePaymentProvider
from .test_checkout import add, prepare_physical_checkout, product

pytestmark = pytest.mark.django_db


# --- Helpers --------------------------------------------------------------------------------------


def stock(tenant: Tenant, variant: ProductVariant, quantity: int, **kwargs: Any) -> None:
    with tenant_context(tenant):
        inventory.receive(
            variant=variant,
            location=inventory.default_location(),
            quantity=quantity,
            request=inventory.StockRequest(reason="order test stock"),
            **kwargs,
        )


def level(tenant: Tenant, variant: ProductVariant) -> StockLevel:
    with tenant_context(tenant):
        return StockLevel.objects.get(variant=variant, location=inventory.default_location())


def review(client: APIClient, checkout_id: str) -> Any:
    return client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": f"review-{uuid.uuid4().hex}"},
        format="json",
    )


def reviewed(client: APIClient, variant: ProductVariant, **kwargs: Any) -> str:
    checkout_id, _ = prepare_physical_checkout(client, variant, **kwargs)
    response = review(client, checkout_id)
    assert response.status_code == 200, response.data
    return checkout_id


def place(
    client: APIClient, checkout_id: str, method: str = "cod", key: str = "place-order-0001"
) -> Any:
    return client.post(
        f"/api/checkout/{checkout_id}/order/",
        {"payment_method": method, "idempotency_key": key},
        format="json",
    )


def merchant(tenant: Tenant, role: str = StaffRole.OWNER) -> APIClient:
    client = APIClient()
    user = make_user(
        tenant,
        account_type=AccountType.STAFF,
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.test",
        password="staff-passphrase-1234",
    )
    with tenant_context(tenant):
        StaffMembership.objects.create(user=user, role=role)
        _, token = sessions.start(user)
    client.cookies["mc_staff"] = token
    return client


def customer_client(tenant: Tenant, email: str) -> APIClient:
    client = APIClient()
    user = make_user(
        tenant,
        account_type=AccountType.CUSTOMER,
        email=email,
        password="customer-passphrase-1234",
    )
    with tenant_context(tenant):
        _, token = sessions.start(user)
    client.cookies["mc_customer"] = token
    return client


def digital(tenant: Tenant) -> ProductVariant:
    variant = product(tenant)
    with tenant_context(tenant):
        variant.kind = "digital"
        variant.digital_format = "pdf"
        variant.save(update_fields=["kind", "digital_format", "updated_at"])
    return variant


def digital_checkout(client: APIClient, variant: ProductVariant) -> str:
    assert add(client, variant).status_code == 200
    checkout_id = client.post("/api/checkout/", {}, format="json").data["id"]
    client.patch(
        f"/api/checkout/{checkout_id}/", {"contact_email": "reader@example.test"}, format="json"
    )
    assert review(client, checkout_id).status_code == 200
    return str(checkout_id)


@pytest.fixture
def fakepay(settings: Settings, store: Any) -> Iterator[type[FakePaymentProvider]]:
    settings.MATECART_PAYMENT_PROVIDER = "tests.fakes.FakePaymentProvider"
    reset_provider_cache()
    FakePaymentProvider.reset()
    with tenant_context(store.tenant):
        set_module(key="online_payments", enabled=True, actor=None)
    yield FakePaymentProvider
    reset_provider_cache()


def callback(
    client: APIClient, intent: PaymentIntent, *, status: str = "succeeded", **overrides: str
) -> Any:
    payload = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "reference": str(intent.pk),
        "provider_ref": intent.provider_ref,
        "transaction_ref": f"txn_{intent.pk}",
        "status": status,
        "amount": f"{intent.amount:.2f}",
        "currency": intent.currency,
        **overrides,
    }
    body, signature = FakePaymentProvider.signed(payload)
    return client.post(
        "/api/payments/callbacks/fakepay/",
        body,
        content_type="application/json",
        HTTP_X_FAKE_SIGNATURE=signature,
    )


def online_order(client: APIClient, tenant: Tenant, variant: ProductVariant) -> tuple[str, Any]:
    checkout_id = reviewed(client, variant)
    placed = place(client, checkout_id, "online")
    assert placed.status_code == 201, placed.data
    started = client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    assert started.status_code == 200, started.data
    with tenant_context(tenant):
        intent = PaymentIntent.objects.get(order_id=placed.data["id"])
    return placed.data["id"], intent


# --- Placement ------------------------------------------------------------------------------------


def test_cod_order_converts_reviewed_checkout_once(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 3)
    checkout_id = reviewed(api_client, variant)

    placed = place(api_client, checkout_id)
    replay = place(api_client, checkout_id)
    other_key = place(api_client, checkout_id, key="another-key-0002")

    assert placed.status_code == 201
    assert placed.data["number"] == "ORD-000001"
    assert placed.data["status"] == "placed"
    assert placed.data["payment_method"] == "cod"
    assert placed.data["payment_status"] == "unpaid"
    assert placed.data["amount_paid"] == "0.0000"
    assert placed.data["invoice_number"] == "INV-000001"
    assert placed.data["items"][0]["sku"] == variant.sku
    assert replay.status_code == 200
    assert replay.data["id"] == placed.data["id"]
    assert other_key.status_code == 409
    assert other_key.data["error"]["code"] == "checkout_completed"
    stock_level = level(store.tenant, variant)
    assert (stock_level.on_hand, stock_level.reserved) == (Decimal(3), Decimal(1))
    with tenant_context(store.tenant):
        assert Order.objects.count() == 1
        assert Invoice.objects.count() == 1
        session = CheckoutSession.objects.get(pk=checkout_id)
        assert session.status == CheckoutStatus.COMPLETED
        assert Cart.objects.get(pk=session.cart_id).status == CartStatus.CONVERTED
        allocation = OrderAllocation.objects.get()
        assert allocation.status == AllocationStatus.ALLOCATED
        assert allocation.quantity == Decimal(1)
        intent = PaymentIntent.objects.get()
        assert intent.status == IntentStatus.AWAITING_COLLECTION
        assert PaymentTransaction.objects.count() == 0
        # Only the review reserved stock; placement wrote no ledger movement of its own.
        assert StockMovement.objects.filter(movement_type=MovementType.RESERVED).count() == 1
        assert StockMovement.objects.filter(reference_type="order").count() == 0
    # The checkout page now points at the order; a new cart starts empty.
    shown = api_client.get(f"/api/checkout/{checkout_id}/")
    assert shown.data["order"] == placed.data["id"]
    assert shown.data["order_creation_available"] is False
    assert api_client.get("/api/cart/").data["item_count"] == 0


def test_checkout_expiry_cannot_release_converted_stock(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    checkout_id = reviewed(api_client, variant)
    assert place(api_client, checkout_id).status_code == 201
    with tenant_context(store.tenant):
        CheckoutSession.objects.filter(pk=checkout_id).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        checkout_services.release_checkout(
            session=CheckoutSession.objects.get(pk=checkout_id), expired=True
        )
    expire_checkout(checkout_id, tenant_id=str(store.tenant.pk))
    assert level(store.tenant, variant).reserved == Decimal(1)


@pytest.mark.parametrize("state", ["expired", "released", "draft"])
def test_only_a_live_reviewed_checkout_becomes_an_order(
    store: Any, api_client: APIClient, state: str
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    if state == "draft":
        checkout_id, _ = prepare_physical_checkout(api_client, variant)
    else:
        checkout_id = reviewed(api_client, variant)
    if state == "expired":
        with tenant_context(store.tenant):
            CheckoutSession.objects.filter(pk=checkout_id).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
    if state == "released":
        api_client.post(f"/api/checkout/{checkout_id}/release/", {}, format="json")

    response = place(api_client, checkout_id)

    assert response.status_code == 409
    expected = {"draft": "checkout_not_reviewed"}.get(state, "checkout_expired")
    assert response.data["error"]["code"] == expected
    assert level(store.tenant, variant).reserved == Decimal(0)
    with tenant_context(store.tenant):
        assert not Order.objects.exists()


def test_order_snapshots_survive_catalogue_changes(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True, amount="250.00")
    stock(store.tenant, variant, 1)
    placed = place(api_client, reviewed(api_client, variant))
    with tenant_context(store.tenant):
        Product.objects.filter(pk=variant.product_id).update(name="Renamed")
        item = OrderItem.objects.get()
        with pytest.raises(ImmutableRecordError):
            item.save()
        invoice = Invoice.objects.get()
        with pytest.raises(ImmutableRecordError):
            invoice.save()
    shown = api_client.get(f"/api/orders/{placed.data['id']}/")
    assert shown.data["items"][0]["product_name"] == placed.data["items"][0]["product_name"]
    assert shown.data["items"][0]["unit_price"] == "250.0000"
    assert shown.data["grand_total"] == placed.data["grand_total"]


def test_payment_methods_follow_modules_and_order_type(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    checkout_id = reviewed(api_client, variant)
    shown = api_client.get(f"/api/checkout/{checkout_id}/")
    assert shown.data["payment_methods"] == ["cod"]
    assert shown.data["order_creation_available"] is True

    online = place(api_client, checkout_id, "online")
    assert online.status_code == 400
    assert "payment_method" in online.data["error"]["details"]
    with tenant_context(store.tenant):
        StoreModule.objects.create(key="cash_on_delivery", enabled=False)
    disabled = place(api_client, checkout_id, "cod")
    assert disabled.status_code == 400

    reader = APIClient()
    digital_id = digital_checkout(reader, digital(store.tenant))
    shown_digital = reader.get(f"/api/checkout/{digital_id}/")
    assert shown_digital.data["payment_methods"] == []
    assert shown_digital.data["order_creation_available"] is False
    assert place(reader, digital_id, "cod").status_code == 400


def test_online_payments_module_needs_a_configured_provider(store: Any) -> None:
    with tenant_context(store.tenant), pytest.raises(ConflictError) as caught:
        set_module(key="online_payments", enabled=True, actor=None)
    assert caught.value.get_codes() == "payment_provider_unavailable"


def test_order_ownership_guest_customer_and_tenant(
    store: Any, api_client: APIClient, tenant_b: Tenant
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 3)
    guest = place(api_client, reviewed(api_client, variant))
    assert guest.cookies["mc_order"]["httponly"]
    buyer = customer_client(store.tenant, "buyer@example.test")
    owned = place(buyer, reviewed(buyer, variant))
    assert "mc_order" not in owned.cookies
    stranger = customer_client(store.tenant, "stranger@example.test")

    assert api_client.get(f"/api/orders/{guest.data['id']}/").status_code == 200
    assert buyer.get(f"/api/orders/{owned.data['id']}/").status_code == 200
    for client, order_id in (
        (APIClient(), guest.data["id"]),
        (stranger, guest.data["id"]),
        (stranger, owned.data["id"]),
        (api_client, owned.data["id"]),
    ):
        denied = client.get(f"/api/orders/{order_id}/")
        assert denied.status_code == 403
        assert client.get(f"/api/orders/{order_id}/invoice/").status_code == 403
        assert client.post(f"/api/orders/{order_id}/cancel/", {}, format="json").status_code == 403
    with tenant_context(tenant_b):
        assert not Order.objects.filter(pk=guest.data["id"]).exists()
        assert not Invoice.objects.exists()


def test_cancellation_returns_stock_coupon_and_slot_exactly_once(
    store: Any, api_client: APIClient
) -> None:
    set_preset(store.tenant, "grocery")
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    with tenant_context(store.tenant):
        Coupon.objects.create(
            code="ONCE", discount_type=CouponDiscountType.PERCENT, value=10, max_redemptions=1
        )
        slot = DeliverySlot.objects.create(
            label="Evening",
            starts_at=timezone.now() + timedelta(hours=3),
            ends_at=timezone.now() + timedelta(hours=5),
            capacity=1,
        )
    checkout_id = reviewed(api_client, variant, coupon_code="ONCE", delivery_slot=slot)
    placed = place(api_client, checkout_id)
    replay = place(api_client, checkout_id)
    assert placed.status_code == 201
    assert replay.status_code == 200
    assert placed.data["delivery_slot_label"] == "Evening"
    assert placed.data["coupon_code"] == "ONCE"
    with tenant_context(store.tenant):
        slot.refresh_from_db()
        assert slot.bookings == 1
        assert Coupon.objects.get(code="ONCE").times_redeemed == 1
        assert CouponRedemption.objects.get().status == RedemptionStatus.REDEEMED
    # The converted hold no longer counts as a temporary hold: the slot is simply booked.
    other = APIClient()
    other_id, _ = prepare_physical_checkout(other, variant)
    assert other.get(f"/api/checkout/{other_id}/").data["delivery_slots"] == []

    cancelled = api_client.post(f"/api/orders/{placed.data['id']}/cancel/", {}, format="json")
    again = api_client.post(f"/api/orders/{placed.data['id']}/cancel/", {}, format="json")

    assert cancelled.status_code == again.status_code == 200
    assert cancelled.data["status"] == "cancelled"
    assert cancelled.data["can_cancel"] is False
    assert level(store.tenant, variant).reserved == Decimal(0)
    with tenant_context(store.tenant):
        slot.refresh_from_db()
        assert slot.bookings == 0
        assert Coupon.objects.get(code="ONCE").times_redeemed == 0
        assert CouponRedemption.objects.get().status == RedemptionStatus.VOIDED
        assert StockMovement.objects.filter(movement_type=MovementType.RELEASED).count() == 1
        assert OrderAllocation.objects.get().status == AllocationStatus.RELEASED
        assert PaymentIntent.objects.get().status == IntentStatus.CANCELLED
        events = list(
            OrderStatusEvent.objects.order_by("occurred_at").values_list("event", flat=True)
        )
        assert events == ["placed", "cancelled"]


def test_exhausted_coupon_blocks_placement_and_releases_the_hold(
    store: Any, api_client: APIClient
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    with tenant_context(store.tenant):
        Coupon.objects.create(
            code="LAST", discount_type=CouponDiscountType.PERCENT, value=5, max_redemptions=1
        )
    other = APIClient()
    first = reviewed(api_client, variant, coupon_code="LAST")
    second = reviewed(other, variant, coupon_code="LAST")
    assert place(api_client, first).status_code == 201

    blocked = place(other, second)

    assert blocked.status_code == 409
    assert blocked.data["error"]["code"] == "coupon_unavailable"
    assert level(store.tenant, variant).reserved == Decimal(1)
    with tenant_context(store.tenant):
        assert CheckoutSession.objects.get(pk=second).status == CheckoutStatus.RELEASED
        assert Coupon.objects.get(code="LAST").times_redeemed == 1


def test_state_machine_rejects_illegal_and_unauthorised_transitions(
    store: Any, api_client: APIClient
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    placed = place(api_client, reviewed(api_client, variant))
    url = f"/api/manage/orders/{placed.data['id']}/transition/"
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)

    assert editor.post(url, {"status": "confirmed"}, format="json").status_code == 403
    assert handler.post(url, {"status": "shipped"}, format="json").status_code == 400
    confirmed = handler.post(url, {"status": "confirmed"}, format="json")
    processing = handler.post(url, {"status": "processing"}, format="json")
    back = handler.post(url, {"status": "confirmed"}, format="json")
    customer_cancel = api_client.post(f"/api/orders/{placed.data['id']}/cancel/", {}, format="json")
    cancelled = handler.post(url, {"status": "cancelled", "reason": "out_of_area"}, format="json")
    revived = handler.post(url, {"status": "processing"}, format="json")

    assert confirmed.data["status"] == "confirmed"
    assert processing.data["status"] == "processing"
    assert back.status_code == 409
    assert back.data["error"]["code"] == "invalid_transition"
    assert customer_cancel.status_code == 409
    assert cancelled.data["status"] == "cancelled"
    assert cancelled.data["cancel_reason"] == "out_of_area"
    assert revived.status_code == 409
    assert [event["event"] for event in cancelled.data["events"]] == [
        "placed",
        "confirmed",
        "processing",
        "cancelled",
    ]
    with tenant_context(store.tenant):
        event = OrderStatusEvent.objects.first()
        assert event is not None
        with pytest.raises(ImmutableRecordError):
            event.save()
        with pytest.raises(ImmutableRecordError):
            event.delete()


# --- Batch, serial, digital -----------------------------------------------------------------------


def milk(tenant: Tenant) -> ProductVariant:
    set_preset(tenant, "grocery")
    row = simple_product(
        tenant,
        sku=f"MILK-{uuid.uuid4().hex[:6].upper()}",
        name="Milk",
        amount="90.00",
        publish=True,
        track_inventory=True,
        product_type="grocery",
        tracking_mode="batch",
        details={"is_perishable": True},
        variant={"net_quantity": Decimal("1"), "net_unit": "l"},
    )
    return first_variant(tenant, row)


def lot(tenant: Tenant, code: str, days: int) -> inventory.BatchInput:
    with tenant_context(tenant):
        today = inventory.store_today()
    return inventory.BatchInput(code=code, expires_on=today + timedelta(days=days))


def test_batch_expiring_after_review_uses_fresh_stock_or_blocks(
    store: Any, api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = milk(store.tenant)
    stock(store.tenant, variant, 1, batch=lot(store.tenant, "SOON", 1))
    stock(store.tenant, variant, 1, batch=lot(store.tenant, "LATER", 5))
    first = reviewed(api_client, variant)
    other = APIClient()
    second = reviewed(other, variant)
    later = lot(store.tenant, "X", 2).expires_on
    monkeypatch.setattr(inventory, "store_today", lambda: later)

    # One fresh lot remains: the first order takes it, the second cannot be backed.
    assert place(api_client, first).status_code == 201
    blocked = place(other, second)

    assert blocked.status_code == 409
    assert blocked.data["error"]["code"] == "stock_expired"
    assert level(store.tenant, variant).reserved == Decimal(1)
    with tenant_context(store.tenant):
        assert Order.objects.count() == 1
        assert CheckoutSession.objects.get(pk=second).status == CheckoutStatus.RELEASED


def test_fulfilment_handoff_is_fefo_and_never_deducts_twice(
    store: Any, api_client: APIClient
) -> None:
    variant = milk(store.tenant)
    stock(store.tenant, variant, 2, batch=lot(store.tenant, "LATE", 9))
    stock(store.tenant, variant, 1, batch=lot(store.tenant, "EARLY", 2))
    placed = place(api_client, reviewed(api_client, variant))
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    handler.post(
        f"/api/manage/orders/{placed.data['id']}/transition/",
        {"status": "confirmed"},
        format="json",
    )
    with tenant_context(store.tenant):
        order = Order.objects.get()
        services.fulfil_order_stock(order=order)
        services.fulfil_order_stock(order=order)
        movements = list(StockMovement.objects.filter(movement_type=MovementType.FULFILLED))
        assert [movement.batch.code for movement in movements if movement.batch] == ["EARLY"]
        assert OrderAllocation.objects.get().status == AllocationStatus.FULFILLED
    stock_level = level(store.tenant, variant)
    assert (stock_level.on_hand, stock_level.reserved) == (Decimal(2), Decimal(0))
    cancel = handler.post(
        f"/api/manage/orders/{placed.data['id']}/transition/",
        {"status": "cancelled"},
        format="json",
    )
    assert cancel.status_code == 409


def test_serial_units_are_sold_once(store: Any, api_client: APIClient) -> None:
    row = simple_product(
        store.tenant,
        sku="PHONE-1",
        name="Phone",
        amount="1000.00",
        publish=True,
        track_inventory=True,
        tracking_mode="serial",
    )
    variant = first_variant(store.tenant, row)
    stock(store.tenant, variant, 1, serials=["SN-0001"])
    placed = place(api_client, reviewed(api_client, variant))
    other = APIClient()
    assert add(other, variant).status_code == 409
    merchant(store.tenant).post(
        f"/api/manage/orders/{placed.data['id']}/transition/",
        {"status": "confirmed"},
        format="json",
    )
    with tenant_context(store.tenant):
        order = Order.objects.get()
        allocation = OrderAllocation.objects.get()
        with pytest.raises(Exception, match="serial"):
            services.fulfil_order_stock(order=order)
        services.fulfil_order_stock(order=order, serials={str(allocation.pk): ["sn-0001"]})
        assert SerialNumber.objects.get().status == SerialStatus.SOLD
        assert inventory.reconcile() == []


# --- Online payments ------------------------------------------------------------------------------


def test_online_payment_is_confirmed_only_by_verified_callbacks(
    store: Any, api_client: APIClient, fakepay: type[FakePaymentProvider]
) -> None:
    variant = digital(store.tenant)
    checkout_id = digital_checkout(api_client, variant)
    assert api_client.get(f"/api/checkout/{checkout_id}/").data["payment_methods"] == ["online"]
    placed = place(api_client, checkout_id, "online")
    assert placed.data["status"] == "pending_payment"
    assert placed.data["can_pay"] is True
    started = api_client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    restarted = api_client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    assert started.data["redirect_url"].startswith("https://pay.example.test/")
    assert restarted.data == started.data
    assert len(fakepay.initiated) == 1
    with tenant_context(store.tenant):
        intent = PaymentIntent.objects.get()
        # Digital goods hold no stock and write no ledger rows.
        assert not OrderAllocation.objects.exists()
        assert not StockMovement.objects.exists()

    body, _ = FakePaymentProvider.signed({"event_id": "forged"})
    forged = api_client.post(
        "/api/payments/callbacks/fakepay/",
        body,
        content_type="application/json",
        HTTP_X_FAKE_SIGNATURE="0" * 64,
    )
    unknown_provider = api_client.post("/api/payments/callbacks/otherpay/", {}, format="json")
    assert forged.status_code == 400
    assert forged.data["error"]["code"] == "invalid_callback"
    assert unknown_provider.status_code == 404
    assert api_client.get(f"/api/orders/{placed.data['id']}/").data["payment_status"] == "unpaid"

    mismatch = callback(api_client, intent, amount="1.00")
    paid = callback(api_client, intent, event_id="evt-paid-1")
    replayed = callback(api_client, intent, event_id="evt-paid-1")
    second_event = callback(api_client, intent)
    late_failure = callback(api_client, intent, status="failed", transaction_ref="txn-other")

    assert mismatch.data["outcome"] == "amount_mismatch"
    assert paid.data["outcome"] == "payment_succeeded"
    assert replayed.data["outcome"] == "duplicate"
    assert second_event.data["outcome"] == "duplicate_transaction"
    assert late_failure.data["outcome"] == "ignored_after_success"
    shown = api_client.get(f"/api/orders/{placed.data['id']}/")
    assert shown.data["status"] == "confirmed"
    assert shown.data["payment_status"] == "paid"
    assert shown.data["amount_paid"] == shown.data["grand_total"]
    with tenant_context(store.tenant):
        assert PaymentTransaction.objects.count() == 1
        assert ProviderCallback.objects.count() == 4


def test_failed_then_successful_payment_and_provider_timeouts(
    store: Any, api_client: APIClient, fakepay: type[FakePaymentProvider]
) -> None:
    variant = digital(store.tenant)
    checkout_id = digital_checkout(api_client, variant)
    placed = place(api_client, checkout_id, "online")
    fakepay.unavailable = True
    timeout = api_client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    assert timeout.status_code == 503
    assert timeout.data["error"]["code"] == "payment_provider_unavailable"
    fakepay.unavailable = False
    assert (
        api_client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json").status_code
        == 200
    )
    with tenant_context(store.tenant):
        first = PaymentIntent.objects.get()

    failed = callback(api_client, first, status="failed", transaction_ref="txn-declined")
    assert failed.data["outcome"] == "payment_failed"
    assert api_client.get(f"/api/orders/{placed.data['id']}/").data["status"] == "pending_payment"
    retry = api_client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    with tenant_context(store.tenant):
        second = PaymentIntent.objects.exclude(pk=first.pk).get()
    assert retry.status_code == 200
    assert callback(api_client, second).data["outcome"] == "payment_succeeded"
    # A delayed success for the declined attempt is still verified money and is kept on record.
    assert callback(api_client, first, transaction_ref="txn-late").data["outcome"] == (
        "payment_succeeded"
    )
    with tenant_context(store.tenant):
        order = Order.objects.get()
        assert order.status == "confirmed"
        assert order.amount_paid == order.grand_total * 2


def test_unpaid_online_order_expires_and_late_payment_needs_refund(
    store: Any, api_client: APIClient, fakepay: type[FakePaymentProvider]
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    order_id, intent = online_order(api_client, store.tenant, variant)
    assert level(store.tenant, variant).reserved == Decimal(1)
    with tenant_context(store.tenant):
        Order.objects.filter(pk=order_id).update(
            payment_due_at=timezone.now() - timedelta(seconds=1)
        )

    expired = api_client.get(f"/api/orders/{order_id}/")
    late = callback(api_client, intent)

    assert expired.data["status"] == "cancelled"
    assert expired.data["payment_status"] == "failed"
    assert expired.data["cancel_reason"] == "payment_expired"
    assert level(store.tenant, variant).reserved == Decimal(0)
    assert late.data["outcome"] == "payment_succeeded"
    with tenant_context(store.tenant):
        order = Order.objects.get()
        assert order.status == "cancelled"
        assert order.payment_status == "paid"
        assert OrderStatusEvent.objects.filter(event="payment_after_cancellation").exists()


# --- Refunds and cash on delivery -----------------------------------------------------------------


def test_provider_refunds_are_limited_idempotent_and_honest(
    store: Any, api_client: APIClient, fakepay: type[FakePaymentProvider]
) -> None:
    variant = digital(store.tenant)
    placed = place(api_client, digital_checkout(api_client, variant), "online")
    api_client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    with tenant_context(store.tenant):
        intent = PaymentIntent.objects.get()
    callback(api_client, intent)
    url = f"/api/manage/orders/{placed.data['id']}/refunds/"
    owner = merchant(store.tenant)
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)

    def refund(amount: str, key: str, client: APIClient = owner) -> Any:
        return client.post(
            url, {"amount": amount, "reason": "Damaged", "idempotency_key": key}, format="json"
        )

    assert refund("10.00", "refund-key-forbidden", editor).status_code == 403
    too_much = refund("100.01", "refund-key-too-much")
    fakepay.refund_status = "failed"
    failed = refund("10.00", "refund-key-failed")
    fakepay.refund_status = "succeeded"
    fakepay.unavailable = True
    pending = refund("30.00", "refund-key-partial")
    fakepay.unavailable = False
    completed = refund("30.00", "refund-key-partial")
    replay = refund("30.00", "refund-key-partial")
    conflict = refund("31.00", "refund-key-partial")
    rest = refund("70.00", "refund-key-rest")
    beyond = refund("0.01", "refund-key-beyond")

    assert too_much.status_code == 409
    assert too_much.data["error"]["code"] == "refund_exceeds_paid"
    assert failed.data["status"] == "failed"
    assert pending.data["status"] == "pending"
    assert completed.data["status"] == replay.data["status"] == "succeeded"
    assert completed.data["id"] == pending.data["id"] == replay.data["id"]
    assert conflict.status_code == 409
    assert rest.data["status"] == "succeeded"
    assert beyond.status_code == 409
    shown = owner.get(f"/api/manage/orders/{placed.data['id']}/")
    assert shown.data["payment_status"] == "refunded"
    assert shown.data["amount_refunded"] == "100.0000"
    assert len(shown.data["refunds"]) == 3
    with tenant_context(store.tenant):
        assert PaymentTransaction.objects.filter(kind="refund").count() == 2


def test_cod_stays_unpaid_until_collected_then_refunds_manually(
    store: Any, api_client: APIClient
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    placed = place(api_client, reviewed(api_client, variant))
    order_url = f"/api/manage/orders/{placed.data['id']}/"
    owner = merchant(store.tenant)
    refund_body = {"amount": "10.00", "reason": "Goodwill", "idempotency_key": "cod-refund-1"}

    early_collection = owner.post(f"{order_url}cod-collection/", {}, format="json")
    unpaid_refund = owner.post(f"{order_url}refunds/", refund_body, format="json")
    invoice = api_client.get(f"/api/orders/{placed.data['id']}/invoice/")
    owner.post(f"{order_url}transition/", {"status": "confirmed"}, format="json")
    collected = owner.post(f"{order_url}cod-collection/", {}, format="json")
    collected_again = owner.post(f"{order_url}cod-collection/", {}, format="json")
    refunded = owner.post(f"{order_url}refunds/", refund_body, format="json")
    paid_invoice = api_client.get(f"/api/orders/{placed.data['id']}/invoice/")

    assert early_collection.status_code == 409
    assert unpaid_refund.status_code == 409
    assert invoice.data["number"] == "INV-000001"
    assert invoice.data["payment_status"] == "unpaid"
    assert invoice.data["amount_paid"] == "0.0000"
    assert invoice.data["order_number"] == placed.data["number"]
    assert invoice.data["seller"]["name"] == store.name
    assert collected.data["payment_status"] == "paid"
    assert collected_again.data["amount_paid"] == collected.data["amount_paid"]
    assert refunded.data["method"] == "manual"
    assert refunded.data["status"] == "succeeded"
    assert paid_invoice.data["payment_status"] == "partially_refunded"
    assert paid_invoice.data["amount_refunded"] == "10.0000"
    assert paid_invoice.data["grand_total"] == invoice.data["grand_total"]
    with tenant_context(store.tenant):
        assert PaymentTransaction.objects.filter(kind="cod_collection").count() == 1


def test_refunds_require_recent_authentication(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    placed = place(api_client, reviewed(api_client, variant))
    owner = merchant(store.tenant)
    with tenant_context(store.tenant):
        AuthSession.objects.update(authenticated_at=timezone.now() - timedelta(hours=1))
    response = owner.post(
        f"/api/manage/orders/{placed.data['id']}/refunds/",
        {"amount": "1.00", "reason": "x", "idempotency_key": "stale-session-1"},
        format="json",
    )
    assert response.status_code == 403
    assert response.data["error"]["code"] == "reauthentication_required"


def test_invoice_numbers_are_sequential_per_store(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 3)
    numbers = []
    for _ in range(3):
        client = APIClient()
        placed = place(client, reviewed(client, variant))
        numbers.append((placed.data["number"], placed.data["invoice_number"]))
    assert numbers == [
        ("ORD-000001", "INV-000001"),
        ("ORD-000002", "INV-000002"),
        ("ORD-000003", "INV-000003"),
    ]


# --- Phase 08.1 corrections -----------------------------------------------------------------------


@pytest.mark.parametrize("change", ["disabled", "country", "currency", "minimum"])
def test_placement_rechecks_the_reviewed_shipping_method(
    store: Any, api_client: APIClient, change: str
) -> None:
    set_preset(store.tenant, "grocery")
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    with tenant_context(store.tenant):
        Coupon.objects.create(code="SHIP5", discount_type=CouponDiscountType.PERCENT, value=5)
        slot = DeliverySlot.objects.create(
            label="Morning",
            starts_at=timezone.now() + timedelta(hours=3),
            ends_at=timezone.now() + timedelta(hours=5),
            capacity=1,
        )
    checkout_id = reviewed(api_client, variant, coupon_code="SHIP5", delivery_slot=slot)
    reviewed_total = api_client.get(f"/api/checkout/{checkout_id}/").data["grand_total"]
    with tenant_context(store.tenant):
        methods = ShippingMethod.objects.all()
        {
            "disabled": lambda: methods.update(is_active=False),
            "country": lambda: methods.update(supported_countries=["IN"]),
            "currency": lambda: methods.update(currency="USD"),
            "minimum": lambda: methods.update(minimum_subtotal=Decimal("100000")),
        }[change]()

    blocked = place(api_client, checkout_id)

    assert blocked.status_code == 409
    assert blocked.data["error"]["code"] == "shipping_unavailable"
    assert level(store.tenant, variant).reserved == Decimal(0)
    with tenant_context(store.tenant):
        assert not Order.objects.exists()
        assert not Invoice.objects.exists()
        assert not PaymentIntent.objects.exists()
        assert not CouponRedemption.objects.exists()
        assert Coupon.objects.get(code="SHIP5").times_redeemed == 0
        slot.refresh_from_db()
        assert slot.bookings == 0
        session = CheckoutSession.objects.get(pk=checkout_id)
        assert session.status == CheckoutStatus.RELEASED
        assert str(session.grand_total) == reviewed_total


def test_same_key_replay_is_bound_to_the_payment_method(
    store: Any, api_client: APIClient, fakepay: type[FakePaymentProvider]
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    cod_checkout = reviewed(api_client, variant)
    placed = place(api_client, cod_checkout, "cod", "same-key-cod-01")
    replay = place(api_client, cod_checkout, "cod", "same-key-cod-01")
    switched = place(api_client, cod_checkout, "online", "same-key-cod-01")
    other_key = place(api_client, cod_checkout, "cod", "other-key-cod-01")

    assert placed.status_code == 201
    assert replay.status_code == 200
    assert replay.data["id"] == placed.data["id"]
    assert switched.status_code == 409
    assert switched.data["error"]["code"] == "idempotency_conflict"
    assert other_key.status_code == 409
    assert other_key.data["error"]["code"] == "checkout_completed"

    buyer = APIClient()
    online_checkout = reviewed(buyer, variant)
    online = place(buyer, online_checkout, "online", "same-key-online-01")
    reverse = place(buyer, online_checkout, "cod", "same-key-online-01")
    online_replay = place(buyer, online_checkout, "online", "same-key-online-01")

    assert online.status_code == 201
    assert reverse.status_code == 409
    assert reverse.data["error"]["code"] == "idempotency_conflict"
    assert online_replay.status_code == 200
    assert online_replay.data["id"] == online.data["id"]
    with tenant_context(store.tenant):
        assert list(Order.objects.order_by("number").values_list("payment_method", flat=True)) == [
            "cod",
            "online",
        ]
        assert Invoice.objects.count() == 2
        assert PaymentIntent.objects.count() == 1
        assert StockMovement.objects.filter(movement_type=MovementType.RESERVED).count() == 2
        assert StockMovement.objects.filter(reference_type="order").count() == 0
    assert level(store.tenant, variant).reserved == Decimal(2)
