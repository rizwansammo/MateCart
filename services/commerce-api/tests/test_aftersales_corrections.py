"""Phase 10.1: historical file privacy, refund attribution and replacement provenance."""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from commerce.aftersales.models import (
    AfterSalesCase,
    CaseKind,
    CaseStatus,
    DigitalEntitlement,
    DigitalProductAsset,
    ReturnRefundEvidence,
)
from commerce.aftersales.services import required_return_refund
from commerce.catalog.models import Category
from commerce.media.models import MediaAsset, MediaKind
from commerce.orders.models import Order, OrderItem, PaymentIntent, Refund
from commerce.tenancy.context import tenant_context

from .test_aftersales import fakepay as fakepay
from .test_checkout import add, product
from .test_fulfillment import move, prepare
from .test_orders import (
    callback,
    customer_client,
    digital,
    digital_checkout,
    merchant,
    place,
    reviewed,
    stock,
)

pytestmark = pytest.mark.django_db


def _document(tenant: Any, name: str) -> MediaAsset:
    with tenant_context(tenant):
        asset = MediaAsset(
            kind=MediaKind.DOCUMENT,
            content_type="application/pdf",
            size_bytes=14,
            checksum_sha256="a" * 64,
        )
        asset.file.save(name, ContentFile(b"%PDF-1.4\n%%EOF\n"), save=False)
        asset.save()
        return asset


def _paid_digital(store: Any, fakepay: Any) -> tuple[APIClient, APIClient, Any]:
    customer = customer_client(store.tenant, "digital-security@example.test")
    staff = merchant(store.tenant)
    variant = digital(store.tenant)
    first = _document(store.tenant, "original.pdf")
    assigned = staff.post(
        "/api/manage/digital/assets/",
        {"variant": str(variant.pk), "asset": str(first.pk), "download_limit": 2},
        format="json",
    )
    assert assigned.status_code == 200, assigned.data
    order = place(
        customer,
        digital_checkout(customer, variant),
        method="online",
        key="phase-101-digital-original",
    )
    assert order.status_code == 201, order.data
    payment_start = customer.post(f"/api/orders/{order.data['id']}/payment/", {}, format="json")
    assert payment_start.status_code == 200
    with tenant_context(store.tenant):
        intent = PaymentIntent.objects.get(order_id=order.data["id"])
    assert callback(APIClient(), intent).status_code == 200
    return customer, staff, variant


def test_historical_and_current_digital_assets_stay_private(
    store: Any,
    fakepay: Any,
) -> None:
    customer, staff, variant = _paid_digital(store, fakepay)
    outsider = customer_client(store.tenant, "not-the-buyer@example.test")
    with tenant_context(store.tenant):
        original = DigitalEntitlement.objects.get()
        old_asset_id = original.asset_id
        old_file = original.asset.file.name
        new_asset = _document(store.tenant, "replacement.pdf")
    changed = staff.post(
        "/api/manage/digital/assets/",
        {"variant": str(variant.pk), "asset": str(new_asset.pk), "download_limit": 4},
        format="json",
    )
    assert changed.status_code == 200, changed.data
    with tenant_context(store.tenant):
        entitlement = DigitalEntitlement.objects.get()
        mapping = DigitalProductAsset.objects.get(variant=variant)
        assert entitlement.asset_id == old_asset_id
        assert entitlement.asset.file.name == old_file
        assert entitlement.download_limit == 2
        assert mapping.asset_id == new_asset.pk
        # Simulate an incorrect later public catalogue association with both
        # private media IDs. The public file endpoint must still deny access.
        Category.objects.create(
            name="Old digital file", slug="old-digital-file", image=entitlement.asset
        )
        Category.objects.create(name="New digital file", slug="new-digital-file", image=new_asset)
    for pk in (old_asset_id, new_asset.pk):
        url = f"/api/catalog/media/{pk}/"
        assert APIClient().get(url).status_code == 404
        assert outsider.get(url).status_code == 404
        assert APIClient().get(f"/api/manage/media/assets/{pk}/file/").status_code in (401, 403)
    url = f"/api/account/library/{entitlement.pk}/download/"
    assert outsider.get(url).status_code == 404
    assert APIClient().get(url).status_code == 401
    for remaining in (1, 0):
        downloaded = customer.get(url)
        assert downloaded.status_code == 200
        assert downloaded["Cache-Control"] == "private, no-store"
        assert b"".join(downloaded.streaming_content).startswith(b"%PDF-1.4")
        with tenant_context(store.tenant):
            refreshed = DigitalEntitlement.objects.get(pk=entitlement.pk)
            assert refreshed.download_limit - refreshed.access_count == remaining
    assert customer.get(url).status_code == 409
    assert customer.get("/api/account/library/").data[0]["state"] == "unavailable"


