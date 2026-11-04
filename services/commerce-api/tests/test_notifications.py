"""Phase 13 notification engine: outbox, idempotency, delivery states, commerce events, inbox,
templates, preferences, consent, SMS accounting and stock alerts (D-086 to D-090)."""

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.core import delivery
from commerce.identity import sessions
from commerce.identity.models import AccountType
from commerce.inventory import services as inventory
from commerce.inventory.models import StockLevel
from commerce.marketing.models import ConsentRecord
from commerce.notifications import events, jobs, providers, rendering, services
from commerce.notifications.models import (
    AppendOnlyError,
    CommunicationConsent,
    DeliveryAttempt,
    Notification,
    NotificationStatus,
    NotificationTemplate,
    SenderStatus,
    SmsSettings,
    SmsUsage,
)
from commerce.notifications.registry import BY_KEY
from commerce.orders.models import Order
from commerce.staff.permissions import StaffRole
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_user
from .fakes import FakeSmsProvider
from .test_checkout import product
from .test_marketing_loyalty import confirm, deliver, member
from .test_orders import merchant, place, reviewed, stock

pytestmark = pytest.mark.django_db
Capture = Callable[..., AbstractContextManager[Any]]


@pytest.fixture
def committed(django_capture_on_commit_callbacks: Capture) -> Callable[[], Any]:
    """Run on-commit hooks (queueing and the eager worker) like a committed request would."""
    return lambda: django_capture_on_commit_callbacks(execute=True)


def kinds(tenant: Tenant, **filters: Any) -> list[tuple[str, str, str]]:
    with tenant_context(tenant):
        return sorted(
            Notification.objects.filter(**filters).values_list("kind", "channel", "status")
        )


def placed_order(store: Any, committed: Callable[[], Any], client: APIClient | None = None) -> Any:
    variant = product(store.tenant, tracked=True, amount="750.00")
    stock(store.tenant, variant, 5)
    buyer = client or APIClient()
    with committed():
        placed = place(buyer, reviewed(buyer, variant), key=f"n-{uuid.uuid4().hex}")
    assert placed.status_code == 201, placed.data
    return placed


# --- Outbox and idempotency -----------------------------------------------------------------------


def test_order_placement_notifies_customer_and_staff_once(
    store: Any, committed: Callable[[], Any]
) -> None:
    owner = merchant(store.tenant)
    placed = placed_order(store, committed)
    with tenant_context(store.tenant):
        order = Order.objects.get(pk=placed.data["id"])
        with transaction.atomic():
            events.order_placed(order)  # a retried or replayed event
            events.order_placed(order)

    rows = kinds(store.tenant)
    assert ("order.placed", "email", "sent") in rows
    assert ("staff.order.new", "in_app", "sent") in rows
    assert rows.count(("order.placed", "email", "sent")) == 1
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["guest@example.test"]
    assert placed.data["number"] in message.subject
    assert "BDT 750.00" in message.body
    assert isinstance(message, EmailMultiAlternatives)
    html = str(message.alternatives[0][0])
    assert placed.data["number"] in html
    assert "<script" not in html
    inbox = owner.get("/api/manage/notifications/inbox/").json()
    assert inbox["results"][0]["link"] == f"/manage/orders/{placed.data['id']}"


def test_notifications_roll_back_with_the_business_transaction(
    store: Any, committed: Callable[[], Any]
) -> None:
    placed = placed_order(store, committed)
    with tenant_context(store.tenant):
        order = Order.objects.get(pk=placed.data["id"])
        before = Notification.objects.count()

        def business_change_that_fails() -> None:
            with transaction.atomic():
                services.emit(
                    "order.confirmed",
                    event_id=str(order.pk),
                    values={"order_number": order.number},
                    customer=services.Recipient(user=None, email="x@example.test"),
                )
                raise RuntimeError("business rollback")

        with pytest.raises(RuntimeError, match="business rollback"):
            business_change_that_fails()
        assert Notification.objects.count() == before


