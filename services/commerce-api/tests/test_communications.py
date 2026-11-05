"""Phase 14 unified inbox: conversation lifecycle, ordering, idempotency, assignment, notes,
unread state, permissions, tenant isolation, product sharing and analytics."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.db import connection as connection_db
from django.utils import timezone
from pytest_django.fixtures import Settings as SettingsWrapper

from commerce.audit.models import AuditEvent
from commerce.communications import livechat, policy, retention, services
from commerce.communications.models import (
    ChannelType,
    Conversation,
    ConversationEvent,
    ConversationNote,
    ConversationReadState,
    ConversationStatus,
    DeliveryStatus,
    Message,
    MessageKind,
    Participant,
)
from commerce.core.domain import ConflictError
from commerce.identity.models import User
from commerce.notifications.models import Notification
from commerce.staff.permissions import StaffRole
from commerce.stores import modules
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .comms_helpers import (  # noqa: F401 (run_on_commit is a fixture)
    INBOX,
    connection,
    customer,
    enable_live_chat,
    run_on_commit,
    staff,
)
from .factories import make_store_settings
from .test_checkout import product
from .test_inventory_concurrency import run_concurrently
from .test_orders import stock

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("run_on_commit")]


def guest(tenant: Tenant) -> Participant:
    with tenant_context(tenant):
        visitor = livechat.resolve_visitor(guest_token=None, customer=None, create=True)
        assert visitor.participant is not None
        return visitor.participant


def say(tenant: Tenant, participant: Participant, text: str = "Hello", key: str = "") -> Message:
    with tenant_context(tenant):
        message, _ = services.visitor_send(
            participant=participant, body=text, client_key=key or uuid.uuid4().hex
        )
        return message


def conv(tenant: Tenant, participant: Participant) -> Conversation:
    with tenant_context(tenant):
        found = services.latest_conversation(participant)
        assert found is not None
        return found


@pytest.fixture
def shop(store: Any, deployment_tenant: Tenant) -> Tenant:
    enable_live_chat(deployment_tenant)
    return deployment_tenant


# --- Lifecycle and ordering -----------------------------------------------------------------------


def test_visitor_messages_open_one_conversation_with_stable_sequence(shop: Tenant) -> None:
    visitor = guest(shop)
    first = say(shop, visitor, "Hi")
    second = say(shop, visitor, "Is this in stock?")
    with tenant_context(shop):
        conversation = Conversation.objects.get(participant=visitor)
        assert [first.seq, second.seq] == [1, 2]
        assert conversation.status == ConversationStatus.OPEN
        assert conversation.inbound_count == 2
        assert conversation.last_preview == "Is this in stock?"
        assert first.delivery_status == DeliveryStatus.RECEIVED
        # A guest chat never creates a customer account.
        assert visitor.customer_id is None
        assert not User.objects.filter(account_type="customer").exists()


def test_client_key_makes_visitor_send_idempotent(shop: Tenant) -> None:
    visitor = guest(shop)
    one = say(shop, visitor, "Same", key="key-aaaaaaaa")
    two = say(shop, visitor, "Same", key="key-aaaaaaaa")
    assert one.pk == two.pk
    with tenant_context(shop):
        assert Message.objects.count() == 1


def test_resolved_reopens_and_closed_starts_new_conversation(shop: Tenant) -> None:
    _, agent = staff(shop)
    visitor = guest(shop)
    say(shop, visitor)
    first = conv(shop, visitor)
    with tenant_context(shop):
        services.change_status(
            conversation=first, actor=agent, status="resolved", expected_version=first.version
        )
    say(shop, visitor, "One more thing")
    with tenant_context(shop):
        first.refresh_from_db()
        assert first.status == ConversationStatus.OPEN  # resolved -> reopened by the customer
        services.change_status(
            conversation=first, actor=agent, status="closed", expected_version=first.version
        )
    say(shop, visitor, "New question")
    with tenant_context(shop):
        rows = list(Conversation.objects.filter(participant=visitor).order_by("created_at"))
        assert len(rows) == 2
        assert rows[0].status == ConversationStatus.CLOSED
        assert rows[1].status == ConversationStatus.OPEN
        assert Message.objects.filter(conversation=rows[0]).count() == 2  # history retained


def test_database_allows_one_active_conversation_per_participant(shop: Tenant) -> None:
    visitor = guest(shop)
    say(shop, visitor)
    with tenant_context(shop), pytest.raises(IntegrityError), transaction.atomic():
        Conversation.objects.create(channel="website", participant=visitor)


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_messages_create_one_conversation(deployment_tenant: Tenant) -> None:
    make_store_settings(deployment_tenant)
    enable_live_chat(deployment_tenant)
    visitor = guest(deployment_tenant)

    def send(index: int) -> Any:
        return services.visitor_send(
            participant=visitor, body=f"race {index}", client_key=f"race-key-{index}"
        )[0].seq

    results = run_concurrently(deployment_tenant, 6, send)
    assert all(isinstance(r, int) for r in results), results
    assert sorted(results) == [1, 2, 3, 4, 5, 6]
    with tenant_context(deployment_tenant):
        assert Conversation.objects.filter(participant=visitor).count() == 1


def test_state_machine_refuses_invalid_and_stale_changes(shop: Tenant) -> None:
    _, agent = staff(shop)
    visitor = guest(shop)
    say(shop, visitor)
    conversation = conv(shop, visitor)
    with tenant_context(shop):
        services.change_status(
            conversation=conversation, actor=agent, status="pending", expected_version=1
        )
        with pytest.raises(ConflictError) as stale:
            services.change_status(
                conversation=conversation, actor=agent, status="resolved", expected_version=1
            )
        assert stale.value.get_codes() == "conversation_changed"
        services.change_status(
            conversation=conversation, actor=agent, status="closed", expected_version=2
        )
        with pytest.raises(ConflictError):
            services.change_status(
                conversation=conversation, actor=agent, status="pending", expected_version=3
            )
        kinds = list(
            ConversationEvent.objects.filter(conversation=conversation).values_list(
                "to_status", flat=True
            )
        )
        assert kinds == ["pending", "closed"]
        assert (
            AuditEvent.objects.filter(action="communications.conversation.status_changed").count()
            == 2
        )


def test_closed_conversation_cannot_reopen_when_a_newer_one_is_active(shop: Tenant) -> None:
    _, agent = staff(shop)
    visitor = guest(shop)
    say(shop, visitor)
    old = conv(shop, visitor)
    with tenant_context(shop):
        services.change_status(conversation=old, actor=agent, status="closed", expected_version=1)
    say(shop, visitor, "again")
    with tenant_context(shop), pytest.raises(ConflictError) as exc:
        services.change_status(conversation=old, actor=agent, status="open", expected_version=2)
    assert exc.value.get_codes() == "newer_conversation_active"


# --- Inbox API ------------------------------------------------------------------------------------


def test_inbox_lists_filters_searches_and_paginates(shop: Tenant) -> None:
    client, agent = staff(shop, StaffRole.ORDER_HANDLER)
    visitors = [guest(shop) for _ in range(5)]
    for index, visitor in enumerate(visitors):
        say(shop, visitor, f"message {index}")
    with tenant_context(shop):
        Participant.objects.filter(pk=visitors[0].pk).update(display_name="Nusrat Jahan")
        services.assign(
            conversation=conv(shop, visitors[1]),
            actor=agent,
            assignee_id=agent.pk,
            expected_version=1,
        )
    page = client.get(f"{INBOX}conversations/", {"limit": 2})
    assert page.status_code == 200, page.data
    assert len(page.data["results"]) == 2
    assert page.data["next_cursor"]
    rest = client.get(f"{INBOX}conversations/", {"limit": 10, "cursor": page.data["next_cursor"]})
    ids = [r["id"] for r in page.data["results"]] + [r["id"] for r in rest.data["results"]]
    assert len(ids) == 5
    assert len(set(ids)) == 5
    assert page.data["results"][0]["unread"] == 1
    assert page.data["unread_conversations"] == 5
    mine = client.get(f"{INBOX}conversations/", {"view": "mine"})
    assert [r["id"] for r in mine.data["results"]] == [str(conv(shop, visitors[1]).pk)]
    unassigned = client.get(f"{INBOX}conversations/", {"view": "unassigned"})
    assert len(unassigned.data["results"]) == 4
    found = client.get(f"{INBOX}conversations/", {"q": "nusrat"})
    assert [r["display_name"] for r in found.data["results"]] == ["Nusrat Jahan"]
    website = client.get(f"{INBOX}conversations/", {"channel": "whatsapp"})
    assert website.data["results"] == []
    bad = client.get(f"{INBOX}conversations/", {"cursor": "not-a-cursor"})
    assert bad.status_code == 400


def test_unread_is_per_staff_member(shop: Tenant) -> None:
    alice_client, _alice = staff(shop, name="Alice")
    bob_client, bob = staff(shop, StaffRole.MANAGER, name="Bob")
    visitor = guest(shop)
    say(shop, visitor)
    say(shop, visitor, "second")
    conversation = conv(shop, visitor)
    assert alice_client.post(f"{INBOX}conversations/{conversation.pk}/read/").status_code == 204
    alice_view = alice_client.get(f"{INBOX}conversations/", {"view": "unread"})
    bob_view = bob_client.get(f"{INBOX}conversations/", {"view": "unread"})
    assert alice_view.data["results"] == []
    assert bob_view.data["results"][0]["unread"] == 2
    say(shop, visitor, "third")
    alice_view = alice_client.get(f"{INBOX}conversations/")
    assert alice_view.data["results"][0]["unread"] == 1
    with tenant_context(shop):
        assert ConversationReadState.objects.filter(user=bob).count() == 0


def test_staff_reply_reaches_website_thread_idempotently(shop: Tenant) -> None:
    client, _ = staff(shop)
    visitor = guest(shop)
    say(shop, visitor)
    conversation = conv(shop, visitor)
    url = f"{INBOX}conversations/{conversation.pk}/messages/"
    first = client.post(url, {"client_key": "reply-0001", "body": "Yes, in stock."}, format="json")
    again = client.post(url, {"client_key": "reply-0001", "body": "Yes, in stock."}, format="json")
    assert first.status_code == 201, first.data
    assert again.status_code == 200
    assert again.data["id"] == first.data["id"]
    assert first.data["delivery_status"] == "accepted"
    thread = client.get(f"{INBOX}conversations/{conversation.pk}/thread/")
    assert [m["direction"] for m in thread.data["messages"]] == ["inbound", "outbound"]
    with tenant_context(shop):
        conversation.refresh_from_db()
        assert conversation.first_response_at is not None


def test_markup_is_stored_as_plain_text(shop: Tenant) -> None:
    visitor = guest(shop)
    message = say(shop, visitor, "<img src=x onerror=alert(1)>\x00 hi")
    assert message.body == "<img src=x onerror=alert(1)> hi"


def test_internal_notes_stay_with_staff(shop: Tenant) -> None:
    client, _ = staff(shop)
    visitor = guest(shop)
    say(shop, visitor)
    conversation = conv(shop, visitor)
    note = client.post(
        f"{INBOX}conversations/{conversation.pk}/notes/",
        {"body": "Customer is a reseller"},
        format="json",
    )
    assert note.status_code == 201
    thread = client.get(f"{INBOX}conversations/{conversation.pk}/thread/")
    assert [n["body"] for n in thread.data["notes"]] == ["Customer is a reseller"]
    assert all(m["body"] != "Customer is a reseller" for m in thread.data["messages"])
    with tenant_context(shop):
        assert Message.objects.filter(body__contains="reseller").count() == 0
        assert ConversationNote.objects.count() == 1


def test_assignment_needs_a_messaging_team_member_and_notifies_them(shop: Tenant) -> None:
    owner_client, _owner = staff(shop, name="Owner One")
    _, handler = staff(shop, StaffRole.ORDER_HANDLER, name="Hasan Ali")
    _, editor = staff(shop, StaffRole.CONTENT_EDITOR)
    visitor = guest(shop)
    say(shop, visitor)
    conversation = conv(shop, visitor)
    url = f"{INBOX}conversations/{conversation.pk}/assign/"
    refused = owner_client.post(url, {"assignee": str(editor.pk), "version": 1}, format="json")
    assert refused.status_code == 400
    done = owner_client.post(url, {"assignee": str(handler.pk), "version": 1}, format="json")
    assert done.status_code == 200, done.data
    assert done.data["assignee"]["name"] == "Hasan Ali"
    stale = owner_client.post(url, {"assignee": None, "version": 1}, format="json")
    assert stale.status_code == 409
    with tenant_context(shop):
        alerts = Notification.objects.filter(kind="staff.conversation.assigned")
        assert {row.recipient_user_id for row in alerts} == {handler.pk}
        assert ConversationEvent.objects.filter(kind="assigned", assignee=handler).exists()


def test_new_conversation_alerts_staff_once(shop: Tenant) -> None:
    staff(shop)
    visitor = guest(shop)
    say(shop, visitor)
    say(shop, visitor, "second")
    with tenant_context(shop):
        rows = Notification.objects.filter(kind="staff.conversation.new", channel="in_app")
        assert rows.count() == 1
        # Alerts carry no message text: messages live in the inbox only.
        assert "second" not in rows.get().body_text


def test_inbox_requires_messages_permission(shop: Tenant, api_client: Any) -> None:
    editor_client, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    customer_client, _, _ = customer(shop)
    assert editor_client.get(f"{INBOX}conversations/").status_code == 403
    assert customer_client.get(f"{INBOX}conversations/").status_code == 401
    assert api_client.get(f"{INBOX}conversations/").status_code == 401


def test_conversations_are_isolated_between_stores(
    shop: Tenant, tenant_b: Tenant, settings: SettingsWrapper
) -> None:
    client, _ = staff(shop)
    make_store_settings(tenant_b)
    enable_live_chat(tenant_b)
    with tenant_context(tenant_b):
        other = livechat.resolve_visitor(guest_token=None, customer=None, create=True).participant
        assert other is not None
        services.visitor_send(participant=other, body="other store", client_key="other-key-1")
        foreign = Conversation.objects.get()
    assert client.get(f"{INBOX}conversations/").data["results"] == []
    assert client.get(f"{INBOX}conversations/{foreign.pk}/").status_code == 404
    assert client.get(f"{INBOX}conversations/{foreign.pk}/thread/").status_code == 404
    reply = client.post(
        f"{INBOX}conversations/{foreign.pk}/messages/",
        {"client_key": "x-key-0001", "body": "hi"},
        format="json",
    )
    assert reply.status_code == 404

    # The database refuses cross-tenant references even when built by hand.
    def forge() -> None:
        with transaction.atomic():
            Message.objects.create(
                conversation_id=foreign.pk, seq=99, direction="inbound", delivery_status="received"
            )
            with connection_db.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    with tenant_context(shop), pytest.raises(IntegrityError):
        forge()


# --- Policy ---------------------------------------------------------------------------------------


def test_reply_windows_follow_each_provider(shop: Tenant, settings: SettingsWrapper) -> None:
    wa = connection(shop)
    fb = connection(shop, ChannelType.MESSENGER)
    now = timezone.now()
    with tenant_context(shop):
        wa_person = Participant.objects.create(
            channel="whatsapp", connection=wa, external_id="88017"
        )
        fb_person = Participant.objects.create(channel="messenger", connection=fb, external_id="61")
        wa_conv = Conversation(
            channel="whatsapp", connection=wa, participant=wa_person, last_inbound_at=now
        )
        fb_conv = Conversation(
            channel="messenger", connection=fb, participant=fb_person, last_inbound_at=now
        )
    assert policy.reply_policy(wa_conv, wa, wa_person, now).can_reply
    later = now + timedelta(hours=25)
    expired = policy.reply_policy(wa_conv, wa, wa_person, later)
    assert not expired.can_reply
    assert expired.reason == "template_required"
    assert not expired.templates_allowed  # no recorded opt-in yet
    wa_person.template_opt_in_at = now
    assert policy.reply_policy(wa_conv, wa, wa_person, later).templates_allowed
    wa_person.opted_out_at = now
    assert not policy.reply_policy(wa_conv, wa, wa_person, later).templates_allowed
    assert policy.reply_policy(fb_conv, fb, fb_person, now).can_reply
    assert policy.reply_policy(fb_conv, fb, fb_person, later).reason == "window_closed"
    settings.META_HUMAN_AGENT_APPROVED = True
    tagged = policy.reply_policy(fb_conv, fb, fb_person, later)
    assert tagged.can_reply
    assert tagged.tag == "HUMAN_AGENT"
    assert not policy.reply_policy(fb_conv, fb, fb_person, now + timedelta(days=8)).can_reply
    fb.is_enabled = False
    assert policy.reply_policy(fb_conv, fb, fb_person, now).reason == "channel_disabled"
    fb.is_enabled, fb.status = True, "revoked"
    assert policy.reply_policy(fb_conv, fb, fb_person, now).reason == "connection_revoked"


def test_outside_window_reply_is_refused_with_reason(shop: Tenant) -> None:
    client, _ = staff(shop)
    wa = connection(shop)
    with tenant_context(shop):
        person = Participant.objects.create(channel="whatsapp", connection=wa, external_id="8801")
        services.receive_inbound(participant=person, kind="text", body="hi", external_id="wamid.1")
        conversation = Conversation.objects.get()
        Conversation.objects.filter(pk=conversation.pk).update(
            last_inbound_at=timezone.now() - timedelta(hours=30)
        )
    response = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "late-reply-1", "body": "Sorry for the delay"},
        format="json",
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "template_required"
    detail = client.get(f"{INBOX}conversations/{conversation.pk}/")
    assert detail.data["policy"]["can_reply"] is False
    with tenant_context(shop):
        assert Message.objects.filter(direction="outbound").count() == 0


# --- Products -------------------------------------------------------------------------------------


def test_product_sharing_uses_published_catalogue_values(
    shop: Tenant, settings: SettingsWrapper
) -> None:
    client, _ = staff(shop)
    variant = product(shop, tracked=True, amount="1250.00")
    stock(shop, variant, 3)
    hidden = product(shop)
    with tenant_context(shop):
        hidden.product.status = "draft"
        hidden.product.save(update_fields=["status"])
    found = client.get(f"{INBOX}products/", {"q": variant.sku})
    assert found.status_code == 200
    row = found.data["results"][0]
    assert row["amount"] == "1250.00"
    assert row["in_stock"] is True
    assert row["available_quantity"] == "3.000"
    assert client.get(f"{INBOX}products/", {"q": hidden.sku}).data["results"] == []
    visitor = guest(shop)
    say(shop, visitor)
    conversation = conv(shop, visitor)
    shared = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "share-0001", "product_variant": str(variant.pk)},
        format="json",
    )
    assert shared.status_code == 201, shared.data
    assert shared.data["kind"] == MessageKind.PRODUCT
    assert shared.data["product"]["amount"] == "1250.00"
    assert shared.data["product"]["url"].startswith("/products/")
    refused = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "share-0002", "product_variant": str(hidden.pk)},
        format="json",
    )
    assert refused.status_code == 400


def test_external_product_links_never_use_local_addresses(
    shop: Tenant, settings: SettingsWrapper
) -> None:
    from commerce.communications import products

    variant = product(shop, amount="99.00")
    settings.MATECART_PUBLIC_BASE_URL = "http://127.0.0.1:3400"
    with tenant_context(shop):
        _, data, text = products.snapshot(variant.pk, ChannelType.WHATSAPP)
    assert data["url"] == ""
    assert "127.0.0.1" not in text
    settings.MATECART_PUBLIC_BASE_URL = "https://shop.example.com"
    with tenant_context(shop):
        _, data, text = products.snapshot(variant.pk, ChannelType.WHATSAPP)
    assert data["url"].startswith("https://shop.example.com/products/")
    assert data["url"] in text


# --- Module gating, analytics, retention ----------------------------------------------------------


def test_live_chat_module_is_available_but_off_by_default(store: Any) -> None:
    with tenant_context(store.tenant):
        state = next(s for s in modules.module_states() if s.spec.key == "live_chat")
        assert state.spec.available
        assert not state.enabled


def test_analytics_counts_real_records(shop: Tenant) -> None:
    client, _agent = staff(shop, name="Agent Smith")
    visitor = guest(shop)
    say(shop, visitor)
    conversation = conv(shop, visitor)
    client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "first-reply", "body": "Hello!"},
        format="json",
    )
    report = client.get(f"{INBOX}analytics/", {"days": 7})
    assert report.status_code == 200, report.data
    data = report.data
    assert data["new_conversations"] == 1
    assert data["messages_received"] == 1
    assert data["messages_sent"] == 1
    assert data["median_first_response_seconds"] is not None
    assert data["by_channel"] == [{"channel": "website", "conversations": 1}]
    assert data["timezone"] == "Asia/Dhaka"
    me = next(a for a in data["agents"] if a["name"] == "Agent Smith")
    assert me["replies"] == 1
    handler, _ = staff(shop, StaffRole.ORDER_HANDLER)
    assert handler.get(f"{INBOX}analytics/").data["linked_order_value"] is None


def test_retention_redacts_old_closed_conversations(
    shop: Tenant, settings: SettingsWrapper
) -> None:
    _, agent = staff(shop)
    visitor = guest(shop)
    say(shop, visitor, "my address is house 7")
    conversation = conv(shop, visitor)
    with tenant_context(shop):
        services.add_note(conversation=conversation, actor=agent, body="private")
        services.change_status(
            conversation=conversation, actor=agent, status="closed", expected_version=1
        )
        Conversation.objects.filter(pk=conversation.pk).update(
            closed_at=timezone.now() - timedelta(days=400)
        )
        result = retention.apply()
        assert result["redacted"] == 1
        conversation.refresh_from_db()
        assert conversation.redacted_at is not None
        assert Message.objects.get().body == ""
        assert ConversationNote.objects.count() == 0
        assert Conversation.objects.get().inbound_count == 1  # evidence of activity stays
        assert retention.apply()["redacted"] == 0  # idempotent


def test_signed_in_customer_chats_as_themselves(shop: Tenant) -> None:
    _, user, _ = customer(shop)
    with tenant_context(shop):
        visitor = livechat.resolve_visitor(guest_token=None, customer=user, create=True)
        again = livechat.resolve_visitor(guest_token="forged-token", customer=user)
    assert visitor.participant is not None
    assert again.participant is not None
    assert visitor.participant.pk == again.participant.pk
    assert visitor.participant.customer_id == user.pk
