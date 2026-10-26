import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from commerce.catalog.models import ProductVariant
from commerce.checkout.models import (
    Address,
    Cart,
    CartItem,
    CheckoutLine,
    CheckoutReservation,
    CheckoutSession,
    Coupon,
    CouponDiscountType,
    DeliverySlot,
    ShippingMethod,
    TaxRate,
)
from commerce.identity import sessions
from commerce.identity.models import AccountType
from commerce.inventory import services as inventory
from commerce.inventory.models import Batch, StockLevel
from commerce.pricing import services as pricing
from commerce.pricing.models import Price
from commerce.tenancy.context import tenant_context
from commerce.tenancy.exceptions import CrossTenantAccessError, TenantContextMissing
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, set_preset, simple_product
from .factories import make_store_settings, make_user

pytestmark = pytest.mark.django_db


def product(tenant: Tenant, *, tracked: bool = False, amount: str = "100.00") -> ProductVariant:
    row = simple_product(
        tenant,
        sku=f"SKU-{uuid.uuid4().hex[:8].upper()}",
        amount=amount,
        publish=True,
        track_inventory=tracked,
    )
    return first_variant(tenant, row)


def add(client: APIClient, variant: ProductVariant, quantity: str = "1") -> Any:
    return client.post(
        "/api/cart/items/", {"variant": str(variant.pk), "quantity": quantity}, format="json"
    )


def prepare_physical_checkout(
    client: APIClient,
    variant: ProductVariant,
    *,
    coupon_code: str = "",
    delivery_slot: DeliverySlot | None = None,
) -> tuple[str, Any]:
    assert add(client, variant).status_code == 200
    created = client.post("/api/checkout/", {}, format="json")
    checkout_id = created.data["id"]
    details = client.patch(
        f"/api/checkout/{checkout_id}/",
        {
            "contact_email": "guest@example.test",
            "recipient_name": "Guest Buyer",
            "address_line_1": "1 Test Street",
            "city": "Dhaka",
            "country": "BD",
            "coupon_code": coupon_code,
        },
        format="json",
    )
    assert details.status_code == 200
    selection = {"shipping_method": details.data["shipping_methods"][0]["id"]}
    if delivery_slot is not None:
        selection["delivery_slot"] = str(delivery_slot.pk)
    selected = client.patch(f"/api/checkout/{checkout_id}/", selection, format="json")
    assert selected.status_code == 200
    return checkout_id, selected


def test_guest_cart_creation_reuse_and_duplicate_line(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant)

    empty = api_client.get("/api/cart/")
    token = empty.cookies["mc_cart"].value
    assert empty.status_code == 200
    assert empty.data["item_count"] == 0

    added = add(api_client, variant, "2")
    repeated = add(api_client, variant, "3")
    assert added.status_code == 200
    assert repeated.status_code == 200
    assert repeated.data["lines"][0]["quantity"] == "5.000"
    assert api_client.cookies["mc_cart"].value == token

    refreshed = api_client.get("/api/cart/")
    assert refreshed.data["lines"][0]["variant"] == str(variant.pk)


def test_guest_ownership_isolation(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant)
    assert add(api_client, variant).status_code == 200
    checkout = api_client.post("/api/checkout/", {}, format="json")
    other = APIClient()

    denied = other.get(f"/api/checkout/{checkout.data['id']}/")

    assert denied.status_code == 403
    assert denied.data["error"]["code"] == "permission_denied"