def _physical_orders(store: Any) -> tuple[APIClient, APIClient, Any, Any]:
    buyer = customer_client(store.tenant, "return-buyer@example.test")
    staff = merchant(store.tenant)
    variant = product(store.tenant, tracked=True, amount="100.00")
    stock(store.tenant, variant, 12)
    # Add one unit before review's normal add to create a two-unit immutable line.
    assert add(buyer, variant, "1").status_code == 200
    order = place(buyer, reviewed(buyer, variant), key="phase-101-original-order")
    assert order.status_code == 201, order.data
    confirmed = staff.post(
        f"/api/manage/orders/{order.data['id']}/transition/",
        {"status": "confirmed"},
        format="json",
    )
    assert confirmed.status_code == 200, confirmed.data
    paid = staff.post(
        f"/api/manage/orders/{order.data['id']}/cod-collection/",
        {},
        format="json",
    )
    assert paid.status_code == 200, paid.data
    return buyer, staff, variant, order


def _case(store: Any, order: Any, kind: str, *, quantity: str = "1") -> AfterSalesCase:
    with tenant_context(store.tenant):
        stored_order = Order.objects.get(pk=order.data["id"])
        item = OrderItem.objects.get(order=stored_order)
        return AfterSalesCase.objects.create(
            customer=stored_order.customer,
            order_item=item,
            kind=kind,
            status=CaseStatus.AWAITING_REFUND if kind == CaseKind.RETURN else CaseStatus.INSPECTED,
            quantity=Decimal(quantity),
            request_key_hash=uuid.uuid4().hex,
            subject="Specific customer claim",
            reason="Purchased unit has a defect",
        )


def _refund(
    staff: APIClient,
    order: Any,
    amount: str,
    key: str,
    case: AfterSalesCase | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "amount": amount,
        "reason": "Return adjustment",
        "idempotency_key": key,
    }
    if case is not None:
        payload["return_case"] = str(case.pk)
    return staff.post(
        f"/api/manage/orders/{order.data['id']}/refunds/",
        payload,
        format="json",
    )


def _resolve_return(staff: APIClient, case: AfterSalesCase, refund_id: Any) -> Any:
    return staff.patch(
        f"/api/manage/aftersales/cases/{case.pk}/",
        {"status": "resolved", "reason": "Verified returned items", "refund": str(refund_id)},
        format="json",
    )


def test_return_rejects_unrelated_and_insufficient_refunds_and_accepts_partial(
    store: Any,
    tenant_b: Any,
) -> None:
    _buyer, staff, _variant, order = _physical_orders(store)
    first = _case(store, order, CaseKind.RETURN)
    second = _case(store, order, CaseKind.RETURN)
    with tenant_context(store.tenant):
        assert required_return_refund(first) == Decimal("100.00")
    unrelated = _refund(staff, order, "10.00", "unrelated-refund-101")
    assert unrelated.status_code == 200, unrelated.data
    assert _resolve_return(staff, first, unrelated.data["id"]).status_code == 409

    too_much = _refund(staff, order, "101.00", "excessive-refund-101", first)
    assert too_much.status_code == 409
    assert too_much.data["error"]["code"] == "return_refund_exceeds"

    partial = _refund(staff, order, "40.00", "partial-refund-101-a", first)
    assert partial.status_code == 200, partial.data
    assert _resolve_return(staff, first, partial.data["id"]).status_code == 409
    wrong_claim = _resolve_return(staff, second, partial.data["id"])
    assert wrong_claim.status_code == 409

    rest = _refund(staff, order, "60.00", "partial-refund-101-b", first)
    assert rest.status_code == 200, rest.data
    assert _resolve_return(staff, first, rest.data["id"]).status_code == 200
    assert _resolve_return(staff, second, rest.data["id"]).status_code == 409
    replay_with_other_claim = _refund(staff, order, "60.00", "partial-refund-101-b", second)
    assert replay_with_other_claim.status_code == 409
    with tenant_context(store.tenant):
        assert ReturnRefundEvidence.objects.filter(case=first).count() == 2
        assert ReturnRefundEvidence.objects.filter(case=second).count() == 0
        assert Refund.objects.count() == 3
    with tenant_context(tenant_b):
        assert ReturnRefundEvidence.objects.count() == 0


