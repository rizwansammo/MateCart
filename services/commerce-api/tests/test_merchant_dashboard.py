"""Phase 11 merchant dashboard: assisted orders, reports, customers and merchant views."""

import uuid
from datetime import UTC, date, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from commerce.audit.models import AuditEvent
from commerce.catalog.models import Product
from commerce.checkout.models import (
    Cart,
    CheckoutSession,
    CheckoutStatus,
    Coupon,
    CouponDiscountType,
    ShippingMethod,
    TaxRate,
)
from commerce.identity.models import AccountType, User
from commerce.inventory import services as inventory
from commerce.inventory.models import StockLevel, StockMovement
from commerce.orders.models import (
    AllocationStatus,
    AssistedOrder,
    ImmutableRecordError,
    IntentStatus,
    Invoice,
    Order,
    OrderAllocation,
    OrderStatusEvent,
    PaymentIntent,
)
from commerce.orders.reports import day_bounds, store_today
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_store_settings, make_user
from .test_checkout import product
from .test_inventory_concurrency import run_concurrently
from .test_orders import (
    customer_client,
    digital,
    level,
    lot,
    merchant,
    milk,
    place,
    reviewed,
    stock,
)

pytestmark = pytest.mark.django_db

ASSISTED = "/api/manage/orders/assisted/"


def details(**overrides: Any) -> dict[str, Any]:
    return {
        "recipient_name": "Rahim Uddin",
        "contact_phone": "01712345678",
        "address_line_1": "House 7, Road 3",
        "city": "Dhaka",
        "country": "BD",
        "payment_method": "cod",
        "source": "facebook",
        "source_reference": "Page chat 18:04",
        "customer_request_attested": True,
        "idempotency_key": f"assisted-{uuid.uuid4().hex}",
        **overrides,
    }


def lines(*pairs: tuple[Any, str]) -> list[dict[str, str]]:
    return [{"variant": str(variant.pk), "quantity": quantity} for variant, quantity in pairs]


def shipping(tenant: Tenant, amount: str = "60.00", **fields: Any) -> ShippingMethod:
    with tenant_context(tenant):
        return ShippingMethod.objects.create(
            code=fields.pop("code", "dhaka"),
            name=fields.pop("name", "Inside Dhaka"),
            amount=Decimal(amount),
            currency="BDT",
            **fields,
        )


def eligible_method() -> str:
    """The store's first active delivery method, or a free standard one (staff always choose)."""
    tenant_id = settings.MATECART_TENANT_ID
    assert tenant_id is not None
    tenant = Tenant.objects.get(pk=tenant_id)
    with tenant_context(tenant):
        method = ShippingMethod.objects.filter(is_active=True).order_by(
            "position", "code"
        ).first() or ShippingMethod.objects.create(
            code="standard", name="Standard delivery", amount=Decimal(0), currency="BDT"
        )
    return str(method.pk)


def create(client: APIClient, items: list[dict[str, str]], **overrides: Any) -> Any:
    overrides.setdefault("shipping_method", eligible_method())
    return client.post(ASSISTED, {"items": items, **details(**overrides)}, format="json")


# --- Merchant-assisted orders ---------------------------------------------------------------------


