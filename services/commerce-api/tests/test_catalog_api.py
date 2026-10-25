"""Merchant catalogue, pricing and inventory APIs and the public catalogue, over HTTP."""

from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

import pytest
from rest_framework.test import APIClient

from commerce.catalog.models import AttributeDefinition, AttributeOption, Category
from commerce.identity import sessions
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import (
    STAFF_COOKIE,
    first_variant,
    sign_in_staff,
    simple_product,
    size_attribute,
)
from .factories import make_store_settings, make_user
from .helpers import Api

pytestmark = pytest.mark.usefixtures("store")

PRODUCTS = "/api/manage/catalog/products/"


@pytest.fixture
def owner(api: Api, deployment_tenant: Tenant) -> Api:
    sign_in_staff(api, deployment_tenant, StaffRole.OWNER, "owner@example.com")
    return api


@pytest.fixture
def editor(make_api: Callable[[], Api], deployment_tenant: Tenant) -> Api:
    browser = make_api()
    sign_in_staff(browser, deployment_tenant, StaffRole.CONTENT_EDITOR, "editor@example.com")
    return browser


def shirt_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Oxford Shirt",
        "product_type": "apparel",
        "options": ["size"],
        "variants": [
            {
                "sku": "ox-s",
                "options": {"size": "s"},
                "price": {"amount": "1200.00", "cost_amount": "713.37"},
            },
            {
                "sku": "ox-m",
                "options": {"size": "m"},
                "price": {"amount": "1250.00", "compare_at_amount": "1400"},
            },
        ],
        "apparel": {"material": "Cotton", "fit": "Regular", "audience": "men"},
        "tags": ["Shirts", "shirts", " Office "],
        **overrides,
    }


# --- Access ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (None, 401),
        ("customer", 401),
        (StaffRole.ORDER_HANDLER, 403),
        (StaffRole.CONTENT_EDITOR, 200),
        (StaffRole.MANAGER, 200),
        (StaffRole.OWNER, 200),
    ],
)
def test_catalogue_management_needs_catalog_manage(
    api: Api, deployment_tenant: Tenant, role: str | None, expected: int
) -> None:
    if role == "customer":
        customer = make_user(
            deployment_tenant, email="shopper@example.com", password="shopper-pass-123"
        )
        with tenant_context(deployment_tenant):
            _, token = sessions.start(customer)
        api.client.cookies[STAFF_COOKIE] = token  # a customer session in the staff cookie
    elif role is not None:
        sign_in_staff(api, deployment_tenant, role, f"{role}@example.com")

    for url in (
        PRODUCTS,
        "/api/manage/inventory/levels/",
        "/api/manage/pricing/price-lists/",
        "/api/manage/media/assets/",
    ):
        assert api.get(url).status_code == expected, url
    # Permitted roles reach validation (no "size" attribute exists here); others never do.
    assert api.post(PRODUCTS, shirt_payload()).status_code == (400 if expected == 200 else expected)


