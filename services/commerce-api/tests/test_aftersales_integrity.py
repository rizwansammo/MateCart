"""Phase 10 isolation, replay safety and state-transition regressions."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from commerce.aftersales.models import (
    AfterSalesCase,
    CaseMessage,
    DigitalEntitlement,
    DigitalProductAsset,
    ProductReview,
)
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .test_orders import customer_client, merchant

pytestmark = pytest.mark.django_db


def test_customer_case_replay_and_cross_tenant_isolation(
    store: Any,
    tenant_b: Tenant,
) -> None:
    buyer = customer_client(store.tenant, "tenant-a-support@example.test")
    body = {
        "kind": "support",
        "subject": "Help with delivery",
        "reason": "I have a delivery question.",
        "idempotency_key": "customer-safe-replay-0001",
    }
    first = buyer.post("/api/cases/", body, format="json")
    assert first.status_code == 201, first.data
    replay = buyer.post("/api/cases/", body, format="json")
    assert replay.status_code == 201
    assert replay.data["id"] == first.data["id"]
    conflict = buyer.post(
        "/api/cases/",
        {**body, "subject": "A different question"},
        format="json",
    )
    assert conflict.status_code == 409
    with tenant_context(tenant_b):
        assert not AfterSalesCase.objects.exists()
        assert not CaseMessage.objects.exists()
        assert not ProductReview.objects.exists()
        assert not DigitalEntitlement.objects.exists()
        assert not DigitalProductAsset.objects.exists()


def test_unapproved_case_cannot_be_resolved(
    store: Any,
) -> None:
    buyer = customer_client(store.tenant, "case-approval@example.test")
    created = buyer.post(
        "/api/cases/",
        {
            "kind": "support",
            "subject": "I need assistance",
            "reason": "An order question about payment.",
            "idempotency_key": "support-approval-test-001",
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    handler = merchant(store.tenant)
    rejected = handler.patch(
        f"/api/manage/aftersales/cases/{created.data['id']}/",
        {"status": "resolved", "reason": "Resolved without approval"},
        format="json",
    )
    assert rejected.status_code == 409
    assert (
        handler.get(f"/api/manage/aftersales/cases/{created.data['id']}/").data["status"]
        == "requested"
    )
    assert APIClient().get("/api/manage/aftersales/cases/").status_code == 401