def test_assisted_cod_order_uses_the_commerce_engine(store: Any) -> None:
    first = product(store.tenant, tracked=True, amount="1200.00")
    second = product(store.tenant, amount="300.00")
    stock(store.tenant, first, 5)
    method = shipping(store.tenant)
    with tenant_context(store.tenant):
        TaxRate.objects.create(name="VAT", country="BD", rate=Decimal("0.05"))
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    with tenant_context(store.tenant):
        accounts_before = User.objects.count()

    response = create(
        handler,
        lines((first, "2"), (second, "1")),
        shipping_method=str(method.pk),
        contact_email="rahim@example.test",
    )

    assert response.status_code == 201, response.data
    data = response.data
    assert data["source"] == "facebook"
    assert data["status"] == "placed"
    assert data["payment_method"] == "cod"
    assert data["payment_status"] == "unpaid"
    assert data["amount_paid"] == "0.0000"
    assert data["item_subtotal"] == "2700.0000"
    assert data["shipping_total"] == "60.0000"
    assert data["tax_total"] == "135.0000"
    assert data["grand_total"] == "2895.0000"
    assert data["contact_phone"] == "+8801712345678"
    assert data["customer"] is None
    assert data["assisted"]["customer_request_attested"] is True
    assert data["assisted"]["source_reference"] == "Page chat 18:04"
    assert data["invoice_number"] == "INV-000001"
    assert data["allowed_transitions"] == ["confirmed", "cancelled"]
    with tenant_context(store.tenant):
        order = Order.objects.get(pk=data["id"])
        provenance = AssistedOrder.objects.get(order=order)
        # No account is created or linked, and no browser credential exists for the order.
        assert User.objects.count() == accounts_before
        assert order.customer_id is None
        assert order.guest_access_hash == ""
        assert provenance.created_by.account_type == AccountType.STAFF
        assert Invoice.objects.filter(order=order).count() == 1
        assert PaymentIntent.objects.get(order=order).status == IntentStatus.AWAITING_COLLECTION
        allocation = OrderAllocation.objects.get(order=order)
        assert (allocation.quantity, allocation.status) == (Decimal(2), AllocationStatus.ALLOCATED)
        session = CheckoutSession.objects.get(pk=order.checkout_id)
        assert session.status == CheckoutStatus.COMPLETED
        assert session.assisted_by_id == provenance.created_by_id
        assert Cart.objects.get(pk=session.cart_id).guest_token_hash == ""
        assert OrderStatusEvent.objects.get(order=order).actor_id == provenance.created_by_id
        placed = AuditEvent.objects.get(action="order.placed")
        assert placed.changes["source"] == "facebook"
        assert placed.actor_user_id == provenance.created_by_id
    assert level(store.tenant, first).reserved == Decimal(2)


def test_assisted_order_is_idempotent_per_request(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 3)
    owner = merchant(store.tenant)
    body = {
        "items": lines((variant, "1")),
        **details(idempotency_key="same-request-001"),
        "shipping_method": eligible_method(),
    }

    first = owner.post(ASSISTED, body, format="json")
    replay = owner.post(ASSISTED, body, format="json")
    changed = owner.post(ASSISTED, {**body, "items": lines((variant, "2"))}, format="json")

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.data["id"] == first.data["id"]
    assert changed.status_code == 409
    assert changed.data["error"]["code"] == "idempotency_conflict"
    with tenant_context(store.tenant):
        assert Order.objects.count() == 1
    assert level(store.tenant, variant).reserved == Decimal(1)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"customer_request_attested": False}, "customer_request_attested"),
        ({"payment_method": "online"}, "payment_method"),
        ({"source": "storefront"}, "source"),
        ({"contact_phone": ""}, "contact_phone"),
        ({"recipient_name": ""}, "recipient_name"),
        ({"contact_phone": "12"}, "contact_phone"),
    ],
)
def test_assisted_order_input_is_validated(
    store: Any, overrides: dict[str, Any], field: str
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)

    response = create(merchant(store.tenant), lines((variant, "1")), **overrides)

    assert response.status_code == 400
    assert field in response.data["error"]["details"]
    with tenant_context(store.tenant):
        assert not Order.objects.exists()
        assert not StockMovement.objects.filter(movement_type="reserved").exists()