def test_return_proration_uses_original_line_discount_tax_and_quantity() -> None:
    order = SimpleNamespace(
        item_subtotal=Decimal("300.00"),
        discount_total=Decimal("30.00"),
        tax_total=Decimal("15.00"),
        grand_total=Decimal("285.00"),
        currency="BDT",
    )
    item = SimpleNamespace(
        order=order,
        line_total=Decimal("200.00"),
        quantity=Decimal("2"),
    )
    case = SimpleNamespace(order_item=item, quantity=Decimal("1"))
    assert required_return_refund(cast(AfterSalesCase, case)) == Decimal("95.00")


def _new_order(store: Any, buyer: APIClient, variant: Any) -> Any:
    order = place(
        buyer,
        reviewed(buyer, variant),
        key=f"exchange-replacement-{uuid.uuid4().hex}",
    )
    assert order.status_code == 201, order.data
    return order


def _bind_exchange(staff: APIClient, case: AfterSalesCase, order_id: Any) -> Any:
    return staff.patch(
        f"/api/manage/aftersales/cases/{case.pk}/",
        {"status": "replacement_pending", "replacement_order": str(order_id)},
        format="json",
    )


def _resolve_exchange(staff: APIClient, case: AfterSalesCase, order_id: Any) -> Any:
    return staff.patch(
        f"/api/manage/aftersales/cases/{case.pk}/",
        {
            "status": "resolved",
            "reason": "Verified delivered replacement",
            "replacement_order": str(order_id),
        },
        format="json",
    )


def test_exchange_requires_pre_dispatch_binding_and_verified_delivery(store: Any) -> None:
    buyer, staff, variant, original = _physical_orders(store)
    case = _case(store, original, CaseKind.EXCHANGE)
    other = _case(store, original, CaseKind.EXCHANGE)
    wrong_buyer = customer_client(store.tenant, "different-buyer@example.test")
    unrelated = _new_order(store, wrong_buyer, variant)
    assert _bind_exchange(staff, case, unrelated.data["id"]).status_code == 409

    normal = _new_order(store, buyer, variant)
    assert _resolve_exchange(staff, case, normal.data["id"]).status_code == 409
    assert _bind_exchange(staff, case, normal.data["id"]).status_code == 200
    assert _bind_exchange(staff, other, normal.data["id"]).status_code == 409
    assert _resolve_exchange(staff, case, normal.data["id"]).status_code == 409

    confirmed = staff.post(
        f"/api/manage/orders/{normal.data['id']}/transition/",
        {"status": "confirmed"},
        format="json",
    )
    assert confirmed.status_code == 200, confirmed.data
    shipment = prepare(staff, normal.data["id"])
    for target in ("picking", "packed", "ready_for_dispatch", "dispatched", "delivered"):
        result = move(staff, shipment.data["id"], target)
        assert result.status_code == 200, (target, result.data)

    assert _resolve_exchange(staff, case, unrelated.data["id"]).status_code == 409
    success = _resolve_exchange(staff, case, normal.data["id"])
    assert success.status_code == 200, success.data
    with tenant_context(store.tenant):
        stored = AfterSalesCase.objects.get(pk=case.pk)
        assert str(stored.replacement_order_id) == str(normal.data["id"])


def test_exchange_rejects_wrong_quantity_and_retroactive_binding(store: Any) -> None:
    buyer, staff, variant, original = _physical_orders(store)
    oversized = _case(store, original, CaseKind.EXCHANGE, quantity="2")
    existing = _new_order(store, buyer, variant)
    assert _bind_exchange(staff, oversized, existing.data["id"]).status_code == 409

    wrong_variant = product(store.tenant, tracked=True, amount="150.00")
    stock(store.tenant, wrong_variant, 3)
    wrong_sku = _new_order(store, buyer, wrong_variant)
    assert _bind_exchange(staff, oversized, wrong_sku.data["id"]).status_code == 409

    case = _case(store, original, CaseKind.EXCHANGE)
    confirmed = staff.post(
        f"/api/manage/orders/{existing.data['id']}/transition/",
        {"status": "confirmed"},
        format="json",
    )
    assert confirmed.status_code == 200
    shipment = prepare(staff, existing.data["id"])
    for target in ("picking", "packed", "ready_for_dispatch", "dispatched", "delivered"):
        assert move(staff, shipment.data["id"], target).status_code == 200
    assert _bind_exchange(staff, case, existing.data["id"]).status_code == 409