def test_a_notification_failure_never_breaks_an_order(
    store: Any, committed: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(**_: Any) -> Any:
        raise ValueError("template bug")

    monkeypatch.setattr(rendering, "render", broken)
    placed = placed_order(store, committed)
    assert placed.status_code == 201
    with tenant_context(store.tenant):
        assert Order.objects.filter(pk=placed.data["id"]).exists()
        assert not Notification.objects.exists()


def test_cod_is_never_reported_paid_before_collection(
    store: Any, committed: Callable[[], Any]
) -> None:
    owner = merchant(store.tenant)
    placed = placed_order(store, committed)
    assert not [k for k in kinds(store.tenant) if k[0] == "payment.succeeded"]
    with committed():
        confirm(owner, placed.data["id"])
        owner.post(f"/api/manage/orders/{placed.data['id']}/cod-collection/", {}, format="json")
        owner.post(f"/api/manage/orders/{placed.data['id']}/cod-collection/", {}, format="json")
    rows = kinds(store.tenant)
    assert rows.count(("payment.succeeded", "email", "sent")) == 1
    assert ("order.confirmed", "email", "sent") in rows
    assert ("staff.order.to_fulfil", "in_app", "sent") in rows  # the owner manages couriers


def test_shipments_notify_once_and_replays_do_not_repeat(
    store: Any, committed: Callable[[], Any]
) -> None:
    owner = merchant(store.tenant)
    placed = placed_order(store, committed)
    with committed():
        confirm(owner, placed.data["id"])
        deliver(owner, placed.data["id"])
        with tenant_context(store.tenant):
            from commerce.fulfillment.models import Shipment

            shipment_id = Shipment.objects.get(order_id=placed.data["id"]).pk
        replay = owner.post(
            f"/api/manage/fulfillment/shipments/{shipment_id}/transition/",
            {"status": "delivered"},
            format="json",
        )
        assert replay.status_code == 200
    rows = kinds(store.tenant, channel="email")
    assert rows.count(("shipment.dispatched", "email", "sent")) == 1
    assert rows.count(("shipment.delivered", "email", "sent")) == 1


def test_cancellation_and_refund_events(store: Any, committed: Callable[[], Any]) -> None:
    owner = merchant(store.tenant)
    placed = placed_order(store, committed)
    with committed():
        confirm(owner, placed.data["id"])
        owner.post(f"/api/manage/orders/{placed.data['id']}/cod-collection/", {}, format="json")
        owner.post(
            f"/api/manage/orders/{placed.data['id']}/refunds/",
            {"amount": "100.00", "reason": "Goodwill", "idempotency_key": "refund-note-0001"},
            format="json",
        )
    second = placed_order(store, committed)
    with committed():
        owner.post(
            f"/api/manage/orders/{second.data['id']}/transition/",
            {"status": "cancelled"},
            format="json",
        )
    rows = kinds(store.tenant)
    assert ("refund.completed", "email", "sent") in rows
    assert ("order.cancelled", "email", "sent") in rows
    assert ("staff.order.cancelled", "in_app", "sent") in rows
    refund_mail = next(m for m in mail.outbox if "Refund" in m.subject)
    assert "BDT 100.00" in refund_mail.body


# --- Delivery states and retries ------------------------------------------------------------------


def _pending_email(tenant: Tenant) -> Notification:
    with tenant_context(tenant), transaction.atomic():
        created = services.emit(
            "order.cancelled",
            event_id=str(uuid.uuid4()),
            values={"order_number": "ORD-999999", "order_total": "BDT 1.00"},
            customer=services.Recipient(user=None, email="retry@example.test", name="R"),
        )
    row = next(item for item in created if item.channel == "email")
    return row


class Outcomes(list[providers.Result]):
    """Queued provider results; ``calls`` records what was actually sent."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[providers.Result] = []


@pytest.fixture
def outcomes(monkeypatch: pytest.MonkeyPatch) -> Outcomes:
    queue = Outcomes()

    def fake(email: providers.Email, store_name: str) -> providers.Result:
        result = queue.pop(0)
        queue.calls.append(result)
        return result

    monkeypatch.setattr(providers, "send_default", fake)
    return queue


def test_retryable_failures_back_off_then_succeed(store: Any, outcomes: Outcomes) -> None:
    row = _pending_email(store.tenant)
    outcomes.extend([providers.Result(providers.RETRYABLE, "timeout", provider="default")])
    with tenant_context(store.tenant):
        first = services.deliver(row.pk)
        early = services.deliver(row.pk)
        Notification.objects.filter(pk=row.pk).update(scheduled_at=timezone.now())
        outcomes.append(
            providers.Result(providers.ACCEPTED, reference="<id@x>", provider="default")
        )
        second = services.deliver(row.pk)
        attempts = list(DeliveryAttempt.objects.filter(notification_id=row.pk).order_by("number"))
        final = Notification.objects.get(pk=row.pk)
    assert (first, early, second) == ("pending", "not_due", "sent")
    assert [a.outcome for a in attempts] == ["retryable", "accepted"]
    assert attempts[0].next_retry_at is not None
    assert final.attempts == 2
    assert final.sent_at is not None


def test_retries_are_bounded_and_permanent_errors_stop(store: Any, outcomes: Outcomes) -> None:
    row = _pending_email(store.tenant)
    with tenant_context(store.tenant):
        for _ in range(5):
            outcomes.append(providers.Result(providers.RETRYABLE, "timeout", provider="default"))
            services.deliver(row.pk)
            Notification.objects.filter(pk=row.pk).update(scheduled_at=timezone.now())
        exhausted = Notification.objects.get(pk=row.pk)
        other = _pending_email(store.tenant)
        outcomes.append(
            providers.Result(providers.PERMANENT, "recipient_refused", provider="default")
        )
        services.deliver(other.pk)
        refused = Notification.objects.get(pk=other.pk)
    assert exhausted.status == "failed"
    assert exhausted.failure_code == "retries_exhausted"
    assert exhausted.attempts == 5
    assert refused.status == "failed"
    assert refused.failure_code == "recipient_refused"


def test_unknown_outcomes_and_duplicate_tasks_never_resend(store: Any, outcomes: Outcomes) -> None:
    row = _pending_email(store.tenant)
    outcomes.append(providers.Result(providers.UNKNOWN, "outcome_unknown", provider="default"))
    with tenant_context(store.tenant):
        services.deliver(row.pk)
        services.deliver(row.pk)  # a duplicate task delivery
        unknown = Notification.objects.get(pk=row.pk)
        sent = _pending_email(store.tenant)
        outcomes.append(providers.Result(providers.ACCEPTED, provider="default"))
        services.deliver(sent.pk)
        services.deliver(sent.pk)
        stale = _pending_email(store.tenant)
        Notification.objects.filter(pk=stale.pk).update(
            status=NotificationStatus.PROCESSING, claimed_at=timezone.now() - timedelta(hours=1)
        )
        services.deliver(stale.pk)  # a worker died mid-send
        crashed = Notification.objects.get(pk=stale.pk)
    assert unknown.status == "failed"
    assert unknown.failure_code == "outcome_unknown"
    assert len(outcomes.calls) == 2
    assert crashed.status == "failed"
    assert crashed.failure_code == "outcome_unknown"


def test_sweeper_recovers_rows_the_broker_never_received(store: Any) -> None:
    row = _pending_email(store.tenant)
    with tenant_context(store.tenant):
        Notification.objects.filter(pk=row.pk).update(status=NotificationStatus.PENDING)
        due = services.due_ids()
        Notification.objects.filter(pk=row.pk).update(
            status=NotificationStatus.QUEUED, queued_at=timezone.now()
        )
        fresh_queue = services.due_ids()
        Notification.objects.filter(pk=row.pk).update(
            queued_at=timezone.now() - timedelta(minutes=10)
        )
        stale_queue = services.due_ids()
    assert row.pk in due
    assert row.pk not in fresh_queue
    assert row.pk in stale_queue


def test_manual_retry_is_permitted_rate_limited_and_audited(
    store: Any, outcomes: Outcomes, committed: Callable[[], Any]
) -> None:
    row = _pending_email(store.tenant)
    outcomes.append(providers.Result(providers.PERMANENT, "server_rejected", provider="default"))
    with tenant_context(store.tenant):
        services.deliver(row.pk)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    owner = merchant(store.tenant)
    url = f"/api/manage/notifications/deliveries/{row.pk}/retry/"
    denied = handler.post(url, {}, format="json")
    outcomes.append(providers.Result(providers.ACCEPTED, provider="default"))
    with committed():
        retried = owner.post(url, {}, format="json")
    again = owner.post(url, {}, format="json")
    history = owner.get("/api/manage/notifications/deliveries/").json()
    attempts = owner.get(f"/api/manage/notifications/deliveries/{row.pk}/attempts/").json()
    assert denied.status_code == 403
    assert retried.status_code == 200
    assert again.status_code == 409
    assert history["results"][0]["recipient"] == "r***@example.test"
    assert "retry@example.test" not in str(history)
    assert [a["outcome"] for a in attempts] == ["permanent", "accepted"]


def test_delivery_attempts_are_append_only(store: Any, outcomes: Outcomes) -> None:
    row = _pending_email(store.tenant)
    outcomes.append(providers.Result(providers.ACCEPTED, provider="default"))
    with tenant_context(store.tenant):
        services.deliver(row.pk)
        attempt = DeliveryAttempt.objects.get(notification_id=row.pk)
        with pytest.raises(AppendOnlyError):
            attempt.save()
        with pytest.raises(DatabaseError), transaction.atomic():
            DeliveryAttempt.objects.filter(pk=attempt.pk).update(outcome="permanent")


# --- In-app inboxes -------------------------------------------------------------------------------


def test_staff_inbox_follows_permissions_and_ownership(
    store: Any, committed: Callable[[], Any], tenant_b: Tenant
) -> None:
    owner = merchant(store.tenant)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    placed = placed_order(store, committed)
    with committed():
        confirm(owner, placed.data["id"])
        owner.post(f"/api/manage/orders/{placed.data['id']}/cod-collection/", {}, format="json")
    owner_kinds = {
        i["kind"] for i in owner.get("/api/manage/notifications/inbox/").json()["results"]
    }
    handler_kinds = {
        i["kind"] for i in handler.get("/api/manage/notifications/inbox/").json()["results"]
    }
    editor_inbox = editor.get("/api/manage/notifications/inbox/").json()
    unread = handler.get("/api/manage/notifications/inbox/unread/").json()["unread"]
    owner_ids = [i["id"] for i in owner.get("/api/manage/notifications/inbox/").json()["results"]]
    foreign = handler.post(
        "/api/manage/notifications/inbox/read/", {"ids": owner_ids}, format="json"
    ).json()
    cleared = handler.post("/api/manage/notifications/inbox/read/", {"all": True}, format="json")

    assert {"staff.order.new", "staff.cod.collected"} <= owner_kinds
    assert "staff.order.new" in handler_kinds
    assert "staff.cod.collected" not in handler_kinds  # needs reports.view
    assert editor_inbox["count"] == 0  # no orders.manage, no financial alerts
    assert foreign["unread"] == unread  # another user's ids change nothing
    assert cleared.json()["unread"] == 0
    with tenant_context(tenant_b):
        assert not Notification.objects.exists()


def test_customer_inbox_is_private(store: Any, committed: Callable[[], Any]) -> None:
    client, _user = member(store.tenant)
    other, _ = member(store.tenant)
    placed_order(store, committed, client)
    mine = client.get("/api/account/notifications/").json()
    theirs = other.get("/api/account/notifications/").json()
    anonymous = APIClient().get("/api/account/notifications/")
    staff_view = merchant(store.tenant).get("/api/account/notifications/")
    assert [i["kind"] for i in mine["results"]] == ["order.placed"]
    assert theirs["count"] == 0
    assert anonymous.status_code in (401, 403)
    assert staff_view.status_code in (401, 403)
    marked = client.post("/api/account/notifications/read/", {"all": True}, format="json")
    assert marked.json()["unread"] == 0


# --- Templates and rendering ----------------------------------------------------------------------


def test_templates_validate_variables_and_markup_and_need_settings_access(store: Any) -> None:
    owner = merchant(store.tenant)
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    url = "/api/manage/notifications/templates/order.placed/en/"
    unknown = owner.put(url, {"subject": "Order {password}"}, format="json")
    markup = owner.put(url, {"body": "<script>alert(1)</script>"}, format="json")
    stray = owner.put(url, {"subject": "Order {order_number"}, format="json")
    denied = editor.put(url, {"subject": "Hi"}, format="json")
    security = owner.put(
        "/api/manage/notifications/templates/security.password_changed/en/",
        {"is_enabled": False},
        format="json",
    )
    saved = owner.put(url, {"subject": "Thanks! {order_number} is in"}, format="json")
    saved_again = owner.put(url, {"subject": "Thanks! {order_number} is ours"}, format="json")
    preview = owner.post(
        f"{url}preview/", {"subject": "Hello {customer_name}", "body": "A & B"}, format="json"
    )

    assert unknown.status_code == 400
    assert markup.status_code == 400
    assert stray.status_code == 400
    assert denied.status_code == 403
    assert security.status_code == 400
    assert saved.status_code == 200
    en = next(item for item in saved_again.json()["locales"] if item["locale"] == "en")
    assert en["version"] == 2
    assert en["customized"] is True
    assert preview.json()["subject"] == "Hello Rahim Uddin"
    assert "A &amp; B" in preview.json()["html"]


def test_rendering_escapes_values_and_blocks_header_injection() -> None:
    kind = BY_KEY["order.placed"]
    brand = rendering.Branding("Shop <b>", "#zzzzzz", "javascript:alert(1)", "", "", "")
    content = rendering.render(
        kind=kind,
        locale="en",
        values={
            "customer_name": "<script>alert(1)</script>",
            "order_number": "ORD-1\r\nBcc: victim@example.test",
            "order_total": "BDT 1.00",
            "payment_method": "Cash",
        },
        override=None,
        branding=brand,
        link_path="//evil.example/steal",
    )
    assert "<script>" not in content.html
    assert "&lt;script&gt;" in content.html
    assert "\r" not in content.subject
    assert "\n" not in content.subject
    assert "javascript:" not in content.html  # an unsafe logo URL is replaced by the name
    assert "evil.example" not in content.html  # only same-origin paths become links
    assert "#zzzzzz" not in content.html


def test_custom_and_bangla_templates_are_used_and_fall_back(
    store: Any, committed: Callable[[], Any]
) -> None:
    with tenant_context(store.tenant):
        StoreSettings.objects.update(default_language="bn")
        NotificationTemplate.objects.create(
            key="order.placed", locale="en", subject="EN only {order_number}"
        )
    placed_order(store, committed)
    with tenant_context(store.tenant):
        subject = Notification.objects.get(kind="order.placed", channel="email").subject
    assert subject.startswith("EN only")  # no Bangla override: the English override is used
    with tenant_context(store.tenant):
        NotificationTemplate.objects.all().delete()
    second = placed_order(store, committed)
    with tenant_context(store.tenant):
        bangla = (
            Notification.objects.filter(kind="order.placed", channel="email")
            .exclude(subject__startswith="EN only")
            .get()
            .subject
        )
    assert second.data["number"] in bangla
    assert "অর্ডার" in bangla


def test_a_disabled_template_suppresses_that_message(
    store: Any, committed: Callable[[], Any]
) -> None:
    with tenant_context(store.tenant):
        NotificationTemplate.objects.create(key="order.placed", locale="en", is_enabled=False)
    placed_order(store, committed)
    assert not [k for k in kinds(store.tenant) if k[0] == "order.placed"]
    assert len(mail.outbox) == 0


# --- Preferences, marketing consent and unsubscribe -----------------------------------------------


def test_marketing_needs_explicit_opt_in_and_unsubscribe_works(
    store: Any, committed: Callable[[], Any]
) -> None:
    from .test_marketing_loyalty import enable_loyalty, grant

    enable_loyalty(store.tenant)
    client, user = member(store.tenant)
    _other_client, other = member(store.tenant)
    grant(store.tenant, user, 300)
    grant(store.tenant, other, 300)
    with tenant_context(store.tenant):
        # Accepting marketing cookies (Phase 12) is not consent to marketing email.
        ConsentRecord.objects.create(
            subject_digest="a" * 64,
            analytics=True,
            marketing=True,
            policy_version="1",
            source="banner",
            recorded_at=timezone.now(),
        )
    essential = client.put(
        "/api/account/notifications/preferences/",
        {"topic": "order_updates", "channel": "email", "enabled": False},
        format="json",
    )
    opted = client.put(
        "/api/account/notifications/preferences/",
        {"topic": "marketing", "channel": "email", "enabled": True},
        format="json",
    )
    with tenant_context(store.tenant), committed():
        sent = jobs.points_reminders()
        again = jobs.points_reminders()
    with tenant_context(store.tenant):
        reminder = Notification.objects.get(kind="marketing.points_reminder")
        token = services.unsubscribe_token(user, "email")
    assert essential.status_code == 400
    assert opted.status_code == 200
    assert sent == 1
    assert again == 0
    assert reminder.recipient_user_id == user.pk
    assert "/unsubscribe?token=" in mail.outbox[-1].body

    response = APIClient().post("/api/notifications/unsubscribe/", {"token": token}, format="json")
    replay = APIClient().post("/api/notifications/unsubscribe/", {"token": token}, format="json")
    forged = APIClient().post(
        "/api/notifications/unsubscribe/", {"token": token[:-3] + "abc"}, format="json"
    )
    with tenant_context(store.tenant):
        evidence = list(
            CommunicationConsent.objects.filter(user=user).values_list("granted", "source")
        )
        allowed = services.marketing_allowed(user, "email")
    assert response.json() == {"unsubscribed": True}
    assert replay.json() == {"unsubscribed": True}
    assert forged.json() == {"unsubscribed": False}
    assert allowed is False
    assert evidence == [(True, "account"), (False, "unsubscribe_link")]
    prefs = client.get("/api/account/notifications/preferences/").json()
    assert {(p["topic"], p["channel"]): p["enabled"] for p in prefs}[
        ("marketing", "email")
    ] is False


def test_unsent_marketing_is_suppressed_after_withdrawal(store: Any) -> None:
    _client, user = member(store.tenant)
    with tenant_context(store.tenant):
        services.set_preference(
            user=user, topic="marketing", channel="email", enabled=True, source="account"
        )
        with transaction.atomic():
            created = services.emit(
                "marketing.points_reminder",
                event_id="manual",
                values={"points": "100"},
                customer=services.Recipient(user=user, email=user.email or "", name="M"),
            )
        services.set_preference(
            user=user, topic="marketing", channel="email", enabled=False, source="account"
        )
        result = services.deliver(created[0].pk)
    assert result == "suppressed"


# --- SMS ------------------------------------------------------------------------------------------


@pytest.fixture
def sms(settings: Settings) -> Iterator[list[Any]]:
    settings.MATECART_SMS_PROVIDER = "tests.fakes.FakeSmsProvider"
    delivery._provider.cache_clear()
    FakeSmsProvider.outbox.clear()
    yield FakeSmsProvider.outbox
    delivery._provider.cache_clear()


def test_sms_segments_follow_gsm7_and_ucs2() -> None:
    assert providers.sms_segments("a" * 160) == (1, "gsm7")
    assert providers.sms_segments("a" * 161) == (2, "gsm7")
    assert providers.sms_segments("{" * 80) == (1, "gsm7")  # extended characters count twice
    assert providers.sms_segments("অ" * 70) == (1, "ucs2")
    assert providers.sms_segments("অ" * 71) == (2, "ucs2")


def test_sender_ids_need_netamate_approval(store: Any, sms: list[Any]) -> None:
    owner = merchant(store.tenant)
    requested = owner.patch(
        "/api/manage/notifications/sms/", {"requested_sender": "RahimShop"}, format="json"
    )
    early = owner.patch("/api/manage/notifications/sms/", {"is_enabled": True}, format="json")
    with pytest.raises(Exception):  # noqa: B017,PT011 - only the requested id can be approved
        call_command("approve_sms_sender", "OtherName")
    call_command("approve_sms_sender", "RahimShop", "--allowance", "100")
    enabled = owner.patch("/api/manage/notifications/sms/", {"is_enabled": True}, format="json")
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    assert requested.json()["sender_status"] == "pending"
    assert early.status_code == 400
    assert enabled.json()["sender_status"] == "approved"
    assert enabled.json()["is_enabled"] is True
    assert enabled.json()["ready"] is True
    assert editor.get("/api/manage/notifications/sms/").status_code == 403


def test_order_sms_uses_the_approved_sender_and_counts_segments(
    store: Any, committed: Callable[[], Any], sms: list[Any]
) -> None:
    with tenant_context(store.tenant):
        SmsSettings.objects.create(
            is_enabled=True,
            requested_sender="RahimShop",
            approved_sender="RahimShop",
            sender_status=SenderStatus.APPROVED,
            monthly_allowance=10,
        )
    variant = product(store.tenant, tracked=True, amount="200.00")
    stock(store.tenant, variant, 5)
    buyer = APIClient()
    checkout_id = reviewed(buyer, variant)
    buyer.patch(f"/api/checkout/{checkout_id}/", {}, format="json")
    with tenant_context(store.tenant):
        from commerce.checkout.models import CheckoutSession

        CheckoutSession.objects.filter(pk=checkout_id).update(contact_phone="+8801712345678")
    with committed():
        place(buyer, checkout_id, key=f"sms-{uuid.uuid4().hex}")
    with tenant_context(store.tenant):
        usage = list(SmsUsage.objects.values_list("segments", "accepted", "funded_by"))
        balance = services.sms_balance()
    assert len(sms) == 1
    assert sms[0].to == "+8801712345678"
    assert sms[0].sender == "RahimShop"
    assert usage == [(1, True, "allowance")]
    assert balance["used"] == 1
    assert balance["remaining"] == 9


def test_sms_stops_when_the_allowance_is_used(
    store: Any, committed: Callable[[], Any], sms: list[Any]
) -> None:
    with tenant_context(store.tenant):
        SmsSettings.objects.create(
            is_enabled=True,
            requested_sender="RahimShop",
            approved_sender="RahimShop",
            sender_status=SenderStatus.APPROVED,
            monthly_allowance=0,
        )
        with transaction.atomic():
            created = services.emit(
                "order.placed",
                event_id="allowance",
                values={"order_number": "ORD-1", "order_total": "BDT 1.00"},
                customer=services.Recipient(user=None, phone="+8801712345678"),
            )
        row = next(item for item in created if item.channel == "sms")
        result = services.deliver(row.pk)
        suppressed = Notification.objects.get(pk=row.pk)
    assert result == "suppressed"
    assert suppressed.failure_code == "sms_allowance_exhausted"
    assert sms == []


# --- Inventory, fraud, after sales and security ---------------------------------------------------


def test_stock_alerts_fire_on_threshold_transitions_only(
    store: Any, committed: Callable[[], Any]
) -> None:
    merchant(store.tenant)
    variant = product(store.tenant, tracked=True)
    stock(store.tenant, variant, 10)
    with tenant_context(store.tenant):
        StockLevel.objects.filter(variant=variant).update(low_stock_threshold=Decimal(3))

    def adjust(quantity: int) -> None:
        with tenant_context(store.tenant):
            inventory.adjust(
                variant=variant,
                location=inventory.default_location(),
                quantity=Decimal(quantity),
                movement_type="adjustment",
                request=inventory.StockRequest(reason="test"),
            )

    def run() -> int:
        with tenant_context(store.tenant), committed():
            return jobs.stock_alerts()

    assert run() == 0
    adjust(-8)  # 2 left: low
    assert run() == 1
    assert run() == 0  # still low: no repeat
    adjust(-2)  # out
    assert run() == 1
    adjust(10)  # recovered: silent
    assert run() == 0
    adjust(-9)  # low again: a new alert
    assert run() == 1
    assert sorted(k[0] for k in kinds(store.tenant)) == [
        "staff.stock.low",
        "staff.stock.low",
        "staff.stock.out",
    ]


def test_after_sales_messages_notify_the_other_side(store: Any) -> None:
    from .test_orders import customer_client

    buyer = customer_client(store.tenant, "case-notify@example.test")
    staff = merchant(store.tenant)
    created = buyer.post(
        "/api/cases/",
        {
            "kind": "support",
            "subject": "Question",
            "reason": "Please help with my purchase.",
            "quantity": "1.000",
            "idempotency_key": "support-notify-0001",
        },
        format="json",
    )
    case = created.data["id"]
    staff.patch(f"/api/manage/aftersales/cases/{case}/", {"status": "approved"}, format="json")
    staff.post(f"/api/manage/aftersales/cases/{case}/", {"body": "We are on it."}, format="json")
    buyer.post(f"/api/cases/{case}/", {"body": "Thanks"}, format="json")
    rows = {k[0] for k in kinds(store.tenant)}
    assert {
        "case.submitted",
        "staff.case.new",
        "case.updated",
        "case.reply",
        "staff.case.message",
    } <= rows


def test_account_security_changes_are_notified_without_secrets(
    store: Any, committed: Callable[[], Any]
) -> None:
    from commerce.identity import mfa, passwords

    user = make_user(
        store.tenant,
        account_type=AccountType.CUSTOMER,
        email="secure@example.test",
        password="customer-passphrase-1234",
        email_verified_at=timezone.now(),
    )
    with tenant_context(store.tenant), committed():
        session, _ = sessions.start(user)
        passwords.change(
            user=user,
            current_password="customer-passphrase-1234",
            new_password="another-long-passphrase-5678",
            keep=session,
        )
        mfa.enable_email(user)
    rows = kinds(store.tenant)
    assert ("security.password_changed", "email", "sent") in rows
    assert ("security.mfa_changed", "email", "sent") in rows
    bodies = " ".join(str(m.body) for m in mail.outbox)
    assert "another-long-passphrase" not in bodies


def test_authentication_codes_never_enter_the_notification_engine(
    store: Any, committed: Callable[[], Any]
) -> None:
    from commerce.identity import passwords

    make_user(
        store.tenant,
        account_type=AccountType.CUSTOMER,
        email="reset@example.test",
        password="customer-passphrase-1234",
        email_verified_at=timezone.now(),
    )
    with tenant_context(store.tenant), committed():
        passwords.request_reset(
            realm="customer",
            email="reset@example.test",
            ip="127.0.0.1",
            link_base="http://testserver/account/reset",
            store_name="Shop",
        )
    with tenant_context(store.tenant):
        assert not Notification.objects.exists()
    assert len(mail.outbox) == 1
    assert "/account/reset" in mail.outbox[0].body


# --- Retention ------------------------------------------------------------------------------------


def test_retention_clears_content_and_old_read_messages(
    store: Any, committed: Callable[[], Any]
) -> None:
    merchant(store.tenant)
    placed_order(store, committed)
    old = timezone.now() - timedelta(days=200)
    with tenant_context(store.tenant):
        Notification.objects.update(created_at=old)
        Notification.objects.filter(channel="in_app").update(read_at=old)
        result = services.cleanup()
        email = Notification.objects.get(kind="order.placed", channel="email")
    assert result["content_cleared"] >= 1
    assert result["read_in_app"] >= 1
    assert email.body_text == ""
    assert email.recipient_address == "—"
