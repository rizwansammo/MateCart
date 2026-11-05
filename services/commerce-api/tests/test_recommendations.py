"""Phase 15 related products (D-110): deterministic, published and sellable only, merchant pins
and hides, permissions and tenant isolation. No AI provider is involved."""

from __future__ import annotations

from typing import Any

import pytest

from commerce.catalog.models import Category, ProductCategory
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import simple_product
from .comms_helpers import staff
from .factories import make_store_settings

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(store: Any, deployment_tenant: Tenant) -> Tenant:
    return deployment_tenant


def place(tenant: Tenant, product: Any, category: Category, primary: bool = True) -> None:
    with tenant_context(tenant):
        ProductCategory.objects.create(product=product, category=category, is_primary=primary)


def related(client: Any, slug: str) -> list[str]:
    response = client.get(f"/api/catalog/products/{slug}/related/")
    assert response.status_code == 200
    return [row["name"] for row in response.json()]


def test_related_products_are_published_priced_and_sellable(shop: Tenant, api_client: Any) -> None:
    with tenant_context(shop):
        tops = Category.objects.create(name="Tops", slug="tops")
    main = simple_product(
        shop, sku="M-1", name="Main", publish=True, track_inventory=False, tags=["cotton"]
    )
    same = simple_product(
        shop, sku="S-1", name="Same category", publish=True, track_inventory=False
    )
    tagged = simple_product(
        shop, sku="T-1", name="Shared tag", publish=True, track_inventory=False, tags=["cotton"]
    )
    draft = simple_product(shop, sku="D-1", name="Draft", track_inventory=False)
    out_of_stock = simple_product(shop, sku="O-1", name="No stock", publish=True)
    for product in (main, same, draft, out_of_stock):
        place(shop, product, tops)
    names = related(api_client, main.slug)
    assert names == ["Same category", "Shared tag"]
    assert "Draft" not in names
    assert "No stock" not in names
    assert "Main" not in names
    assert tagged.name in names


def test_pins_and_hides_override_the_automatic_list(shop: Tenant, api_client: Any) -> None:
    with tenant_context(shop):
        tops = Category.objects.create(name="Tops", slug="tops")
    main = simple_product(shop, sku="M-1", name="Main", publish=True, track_inventory=False)
    a = simple_product(shop, sku="A-1", name="Alpha", publish=True, track_inventory=False)
    b = simple_product(shop, sku="B-1", name="Beta", publish=True, track_inventory=False)
    pinned = simple_product(shop, sku="P-1", name="Pinned", publish=True, track_inventory=False)
    for product in (main, a, b):
        place(shop, product, tops)
    client, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    url = f"/api/manage/catalog/products/{main.pk}/recommendations/"
    response = client.put(url, {"pinned": [str(pinned.pk)], "hidden": [str(a.pk)]}, format="json")
    assert response.status_code == 200
    body = response.json()
    assert [row["name"] for row in body["pinned"]] == ["Pinned"]
    assert [row["name"] for row in body["storefront"]] == ["Pinned", "Beta"]
    assert related(api_client, main.slug) == ["Pinned", "Beta"]
    refused = client.put(url, {"pinned": [str(main.pk)], "hidden": []}, format="json")
    assert refused.status_code == 400


def test_recommendation_overrides_need_catalogue_permission(shop: Tenant) -> None:
    main = simple_product(shop, sku="M-1", name="Main", publish=True)
    handler, _ = staff(shop, StaffRole.ORDER_HANDLER)
    assert (
        handler.get(f"/api/manage/catalog/products/{main.pk}/recommendations/").status_code == 403
    )


def test_other_stores_products_cannot_be_pinned(shop: Tenant, tenant_b: Tenant) -> None:
    make_store_settings(tenant_b, name="Other")
    main = simple_product(shop, sku="M-1", name="Main", publish=True)
    other = simple_product(tenant_b, sku="X-1", name="Other", publish=True)
    client, _ = staff(shop)
    response = client.put(
        f"/api/manage/catalog/products/{main.pk}/recommendations/",
        {"pinned": [str(other.pk)], "hidden": []},
        format="json",
    )
    assert response.status_code == 400
