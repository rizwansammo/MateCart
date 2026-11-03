"""Phase 11.1 merchant dashboard integrity: reviewed manual-order totals, confirmed COD
collection and sellable-stock alerts."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from commerce.audit.models import AuditEvent
from commerce.catalog.models import Product
from commerce.checkout.models import Cart, CheckoutSession, ShippingMethod, TaxRate
from commerce.identity.models import AuthSession
from commerce.inventory import services as inventory
from commerce.inventory.models import StockLevel, StockLocation
from commerce.orders.models import (
    AssistedOrder,
    IntentStatus,
    Order,
    PaymentIntent,
    PaymentTransaction,
    TransactionKind,
)
from commerce.pricing.models import Price
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .test_checkout import product
from .test_merchant_dashboard import ASSISTED, create, details, lines, quoted_totals, shipping
from .test_orders import customer_client, level, lot, merchant, milk, stock

pytestmark = pytest.mark.django_db

STAFF_PASSWORD = "staff-passphrase-1234"


# --- Manual order totals --------------------------------------------------------------------------


def test_placement_refuses_a_total_that_changed_after_review(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="250.00")
    stock(store.tenant, variant, 3)
    method = shipping(store.tenant, amount="60.00")
    owner = merchant(store.tenant)
    body = {"items": lines((variant, "1")), **details(), "shipping_method": str(method.pk)}
    reviewed = quoted_totals(owner, body)
    with tenant_context(store.tenant):
        Price.objects.filter(variant=variant).update(amount=Decimal("275.00"))
        ShippingMethod.objects.filter(pk=method.pk).update(amount=Decimal("80.00"))

    stale = owner.post(ASSISTED, {**body, "expected_totals": reviewed}, format="json")

    assert reviewed["grand_total"] == "310.0000"
    assert stale.status_code == 409
    assert stale.data["error"]["code"] == "quote_changed"
    with tenant_context(store.tenant):
        assert not Order.objects.exists()
        assert not AssistedOrder.objects.exists()
        assert not CheckoutSession.objects.exists()
        assert not Cart.objects.exists()
    assert level(store.tenant, variant).reserved == Decimal(0)

    # The merchant reviews the new total; the same request (same key) can then be placed.
    fresh = quoted_totals(owner, body)
    placed = owner.post(ASSISTED, {**body, "expected_totals": fresh}, format="json")

    assert fresh["grand_total"] == "355.0000"
    assert placed.status_code == 201, placed.data
    assert placed.data["grand_total"] == "355.0000"
    assert level(store.tenant, variant).reserved == Decimal(1)


def test_placement_needs_the_reviewed_totals(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="400.00")
    stock(store.tenant, variant, 2)
    method = shipping(store.tenant)
    owner = merchant(store.tenant)
    body = {"items": lines((variant, "1")), **details(), "shipping_method": str(method.pk)}
    reviewed = quoted_totals(owner, body)

    missing = owner.post(ASSISTED, body, format="json")
    other_currency = owner.post(
        ASSISTED, {**body, "expected_totals": {**reviewed, "currency": "USD"}}, format="json"
    )
    with tenant_context(store.tenant):
        TaxRate.objects.create(name="VAT", country="BD", rate=Decimal("0.05"))
    taxed = owner.post(ASSISTED, {**body, "expected_totals": reviewed}, format="json")

    assert missing.status_code == 400
    assert "expected_totals" in missing.data["error"]["details"]
    assert other_currency.status_code == 409
    assert other_currency.data["error"]["code"] == "quote_changed"
    assert taxed.status_code == 409
    assert taxed.data["error"]["code"] == "quote_changed"
    with tenant_context(store.tenant):
        assert not Order.objects.exists()
    assert level(store.tenant, variant).reserved == Decimal(0)


# --- COD collection -------------------------------------------------------------------------------


def confirmed_cod_order(tenant: Tenant) -> tuple[APIClient, str]:
    variant = product(tenant, tracked=True, amount="500.00")
    stock(tenant, variant, 1)
    owner = merchant(tenant)
    placed = create(owner, lines((variant, "1")))
    assert placed.status_code == 201, placed.data
    url = f"/api/manage/orders/{placed.data['id']}/"
    assert (
        owner.post(f"{url}transition/", {"status": "confirmed"}, format="json").status_code == 200
    )
    return owner, url


def age_sessions(tenant: Tenant) -> None:
    with tenant_context(tenant):
        AuthSession.objects.update(
            authenticated_at=timezone.now() - timedelta(hours=1), reauthenticated_at=None
        )


def test_cod_collection_needs_recent_authentication(store: Any) -> None:
    owner, url = confirmed_cod_order(store.tenant)
    age_sessions(store.tenant)

    stale = owner.post(f"{url}cod-collection/", {}, format="json")

    assert stale.status_code == 403
    assert stale.data["error"]["code"] == "reauthentication_required"
    with tenant_context(store.tenant):
        order = Order.objects.get()
        assert order.payment_status == "unpaid"
        assert order.amount_paid == Decimal(0)
        assert not PaymentTransaction.objects.filter(kind=TransactionKind.COD_COLLECTION).exists()
        assert PaymentIntent.objects.get().status == IntentStatus.AWAITING_COLLECTION

    confirmed = owner.post(
        "/api/manage/account/reauthenticate/", {"password": STAFF_PASSWORD}, format="json"
    )
    collected = owner.post(f"{url}cod-collection/", {}, format="json")
    again = owner.post(f"{url}cod-collection/", {}, format="json")

    assert confirmed.status_code == 200
    assert collected.status_code == 200
    assert collected.data["payment_status"] == "paid"
    assert collected.data["amount_paid"] == collected.data["grand_total"] == "500.0000"
    assert collected.data["can_record_cod"] is False
    # A duplicate submission records nothing more.
    assert again.status_code == 200
    assert again.data["amount_paid"] == "500.0000"
    with tenant_context(store.tenant):
        collection = PaymentTransaction.objects.get(kind=TransactionKind.COD_COLLECTION)
        assert (collection.amount, collection.currency) == (Decimal("500.0000"), "BDT")
        assert PaymentIntent.objects.get().status == IntentStatus.SUCCEEDED
        assert AuditEvent.objects.filter(action="order.cod.collected").count() == 1


def test_cod_collection_is_refused_without_order_permission(store: Any) -> None:
    _, url = confirmed_cod_order(store.tenant)
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)

    refused = [
        editor.post(f"{url}cod-collection/", {}, format="json"),
        APIClient().post(f"{url}cod-collection/", {}, format="json"),
        customer_client(store.tenant, "cod@example.test").post(
            f"{url}cod-collection/", {}, format="json"
        ),
    ]

    assert refused[0].status_code == 403
    assert all(response.status_code in (401, 403) for response in refused)
    with tenant_context(store.tenant):
        assert Order.objects.get().payment_status == "unpaid"
        assert not PaymentTransaction.objects.filter(kind=TransactionKind.COD_COLLECTION).exists()


def test_order_handlers_with_a_recent_sign_in_can_record_cod(store: Any) -> None:
    _, url = confirmed_cod_order(store.tenant)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)

    collected = handler.post(f"{url}cod-collection/", {}, format="json")

    assert collected.status_code == 200
    assert collected.data["payment_status"] == "paid"


# --- Stock alerts ---------------------------------------------------------------------------------


def stock_alerts(tenant: Tenant) -> dict[str, Any]:
    data: dict[str, Any] = (
        merchant(tenant, StaffRole.CONTENT_EDITOR).get("/api/manage/dashboard/").data["inventory"]
    )
    return data


def threshold(tenant: Tenant, variant: Any, value: int) -> None:
    with tenant_context(tenant):
        StockLevel.objects.filter(variant=variant).update(low_stock_threshold=Decimal(value))


def test_expired_batches_are_not_sellable_in_stock_alerts(
    store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = milk(store.tenant)
    stock(store.tenant, variant, 5, batch=lot(store.tenant, "OLD", 1))
    threshold(store.tenant, variant, 2)
    later = lot(store.tenant, "X", 2).expires_on

    before = stock_alerts(store.tenant)
    monkeypatch.setattr(inventory, "store_today", lambda: later)
    after = stock_alerts(store.tenant)

    assert (before["low_stock"], before["out_of_stock"]) == (0, 0)
    assert (after["low_stock"], after["out_of_stock"]) == (1, 1)
    item = after["low_stock_items"][0]
    # Physical stock is still there; none of it can be sold.
    assert (item["on_hand"], item["available"], item["sellable"]) == ("5.000", "5.000", "0.000")


def test_mixed_expiry_batches_and_reservations(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    variant = milk(store.tenant)
    stock(store.tenant, variant, 3, batch=lot(store.tenant, "OLD", 1))
    stock(store.tenant, variant, 2, batch=lot(store.tenant, "NEW", 10))
    threshold(store.tenant, variant, 2)
    reserved = create(merchant(store.tenant), lines((variant, "1")))
    assert reserved.status_code == 201, reserved.data
    later = lot(store.tenant, "X", 2).expires_on

    before = stock_alerts(store.tenant)
    monkeypatch.setattr(inventory, "store_today", lambda: later)
    after = stock_alerts(store.tenant)

    # Before expiry: 5 unexpired - 1 reserved = 4 sellable, above the alert.
    assert (before["low_stock"], before["out_of_stock"]) == (0, 0)
    # After: only NEW (2) is unexpired, and the reservation still counts against it.
    assert (after["low_stock"], after["out_of_stock"]) == (1, 0)
    item = after["low_stock_items"][0]
    assert (item["on_hand"], item["available"], item["sellable"]) == ("5.000", "4.000", "1.000")
    with tenant_context(store.tenant):
        assert inventory.sellable_quantity(variant) == Decimal(1)


def test_stock_alerts_count_variants_once_across_active_locations(store: Any) -> None:
    with tenant_context(store.tenant):
        main = inventory.default_location()
        shop = inventory.save_location(
            location=None, data={"code": "shop", "name": "Shop"}, actor=None
        )
        warehouse = inventory.save_location(
            location=None, data={"code": "wh", "name": "Warehouse"}, actor=None
        )
    spread = product(store.tenant, tracked=True)
    parked = product(store.tenant, tracked=True)
    untracked = product(store.tenant)
    with tenant_context(store.tenant):
        for variant, location, quantity in (
            (spread, main, 1),
            (spread, shop, 1),
            (parked, warehouse, 3),
        ):
            inventory.receive(
                variant=variant,
                location=location,
                quantity=quantity,
                request=inventory.StockRequest(reason="alert test stock"),
            )
        StockLocation.objects.filter(pk=warehouse.pk).update(is_active=False)
        # A level row for a product whose stock is not tracked is never an alert.
        StockLevel.objects.create(variant=untracked, location=main)
        assert not Product.objects.get(pk=untracked.product_id).track_inventory
    threshold(store.tenant, spread, 2)

    alerts = stock_alerts(store.tenant)

    # `spread` is low at two locations but is one variant; `parked` sits only at an inactive
    # location, so nothing can be promised and it is out of stock.
    assert alerts["low_stock"] == 1
    assert alerts["out_of_stock"] == 1
    assert sorted(item["location_code"] for item in alerts["low_stock_items"]) == ["MAIN", "SHOP"]
    report = merchant(store.tenant).get("/api/manage/reports/sales/").data["inventory"]
    assert report == {"low_stock": 1, "out_of_stock": 1}
    with tenant_context(store.tenant):
        # The SQL figure agrees with the inventory engine's per-variant calculation.
        sellable = inventory.sellable_quantities([spread, parked])
        assert (sellable[spread.pk], sellable[parked.pk]) == (Decimal(2), Decimal(0))
        per_level = inventory.with_sellable(StockLevel.objects.filter(variant=spread))
        assert sum(per_level.values_list("sellable", flat=True)) == Decimal(2)