def test_cart_update_remove_and_stock_validation(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        inventory.receive(
            variant=variant,
            location=inventory.default_location(),
            quantity=2,
            request=inventory.StockRequest(reason="test stock"),
        )
    added = add(api_client, variant, "2")
    item_id = added.data["lines"][0]["id"]

    too_many = api_client.patch(f"/api/cart/items/{item_id}/", {"quantity": "3"}, format="json")
    updated = api_client.patch(f"/api/cart/items/{item_id}/", {"quantity": "1"}, format="json")
    removed = api_client.delete(f"/api/cart/items/{item_id}/")

    assert too_many.status_code == 409
    assert too_many.data["error"]["code"] == "insufficient_stock"
    assert updated.data["lines"][0]["quantity"] == "1.000"
    assert removed.data["lines"] == []


def test_unpublished_and_missing_price_variants_are_rejected(
    store: Any, api_client: APIClient
) -> None:
    draft = simple_product(store.tenant, sku="DRAFT-1", publish=False, track_inventory=False)
    no_price = simple_product(
        store.tenant,
        sku="NOPRICE-1",
        amount=None,
        publish=False,
        track_inventory=False,
    )
    with tenant_context(store.tenant):
        no_price.status = "active"
        no_price.published_at = timezone.now()
        no_price.save(update_fields=["status", "published_at", "updated_at"])

    assert add(api_client, first_variant(store.tenant, draft)).status_code == 409
    response = add(api_client, first_variant(store.tenant, no_price))
    assert response.status_code == 409
    assert response.data["error"]["code"] == "price_unavailable"


def test_sign_in_associates_guest_cart_without_leaking_it(
    store: Any, api_client: APIClient
) -> None:
    variant = product(store.tenant)
    assert add(api_client, variant).status_code == 200
    guest_token = api_client.cookies["mc_cart"].value
    customer = make_user(
        store.tenant,
        account_type=AccountType.CUSTOMER,
        email="buyer@example.test",
        password="customer-passphrase-1234",
    )
    with tenant_context(store.tenant):
        _, session_token = sessions.start(customer)
    api_client.cookies["mc_customer"] = session_token

    associated = api_client.get("/api/cart/")
    assert associated.status_code == 200
    assert associated.data["item_count"] == 1
    assert associated.cookies["mc_cart"].value == ""

    old_browser = APIClient()
    old_browser.cookies["mc_cart"] = guest_token
    assert old_browser.get("/api/cart/").data["item_count"] == 0


def test_digital_checkout_does_not_require_shipping(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        variant.kind = "digital"
        variant.digital_format = "pdf"
        variant.save(update_fields=["kind", "digital_format", "updated_at"])
    assert add(api_client, variant).status_code == 200
    created = api_client.post("/api/checkout/", {}, format="json")
    checkout_id = created.data["id"]
    details = api_client.patch(
        f"/api/checkout/{checkout_id}/",
        {"contact_email": "guest@example.test"},
        format="json",
    )
    reviewed = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "digital-review-1"},
        format="json",
    )

    assert created.data["requires_shipping"] is False
    assert details.data["shipping_methods"] == []
    assert reviewed.status_code == 200
    assert reviewed.data["status"] == "reviewed"
    assert reviewed.data["order_creation_available"] is False


def test_physical_review_totals_reservation_idempotency_and_release(
    store: Any, api_client: APIClient
) -> None:
    variant = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        location = inventory.default_location()
        inventory.receive(
            variant=variant,
            location=location,
            quantity=10,
            request=inventory.StockRequest(reason="test stock"),
        )
        Coupon.objects.create(
            code="SAVE10",
            discount_type=CouponDiscountType.PERCENT,
            value=Decimal("10"),
            minimum_subtotal=Decimal("100"),
        )
        TaxRate.objects.create(
            name="Test tax",
            country="BD",
            rate=Decimal("0.15"),
            applies_to_shipping=True,
        )
    assert add(api_client, variant, "2").status_code == 200
    created = api_client.post("/api/checkout/", {}, format="json")
    checkout_id = created.data["id"]
    address = api_client.patch(
        f"/api/checkout/{checkout_id}/",
        {
            "contact_email": "guest@example.test",
            "recipient_name": "Guest Buyer",
            "address_line_1": "1 Test Street",
            "city": "Dhaka",
            "country": "BD",
            "coupon_code": "save10",
        },
        format="json",
    )
    method_id = address.data["shipping_methods"][0]["id"]
    with tenant_context(store.tenant):
        ShippingMethod.objects.filter(pk=method_id).update(amount=Decimal("30"))

    details = api_client.patch(
        f"/api/checkout/{checkout_id}/",
        {
            "shipping_method": method_id,
        },
        format="json",
    )
    reviewed = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "physical-review-1"},
        format="json",
    )
    replay = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "physical-review-1"},
        format="json",
    )
    conflict = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "physical-review-2"},
        format="json",
    )

    assert details.data["item_subtotal"] == "200.0000"
    assert details.data["discount_total"] == "20.0000"
    assert details.data["shipping_total"] == "30.0000"
    assert details.data["tax_total"] == "31.5000"
    assert details.data["grand_total"] == "241.5000"
    assert reviewed.status_code == replay.status_code == 200
    assert conflict.status_code == 409
    assert conflict.data["error"]["code"] == "duplicate_submission"
    with tenant_context(store.tenant):
        assert StockLevel.objects.get(variant=variant, location=location).reserved == 2

    released = api_client.post(f"/api/checkout/{checkout_id}/release/", {}, format="json")
    assert released.data["status"] == "released"
    with tenant_context(store.tenant):
        assert StockLevel.objects.get(variant=variant, location=location).reserved == 0


