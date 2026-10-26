"""Customer order history access and pagination regression coverage."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from commerce.tenancy.context import tenant_context
from commerce.orders.models import Order
from .test_orders import customer_client, place, product, reviewed, stock

pytestmark = pytest.mark.django_db


def test_order_history_requires_customer_session(store: Any, api_client: APIClient) -> None:
    response = api_client.get("/api/orders/")
    assert response.status_code == 401


def test_order_history_lists_only_customer_orders(store: Any) -> None:
    first = customer_client(store.tenant, "history-first@example.test")
    second = customer_client(store.tenant, "history-second@example.test")
    variant = product(store.tenant)
    stock(store.tenant, variant, 3)

    first_order = place(first, reviewed(first, variant), key="customer-history-first")
    assert first_order.status_code == 201, first_order.data

    second_order = place(second, reviewed(second, variant), key="customer-history-second")
    assert second_order.status_code == 201, second_order.data

    first_list = first.get("/api/orders/?page_size=1")
    assert first_list.status_code == 200
    assert first_list.data["count"] == 1
    assert len(first_list.data["results"]) == 1
    assert first_list.data["results"][0]["id"] == first_order.data["id"]

    second_list = second.get("/api/orders/")
    assert second_list.status_code == 200
    assert second_list.data["count"] == 1
    assert second_list.data["results"][0]["id"] == second_order.data["id"]

    empty_page = first.get("/api/orders/?page=2&page_size=1")
    assert empty_page.status_code == 200
    assert empty_page.data["results"] == []
    with tenant_context(store.tenant):
        assert Order.objects.count() == 2


def test_order_history_rejects_invalid_pagination(store: Any) -> None:
    client = customer_client(store.tenant, "history-pages@example.test")
    response = client.get("/api/orders/?page_size=101")
    assert response.status_code == 400
