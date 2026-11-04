"""Phase 12 promotions: campaigns, flash sales, precedence, coupons and checkout integrity."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from commerce.audit.models import AuditEvent
from commerce.catalog.models import Category, Collection, CollectionProduct, ProductCategory
from commerce.checkout.models import CheckoutLine, Coupon, CouponDiscountType
from commerce.marketing import promotions
from commerce.marketing.models import (
    Campaign,
    CampaignTarget,
    FlashSale,
    FlashSaleItem,
    FlashSaleUsage,
    PromotionStatus,
    UsageStatus,
)
from commerce.orders.models import OrderItem
from commerce.pricing import services as pricing
from commerce.pricing.models import Price
from commerce.staff.permissions import StaffRole
from commerce.stores import modules
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, simple_product
from .test_checkout import add, product
from .test_orders import merchant, place, review, reviewed, stock

pytestmark = pytest.mark.django_db

CAMPAIGNS = "/api/manage/marketing/campaigns/"
FLASH = "/api/manage/marketing/flash-sales/"
COUPONS = "/api/manage/marketing/coupons/"


def window(start_hours: float = -1, end_hours: float = 24) -> dict[str, str]:
    now = timezone.now()
    return {
        "starts_at": (now + timedelta(hours=start_hours)).isoformat(),
        "ends_at": (now + timedelta(hours=end_hours)).isoformat(),
    }


def campaign(
    staff: APIClient, *, targets: dict[str, list[Any]], activate: bool = True, **fields: Any
) -> Any:
    body = {
        "name": fields.pop("name", "Eid sale"),
        "kind": fields.pop("kind", "discount"),
        "discount_type": fields.pop("discount_type", "percent"),
        "value": fields.pop("value", "10"),
        **window(),
        **fields,
        "targets": {key: [str(pk) for pk in values] for key, values in targets.items()},
    }
    created = staff.post(CAMPAIGNS, body, format="json")
    assert created.status_code == 201, created.data
    if activate:
        active = staff.post(
            f"{CAMPAIGNS}{created.data['id']}/status/", {"action": "activate"}, format="json"
        )
        assert active.status_code == 200, active.data
        return active
    return created


def flash_module(tenant: Tenant, enabled: bool = True) -> None:
    with tenant_context(tenant):
        modules.set_module(key="flash_sales", enabled=enabled, actor=None)


def flash_sale(staff: APIClient, items: list[dict[str, Any]], **fields: Any) -> Any:
    body = {"name": fields.pop("name", "Midnight deal"), **window(), **fields, "items": items}
    created = staff.post(FLASH, body, format="json")
    assert created.status_code == 201, created.data
    active = staff.post(
        f"{FLASH}{created.data['id']}/status/", {"action": "activate"}, format="json"
    )
    assert active.status_code == 200, active.data
    return active


def public_price(client: APIClient, variant: Any) -> dict[str, Any]:
    with tenant_context(Tenant.objects.get(pk=variant.tenant_id)):
        slug = variant.product.slug
    detail = client.get(f"/api/catalog/products/{slug}/")
    assert detail.status_code == 200
    return dict(detail.data["variants"][0]["price"])


# --- Campaigns ------------------------------------------------------------------------------------


def test_campaign_lowers_the_price_everywhere_with_genuine_reference(store: Any) -> None:
    variant = product(store.tenant, amount="1000.00")
    owner = merchant(store.tenant)
    created = campaign(owner, targets={"products": [variant.product_id]}, badge_text="Eid")
    visitor = APIClient()

    price = public_price(visitor, variant)
    listing = visitor.get("/api/catalog/products/").data["results"][0]
    with tenant_context(store.tenant):
        quote = pricing.quote(variant)

    assert created.data["state"] == "live"
    assert price["amount"] == "900.0000"
    assert price["compare_at_amount"] == "1000.0000"
    assert price["promotion"]["kind"] == "campaign"
    assert price["promotion"]["badge"] == "Eid"
    assert listing["price"]["min_amount"] == "900.0000"
    assert listing["price"]["regular_min_amount"] == "1000.0000"
    assert listing["promotion"]["name"] == "Eid sale"
    assert quote is not None
    assert quote.amount == Decimal("900.00")
    assert quote.regular_amount == Decimal("1000.00")
    assert not quote.coupon_eligible
    with tenant_context(store.tenant):
        assert AuditEvent.objects.filter(action="marketing.campaign.activate").count() == 1
        assert AuditEvent.objects.filter(action="marketing.campaign.created").count() == 1


def test_campaign_targets_categories_with_subcategories_and_collections(store: Any) -> None:
    shirts = product(store.tenant, amount="500.00")
    mugs = product(store.tenant, amount="300.00")
    other = product(store.tenant, amount="200.00")
    with tenant_context(store.tenant):
        parent = Category.objects.create(name="Clothing", slug="clothing")
        child = Category.objects.create(name="Shirts", slug="shirts", parent=parent)
        ProductCategory.objects.create(
            product_id=shirts.product_id, category=child, is_primary=True
        )
        kitchen = Collection.objects.create(name="Kitchen", slug="kitchen")
        CollectionProduct.objects.create(collection=kitchen, product_id=mugs.product_id, position=0)
    owner = merchant(store.tenant)
    campaign(owner, targets={"categories": [parent.pk], "collections": [kitchen.pk]})
    visitor = APIClient()

    assert public_price(visitor, shirts)["amount"] == "450.0000"
    assert public_price(visitor, mugs)["amount"] == "270.0000"
    assert public_price(visitor, other)["promotion"] is None


def test_precedence_is_deterministic_and_never_stacks(store: Any) -> None:
    variant = product(store.tenant, amount="1000.00")
    owner = merchant(store.tenant)
    campaign(owner, targets={"products": [variant.product_id]}, value="10")
    best = campaign(
        owner, targets={"products": [variant.product_id]}, value="150", discount_type="fixed"
    )
    campaign(owner, targets={"products": [variant.product_id]}, kind="featured", value=None)

    price = public_price(APIClient(), variant)

    # 1000 - 150 = 850 beats 10 % (900); the discounts are never combined (765).
    assert price["amount"] == "850.0000"
    assert price["promotion"]["name"] == best.data["name"]

    flash_module(store.tenant)
    flash_sale(owner, [{"variant": str(variant.pk), "sale_price": "700.00"}])
    flashed = public_price(APIClient(), variant)
    assert flashed["amount"] == "700.0000"
    assert flashed["promotion"]["kind"] == "flash_sale"


def test_featured_campaign_adds_a_badge_without_changing_the_price(store: Any) -> None:
    variant = product(store.tenant, amount="400.00")
    owner = merchant(store.tenant)
    campaign(
        owner,
        targets={"products": [variant.product_id]},
        kind="featured",
        discount_type="",
        value=None,
        badge_text="New arrival",
    )
    price = public_price(APIClient(), variant)
    assert price["amount"] == "400.0000"
    assert price["compare_at_amount"] is None
    assert price["promotion"] == {
        "kind": "featured",
        "name": "Eid sale",
        "badge": "New arrival",
        "ends_at": price["promotion"]["ends_at"],
    }


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ({"discount_type": "percent", "value": "0"}, "value"),
        ({"discount_type": "percent", "value": "100"}, "value"),
        ({"discount_type": "percent", "value": "12.345"}, "value"),
        ({"discount_type": "fixed", "value": "10.555"}, "value"),
        ({"discount_type": "fixed", "value": "-5"}, "value"),
        ({"discount_type": ""}, "discount_type"),
    ],
)
def test_invalid_discounts_are_refused(
    store: Any, fields: dict[str, Any], error_field: str
) -> None:
    owner = merchant(store.tenant)
    body = {"name": "Bad", "kind": "discount", "value": "5", **window(), **fields}
    response = owner.post(CAMPAIGNS, body, format="json")
    assert response.status_code == 400
    assert error_field in response.data["error"]["details"]


def test_windows_targets_and_lifecycle_are_validated(store: Any) -> None:
    variant = product(store.tenant)
    owner = merchant(store.tenant)
    reversed_window = owner.post(
        CAMPAIGNS,
        {"name": "x", "kind": "discount", "discount_type": "percent", "value": "5", **window(2, 1)},
        format="json",
    )
    too_long = owner.post(
        CAMPAIGNS,
        {
            "name": "x",
            "kind": "discount",
            "discount_type": "percent",
            "value": "5",
            **window(0, 24 * 400),
        },
        format="json",
    )
    missing_target = owner.post(
        CAMPAIGNS,
        {
            "name": "x",
            "kind": "discount",
            "discount_type": "percent",
            "value": "5",
            **window(),
            "targets": {"products": [str(uuid.uuid4())]},
        },
        format="json",
    )
    empty = campaign(owner, targets={}, activate=False)
    no_targets = owner.post(
        f"{CAMPAIGNS}{empty.data['id']}/status/", {"action": "activate"}, format="json"
    )
    live = campaign(owner, targets={"products": [variant.product_id]})
    edit_live = owner.patch(f"{CAMPAIGNS}{live.data['id']}/", {"value": "20"}, format="json")
    paused = owner.post(f"{CAMPAIGNS}{live.data['id']}/status/", {"action": "pause"}, format="json")
    edit_paused = owner.patch(f"{CAMPAIGNS}{live.data['id']}/", {"value": "20"}, format="json")
    cancelled = owner.post(
        f"{CAMPAIGNS}{live.data['id']}/status/", {"action": "cancel"}, format="json"
    )
    reactivate = owner.post(
        f"{CAMPAIGNS}{live.data['id']}/status/", {"action": "activate"}, format="json"
    )

    assert reversed_window.status_code == 400
    assert too_long.status_code == 400
    assert missing_target.status_code == 400
    assert no_targets.status_code == 409
    assert edit_live.status_code == 409
    assert edit_live.data["error"]["code"] == "promotion_locked"
    assert paused.data["state"] == "paused"
    assert edit_paused.status_code == 200
    assert edit_paused.data["value"] == "20.0000"
    assert cancelled.data["state"] == "cancelled"
    assert reactivate.status_code == 409
    assert public_price(APIClient(), variant)["promotion"] is None


def test_scheduled_and_ended_campaigns_do_not_apply(store: Any) -> None:
    variant = product(store.tenant, amount="100.00")
    owner = merchant(store.tenant)
    later = window(start_hours=2, end_hours=5)
    scheduled = campaign(
        owner,
        targets={"products": [variant.product_id]},
        starts_at=later["starts_at"],
        ends_at=later["ends_at"],
    )
    assert scheduled.data["state"] == "scheduled"
    assert public_price(APIClient(), variant)["promotion"] is None
    with tenant_context(store.tenant):
        row = Campaign.objects.get(pk=scheduled.data["id"])
        price = Price.objects.get(variant=variant)
        # Boundaries: the start is inclusive, the end exclusive, on the server clock.
        at_start = promotions.resolve([variant], {variant.pk: price}, now=row.starts_at)
        at_end = promotions.resolve([variant], {variant.pk: price}, now=row.ends_at)
        Campaign.objects.filter(pk=row.pk).update(
            starts_at=timezone.now() - timedelta(days=3), ends_at=timezone.now() - timedelta(days=1)
        )
    assert at_start[variant.pk].amount == Decimal("90.00")
    assert at_end[variant.pk].kind == ""
    assert public_price(APIClient(), variant)["promotion"] is None


def test_fixed_discount_never_reaches_zero_or_another_currency(store: Any) -> None:
    cheap = product(store.tenant, amount="50.00")
    owner = merchant(store.tenant)
    campaign(owner, targets={"products": [cheap.product_id]}, discount_type="fixed", value="50.00")
    with tenant_context(store.tenant):
        row = Campaign.objects.get()
        assert row.currency == "BDT"
        price = Price.objects.get(variant=cheap)
        offer = promotions.resolve([cheap], {cheap.pk: price})[cheap.pk]
        Campaign.objects.filter(pk=row.pk).update(currency="USD", value=Decimal("1"))
        foreign = promotions.resolve([cheap], {cheap.pk: price})[cheap.pk]
    assert offer.kind == ""
    assert offer.amount == Decimal("50.00")
    assert foreign.kind == ""


def test_percentage_rounds_to_the_currency_minor_unit(store: Any) -> None:
    variant = product(store.tenant, amount="99.99")
    campaign(merchant(store.tenant), targets={"products": [variant.product_id]}, value="33.33")
    # 99.99 x 0.6667 = 66.663333 -> 66.66 (BDT has two decimals; half-up).
    assert public_price(APIClient(), variant)["amount"] == "66.6600"


def test_promotion_management_needs_promotions_permission(store: Any, tenant_b: Tenant) -> None:
    variant = product(store.tenant)
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    manager = merchant(store.tenant, StaffRole.MANAGER)
    body = {
        "name": "x",
        "kind": "discount",
        "discount_type": "percent",
        "value": "5",
        **window(),
        "targets": {"products": [str(variant.product_id)]},
    }

    assert editor.post(CAMPAIGNS, body, format="json").status_code == 403
    assert handler.get(CAMPAIGNS).status_code == 403
    assert APIClient().get(CAMPAIGNS).status_code in (401, 403)
    assert editor.get(FLASH).status_code == 403
    assert handler.get(COUPONS).status_code == 403
    created = manager.post(CAMPAIGNS, body, format="json")
    assert created.status_code == 201
    # The browser cannot choose a lifecycle state: unknown fields are ignored.
    sneaky = manager.post(CAMPAIGNS, {**body, "status": "active"}, format="json")
    assert sneaky.data["status"] == "draft"


def test_campaigns_are_tenant_isolated(store: Any, tenant_b: Tenant) -> None:
    variant = product(store.tenant)
    owner = merchant(store.tenant)
    created = campaign(owner, targets={"products": [variant.product_id]})
    with tenant_context(store.tenant):
        assert Campaign.objects.count() == 1
    with tenant_context(tenant_b):
        assert Campaign.objects.count() == 0
        assert CampaignTarget.objects.count() == 0
        assert not Campaign.objects.filter(pk=created.data["id"]).exists()


def test_preview_lists_targeted_variants_with_both_prices(store: Any) -> None:
    variant = product(store.tenant, amount="250.00")
    owner = merchant(store.tenant)
    draft = campaign(owner, targets={"products": [variant.product_id]}, activate=False)
    detail = owner.get(f"{CAMPAIGNS}{draft.data['id']}/")
    assert detail.data["preview"]["variants"] == 1
    row = detail.data["preview"]["rows"][0]
    assert row["regular_amount"] == "250.0000"
    assert row["promotional_amount"] == "225.0000"
    assert detail.data["targets"][0]["kind"] == "product"
    # A draft never changes what customers pay.
    assert public_price(APIClient(), variant)["promotion"] is None


# --- Checkout and orders --------------------------------------------------------------------------


def test_promotional_price_is_snapshotted_and_not_retroactive(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="1000.00")
    stock(store.tenant, variant, 5)
    owner = merchant(store.tenant)
    live = campaign(owner, targets={"products": [variant.product_id]}, value="20")
    customer = APIClient()
    placed = place(customer, reviewed(customer, variant), key=f"p-{uuid.uuid4().hex}")
    assert placed.status_code == 201, placed.data
    owner.post(f"{CAMPAIGNS}{live.data['id']}/status/", {"action": "cancel"}, format="json")

    with tenant_context(store.tenant):
        item = OrderItem.objects.get(order_id=placed.data["id"])
    after = public_price(APIClient(), variant)
    detail = owner.get(f"{CAMPAIGNS}{live.data['id']}/")

    assert placed.data["item_subtotal"] == "800.0000"
    assert item.unit_price == Decimal("800.00")
    assert item.regular_unit_price == Decimal("1000.00")
    assert item.promotion_kind == "campaign"
    assert str(item.promotion_id) == live.data["id"]
    assert after["amount"] == "1000.0000"
    assert detail.data["performance"]["orders"] == 1
    assert detail.data["performance"]["by_currency"][0]["discount"] == "200.0000"


def test_promotion_ending_before_review_follows_the_price_changed_policy(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="500.00")
    stock(store.tenant, variant, 5)
    owner = merchant(store.tenant)
    live = campaign(owner, targets={"products": [variant.product_id]})
    customer = APIClient()
    assert add(customer, variant).status_code == 200
    checkout_id = customer.post("/api/checkout/", {}, format="json").data["id"]
    customer.patch(
        f"/api/checkout/{checkout_id}/",
        {
            "contact_email": "a@example.test",
            "recipient_name": "A",
            "address_line_1": "1 Road",
            "city": "Dhaka",
            "country": "BD",
        },
        format="json",
    )
    method = customer.get(f"/api/checkout/{checkout_id}/").data["shipping_methods"][0]["id"]
    customer.patch(f"/api/checkout/{checkout_id}/", {"shipping_method": method}, format="json")
    assert customer.get(f"/api/checkout/{checkout_id}/").data["item_subtotal"] == "450.0000"

    owner.post(f"{CAMPAIGNS}{live.data['id']}/status/", {"action": "pause"}, format="json")
    changed = review(customer, checkout_id)
    retried = review(customer, checkout_id)

    assert changed.status_code == 409
    assert changed.data["error"]["code"] == "price_changed"
    assert retried.status_code == 200
    assert retried.data["item_subtotal"] == "500.0000"


def test_coupons_never_stack_with_promotional_prices(store: Any) -> None:
    sale = product(store.tenant, tracked=True, amount="1000.00")
    regular = product(store.tenant, tracked=True, amount="400.00")
    stock(store.tenant, sale, 5)
    stock(store.tenant, regular, 5)
    with tenant_context(store.tenant):
        Coupon.objects.create(code="TEN", discount_type=CouponDiscountType.PERCENT, value=10)
        Coupon.objects.create(
            code="FLAT500", discount_type=CouponDiscountType.FIXED, value=500, currency="BDT"
        )
    campaign(merchant(store.tenant), targets={"products": [sale.product_id]}, value="20")

    only_sale = APIClient()
    checkout_id = reviewed_checkout(only_sale, [sale])
    refused = only_sale.patch(
        f"/api/checkout/{checkout_id}/", {"coupon_code": "TEN"}, format="json"
    )

    mixed = APIClient()
    mixed_id = reviewed_checkout(mixed, [sale, regular])
    percent = mixed.patch(f"/api/checkout/{mixed_id}/", {"coupon_code": "TEN"}, format="json")
    fixed = mixed.patch(f"/api/checkout/{mixed_id}/", {"coupon_code": "FLAT500"}, format="json")

    assert refused.status_code == 400
    assert "already on sale" in str(refused.data)
    # 10 % of the regular-price line only (400), not of the sale line (800).
    assert percent.data["discount_total"] == "40.0000"
    # A fixed coupon is capped at the eligible amount.
    assert fixed.data["discount_total"] == "400.0000"
    assert fixed.data["item_subtotal"] == "1200.0000"
    eligible = {line["sku"]: line["coupon_eligible"] for line in fixed.data["lines"]}
    with tenant_context(store.tenant):
        assert eligible[sale.sku] is False
        assert eligible[regular.sku] is True


def reviewed_checkout(client: APIClient, variants: list[Any]) -> str:
    for variant in variants:
        assert add(client, variant).status_code == 200
    checkout_id = str(client.post("/api/checkout/", {}, format="json").data["id"])
    details = client.patch(
        f"/api/checkout/{checkout_id}/",
        {
            "contact_email": "b@example.test",
            "recipient_name": "B",
            "address_line_1": "2 Road",
            "city": "Dhaka",
            "country": "BD",
        },
        format="json",
    )
    client.patch(
        f"/api/checkout/{checkout_id}/",
        {"shipping_method": details.data["shipping_methods"][0]["id"]},
        format="json",
    )
    return checkout_id


def test_existing_coupon_calculation_is_unchanged_without_promotions(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="333.33")
    stock(store.tenant, variant, 3)
    with tenant_context(store.tenant):
        Coupon.objects.create(code="SAVE15", discount_type=CouponDiscountType.PERCENT, value=15)
    client = APIClient()
    checkout_id = reviewed_checkout(client, [variant])
    applied = client.patch(
        f"/api/checkout/{checkout_id}/", {"coupon_code": "SAVE15"}, format="json"
    )
    # 333.33 x 15 % = 49.9995 -> 50.00, as Phase 07 calculated it.
    assert applied.data["discount_total"] == "50.0000"
    assert applied.data["loyalty_discount"] == "0.0000"


# --- Flash sales ----------------------------------------------------------------------------------


def test_flash_sales_need_the_module_and_stop_when_it_is_disabled(store: Any) -> None:
    variant = product(store.tenant, amount="900.00")
    owner = merchant(store.tenant)
    body = {
        "name": "Deal",
        **window(),
        "items": [{"variant": str(variant.pk), "sale_price": "600"}],
    }
    created = owner.post(FLASH, body, format="json")
    blocked = owner.post(
        f"{FLASH}{created.data['id']}/status/", {"action": "activate"}, format="json"
    )
    flash_module(store.tenant)
    activated = owner.post(
        f"{FLASH}{created.data['id']}/status/", {"action": "activate"}, format="json"
    )
    live_price = public_price(APIClient(), variant)
    flash_module(store.tenant, enabled=False)
    off_price = public_price(APIClient(), variant)

    assert blocked.status_code == 409
    assert blocked.data["error"]["code"] == "module_disabled"
    assert activated.status_code == 200
    assert activated.data["state"] == "live"
    assert live_price["amount"] == "600.0000"
    assert live_price["promotion"]["kind"] == "flash_sale"
    assert off_price["amount"] == "900.0000"
    assert off_price["promotion"] is None


def test_flash_sale_prices_are_validated(store: Any) -> None:
    variant = product(store.tenant, amount="100.00")
    unpriced = first_variant(
        store.tenant, simple_product(store.tenant, sku="NOPRICE-1", amount=None)
    )
    owner = merchant(store.tenant)
    for items in (
        [{"variant": str(variant.pk), "sale_price": "100.00"}],
        [{"variant": str(variant.pk), "sale_price": "10.001"}],
        [{"variant": str(unpriced.pk), "sale_price": "5"}],
        [{"variant": str(variant.pk), "sale_price": "5"}] * 2,
    ):
        response = owner.post(FLASH, {"name": "x", **window(), "items": items}, format="json")
        assert response.status_code == 400, items
    too_long = owner.post(
        FLASH,
        {
            "name": "x",
            **window(0, 24 * 15),
            "items": [{"variant": str(variant.pk), "sale_price": "5"}],
        },
        format="json",
    )
    assert too_long.status_code == 400


def test_overlapping_flash_sales_for_a_variant_are_refused(store: Any) -> None:
    variant = product(store.tenant, amount="100.00")
    owner = merchant(store.tenant)
    flash_module(store.tenant)
    flash_sale(owner, [{"variant": str(variant.pk), "sale_price": "80"}])
    second = owner.post(
        FLASH,
        {
            "name": "Other",
            **window(2, 30),
            "items": [{"variant": str(variant.pk), "sale_price": "70"}],
        },
        format="json",
    )
    clash = owner.post(f"{FLASH}{second.data['id']}/status/", {"action": "activate"}, format="json")
    assert clash.status_code == 409
    assert clash.data["error"]["code"] == "flash_sale_overlap"


def test_flash_limits_apply_per_order_and_in_total(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="200.00")
    stock(store.tenant, variant, 10)
    owner = merchant(store.tenant)
    flash_module(store.tenant)
    sale = flash_sale(
        owner,
        [
            {
                "variant": str(variant.pk),
                "sale_price": "120",
                "quantity_limit": 2,
                "per_order_limit": 2,
            }
        ],
    )
    with tenant_context(store.tenant):
        price = Price.objects.get(variant=variant)
        over_order = promotions.resolve(
            [variant], {variant.pk: price}, quantities={variant.pk: Decimal(3)}
        )
    first = APIClient()
    placed = place(first, reviewed(first, variant), key=f"f-{uuid.uuid4().hex}")
    assert placed.data["item_subtotal"] == "120.0000"
    second = APIClient()
    add(second, variant, "2")
    checkout_id = reviewed_checkout(second, [])
    quote = second.get(f"/api/checkout/{checkout_id}/").data

    assert over_order[variant.pk].kind == ""  # 3 units exceed the per-order limit of 2
    # Two units exceed what is left of the 2-unit limit (1 sold): the line is at regular price.
    assert quote["item_subtotal"] == "400.0000"
    detail = owner.get(f"{FLASH}{sale.data['id']}/")
    assert detail.data["items"][0]["units_sold"] == "1.000"
    assert detail.data["performance"]["orders"] == 1


def test_cancellation_releases_flash_units_exactly_once(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="200.00")
    stock(store.tenant, variant, 5)
    owner = merchant(store.tenant)
    flash_module(store.tenant)
    flash_sale(owner, [{"variant": str(variant.pk), "sale_price": "150", "quantity_limit": 1}])
    buyer = APIClient()
    placed = place(buyer, reviewed(buyer, variant), key=f"c-{uuid.uuid4().hex}")
    sold_out = public_price(APIClient(), variant)
    url = f"/api/manage/orders/{placed.data['id']}/transition/"
    owner.post(url, {"status": "cancelled"}, format="json")
    owner.post(url, {"status": "cancelled"}, format="json")
    with tenant_context(store.tenant):
        item = FlashSaleItem.objects.get()
        usage = FlashSaleUsage.objects.get()
    available_again = public_price(APIClient(), variant)

    assert sold_out["promotion"] is None
    assert sold_out["amount"] == "200.0000"
    assert item.units_sold == 0
    assert usage.status == UsageStatus.RELEASED
    assert available_again["amount"] == "150.0000"


@pytest.mark.django_db(transaction=True)
def test_concurrent_placements_never_oversell_a_limited_flash_sale(store: Any) -> None:
    from .test_inventory_concurrency import run_concurrently

    variant = product(store.tenant, tracked=True, amount="300.00")
    stock(store.tenant, variant, 10)
    owner = merchant(store.tenant)
    flash_module(store.tenant)
    flash_sale(owner, [{"variant": str(variant.pk), "sale_price": "100", "quantity_limit": 2}])
    buyers = [APIClient() for _ in range(5)]
    checkouts = [reviewed(buyer, variant) for buyer in buyers]

    results = run_concurrently(
        store.tenant,
        5,
        lambda index: place(
            buyers[index], checkouts[index], key=f"race-{index}-{uuid.uuid4().hex}"
        ),
    )

    placed = [result for result in results if getattr(result, "status_code", 0) == 201]
    refused = [result for result in results if getattr(result, "status_code", 0) == 409]
    with tenant_context(store.tenant):
        item = FlashSaleItem.objects.get()
        committed = FlashSaleUsage.objects.filter(status=UsageStatus.COMMITTED).count()
    assert len(placed) == 2
    assert len(refused) == 3
    assert all(r.data["error"]["code"] == "promotion_unavailable" for r in refused)
    assert item.units_sold == 2
    assert committed == 2


def test_flash_sale_state_is_server_derived(store: Any) -> None:
    variant = product(store.tenant, amount="100.00")
    owner = merchant(store.tenant)
    flash_module(store.tenant)
    sale = flash_sale(owner, [{"variant": str(variant.pk), "sale_price": "50"}], **window(1, 3))
    with tenant_context(store.tenant):
        slug = variant.product.slug
    detail = APIClient().get(f"/api/catalog/products/{slug}/")
    assert sale.data["state"] == "scheduled"
    assert detail.data["variants"][0]["price"]["promotion"] is None
    assert detail.data["upcoming_sale"]["name"] == "Midnight deal"
    assert detail.data["server_time"]
    with tenant_context(store.tenant):
        assert FlashSale.objects.get().status == PromotionStatus.ACTIVE


# --- Coupons --------------------------------------------------------------------------------------


def test_coupon_management_reuses_the_phase_07_rules(store: Any) -> None:
    owner = merchant(store.tenant)
    created = owner.post(
        COUPONS,
        {"code": " welcome10 ", "discount_type": "percent", "value": "10", "max_redemptions": 5},
        format="json",
    )
    duplicate = owner.post(
        COUPONS, {"code": "WELCOME10", "discount_type": "fixed", "value": "50"}, format="json"
    )
    bad_amount = owner.post(
        COUPONS, {"code": "HALF", "discount_type": "fixed", "value": "0.001"}, format="json"
    )
    assert created.status_code == 201
    assert created.data["code"] == "WELCOME10"
    assert duplicate.status_code == 400
    assert bad_amount.status_code == 400
    with tenant_context(store.tenant):
        Coupon.objects.filter(pk=created.data["id"]).update(times_redeemed=2)
    locked = owner.patch(f"{COUPONS}{created.data['id']}/", {"value": "20"}, format="json")
    paused = owner.patch(f"{COUPONS}{created.data['id']}/", {"is_active": False}, format="json")
    below = owner.patch(f"{COUPONS}{created.data['id']}/", {"max_redemptions": 1}, format="json")
    assert locked.status_code == 409
    assert locked.data["error"]["code"] == "coupon_in_use"
    assert paused.status_code == 200
    assert paused.data["is_active"] is False
    assert below.status_code == 400
    with tenant_context(store.tenant):
        assert AuditEvent.objects.filter(action__startswith="marketing.coupon.").count() == 2


def test_checkout_lines_record_the_promotion_snapshot(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="80.00")
    stock(store.tenant, variant, 2)
    live = campaign(merchant(store.tenant), targets={"products": [variant.product_id]}, value="25")
    client = APIClient()
    checkout_id = reviewed(client, variant)
    with tenant_context(store.tenant):
        line = CheckoutLine.objects.get(session_id=uuid.UUID(checkout_id))
    assert line.unit_price == Decimal("60.00")
    assert line.regular_unit_price == Decimal("80.00")
    assert str(line.promotion_id) == live.data["id"]
    assert line.coupon_eligible is False