@pytest.mark.parametrize(
    "coupon_change",
    [
        {"is_active": False},
        {"ends_at": timezone.now() - timedelta(seconds=1)},
        {"max_redemptions": 1, "times_redeemed": 1},
        {"minimum_subtotal": Decimal("101")},
    ],
    ids=["disabled", "expired", "exhausted", "minimum-increased"],
)
def test_coupon_is_revalidated_before_stock_reservation(
    store: Any, api_client: APIClient, coupon_change: dict[str, Any]
) -> None:
    variant = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        location = inventory.default_location()
        inventory.receive(
            variant=variant,
            location=location,
            quantity=2,
            request=inventory.StockRequest(reason="coupon review stock"),
        )
        coupon = Coupon.objects.create(
            code="REVIEW10",
            discount_type=CouponDiscountType.PERCENT,
            value=Decimal("10"),
            ends_at=timezone.now() + timedelta(days=1),
        )
    checkout_id, _ = prepare_physical_checkout(api_client, variant, coupon_code=coupon.code)
    with tenant_context(store.tenant):
        Coupon.objects.filter(pk=coupon.pk).update(**coupon_change)

    reviewed = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": f"invalid-coupon-{uuid.uuid4()}"},
        format="json",
    )

    assert reviewed.status_code == 400
    assert "coupon_code" in reviewed.data["error"]["details"]
    with tenant_context(store.tenant):
        assert StockLevel.objects.get(variant=variant, location=location).reserved == 0
        assert CheckoutReservation.objects.filter(session_id=uuid.UUID(checkout_id)).count() == 0


def test_review_recalculates_a_still_valid_coupon_from_current_configuration(
    store: Any, api_client: APIClient
) -> None:
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        coupon = Coupon.objects.create(
            code="CURRENT",
            discount_type=CouponDiscountType.PERCENT,
            value=Decimal("10"),
        )
    checkout_id, selected = prepare_physical_checkout(api_client, variant, coupon_code=coupon.code)
    assert selected.data["grand_total"] == "90.0000"
    with tenant_context(store.tenant):
        Coupon.objects.filter(pk=coupon.pk).update(value=Decimal("20"))

    reviewed = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "current-coupon-review"},
        format="json",
    )

    assert reviewed.status_code == 200
    assert reviewed.data["discount_total"] == "20.0000"
    assert reviewed.data["grand_total"] == "80.0000"