def test_unavailable_or_short_stock_is_rejected_and_nothing_stays_reserved(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    draft = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        Product.objects.filter(pk=draft.product_id).update(
            status="archived", archived_at=timezone.now()
        )
    owner = merchant(store.tenant)

    short = create(owner, lines((variant, "2")))
    withdrawn = create(owner, lines((draft, "1")))
    unknown = create(owner, [{"variant": str(uuid.uuid4()), "quantity": "1"}])
    duplicate = create(owner, [*lines((variant, "1")), *lines((variant, "1"))])
    empty = create(owner, [])

    assert short.status_code == 409
    assert short.data["error"]["code"] == "insufficient_stock"
    assert withdrawn.status_code == 409
    assert withdrawn.data["error"]["code"] == "unavailable"
    assert unknown.status_code == 400
    assert duplicate.status_code == 400
    assert empty.status_code == 400
    with tenant_context(store.tenant):
        assert not Order.objects.exists()
        assert not CheckoutSession.objects.exists()
        assert not Cart.objects.exists()
    assert level(store.tenant, variant).reserved == Decimal(0)


def test_expired_batch_stock_cannot_back_an_assisted_order(
    store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = milk(store.tenant)
    stock(store.tenant, variant, 3, batch=lot(store.tenant, "OLD", 1))
    later = lot(store.tenant, "X", 2).expires_on
    monkeypatch.setattr(inventory, "store_today", lambda: later)

    response = create(merchant(store.tenant), lines((variant, "1")))

    assert response.status_code == 409
    assert response.data["error"]["code"] == "insufficient_stock"
    with tenant_context(store.tenant):
        assert not Order.objects.exists()


def test_invalid_shipping_digital_cod_and_coupon_rules_hold(store: Any, tenant_b: Tenant) -> None:
    variant = product(store.tenant, tracked=True, amount="500.00")
    stock(store.tenant, variant, 5)
    shipping(store.tenant)
    inactive = shipping(store.tenant, code="old", is_active=False)
    make_store_settings(tenant_b)
    foreign = shipping(tenant_b, code="dhaka")
    ebook = digital(store.tenant)
    with tenant_context(store.tenant):
        Coupon.objects.create(
            code="ONCE", discount_type=CouponDiscountType.PERCENT, value=10, max_redemptions=1
        )
    owner = merchant(store.tenant)

    for method in (inactive, foreign):
        refused = create(owner, lines((variant, "1")), shipping_method=str(method.pk))
        assert refused.status_code == 400
        assert "shipping_method" in refused.data["error"]["details"]
    digital_cod = create(owner, lines((ebook, "1")))
    used = create(owner, lines((variant, "1")), coupon_code="once")
    again = create(owner, lines((variant, "1")), coupon_code="ONCE")

    assert digital_cod.status_code == 400
    assert "payment_method" in digital_cod.data["error"]["details"]
    assert used.status_code == 201
    assert used.data["discount_total"] == "50.0000"
    assert again.status_code == 400
    assert "coupon_code" in again.data["error"]["details"]
    with tenant_context(store.tenant):
        assert Order.objects.count() == 1
        assert Coupon.objects.get(code="ONCE").times_redeemed == 1


def test_assisted_orders_need_order_permission_and_stay_in_their_store(
    store: Any, tenant_b: Tenant
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    make_store_settings(tenant_b)
    foreign = product(tenant_b, tracked=True)
    stock(tenant_b, foreign, 1)
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)

    assert APIClient().post(ASSISTED, {}, format="json").status_code in (401, 403)
    assert create(editor, lines((variant, "1"))).status_code == 403
    assert editor.get(f"{ASSISTED}variants/").status_code == 403
    assert customer_client(store.tenant, "c@example.test").post(ASSISTED, {}).status_code in (
        401,
        403,
    )
    cross = create(merchant(store.tenant), lines((foreign, "1")))
    assert cross.status_code == 400
    with tenant_context(store.tenant):
        assert not Order.objects.exists()
    assert level(tenant_b, foreign).reserved == Decimal(0)


def test_assisted_order_is_not_reachable_by_customers_or_guests(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    placed = create(merchant(store.tenant), lines((variant, "1")), contact_email="c@example.test")
    # A customer account with the same email gains nothing: there is no matching by contact.
    same_email = customer_client(store.tenant, "c@example.test")

    assert placed.status_code == 201
    for client in (APIClient(), same_email):
        assert client.get(f"/api/orders/{placed.data['id']}/").status_code == 403
    assert same_email.get("/api/orders/").data["count"] == 0


def test_quote_prices_the_draft_without_reserving(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="250.00")
    stock(store.tenant, variant, 4)
    method = shipping(store.tenant, amount="80.00")
    owner = merchant(store.tenant)

    quote = owner.post(
        f"{ASSISTED}quote/",
        {"items": lines((variant, "2")), "country": "BD", "shipping_method": str(method.pk)},
        format="json",
    )
    short = owner.post(f"{ASSISTED}quote/", {"items": lines((variant, "9"))}, format="json")
    search = owner.get(f"{ASSISTED}variants/", {"q": variant.sku})

    assert quote.status_code == 200
    assert quote.data["valid"] is True
    assert quote.data["totals"]["grand_total"] == "580.0000"
    assert quote.data["payment_methods"] == ["cod"]
    assert [row["id"] for row in quote.data["shipping_methods"]] == [str(method.pk)]
    assert quote.data["lines"][0]["available_quantity"] == "4.000"
    assert short.data["valid"] is False
    assert short.data["lines"][0]["issue"] == "insufficient_stock"
    assert short.data["totals"] is None
    assert search.data[0]["sku"] == variant.sku
    assert search.data[0]["unit_price"] == "250.0000"
    with tenant_context(store.tenant):
        assert not CheckoutSession.objects.exists()
        assert not Cart.objects.exists()
        assert not ShippingMethod.objects.filter(code="standard").exists()
    assert level(store.tenant, variant).reserved == Decimal(0)


def test_assisted_provenance_is_immutable(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    placed = create(merchant(store.tenant), lines((variant, "1")))
    with tenant_context(store.tenant):
        row = AssistedOrder.objects.get(order_id=placed.data["id"])
        row.source_reference = "changed"
        with pytest.raises(ImmutableRecordError):
            row.save()
        with pytest.raises(ImmutableRecordError):
            row.delete()


@pytest.mark.django_db(transaction=True)
def test_concurrent_assisted_orders_never_oversell(deployment_tenant: Tenant) -> None:
    make_store_settings(deployment_tenant)
    variant = product(deployment_tenant, tracked=True)
    stock(deployment_tenant, variant, 2)
    with tenant_context(deployment_tenant):
        staff = make_user(
            deployment_tenant, account_type=AccountType.STAFF, email="race@example.test"
        )
    method = eligible_method()
    from commerce.orders import assisted

    def attempt(index: int) -> Any:
        data = {
            "items": [{"variant": str(variant.pk), "quantity": Decimal(1)}],
            **details(idempotency_key=f"race-{index}-key"),
        }
        data["shipping_method"] = method
        return assisted.place_assisted_order(data=data, actor=staff).order

    results = run_concurrently(deployment_tenant, 4, attempt)

    assert sum(isinstance(result, Order) for result in results) == 2
    with tenant_context(deployment_tenant):
        assert Order.objects.count() == 2
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(2)


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicates_of_one_request_create_one_order(deployment_tenant: Tenant) -> None:
    make_store_settings(deployment_tenant)
    variant = product(deployment_tenant, tracked=True)
    stock(deployment_tenant, variant, 1)
    with tenant_context(deployment_tenant):
        staff = make_user(
            deployment_tenant, account_type=AccountType.STAFF, email="dupe@example.test"
        )
    from commerce.orders import assisted

    data = {
        "items": [{"variant": str(variant.pk), "quantity": Decimal(1)}],
        **details(idempotency_key="one-request-key"),
        "shipping_method": eligible_method(),
    }
    results = run_concurrently(
        deployment_tenant,
        3,
        lambda _: assisted.place_assisted_order(data=data, actor=staff).order.pk,
    )

    assert len(set(results)) == 1, results
    with tenant_context(deployment_tenant):
        assert Order.objects.count() == 1
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(1)


# --- Merchant order list and detail ---------------------------------------------------------------


def test_merchant_order_list_filters_and_detail(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 3)
    web = place(api_client, reviewed(api_client, variant))
    owner = merchant(store.tenant)
    phone = create(owner, lines((variant, "1")), source="phone", recipient_name="Karim Phone")

    everything = owner.get("/api/manage/orders/")
    by_source = owner.get("/api/manage/orders/", {"source": "phone"})
    by_name = owner.get("/api/manage/orders/", {"q": "karim"})
    detail = owner.get(f"/api/manage/orders/{web.data['id']}/")
    invoice = owner.get(f"/api/manage/orders/{web.data['id']}/invoice/")
    handler_invoice = merchant(store.tenant, StaffRole.ORDER_HANDLER).get(
        f"/api/manage/orders/{web.data['id']}/invoice/"
    )
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)

    assert [row["number"] for row in everything.data["results"]] == [
        phone.data["number"],
        web.data["number"],
    ]
    assert everything.data["results"][1]["source"] == "storefront"
    assert everything.data["results"][0]["item_count"] == 1
    assert everything.data["results"][0]["shipment_status"] is None
    assert [row["id"] for row in by_source.data["results"]] == [phone.data["id"]]
    assert [row["id"] for row in by_name.data["results"]] == [phone.data["id"]]
    assert detail.data["source"] == "storefront"
    assert detail.data["assisted"] is None
    assert detail.data["can_record_cod"] is False
    assert detail.data["refundable_amount"] == "0.0000"
    assert invoice.data["number"] == web.data["invoice_number"]
    assert handler_invoice.status_code == 200
    assert editor.get("/api/manage/orders/").status_code == 403
    assert editor.get(f"/api/manage/orders/{web.data['id']}/invoice/").status_code == 403


def test_merchant_order_views_are_tenant_isolated(store: Any, tenant_b: Tenant) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    placed = create(merchant(store.tenant), lines((variant, "1")))
    make_store_settings(tenant_b)
    with tenant_context(tenant_b):
        assert not Order.objects.filter(pk=placed.data["id"]).exists()
        assert not AssistedOrder.objects.exists()


# --- Dashboard and reports ------------------------------------------------------------------------


def test_dashboard_sections_follow_permissions_and_empty_store(store: Any) -> None:
    owner = merchant(store.tenant).get("/api/manage/dashboard/")
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER).get("/api/manage/dashboard/")
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR).get("/api/manage/dashboard/")

    assert owner.status_code == 200
    assert owner.data["orders"]["total"] == 0
    assert owner.data["recent_orders"] == []
    assert owner.data["finance"]["totals"] == []
    assert owner.data["finance"]["cash"]["cod"] == []
    assert owner.data["inventory"]["low_stock"] == 0
    assert handler.data["orders"] is not None
    assert handler.data["finance"] is None
    assert handler.data["inventory"] is None
    assert editor.data["orders"] is None
    assert editor.data["recent_orders"] is None
    assert editor.data["finance"] is None
    assert editor.data["inventory"] is not None
    assert APIClient().get("/api/manage/dashboard/").status_code in (401, 403)


def test_finance_separates_order_value_collections_refunds_and_cod(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="1000.00")
    stock(store.tenant, variant, 5)
    owner = merchant(store.tenant)
    first = create(owner, lines((variant, "1")))
    create(owner, lines((variant, "2")))
    cancelled = create(owner, lines((variant, "1")))
    url = "/api/manage/orders/{}/"
    owner.post(url.format(cancelled.data["id"]) + "transition/", {"status": "cancelled"})

    before = owner.get("/api/manage/dashboard/").data["finance"]
    owner.post(url.format(first.data["id"]) + "transition/", {"status": "confirmed"})
    owner.post(url.format(first.data["id"]) + "cod-collection/", {}, format="json")
    owner.post(
        url.format(first.data["id"]) + "refunds/",
        {"amount": "250.00", "reason": "Damaged box", "idempotency_key": "report-refund-1"},
        format="json",
    )
    after = owner.get("/api/manage/dashboard/").data["finance"]
    report = owner.get("/api/manage/reports/sales/").data

    # Placing orders is not revenue: nothing is collected until cash is recorded.
    assert before["totals"] == [
        {
            "currency": "BDT",
            "order_value": "3000.0000",
            "collected": "0.0000",
            "refunded": "0.0000",
            "net_collected": "0.0000",
        }
    ]
    assert before["cash"]["cod"][0]["awaiting_collection"] == "3000.0000"
    assert before["cash"]["awaiting_collection_orders"] == 2
    assert after["totals"][0]["collected"] == "1000.0000"
    assert after["totals"][0]["refunded"] == "250.0000"
    assert after["totals"][0]["net_collected"] == "750.0000"
    assert after["cash"]["cod"][0]["awaiting_collection"] == "2000.0000"
    assert report["summary"] == [
        {"currency": "BDT", "order_value": "3000.0000", "average_order_value": "1500.0000"}
    ]
    assert {row["key"]: row["orders"] for row in report["by_status"]} == {
        "cancelled": 1,
        "confirmed": 1,
        "placed": 1,
    }
    assert report["by_source"] == [
        {"key": "facebook", "currency": "BDT", "orders": 2, "value": "3000.0000"}
    ]
    assert report["top_products"][0]["quantity"] == "3.000"
    assert report["top_products"][0]["revenue"] == "3000.0000"


def test_reports_use_store_days_and_bounded_ranges(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    owner = merchant(store.tenant)
    placed = create(owner, lines((variant, "1")))
    with tenant_context(store.tenant):
        today = store_today()
        # 00:30 in Dhaka is still the previous day in UTC: the store's calendar day counts.
        start, _ = day_bounds(today, today)
        early = start + timedelta(minutes=30)
        Order.objects.filter(pk=placed.data["id"]).update(placed_at=early)

    day = owner.get("/api/manage/reports/sales/", {"start": str(today), "end": str(today)}).data
    previous = today - timedelta(days=1)
    yesterday = owner.get(
        "/api/manage/reports/sales/", {"start": str(previous), "end": str(previous)}
    ).data
    too_long = owner.get(
        "/api/manage/reports/sales/", {"start": str(today - timedelta(days=400)), "end": str(today)}
    )
    reversed_range = owner.get(
        "/api/manage/reports/sales/", {"start": str(today), "end": str(previous)}
    )

    assert early.astimezone(UTC).date() == previous
    assert day["daily"] == [
        {"date": today.isoformat(), "currency": "BDT", "orders": 1, "value": "100.0000"}
    ]
    assert yesterday["daily"] == []
    assert yesterday["summary"] == []
    assert too_long.status_code == 400
    assert reversed_range.status_code == 400
    assert (
        merchant(store.tenant, StaffRole.ORDER_HANDLER)
        .get("/api/manage/reports/sales/")
        .status_code
        == 403
    )


def test_dashboard_inventory_lists_low_stock(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    with tenant_context(store.tenant):
        StockLevel.objects.filter(variant=variant).update(low_stock_threshold=Decimal(3))

    data = merchant(store.tenant, StaffRole.CONTENT_EDITOR).get("/api/manage/dashboard/").data

    assert data["inventory"]["low_stock"] == 1
    assert data["inventory"]["low_stock_items"][0]["sku"] == variant.sku
    assert data["inventory"]["low_stock_items"][0]["available"] == "2.000"


# --- Customers ------------------------------------------------------------------------------------


def test_customer_records_are_permissioned_private_and_isolated(
    store: Any, tenant_b: Tenant
) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 2)
    buyer = customer_client(store.tenant, "buyer@example.test")
    place(buyer, reviewed(buyer, variant))
    with tenant_context(store.tenant):
        customer = User.objects.get(email="buyer@example.test")
    make_store_settings(tenant_b)
    foreign = make_user(tenant_b, account_type=AccountType.CUSTOMER, email="far@example.test")
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)

    listing = handler.get("/api/manage/customers/")
    search = handler.get("/api/manage/customers/", {"q": "nobody"})
    detail = handler.get(f"/api/manage/customers/{customer.pk}/")

    assert [row["email"] for row in listing.data["results"]] == ["buyer@example.test"]
    assert listing.data["results"][0]["order_count"] == 1
    assert search.data["count"] == 0
    assert len(detail.data["orders"]) == 1
    assert detail.data["cases"] == []
    for leaked in ("password", "mfa_secret", "totp_secret", "sessions", "last_login"):
        assert leaked not in detail.data
    assert handler.get(f"/api/manage/customers/{foreign.pk}/").status_code == 404
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    assert editor.get("/api/manage/customers/").status_code == 403


# --- After sales merchant view --------------------------------------------------------------------


def test_case_next_statuses_follow_the_state_machine() -> None:
    from commerce.aftersales.models import AfterSalesCase, CaseKind, CaseStatus
    from commerce.aftersales.serializers import next_statuses

    def case(kind: str, status: str) -> AfterSalesCase:
        return AfterSalesCase(kind=kind, status=status)

    assert next_statuses(case(CaseKind.RETURN, CaseStatus.REQUESTED)) == ["approved", "rejected"]
    assert next_statuses(case(CaseKind.RETURN, CaseStatus.APPROVED)) == ["received"]
    assert next_statuses(case(CaseKind.RETURN, CaseStatus.INSPECTED)) == ["awaiting_refund"]
    assert next_statuses(case(CaseKind.RETURN, CaseStatus.AWAITING_REFUND)) == ["resolved"]
    assert next_statuses(case(CaseKind.EXCHANGE, CaseStatus.INSPECTED)) == ["replacement_pending"]
    assert next_statuses(case(CaseKind.WARRANTY, CaseStatus.APPROVED)) == ["received", "resolved"]
    assert next_statuses(case(CaseKind.SUPPORT, CaseStatus.RESOLVED)) == ["closed"]
    assert next_statuses(case(CaseKind.SUPPORT, CaseStatus.CLOSED)) == []


def test_staff_cancel_of_assisted_order_releases_stock_once(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    owner = merchant(store.tenant)
    placed = create(owner, lines((variant, "1")))
    url = f"/api/manage/orders/{placed.data['id']}/transition/"

    cancelled = owner.post(url, {"status": "cancelled"}, format="json")
    again = owner.post(url, {"status": "cancelled"}, format="json")

    assert cancelled.data["status"] == "cancelled"
    assert again.data["status"] == "cancelled"
    assert cancelled.data["allowed_transitions"] == []
    assert level(store.tenant, variant).reserved == Decimal(0)
    with tenant_context(store.tenant):
        assert StockMovement.objects.filter(movement_type="released").count() == 1


def test_day_bounds_are_store_local(store: Any) -> None:
    with tenant_context(store.tenant):
        start, end = day_bounds(date(2026, 11, 3), date(2026, 11, 3))
    assert start.utcoffset() == timedelta(hours=6)
    assert (end - start) == timedelta(days=1)
    assert start.time() == time.min


def test_staff_session_carries_store_facts(store: Any) -> None:
    data = merchant(store.tenant, StaffRole.CONTENT_EDITOR).get("/api/manage/auth/session/").data

    assert data["store_preset"] == "general"
    assert data["store_country"] == "BD"
    assert data["store_currency"] == "BDT"
    assert data["store_timezone"] == "Asia/Dhaka"


def test_shipment_detail_offers_only_valid_staff_steps(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 1)
    owner = merchant(store.tenant)
    placed = create(owner, lines((variant, "1")))
    owner.post(f"/api/manage/orders/{placed.data['id']}/transition/", {"status": "confirmed"})
    prepared = owner.post(f"/api/manage/fulfillment/orders/{placed.data['id']}/shipment/", {})
    shipment_url = f"/api/manage/fulfillment/shipments/{prepared.data['id']}/"

    assert prepared.data["next_statuses"] == ["picking"]
    assert prepared.data["courier_booking_available"] is False
    assert prepared.data["serial_allocations"] == []
    assert prepared.data["payment_method"] == "cod"
    detail = owner.get(f"/api/manage/orders/{placed.data['id']}/").data
    assert detail["shipment_id"] == prepared.data["id"]
    listed = owner.get("/api/manage/fulfillment/shipments/", {"status": ["ready", "picking"]})
    assert [row["id"] for row in listed.data["results"]] == [prepared.data["id"]]
    assert listed.data["results"][0]["order_id"] == placed.data["id"]
    assert (
        owner.get("/api/manage/fulfillment/shipments/", {"status": "delivered"}).data["count"] == 0
    )
    assert owner.get(shipment_url).data["next_statuses"] == ["picking"]


def test_dashboard_days_range_uses_store_today(store: Any) -> None:
    owner = merchant(store.tenant)
    with tenant_context(store.tenant):
        today = store_today()

    one_day = owner.get("/api/manage/dashboard/", {"days": 1}).data
    week = owner.get("/api/manage/reports/sales/", {"days": 7}).data

    assert (one_day["start"], one_day["end"]) == (today.isoformat(), today.isoformat())
    assert week["start"] == (today - timedelta(days=6)).isoformat()
    assert owner.get("/api/manage/dashboard/", {"days": 0}).status_code == 400