def test_mutations_need_the_csrf_token(deployment_tenant: Tenant) -> None:
    strict = Api(APIClient(enforce_csrf_checks=True), lambda **_: nullcontext())
    sign_in_staff(strict, deployment_tenant, StaffRole.OWNER, "owner@example.com")
    response = strict.post("/api/manage/catalog/brands/", {"name": "Acme"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


# --- Products -------------------------------------------------------------------------------------


def test_owner_creates_and_publishes_a_variant_product(
    owner: Api, deployment_tenant: Tenant
) -> None:
    size_attribute(deployment_tenant)
    created = owner.post(PRODUCTS, shirt_payload())
    assert created.status_code == 201, created.json()
    product = created.json()
    assert (product["status"], product["slug"], product["tags"]) == (
        "draft",
        "oxford-shirt",
        ["shirts", "office"],
    )
    assert [v["sku"] for v in product["variants"]] == ["OX-S", "OX-M"]
    assert [v["title"] for v in product["variants"]] == ["S", "M"]
    small = product["variants"][0]
    assert small["prices"][0]["amount"] == "1200.0000"
    assert small["prices"][0]["cost_amount"] == "713.3700"
    assert small["stock"] == {"on_hand": "0.000", "reserved": "0.000", "available": "0.000"}
    assert product["apparel"]["material"] == "Cotton"
    assert product["publication_problems"] == []

    published = owner.post(f"{PRODUCTS}{product['id']}/status/", {"status": "active"})
    assert published.status_code == 200
    assert published.json()["status"] == "active"


def test_cost_prices_are_hidden_from_content_editors(
    owner: Api, editor: Api, deployment_tenant: Tenant
) -> None:
    size_attribute(deployment_tenant)
    product_id = owner.post(PRODUCTS, shirt_payload()).json()["id"]

    seen = editor.get(f"{PRODUCTS}{product_id}/").json()
    assert all("cost_amount" not in price for v in seen["variants"] for price in v["prices"])
    variant_id = seen["variants"][0]["id"]
    refused = editor.put(
        f"/api/manage/pricing/variants/{variant_id}/prices/",
        {"prices": [{"amount": "1100", "cost_amount": "1"}]},
    )
    assert refused.status_code == 403
    allowed = editor.put(
        f"/api/manage/pricing/variants/{variant_id}/prices/", {"prices": [{"amount": "1100"}]}
    )
    assert allowed.status_code == 200
    assert "cost_amount" not in allowed.json()["prices"][0]
    with_cost = owner.get(f"/api/manage/pricing/variants/{variant_id}/prices/").json()
    assert with_cost["prices"][0]["cost_amount"] == "713.3700"  # kept when omitted
    create_with_cost = editor.post(
        PRODUCTS,
        shirt_payload(
            name="Other",
            variants=[
                {
                    "sku": "o-1",
                    "options": {"size": "l"},
                    "price": {"amount": "1", "cost_amount": "1"},
                }
            ],
        ),
    )
    assert create_with_cost.status_code == 403


def test_publication_problems_are_reported(owner: Api) -> None:
    product = owner.post(PRODUCTS, {"name": "No price", "variants": [{"sku": "np-1"}]}).json()
    refused = owner.post(f"{PRODUCTS}{product['id']}/status/", {"status": "active"})
    assert refused.status_code == 400
    error = refused.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["publication"] == ["Set a price for NP-1."]
    assert owner.get(f"{PRODUCTS}{product['id']}/").json()["publication_problems"] == [
        "Set a price for NP-1."
    ]


def test_product_lists_filter_search_sort_and_paginate(
    owner: Api, deployment_tenant: Tenant
) -> None:
    with tenant_context(deployment_tenant):
        shoes = Category.objects.create(name="Shoes", slug="shoes")
    simple_product(deployment_tenant, sku="RUN-1", name="Runner", categories=[shoes], publish=True)
    simple_product(deployment_tenant, sku="BOOT-1", name="Boot", tags=["winter"])
    simple_product(deployment_tenant, sku="SANDAL-1", name="Sandal", amount="99.50")

    def names(**query: Any) -> list[str]:
        response = owner.get(PRODUCTS, **query)
        assert response.status_code == 200, response.json()
        return [row["name"] for row in response.json()["results"]]

    assert names(ordering="name") == ["Boot", "Runner", "Sandal"]
    assert names(status="active") == ["Runner"]
    assert names(category=str(shoes.pk)) == ["Runner"]
    assert names(q="sandal-1") == ["Sandal"]
    assert names(q="winter") == ["Boot"]
    page = owner.get(PRODUCTS, ordering="name", page=2, page_size=2).json()
    assert (page["count"], page["page"], [r["name"] for r in page["results"]]) == (3, 2, ["Sandal"])
    row = owner.get(PRODUCTS, q="Sandal").json()["results"][0]
    assert row["price_range"] == {
        "currency": "BDT",
        "min_amount": "99.5000",
        "max_amount": "99.5000",
    }
    assert (row["variant_count"], row["available"]) == (1, "0.000")
    assert owner.get(PRODUCTS, ordering="price").status_code == 400
    assert owner.get(PRODUCTS, page_size=500).status_code == 400


def test_other_stores_products_are_not_found(owner: Api, tenant_b: Tenant) -> None:
    make_store_settings(tenant_b)
    foreign = simple_product(tenant_b)
    response = owner.get(f"{PRODUCTS}{foreign.pk}/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert owner.patch(f"{PRODUCTS}{foreign.pk}/", {"name": "Taken"}).status_code == 404
    variant = first_variant(tenant_b, foreign)
    assert owner.get(f"/api/manage/catalog/variants/{variant.pk}/").status_code == 404
    with tenant_context(tenant_b):
        foreign.refresh_from_db()
        assert foreign.name == "Plain Tee"


def test_category_tree_and_moves_over_the_api(owner: Api) -> None:
    root = owner.post("/api/manage/catalog/categories/", {"name": "Men"}).json()
    child = owner.post(
        "/api/manage/catalog/categories/", {"name": "Shirts", "parent": root["id"]}
    ).json()
    loop = owner.patch(f"/api/manage/catalog/categories/{root['id']}/", {"parent": child["id"]})
    assert loop.status_code == 400
    tree = owner.get("/api/manage/catalog/categories/tree/").json()
    assert [(n["slug"], [c["slug"] for c in n["children"]]) for n in tree] == [("men", ["shirts"])]


def test_attributes_and_options_over_the_api(owner: Api, deployment_tenant: Tenant) -> None:
    created = owner.post(
        "/api/manage/catalog/attributes/",
        {"code": "colour", "name": "Colour", "value_type": "option", "scope": "variant"},
    )
    assert created.status_code == 201
    option = owner.post(
        f"/api/manage/catalog/attributes/{created.json()['id']}/options/",
        {"value": "Navy Blue", "swatch": "#1f2a44"},
    )
    assert option.status_code == 201
    assert (option.json()["code"], option.json()["swatch"]) == ("navy-blue", "#1F2A44")
    invalid = owner.post(
        "/api/manage/catalog/attributes/",
        {"code": "ram", "name": "RAM", "value_type": "number", "scope": "variant"},
    )
    assert invalid.status_code == 400
    with tenant_context(deployment_tenant):
        assert AttributeOption.objects.filter(attribute__code="colour").count() == 1
        assert not AttributeDefinition.objects.filter(code="ram").exists()


# --- Inventory and pricing APIs -------------------------------------------------------------------


def test_stock_operations_over_the_api(owner: Api, deployment_tenant: Tenant) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant))
    receipt = {
        "variant": str(variant.pk),
        "quantity": "5",
        "idempotency_key": "receipt-0001",
        "reason": "PO 1",
    }

    first = owner.post("/api/manage/inventory/receipts/", receipt)
    replay = owner.post("/api/manage/inventory/receipts/", receipt)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json()["replayed"] is True
    assert replay.json()["operation_id"] == first.json()["operation_id"]

    adjusted = owner.post(
        "/api/manage/inventory/adjustments/",
        {
            "variant": str(variant.pk),
            "quantity": "2",
            "movement_type": "damaged",
            "reason": "Water damage",
        },
    )
    assert adjusted.status_code == 201
    too_many = owner.post(
        "/api/manage/inventory/adjustments/",
        {"variant": str(variant.pk), "quantity": "9", "movement_type": "lost", "reason": "Count"},
    )
    assert too_many.status_code == 409
    assert too_many.json()["error"]["code"] == "insufficient_stock"

    movement_id = adjusted.json()["movements"][0]["id"]
    reversed_ = owner.post(
        f"/api/manage/inventory/movements/{movement_id}/reverse/", {"reason": "Mistake"}
    )
    assert reversed_.status_code == 201

    level = owner.get("/api/manage/inventory/levels/", variant=str(variant.pk)).json()["results"][0]
    assert (level["on_hand"], level["available"], level["location"]["code"]) == (
        "5.000",
        "5.000",
        "MAIN",
    )
    updated = owner.patch(
        f"/api/manage/inventory/levels/{level['id']}/", {"low_stock_threshold": "6"}
    )
    assert updated.json()["is_low"] is True
    assert owner.get("/api/manage/inventory/levels/", low_stock="true").json()["count"] == 1
    movements = owner.get("/api/manage/inventory/movements/", variant=str(variant.pk)).json()
    assert [m["movement_type"] for m in movements["results"]] == ["reversal", "damaged", "receipt"]
    assert owner.get("/api/manage/inventory/reconciliation/").json() == {
        "consistent": True,
        "discrepancies": [],
    }


def test_transfers_need_two_locations_over_the_api(owner: Api, deployment_tenant: Tenant) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant))
    main = owner.get("/api/manage/inventory/locations/").json()["results"][0]
    shop = owner.post("/api/manage/inventory/locations/", {"code": "shop-1", "name": "Shop"}).json()
    owner.post("/api/manage/inventory/receipts/", {"variant": str(variant.pk), "quantity": "3"})
    moved = owner.post(
        "/api/manage/inventory/transfers/",
        {
            "variant": str(variant.pk),
            "quantity": "2",
            "from_location": main["id"],
            "to_location": shop["id"],
        },
    )
    assert moved.status_code == 201
    assert [m["location_code"] for m in moved.json()["movements"]] == ["MAIN", "SHOP-1"]
    deactivate = owner.patch(f"/api/manage/inventory/locations/{shop['id']}/", {"is_active": False})
    assert deactivate.json()["error"]["code"] == "location_has_stock"