def test_one_cart_cannot_hold_two_reviewed_checkouts(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        location = inventory.default_location()
        inventory.receive(
            variant=variant,
            location=location,
            quantity=2,
            request=inventory.StockRequest(reason="single cart hold"),
        )
    first_id, _ = prepare_physical_checkout(api_client, variant)
    with tenant_context(store.tenant):
        first = CheckoutSession.objects.get(pk=first_id)
        second = CheckoutSession.objects.create(
            cart=first.cart,
            guest_token_hash=first.guest_token_hash,
            expires_at=first.expires_at,
            contact_email=first.contact_email,
            recipient_name=first.recipient_name,
            address_line_1=first.address_line_1,
            city=first.city,
            country=first.country,
            shipping_method=first.shipping_method,
            requires_shipping=True,
            currency=first.currency,
            item_subtotal=first.item_subtotal,
            grand_total=first.grand_total,
        )
        line = CheckoutLine.objects.get(session=first)
        CheckoutLine.objects.create(
            session=second,
            variant=line.variant,
            product_name=line.product_name,
            variant_title=line.variant_title,
            sku=line.sku,
            kind=line.kind,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_subtotal=line.line_subtotal,
            price_id=line.price_id,
        )

    first_review = api_client.post(
        f"/api/checkout/{first_id}/review/",
        {"idempotency_key": "single-cart-first"},
        format="json",
    )
    duplicate = api_client.post(
        f"/api/checkout/{second.pk}/review/",
        {"idempotency_key": "single-cart-second"},
        format="json",
    )
    reused = api_client.post("/api/checkout/", {}, format="json")

    assert first_review.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.data["error"]["code"] == "active_checkout_exists"
    assert reused.data["id"] == first_id
    with tenant_context(store.tenant):
        assert StockLevel.objects.get(variant=variant, location=location).reserved == 1
        assert CheckoutReservation.objects.filter(released_at__isnull=True).count() == 1

    assert (
        api_client.post(f"/api/checkout/{first_id}/release/", {}, format="json").status_code == 200
    )
    replacement = api_client.post("/api/checkout/", {}, format="json")
    assert replacement.data["id"] == str(second.pk)
    reviewed_again = api_client.post(
        f"/api/checkout/{second.pk}/review/",
        {"idempotency_key": "single-cart-after-release"},
        format="json",
    )
    assert reviewed_again.status_code == 200
    with tenant_context(store.tenant):
        CheckoutSession.objects.filter(pk=second.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    after_expiry = api_client.post("/api/checkout/", {}, format="json")
    assert after_expiry.status_code == 201
    assert after_expiry.data["id"] not in (first_id, str(second.pk))
    with tenant_context(store.tenant):
        second.refresh_from_db()
        assert second.status == "expired"
        assert StockLevel.objects.get(variant=variant, location=location).reserved == 0


def test_grocery_slot_capacity_is_held_and_released(store: Any, api_client: APIClient) -> None:
    set_preset(store.tenant, "grocery")
    variant = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        location = inventory.default_location()
        inventory.receive(
            variant=variant,
            location=location,
            quantity=3,
            request=inventory.StockRequest(reason="delivery slot stock"),
        )
        slot = DeliverySlot.objects.create(
            label="Only place",
            starts_at=timezone.now() + timedelta(hours=2),
            ends_at=timezone.now() + timedelta(hours=4),
            capacity=1,
        )
    other = APIClient()
    first_id, _ = prepare_physical_checkout(api_client, variant, delivery_slot=slot)
    second_id, _ = prepare_physical_checkout(other, variant, delivery_slot=slot)

    first = api_client.post(
        f"/api/checkout/{first_id}/review/",
        {"idempotency_key": "slot-first-review"},
        format="json",
    )
    replay = api_client.post(
        f"/api/checkout/{first_id}/review/",
        {"idempotency_key": "slot-first-review"},
        format="json",
    )
    full = other.post(
        f"/api/checkout/{second_id}/review/",
        {"idempotency_key": "slot-second-review"},
        format="json",
    )

    assert first.status_code == replay.status_code == 200
    assert full.status_code == 400
    assert "delivery_slot" in full.data["error"]["details"]

    api_client.post(f"/api/checkout/{first_id}/release/", {}, format="json")
    available = other.post(
        f"/api/checkout/{second_id}/review/",
        {"idempotency_key": "slot-second-review"},
        format="json",
    )
    assert available.status_code == 200


def test_grocery_slot_committed_bookings_and_current_state_are_enforced(
    store: Any, api_client: APIClient
) -> None:
    set_preset(store.tenant, "grocery")
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        slot = DeliverySlot.objects.create(
            label="Booked",
            starts_at=timezone.now() + timedelta(hours=2),
            ends_at=timezone.now() + timedelta(hours=4),
            capacity=1,
            bookings=0,
        )
    checkout_id, _ = prepare_physical_checkout(api_client, variant, delivery_slot=slot)
    with tenant_context(store.tenant):
        DeliverySlot.objects.filter(pk=slot.pk).update(bookings=1)

    booked = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "booked-slot-review"},
        format="json",
    )
    assert booked.status_code == 400
    assert "delivery_slot" in booked.data["error"]["details"]

    with tenant_context(store.tenant):
        DeliverySlot.objects.filter(pk=slot.pk).update(bookings=0, is_active=False)
    inactive = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "inactive-slot-review"},
        format="json",
    )
    assert inactive.status_code == 400
    assert "delivery_slot" in inactive.data["error"]["details"]

    with tenant_context(store.tenant):
        DeliverySlot.objects.filter(pk=slot.pk).update(
            is_active=True,
            starts_at=timezone.now() - timedelta(hours=2),
            ends_at=timezone.now() - timedelta(hours=1),
        )
    expired = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "expired-slot-review"},
        format="json",
    )
    assert expired.status_code == 400
    assert "delivery_slot" in expired.data["error"]["details"]


