"""Phase 10 customer ownership and verified purchase regression tests."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from commerce.aftersales.models import AfterSalesCase, DigitalEntitlement, ProductReview
from commerce.media.models import MediaAsset, MediaKind
from commerce.orders.models import PaymentIntent
from commerce.tenancy.context import tenant_context

from .test_orders import (
    callback,
    customer_client,
    digital,
    digital_checkout,
    merchant,
    place,
    product,
    reviewed,
    stock,
)

pytestmark = pytest.mark.django_db


def test_support_request_is_private_idempotent_and_staff_moderated(store: Any) -> None:
    buyer = customer_client(store.tenant, "requester@example.test")
    stranger = customer_client(store.tenant, "other-requester@example.test")
    staff = merchant(store.tenant)
    payload = {
        "kind": "support",
        "subject": "Question about my order",
        "reason": "Please help with my purchase.",
        "quantity": "1.000",
        "idempotency_key": "support-unique-key-123",
    }
    first = buyer.post("/api/cases/", payload, format="json")
    replay = buyer.post("/api/cases/", payload, format="json")
    assert first.status_code == replay.status_code == 201
    assert first.data["id"] == replay.data["id"]
    assert stranger.get(f"/api/cases/{first.data['id']}/").status_code == 404
    assert (
        stranger.post(
            f"/api/cases/{first.data['id']}/", {"body": "No access"}, format="json"
        ).status_code
        == 404
    )
    assert APIClient().get("/api/cases/").status_code == 401

    response = staff.patch(
        f"/api/manage/aftersales/cases/{first.data['id']}/",
        {"status": "approved"},
        format="json",
    )
    assert response.status_code == 200, response.data
    replied = staff.post(
        f"/api/manage/aftersales/cases/{first.data['id']}/",
        {"body": "We are reviewing this."},
        format="json",
    )
    assert replied.status_code == 201, replied.data
    own = buyer.get(f"/api/cases/{first.data['id']}/")
    assert own.status_code == 200
    assert own.data["messages"][0]["body"] == "We are reviewing this."
    with tenant_context(store.tenant):
        assert AfterSalesCase.objects.count() == 1


def test_review_requires_payment_and_genuine_purchase(store: Any, fakepay: Any) -> None:
    buyer = customer_client(store.tenant, "review-buyer@example.test")
    stranger = customer_client(store.tenant, "review-stranger@example.test")
    variant = product(store.tenant)
    stock(store.tenant, variant, 2)
    checked = reviewed(buyer, variant)
    placed = place(buyer, checked, method="online", key="review-checkout-1234")
    assert placed.status_code == 201, placed.data
    item_id = placed.data["items"][0]["id"]
    payload = {"order_item": item_id, "rating": 5, "body": "Exactly as described."}

    unpaid = buyer.post("/api/reviews/", payload, format="json")
    assert unpaid.status_code == 409
    assert stranger.post("/api/reviews/", payload, format="json").status_code == 404

    assert (
        buyer.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json").status_code
        == 200
    )
    with tenant_context(store.tenant):
        intent = PaymentIntent.objects.get(order_id=placed.data["id"])
    assert callback(APIClient(), intent).status_code == 200

    saved = buyer.post("/api/reviews/", payload, format="json")
    assert saved.status_code == 201, saved.data
    assert saved.data["status"] == "pending"
    assert buyer.post("/api/reviews/", payload, format="json").status_code == 409
    with tenant_context(store.tenant):
        assert ProductReview.objects.count() == 1
    staff = merchant(store.tenant)
    accepted = staff.post(
        f"/api/manage/aftersales/reviews/{saved.data['id']}/",
        {"status": "published"},
        format="json",
    )
    assert accepted.status_code == 200, accepted.data
    assert accepted.data["status"] == "published"


def test_digital_library_requires_verified_payment(store: Any, fakepay: Any) -> None:
    buyer = customer_client(store.tenant, "ebook-buyer@example.test")
    stranger = customer_client(store.tenant, "ebook-stranger@example.test")
    variant = digital(store.tenant)
    with tenant_context(store.tenant):
        asset = MediaAsset.objects.create(
            file=f"tenants/{store.tenant.pk}/media/test.pdf",
            kind=MediaKind.DOCUMENT,
            content_type="application/pdf",
            size_bytes=12,
            checksum_sha256="a" * 64,
        )
    staff = merchant(store.tenant)
    mapping = staff.post(
        "/api/manage/digital/assets/",
        {"variant": str(variant.pk), "asset": str(asset.pk), "download_limit": 3},
        format="json",
    )
    assert mapping.status_code == 200, mapping.data
    checkout_id = digital_checkout(buyer, variant)
    placed = place(buyer, checkout_id, method="online", key="digital-paid-1234")
    assert placed.status_code == 201, placed.data
    pending = buyer.get("/api/account/library/")
    assert pending.status_code == 200
    assert pending.data[0]["state"] == "payment_required"
    assert stranger.get("/api/account/library/").data == []

    assert (
        buyer.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json").status_code
        == 200
    )
    with tenant_context(store.tenant):
        intent = PaymentIntent.objects.get(order_id=placed.data["id"])
    assert callback(APIClient(), intent).status_code == 200
    ready = buyer.get("/api/account/library/")
    assert ready.status_code == 200
    assert ready.data[0]["state"] == "ready"
    assert ready.data[0]["remaining_downloads"] == 3
    entitlement_id = ready.data[0]["entitlement_id"]
    assert stranger.get(f"/api/account/library/{entitlement_id}/download/").status_code == 404
    with tenant_context(store.tenant):
        assert DigitalEntitlement.objects.count() == 1
