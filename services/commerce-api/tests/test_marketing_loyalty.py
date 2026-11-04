"""Phase 12 loyalty: earning, redemption, reversal and ledger integrity (D-082)."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from commerce.audit.models import AuditEvent
from commerce.identity import sessions
from commerce.identity.models import AccountType, AuthSession, User
from commerce.marketing import loyalty
from commerce.marketing.models import (
    AppendOnlyError,
    AwardStatus,
    LedgerKind,
    LoyaltyAccount,
    LoyaltyAward,
    LoyaltyLedger,
    LoyaltyProgram,
)
from commerce.orders.models import Order
from commerce.staff.permissions import StaffRole
from commerce.stores import modules
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_user
from .test_checkout import add, product
from .test_marketing_promotions import reviewed_checkout
from .test_orders import merchant, place, review, reviewed, stock

pytestmark = pytest.mark.django_db

ADJUST = "/api/manage/marketing/loyalty/adjust/"


def enable_loyalty(tenant: Tenant, **fields: Any) -> LoyaltyProgram:
    with tenant_context(tenant):
        modules.set_module(key="loyalty", enabled=True, actor=None)
        program = loyalty.program()
        LoyaltyProgram.objects.filter(pk=program.pk).update(is_enabled=True, **fields)
        return LoyaltyProgram.objects.get()


def member(tenant: Tenant, *, verified: bool = True) -> tuple[APIClient, User]:
    client = APIClient()
    user = make_user(
        tenant,
        account_type=AccountType.CUSTOMER,
        email=f"member-{uuid.uuid4().hex[:8]}@example.test",
        password="customer-passphrase-1234",
        email_verified_at=timezone.now() if verified else None,
    )
    with tenant_context(tenant):
        _, token = sessions.start(user)
    client.cookies["mc_customer"] = token
    return client, user


def grant(tenant: Tenant, customer: User, points: int) -> None:
    staff = make_user(tenant, account_type=AccountType.STAFF, email=f"s-{uuid.uuid4().hex}@x.test")
    with tenant_context(tenant):
        loyalty.adjust(
            customer_id=customer.pk, points=points, reason="Opening balance", actor=staff
        )


def account(tenant: Tenant, customer: User) -> LoyaltyAccount:
    with tenant_context(tenant):
        return LoyaltyAccount.objects.get(customer=customer)


def order_for(client: APIClient, variant: Any, *, points: int = 0) -> Any:
    checkout_id = reviewed_checkout(client, [variant])
    if points:
        chosen = client.patch(
            f"/api/checkout/{checkout_id}/", {"loyalty_points": points}, format="json"
        )
        assert chosen.status_code == 200, chosen.data
    assert review(client, checkout_id).status_code == 200
    placed = place(client, checkout_id, key=f"loyal-{uuid.uuid4().hex}")
    assert placed.status_code == 201, placed.data
    return placed


def confirm(staff: APIClient, order_id: str) -> None:
    response = staff.post(
        f"/api/manage/orders/{order_id}/transition/", {"status": "confirmed"}, format="json"
    )
    assert response.status_code == 200, response.data


def deliver(staff: APIClient, order_id: str) -> None:
    shipment = staff.post(f"/api/manage/fulfillment/orders/{order_id}/shipment/", {}, format="json")
    for target in ("picking", "packed", "ready_for_dispatch", "dispatched", "delivered"):
        moved = staff.post(
            f"/api/manage/fulfillment/shipments/{shipment.data['id']}/transition/",
            {"status": target},
            format="json",
        )
        assert moved.status_code == 200, moved.data


def collect(staff: APIClient, order_id: str) -> Any:
    response = staff.post(f"/api/manage/orders/{order_id}/cod-collection/", {}, format="json")
    assert response.status_code == 200, response.data
    return response


def refund(staff: APIClient, order_id: str, amount: str) -> None:
    response = staff.post(
        f"/api/manage/orders/{order_id}/refunds/",
        {"amount": amount, "reason": "Return", "idempotency_key": f"r-{uuid.uuid4().hex}"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["status"] == "succeeded"


def test_points_become_usable_only_after_payment_and_delivery(store: Any) -> None:
    enable_loyalty(store.tenant)
    variant = product(store.tenant, tracked=True, amount="1000.00")
    stock(store.tenant, variant, 5)
    client, customer = member(store.tenant)
    staff = merchant(store.tenant)

    placed = order_for(client, variant)
    order_id = placed.data["id"]
    with tenant_context(store.tenant):
        award = LoyaltyAward.objects.get(order_id=order_id)
    pending = client.get("/api/account/loyalty/").data
    confirm(staff, order_id)
    collect(staff, order_id)
    with tenant_context(store.tenant):
        paid_only = LoyaltyAward.objects.get(order_id=order_id).status
    deliver(staff, order_id)
    collect(staff, order_id)  # a repeated collection changes nothing
    with tenant_context(store.tenant):
        loyalty.sync_order(Order.objects.get(pk=order_id))  # duplicate events are harmless
        loyalty.sync_order(Order.objects.get(pk=order_id))
        earned = LoyaltyLedger.objects.filter(kind=LedgerKind.EARNED)
        assert earned.count() == 1
    summary = client.get("/api/account/loyalty/").data

    assert award.points == 10  # 1 point per 100 BDT of 1000 BDT items
    assert pending["balance"] == 0
    assert pending["pending"] == 10
    assert paid_only == AwardStatus.PENDING  # paid, not yet delivered
    assert summary["balance"] == 10
    assert summary["pending"] == 0
    assert summary["entries"][0]["kind"] == "earned"
    assert summary["entries"][0]["order_number"] == placed.data["number"]
    assert account(store.tenant, customer).lifetime_earned == 10


def test_redemption_reduces_the_total_and_is_restored_once_on_cancellation(store: Any) -> None:
    enable_loyalty(store.tenant)
    variant = product(store.tenant, tracked=True, amount="1000.00")
    stock(store.tenant, variant, 5)
    client, customer = member(store.tenant)
    grant(store.tenant, customer, 500)

    checkout_id = reviewed_checkout(client, [variant])
    offer = client.get(f"/api/checkout/{checkout_id}/").json()["loyalty"]
    chosen = client.patch(f"/api/checkout/{checkout_id}/", {"loyalty_points": 200}, format="json")
    assert review(client, checkout_id).status_code == 200
    placed = place(client, checkout_id, key=f"redeem-{uuid.uuid4().hex}")
    after_placement = account(store.tenant, customer).balance
    staff = merchant(store.tenant)
    url = f"/api/manage/orders/{placed.data['id']}/transition/"
    staff.post(url, {"status": "cancelled"}, format="json")
    staff.post(url, {"status": "cancelled"}, format="json")
    with tenant_context(store.tenant):
        restored = LoyaltyLedger.objects.filter(kind=LedgerKind.RESTORED).count()
        award = LoyaltyAward.objects.get(order_id=placed.data["id"])
        order = Order.objects.get(pk=placed.data["id"])

    assert offer["balance"] == 500
    # Rendered JSON: money is a decimal string, as the storefront contract requires.
    assert offer["point_value"] == "1.0000"
    assert offer["min_redeem_points"] == 100
    assert offer["max_points"] == 500
    assert chosen.data["loyalty_discount"] == "200.0000"
    assert chosen.data["discount_total"] == "200.0000"
    assert placed.data["grand_total"] == "800.0000"
    assert order.loyalty_points_redeemed == 200
    assert after_placement == 300
    assert award.points == 8  # earned on 800 BDT actually paid for items
    assert restored == 1
    assert account(store.tenant, customer).balance == 500
    assert award.status == AwardStatus.CANCELLED


def test_redemption_rules_are_enforced(store: Any) -> None:
    enable_loyalty(store.tenant)
    variant = product(store.tenant, tracked=True, amount="300.00")
    stock(store.tenant, variant, 5)
    client, customer = member(store.tenant)
    grant(store.tenant, customer, 400)
    checkout_id = reviewed_checkout(client, [variant])

    def choose(points: int) -> Any:
        return client.patch(
            f"/api/checkout/{checkout_id}/", {"loyalty_points": points}, format="json"
        )

    below_minimum = choose(50)
    above_cap = choose(200)  # at most 50 % of 300 BDT = 150 points
    negative = choose(-1)
    allowed = choose(150)
    guest = APIClient()
    guest_checkout = reviewed_checkout(guest, [variant])
    guest_points = guest.patch(
        f"/api/checkout/{guest_checkout}/", {"loyalty_points": 100}, format="json"
    )
    unverified, _ = member(store.tenant, verified=False)
    unverified_checkout = reviewed_checkout(unverified, [variant])
    unverified_points = unverified.patch(
        f"/api/checkout/{unverified_checkout}/", {"loyalty_points": 100}, format="json"
    )
    with tenant_context(store.tenant):
        modules.set_module(key="loyalty", enabled=False, actor=None)
    disabled = choose(150)

    assert below_minimum.status_code == 400
    assert above_cap.status_code == 400
    assert "150" in str(above_cap.data)
    assert negative.status_code == 400
    assert allowed.status_code == 200
    assert allowed.data["loyalty_discount"] == "150.0000"
    assert guest_points.status_code == 400
    assert guest.get(f"/api/checkout/{guest_checkout}/").data["loyalty"] is None
    assert unverified_points.status_code == 400
    assert disabled.status_code == 400


def test_balance_spent_after_review_blocks_placement_and_releases_the_hold(store: Any) -> None:
    enable_loyalty(store.tenant)
    variant = product(store.tenant, tracked=True, amount="1000.00")
    stock(store.tenant, variant, 1)
    client, customer = member(store.tenant)
    grant(store.tenant, customer, 300)
    checkout_id = reviewed_checkout(client, [variant])
    client.patch(f"/api/checkout/{checkout_id}/", {"loyalty_points": 300}, format="json")
    assert review(client, checkout_id).status_code == 200
    grant(store.tenant, customer, -250)

    blocked = place(client, checkout_id, key=f"spent-{uuid.uuid4().hex}")

    assert blocked.status_code == 409
    assert blocked.data["error"]["code"] == "loyalty_unavailable"
    with tenant_context(store.tenant):
        assert not Order.objects.exists()
        assert LoyaltyAccount.objects.get(customer=customer).balance == 50
    assert client.get(f"/api/checkout/{checkout_id}/").data["status"] == "released"


def test_refunds_reverse_earned_points_and_record_a_deficit_when_spent(store: Any) -> None:
    enable_loyalty(store.tenant)
    variant = product(store.tenant, tracked=True, amount="1000.00")
    stock(store.tenant, variant, 5)
    client, customer = member(store.tenant)
    staff = merchant(store.tenant)
    first = order_for(client, variant)
    confirm(staff, first.data["id"])
    deliver(staff, first.data["id"])
    collect(staff, first.data["id"])
    assert account(store.tenant, customer).balance == 10
    grant(store.tenant, customer, -6)  # the customer has spent most of the points

    refund(staff, first.data["id"], "500.00")  # half: the award falls from 10 to 5
    half = account(store.tenant, customer)
    refund(staff, first.data["id"], "500.00")  # the rest: the award falls to 0
    full = account(store.tenant, customer)
    second = order_for(client, variant)
    confirm(staff, second.data["id"])
    deliver(staff, second.data["id"])
    collect(staff, second.data["id"])
    repaid = account(store.tenant, customer)
    with tenant_context(store.tenant):
        reversals = list(
            LoyaltyLedger.objects.filter(kind=LedgerKind.REVERSED).order_by("occurred_at")
        )
        award = LoyaltyAward.objects.get(order_id=first.data["id"])
        repayment = LoyaltyLedger.objects.filter(kind=LedgerKind.EARNED).order_by("-occurred_at")[0]

    assert (half.balance, half.deficit) == (0, 1)  # 4 usable, 5 to reverse
    assert (full.balance, full.deficit) == (0, 6)
    assert [(row.points, row.deficit_change) for row in reversals] == [(-4, 1), (0, 5)]
    assert award.status == AwardStatus.CANCELLED
    assert award.earned_points == 0
    # The next 10 points repay the 6-point deficit first.
    assert (repaid.balance, repaid.deficit) == (4, 0)
    assert (repayment.points, repayment.deficit_change) == (4, -6)


def test_guest_unverified_and_merchant_assisted_orders_never_earn(store: Any) -> None:
    enable_loyalty(store.tenant)
    variant = product(store.tenant, tracked=True, amount="1000.00")
    stock(store.tenant, variant, 10)
    guest = APIClient()
    place(guest, reviewed(guest, variant), key=f"g-{uuid.uuid4().hex}")
    unverified, _ = member(store.tenant, verified=False)
    order_for(unverified, variant)
    from .test_merchant_dashboard import create, lines

    assisted = create(merchant(store.tenant), lines((variant, "1")))
    assert assisted.status_code == 201, assisted.data

    with tenant_context(store.tenant):
        assert Order.objects.count() == 3
        assert not LoyaltyAward.objects.exists()


def test_disabled_program_creates_no_awards_but_honours_existing_ones(store: Any) -> None:
    variant = product(store.tenant, tracked=True, amount="500.00")
    stock(store.tenant, variant, 5)
    client, customer = member(store.tenant)
    order_for(client, variant)
    with tenant_context(store.tenant):
        assert not LoyaltyAward.objects.exists()
    enable_loyalty(store.tenant)
    promised = order_for(client, variant)
    with tenant_context(store.tenant):
        LoyaltyProgram.objects.update(is_enabled=False)
    staff = merchant(store.tenant)
    confirm(staff, promised.data["id"])
    deliver(staff, promised.data["id"])
    collect(staff, promised.data["id"])
    assert account(store.tenant, customer).balance == 5


@pytest.mark.django_db(transaction=True)
def test_concurrent_deductions_cannot_overspend(store: Any) -> None:
    from .test_inventory_concurrency import run_concurrently

    _, customer = member(store.tenant)
    grant(store.tenant, customer, 300)
    staff = make_user(store.tenant, account_type=AccountType.STAFF, email="race@example.test")

    results = run_concurrently(
        store.tenant,
        4,
        lambda index: loyalty.adjust(
            customer_id=customer.pk, points=-200, reason="Concurrent use", actor=staff
        ),
    )

    succeeded = [result for result in results if isinstance(result, LoyaltyLedger)]
    assert len(succeeded) == 1
    assert account(store.tenant, customer).balance == 100


def test_ledger_is_append_only_in_the_orm_and_the_database(store: Any) -> None:
    _, customer = member(store.tenant)
    grant(store.tenant, customer, 100)
    with tenant_context(store.tenant):
        entry = LoyaltyLedger.objects.get()
        entry.points = 1000
        with pytest.raises(AppendOnlyError):
            entry.save()
        with pytest.raises(AppendOnlyError):
            entry.delete()
        with pytest.raises(DatabaseError), transaction.atomic():
            LoyaltyLedger.objects.filter(pk=entry.pk).update(points=1000)
        with pytest.raises(DatabaseError), transaction.atomic():
            LoyaltyLedger.objects.filter(pk=entry.pk).delete()
        assert LoyaltyLedger.objects.get().points == 100


def test_adjustments_need_permission_recent_sign_in_and_a_reason(store: Any) -> None:
    _, customer = member(store.tenant)
    body = {
        "customer": str(customer.pk),
        "points": 50,
        "reason": "Goodwill after a delay",
        "idempotency_key": "adjust-0001",
    }
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    owner = merchant(store.tenant)

    assert editor.post(ADJUST, body, format="json").status_code == 403
    assert handler.post(ADJUST, body, format="json").status_code == 403
    assert owner.post(ADJUST, {**body, "reason": "x"}, format="json").status_code == 400
    with tenant_context(store.tenant):
        AuthSession.objects.update(
            authenticated_at=timezone.now() - timedelta(hours=1), reauthenticated_at=None
        )
    stale = owner.post(ADJUST, body, format="json")
    with tenant_context(store.tenant):
        AuthSession.objects.update(authenticated_at=timezone.now())
    granted = owner.post(ADJUST, body, format="json")
    replayed = owner.post(ADJUST, body, format="json")
    overdraw = owner.post(
        ADJUST, {**body, "points": -500, "idempotency_key": "adjust-0002"}, format="json"
    )

    assert stale.status_code == 403
    assert granted.status_code == 201
    assert replayed.data["id"] == granted.data["id"]
    assert overdraw.status_code == 409
    assert account(store.tenant, customer).balance == 50
    with tenant_context(store.tenant):
        event = AuditEvent.objects.get(action="marketing.loyalty.adjusted")
        assert event.changes["points"] == 50
        assert "email" not in str(event.changes)


def test_program_rules_are_validated_and_audited(store: Any) -> None:
    owner = merchant(store.tenant)
    url = "/api/manage/marketing/loyalty/"
    before_module = owner.patch(url, {"is_enabled": True}, format="json")
    with tenant_context(store.tenant):
        modules.set_module(key="loyalty", enabled=True, actor=None)
    bad_value = owner.patch(url, {"point_value": "0.001"}, format="json")
    short_expiry = owner.patch(url, {"expiry_days": 10}, format="json")
    saved = owner.patch(
        url, {"is_enabled": True, "earn_points": 2, "earn_spend": "50.00"}, format="json"
    )

    assert before_module.status_code == 409
    assert bad_value.status_code == 400
    assert short_expiry.status_code == 400
    assert saved.status_code == 200
    assert saved.data["program"]["is_enabled"] is True
    assert saved.data["program"]["earn_points"] == 2
    with tenant_context(store.tenant):
        assert AuditEvent.objects.filter(action="marketing.loyalty.program_updated").exists()


def test_inactivity_expiry_is_recorded_in_the_ledger(store: Any) -> None:
    enable_loyalty(store.tenant, expiry_days=90)
    client, customer = member(store.tenant)
    grant(store.tenant, customer, 120)
    with tenant_context(store.tenant):
        LoyaltyAccount.objects.update(last_activity_at=timezone.now() - timedelta(days=91))
    summary = client.get("/api/account/loyalty/").data
    again = client.get("/api/account/loyalty/").data
    assert summary["balance"] == 0
    assert again["balance"] == 0
    assert [entry["kind"] for entry in again["entries"]] == ["expired", "adjusted"]


def test_loyalty_records_are_private_and_tenant_isolated(store: Any, tenant_b: Tenant) -> None:
    enable_loyalty(store.tenant)
    client, customer = member(store.tenant)
    other_client, other = member(store.tenant)
    grant(store.tenant, customer, 100)
    own = client.get("/api/account/loyalty/").data
    theirs = other_client.get("/api/account/loyalty/").data
    anonymous = APIClient().get("/api/account/loyalty/")
    staff = merchant(store.tenant)
    members = staff.get("/api/manage/marketing/loyalty/members/").data
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)

    assert own["balance"] == 100
    assert theirs["balance"] == 0
    assert anonymous.status_code in (401, 403)
    assert members["count"] == 1
    assert members["results"][0]["customer"] == str(customer.pk)
    assert editor.get("/api/manage/marketing/loyalty/members/").status_code == 403
    with tenant_context(tenant_b):
        assert not LoyaltyAccount.objects.exists()
        assert not LoyaltyLedger.objects.exists()
    del other


def test_cart_adds_do_not_depend_on_loyalty(store: Any) -> None:
    variant = product(store.tenant, amount="10.00")
    client, _ = member(store.tenant)
    assert add(client, variant).status_code == 200
    assert Decimal(client.get("/api/cart/").data["subtotal"]) == Decimal("10")