def test_grocery_digital_checkout_does_not_require_a_configured_slot(
    store: Any, api_client: APIClient
) -> None:
    set_preset(store.tenant, "grocery")
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        variant.kind = "digital"
        variant.digital_format = "pdf"
        variant.save(update_fields=["kind", "digital_format", "updated_at"])
        DeliverySlot.objects.create(
            label="Full physical slot",
            starts_at=timezone.now() + timedelta(hours=2),
            ends_at=timezone.now() + timedelta(hours=4),
            capacity=1,
            bookings=1,
        )
    assert add(api_client, variant).status_code == 200
    created = api_client.post("/api/checkout/", {}, format="json")
    api_client.patch(
        f"/api/checkout/{created.data['id']}/",
        {"contact_email": "digital@example.test"},
        format="json",
    )

    reviewed = api_client.post(
        f"/api/checkout/{created.data['id']}/review/",
        {"idempotency_key": "grocery-digital-review"},
        format="json",
    )
    assert reviewed.status_code == 200
    assert reviewed.data["delivery_slots"] == []


@pytest.mark.parametrize("preset", ["fashion", "electronics", "books", "general"])
def test_non_grocery_presets_do_not_require_delivery_slots(
    store: Any, api_client: APIClient, preset: str
) -> None:
    set_preset(store.tenant, preset)
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        DeliverySlot.objects.create(
            label="Grocery only",
            starts_at=timezone.now() + timedelta(hours=2),
            ends_at=timezone.now() + timedelta(hours=4),
            capacity=1,
            bookings=1,
        )
    checkout_id, selected = prepare_physical_checkout(api_client, variant)

    reviewed = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": f"{preset}-without-slot"},
        format="json",
    )

    assert selected.data["delivery_slots"] == []
    assert reviewed.status_code == 200