def test_price_precision_errors_use_the_envelope(owner: Api, deployment_tenant: Tenant) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant))
    response = owner.put(
        f"/api/manage/pricing/variants/{variant.pk}/prices/", {"prices": [{"amount": "10.555"}]}
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"] == {
        "amount": ["BDT amounts have at most 2 decimal places."]
    }
    assert (
        owner.put(
            f"/api/manage/pricing/variants/{variant.pk}/prices/", {"prices": [{"amount": "10.5"}]}
        ).status_code
        == 200
    )


# --- Public catalogue -----------------------------------------------------------------------------


def test_the_public_catalogue_shows_only_published_safe_data(
    owner: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    size_attribute(deployment_tenant)
    product = owner.post(PRODUCTS, shirt_payload()).json()
    draft = owner.post(
        PRODUCTS, {"name": "Draft", "variants": [{"sku": "dr-1", "price": {"amount": "5"}}]}
    ).json()
    owner.post(f"{PRODUCTS}{product['id']}/status/", {"status": "active"})
    owner.post(
        "/api/manage/inventory/receipts/",
        {"variant": product["variants"][1]["id"], "quantity": "4"},
    )
    visitor = make_api()

    listing = visitor.get("/api/catalog/products/").json()
    assert [row["slug"] for row in listing["results"]] == ["oxford-shirt"]
    summary = listing["results"][0]
    assert summary["price"] == {
        "currency": "BDT",
        "min_amount": "1200.0000",
        "max_amount": "1250.0000",
    }
    assert summary["in_stock"] is True

    detail = visitor.get("/api/catalog/products/oxford-shirt/").json()
    assert [(v["sku"], v["in_stock"]) for v in detail["variants"]] == [
        ("OX-S", False),
        ("OX-M", True),
    ]
    assert detail["variants"][1]["price"] == {
        "currency": "BDT",
        "amount": "1250.0000",
        "compare_at_amount": "1400.0000",
    }
    assert [o["code"] for o in detail["options"][0]["values"]] == ["s", "m"]
    text = str(detail)
    for private in (
        "cost_amount",
        "713.37",
        "on_hand",
        "reserved",
        "publication_problems",
        "status",
    ):
        assert private not in text, private
    assert visitor.get(f"/api/catalog/products/{draft['slug']}/").status_code == 404

    owner.post(f"{PRODUCTS}{product['id']}/status/", {"status": "archived"})
    assert visitor.get("/api/catalog/products/oxford-shirt/").status_code == 404
    assert visitor.get("/api/catalog/products/").json()["count"] == 0


def test_public_browsing_by_category_and_collection(
    owner: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    men = owner.post("/api/manage/catalog/categories/", {"name": "Men"}).json()
    shirts = owner.post(
        "/api/manage/catalog/categories/", {"name": "Shirts", "parent": men["id"]}
    ).json()
    hidden = owner.post(
        "/api/manage/catalog/categories/", {"name": "Hidden", "is_active": False}
    ).json()
    with tenant_context(deployment_tenant):
        shirt_category = Category.objects.get(pk=shirts["id"])
    a = simple_product(
        deployment_tenant, sku="A-1", name="Alpha", categories=[shirt_category], publish=True
    )
    b = simple_product(deployment_tenant, sku="B-1", name="Beta", amount="10", publish=True)
    collection = owner.post("/api/manage/catalog/collections/", {"name": "Editor picks"}).json()
    owner.put(
        f"/api/manage/catalog/collections/{collection['id']}/products/",
        {"products": [str(b.pk), str(a.pk)]},
    )
    visitor = make_api()

    tree = visitor.get("/api/catalog/categories/").json()
    assert [(n["slug"], [c["slug"] for c in n["children"]]) for n in tree] == [("men", ["shirts"])]
    assert hidden["slug"] not in str(tree)
    in_men = visitor.get("/api/catalog/products/", category="men").json()["results"]
    assert [row["slug"] for row in in_men] == ["alpha"]  # descendants included
    featured = visitor.get(
        "/api/catalog/products/", collection="editor-picks", ordering="featured"
    ).json()
    assert [row["slug"] for row in featured["results"]] == ["beta", "alpha"]
    cheapest = visitor.get("/api/catalog/products/", ordering="price_low").json()
    assert [row["slug"] for row in cheapest["results"]] == ["beta", "alpha"]
    assert visitor.get("/api/catalog/products/", category="hidden").status_code == 404
    assert visitor.post("/api/catalog/products/", {}).status_code == 405