def test_expired_slot_hold_does_not_block_another_checkout(
    store: Any, api_client: APIClient
) -> None:
    set_preset(store.tenant, "grocery")
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        slot = DeliverySlot.objects.create(
            label="Reusable place",
            starts_at=timezone.now() + timedelta(hours=2),
            ends_at=timezone.now() + timedelta(hours=4),
            capacity=1,
        )
    other = APIClient()
    first_id, _ = prepare_physical_checkout(api_client, variant, delivery_slot=slot)
    second_id, _ = prepare_physical_checkout(other, variant, delivery_slot=slot)
    assert (
        api_client.post(
            f"/api/checkout/{first_id}/review/",
            {"idempotency_key": "expiring-slot-hold"},
            format="json",
        ).status_code
        == 200
    )
    with tenant_context(store.tenant):
        CheckoutSession.objects.filter(pk=first_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    reviewed = other.post(
        f"/api/checkout/{second_id}/review/",
        {"idempotency_key": "after-expired-slot"},
        format="json",
    )
    assert reviewed.status_code == 200


def test_explicit_shipping_configuration_does_not_get_a_free_fallback(
    store: Any, api_client: APIClient
) -> None:
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        ShippingMethod.objects.create(
            code="us-only",
            name="US only",
            amount=Decimal("25"),
            currency="BDT",
            supported_countries=["US"],
        )
    assert add(api_client, variant).status_code == 200
    created = api_client.post("/api/checkout/", {}, format="json")
    details = api_client.patch(
        f"/api/checkout/{created.data['id']}/",
        {
            "contact_email": "guest@example.test",
            "recipient_name": "Guest",
            "address_line_1": "1 Test Street",
            "city": "Dhaka",
            "country": "BD",
        },
        format="json",
    )

    assert details.data["shipping_methods"] == []
    with tenant_context(store.tenant):
        assert not ShippingMethod.objects.filter(code="standard").exists()


def test_changed_price_requires_fresh_review(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant)
    with tenant_context(store.tenant):
        variant.kind = "digital"
        variant.digital_format = "pdf"
        variant.save(update_fields=["kind", "digital_format", "updated_at"])
    assert add(api_client, variant).status_code == 200
    created = api_client.post("/api/checkout/", {}, format="json")
    checkout_id = created.data["id"]
    api_client.patch(
        f"/api/checkout/{checkout_id}/",
        {"contact_email": "guest@example.test"},
        format="json",
    )
    with tenant_context(store.tenant):
        pricing.set_prices(
            variant=variant,
            entries=[{"amount": Decimal("120.00")}],
            actor=None,
            allow_cost=True,
        )

    changed = api_client.post(
        f"/api/checkout/{checkout_id}/review/",
        {"idempotency_key": "changed-price-1"},
        format="json",
    )
    refreshed = api_client.get(f"/api/checkout/{checkout_id}/")

    assert changed.status_code == 409
    assert changed.data["error"]["code"] == "price_changed"
    assert refreshed.data["item_subtotal"] == "120.0000"
    assert refreshed.data["status"] == "draft"


def test_checkout_expiration_is_enforced(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant)
    assert add(api_client, variant).status_code == 200
    created = api_client.post("/api/checkout/", {}, format="json")
    with tenant_context(store.tenant):
        CheckoutSession.objects.filter(pk=created.data["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    expired = api_client.get(f"/api/checkout/{created.data['id']}/")
    assert expired.status_code == 409
    assert expired.data["error"]["code"] == "checkout_expired"


def test_two_checkouts_cannot_reserve_the_same_last_unit(store: Any) -> None:
    variant = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        location = inventory.default_location()
        inventory.receive(
            variant=variant,
            location=location,
            quantity=1,
            request=inventory.StockRequest(reason="last unit"),
        )
    clients = [APIClient(), APIClient()]
    checkout_ids: list[str] = []
    for client in clients:
        assert add(client, variant).status_code == 200
        created = client.post("/api/checkout/", {}, format="json")
        checkout_ids.append(created.data["id"])
        addressed = client.patch(
            f"/api/checkout/{created.data['id']}/",
            {
                "contact_email": "guest@example.test",
                "recipient_name": "Guest",
                "address_line_1": "1 Test Street",
                "city": "Dhaka",
                "country": "BD",
            },
            format="json",
        )
        client.patch(
            f"/api/checkout/{created.data['id']}/",
            {"shipping_method": addressed.data["shipping_methods"][0]["id"]},
            format="json",
        )

    first = clients[0].post(
        f"/api/checkout/{checkout_ids[0]}/review/",
        {"idempotency_key": "last-unit-first"},
        format="json",
    )
    second = clients[1].post(
        f"/api/checkout/{checkout_ids[1]}/review/",
        {"idempotency_key": "last-unit-second"},
        format="json",
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.data["error"]["code"] == "cart_invalid"
    with tenant_context(store.tenant):
        level = StockLevel.objects.get(variant=variant, location=location)
        assert (level.on_hand, level.reserved, level.available) == (
            Decimal(1),
            Decimal(1),
            Decimal(0),
        )


def test_expired_batch_stock_is_rejected_before_checkout(store: Any, api_client: APIClient) -> None:
    variant = product(store.tenant, tracked=True)
    with tenant_context(store.tenant):
        variant.product.tracking_mode = "batch"
        variant.product.save(update_fields=["tracking_mode", "updated_at"])
        inventory.receive(
            variant=variant,
            location=inventory.default_location(),
            quantity=2,
            batch=inventory.BatchInput(
                code="EXPIRING", expires_on=timezone.localdate() + timedelta(days=1)
            ),
            request=inventory.StockRequest(reason="expiring stock"),
        )
        Batch.objects.filter(variant=variant, code="EXPIRING").update(
            expires_on=timezone.localdate() - timedelta(days=1)
        )

    rejected = add(api_client, variant)

    assert rejected.status_code == 409
    assert rejected.data["error"]["code"] == "insufficient_stock"


def test_customer_addresses_are_isolated(store: Any, api_client: APIClient) -> None:
    owner = make_user(store.tenant, account_type=AccountType.CUSTOMER, email="owner@example.test")
    other = make_user(store.tenant, account_type=AccountType.CUSTOMER, email="other@example.test")
    with tenant_context(store.tenant):
        address = Address.objects.create(
            customer=other,
            recipient_name="Other",
            phone="+8801712345678",
            address_line_1="Other street",
            city="Dhaka",
            country="BD",
        )
        _, token = sessions.start(owner)
    api_client.cookies["mc_customer"] = token

    assert api_client.get("/api/account/addresses/").data == []
    assert (
        api_client.patch(
            f"/api/account/addresses/{address.pk}/", {"city": "Changed"}, format="json"
        ).status_code
        == 403
    )


def checkout_graph(tenant: Tenant) -> dict[str, Any]:
    make_store_settings(tenant)
    variant = product(tenant)
    customer = make_user(
        tenant, account_type=AccountType.CUSTOMER, email=f"buyer@{tenant.handle}.test"
    )
    now = timezone.now()
    with tenant_context(tenant):
        cart = Cart.objects.create(customer=customer, expires_at=now + timedelta(days=1))
        item = CartItem.objects.create(cart=cart, variant=variant, quantity=1)
        address = Address.objects.create(
            customer=customer,
            recipient_name="Buyer",
            phone="+8801712345678",
            address_line_1="Street",
            city="Dhaka",
            country="BD",
        )
        shipping = ShippingMethod.objects.create(
            code="standard", name="Standard", amount=0, currency="BDT"
        )
        tax = TaxRate.objects.create(name="Tax", country="BD", rate=Decimal("0.1"))
        coupon = Coupon.objects.create(
            code="SAVE10",
            discount_type=CouponDiscountType.PERCENT,
            value=10,
        )
        slot = DeliverySlot.objects.create(
            label="Morning",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=2),
        )
        checkout = CheckoutSession.objects.create(
            cart=cart,
            customer=customer,
            expires_at=now + timedelta(minutes=30),
            currency="BDT",
            shipping_method=shipping,
            coupon=coupon,
            delivery_slot=slot,
        )
        price = Price.objects.get(variant=variant)
        line = CheckoutLine.objects.create(
            session=checkout,
            variant=variant,
            product_name=variant.product.name,
            variant_title=variant.title,
            sku=variant.sku,
            kind=variant.kind,
            quantity=1,
            unit_price=price.amount,
            line_subtotal=price.amount,
            price_id=price.pk,
        )
        location = inventory.default_location()
        reservation = CheckoutReservation.objects.create(
            session=checkout,
            variant=variant,
            location=location,
            quantity=1,
            operation_id=variant.pk,
            expires_at=checkout.expires_at,
        )
        return {
            "cart": cart,
            "item": item,
            "address": address,
            "shipping": shipping,
            "tax": tax,
            "coupon": coupon,
            "slot": slot,
            "checkout": checkout,
            "line": line,
            "reservation": reservation,
        }


@pytest.mark.parametrize(
    "name",
    [
        "cart",
        "item",
        "address",
        "shipping",
        "tax",
        "coupon",
        "slot",
        "checkout",
        "line",
        "reservation",
    ],
)
def test_every_checkout_model_is_tenant_scoped(
    tenant_a: Tenant, tenant_b: Tenant, name: str
) -> None:
    foreign = checkout_graph(tenant_b)[name]
    model = type(foreign)
    with tenant_context(tenant_a):
        assert not model.objects.filter(pk=foreign.pk).exists()
        with pytest.raises(CrossTenantAccessError):
            foreign.refresh_from_db()
    with pytest.raises(TenantContextMissing):
        model.objects.filter(pk=foreign.pk).exists()
