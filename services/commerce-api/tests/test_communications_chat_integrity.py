"""Phase 14.1 website chat integrity: every message sequence belongs to a named conversation.

A visitor can have several conversations over time (resolved reopens, closed starts a new one),
and each numbers its messages from 1. Synchronisation, read and delivery receipts and HTTP
history therefore name the conversation they refer to, and the server answers with the
visitor's current conversation, so an old sequence can never skip, mix or mark messages of a
newer one."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient

from commerce.communications import services
from commerce.communications.models import (
    Conversation,
    ConversationStatus,
    DeliveryStatus,
    Message,
    Participant,
)
from commerce.identity.models import User
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .comms_helpers import enable_live_chat, staff
from .factories import make_store_settings
from .test_communications_ws import new_guest, receive, run, socket

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def shop(deployment_tenant: Tenant) -> Tenant:
    make_store_settings(deployment_tenant)
    enable_live_chat(deployment_tenant)
    return deployment_tenant


def visitor_says(tenant: Tenant, participant: Participant, body: str, key: str) -> Message:
    with tenant_context(tenant):
        message, _ = services.visitor_send(participant=participant, body=body, client_key=key)
        return message


def staff_says(tenant: Tenant, agent: User, conversation: Conversation, body: str) -> Message:
    with tenant_context(tenant):
        message, _ = services.staff_send(
            conversation=conversation,
            actor=agent,
            outbound=services.Outbound(body=body),
            client_key=f"staff-{uuid.uuid4().hex}",
        )
        return message


def set_status(tenant: Tenant, agent: User, conversation: Conversation, status: str) -> None:
    with tenant_context(tenant):
        current = Conversation.objects.get(pk=conversation.pk)
        services.change_status(
            conversation=current, actor=agent, status=status, expected_version=current.version
        )


def lifecycle(tenant: Tenant) -> tuple[Participant, str, User, Conversation, Conversation]:
    """Open, reply, resolve, reopen by a visitor reply, close, then a new conversation."""
    participant, token = new_guest(tenant)
    _, agent = staff(tenant)
    first = visitor_says(tenant, participant, "Is the kurti in stock?", "c1-visitor-1")
    with tenant_context(tenant):
        old = Conversation.objects.get(pk=first.conversation_id)
    staff_says(tenant, agent, old, "Yes, size M is in stock.")
    set_status(tenant, agent, old, ConversationStatus.RESOLVED)
    again = visitor_says(tenant, participant, "Great, and delivery?", "c1-visitor-2")
    assert again.conversation_id == old.pk  # resolved reopens the same conversation
    with tenant_context(tenant):
        assert Conversation.objects.get(pk=old.pk).status == ConversationStatus.OPEN
    staff_says(tenant, agent, old, "Two days to Sylhet.")
    set_status(tenant, agent, old, ConversationStatus.CLOSED)
    fresh = visitor_says(tenant, participant, "A new question", "c2-visitor-1")
    assert fresh.conversation_id != old.pk  # closed stays closed; a new one starts
    assert fresh.seq == 1  # with its own sequence
    with tenant_context(tenant):
        new = Conversation.objects.get(pk=fresh.conversation_id)
        old.refresh_from_db()
    assert old.status == ConversationStatus.CLOSED
    assert old.last_seq == 4
    return participant, token, agent, old, new


def bodies(messages: list[dict[str, Any]]) -> list[str]:
    return [m["body"] for m in messages]


def test_sync_with_an_old_conversation_returns_the_new_one_from_the_start(shop: Tenant) -> None:
    _participant, token, agent, old, new = lifecycle(shop)
    staff_says(shop, agent, new, "Happy to help again.")

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        # The widget last saw seq 4 of the closed conversation and reconnects.
        await ws.send_json_to({"type": "sync", "conversation": str(old.pk), "after_seq": 4})
        sync = await receive(ws, "sync")
        assert sync["conversation"] == str(new.pk)
        assert sync["status"] == "open"
        assert bodies(sync["messages"]) == ["A new question", "Happy to help again."]
        assert {m["conversation"] for m in sync["messages"]} == {str(new.pk)}
        # Naming the current conversation is incremental, as before.
        await ws.send_json_to({"type": "sync", "conversation": str(new.pk), "after_seq": 1})
        sync = await receive(ws, "sync")
        assert bodies(sync["messages"]) == ["Happy to help again."]
        await ws.disconnect()

    run(scenario)


def test_receipts_for_an_old_conversation_never_touch_the_new_one(shop: Tenant) -> None:
    _participant, token, agent, old, new = lifecycle(shop)
    reply = staff_says(shop, agent, new, "Happy to help again.")
    assert reply.seq == 2

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        # A late receipt from the old thread (it showed seq 4 there).
        await ws.send_json_to({"type": "delivered", "conversation": str(old.pk), "seq": 4})
        await ws.send_json_to({"type": "read", "conversation": str(old.pk), "seq": 4})
        await ws.send_json_to({"type": "sync", "conversation": str(new.pk), "after_seq": 2})
        await receive(ws, "sync")  # frames are handled in order
        await ws.disconnect()

    run(scenario)
    with tenant_context(shop):
        new.refresh_from_db()
        assert new.visitor_read_seq == 0
        assert Message.objects.get(pk=reply.pk).delivery_status == DeliveryStatus.ACCEPTED
        old.refresh_from_db()
        assert old.visitor_read_seq == 4  # the receipt applied where it belonged

    async def current() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await ws.send_json_to({"type": "delivered", "conversation": str(new.pk), "seq": 2})
        await ws.send_json_to({"type": "read", "conversation": str(new.pk), "seq": 2})
        await ws.send_json_to({"type": "sync", "conversation": str(new.pk), "after_seq": 2})
        await receive(ws, "sync")
        await ws.disconnect()

    run(current)
    with tenant_context(shop):
        new.refresh_from_db()
        assert new.visitor_read_seq == 2
        assert Message.objects.get(pk=reply.pk).delivery_status == DeliveryStatus.READ


def test_live_events_name_their_conversation(shop: Tenant) -> None:
    _participant, token, _agent, _old, new = lifecycle(shop)

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await ws.send_json_to({"type": "message", "client_key": "c2-visitor-2", "body": "More"})
        ack = await receive(ws, "ack")
        assert ack["message"]["conversation"] == str(new.pk)
        assert ack["message"]["seq"] == 2
        await ws.disconnect()

    run(scenario)
    with tenant_context(shop):
        payload = services_payload(Message.objects.get(conversation=new, seq=2))
    assert payload["conversation"] == str(new.pk)
    assert "delivery_status" not in payload


def services_payload(message: Message) -> dict[str, Any]:
    from commerce.communications.serializers import message_payload

    return message_payload(message, staff=False)


def test_status_event_to_the_visitor_names_the_conversation(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from commerce.communications import realtime

    published: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(realtime, "publish", lambda group, event: published.append((group, event)))
    participant, _token = new_guest(shop)
    _, agent = staff(shop)
    message = visitor_says(shop, participant, "Hello", "status-visitor-1")
    with tenant_context(shop):
        conversation = Conversation.objects.get(pk=message.conversation_id)
        group = realtime.visitor_group(participant.pk)
    set_status(shop, agent, conversation, ConversationStatus.CLOSED)
    events = [event for name, event in published if name == group and event["type"] != "message"]
    assert events == [
        {"type": "conversation", "conversation": str(conversation.pk), "status": "closed"}
    ]


def test_http_history_names_the_current_conversation(shop: Tenant) -> None:
    _participant, token, agent, old, new = lifecycle(shop)
    client = APIClient()
    client.cookies["mc_chat"] = token
    state = client.get("/api/chat/config/")
    assert state.status_code == 200
    assert state.json()["conversation_id"] == str(new.pk)
    assert state.data["conversation_status"] == "open"
    assert bodies(state.data["messages"]) == ["A new question"]
    stale = client.get(f"/api/chat/config/?conversation={old.pk}&after_seq=4")
    assert stale.json()["conversation_id"] == str(new.pk)
    assert bodies(stale.data["messages"]) == ["A new question"]  # not skipped
    staff_says(shop, agent, new, "Happy to help again.")
    current = client.get(f"/api/chat/config/?conversation={new.pk}&after_seq=1")
    assert bodies(current.data["messages"]) == ["Happy to help again."]
    sent = client.post(
        "/api/chat/messages/", {"client_key": "http-key-0001", "body": "Via HTTP"}, format="json"
    )
    assert sent.status_code == 201
    assert sent.data["conversation"] == str(new.pk)
    assert sent.data["seq"] == 3
    # A refresh restores the current conversation only (the closed one stays in the inbox).
    refreshed = client.get("/api/chat/config/")
    assert bodies(refreshed.data["messages"]) == [
        "A new question",
        "Happy to help again.",
        "Via HTTP",
    ]


def test_a_reopened_older_conversation_is_the_one_the_visitor_sees(shop: Tenant) -> None:
    """Staff may reopen the visitor's earlier closed conversation when no newer one is active.
    The visitor's next message goes there, so history and sync must show it too."""
    participant, token, agent, old, new = lifecycle(shop)
    set_status(shop, agent, new, ConversationStatus.CLOSED)
    set_status(shop, agent, old, ConversationStatus.OPEN)
    next_message = visitor_says(shop, participant, "Back to the kurti", "c1-visitor-3")
    assert next_message.conversation_id == old.pk
    client = APIClient()
    client.cookies["mc_chat"] = token
    state = client.get("/api/chat/config/")
    assert state.json()["conversation_id"] == str(old.pk)
    assert bodies(state.data["messages"])[-1] == "Back to the kurti"

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await ws.send_json_to({"type": "sync", "conversation": str(new.pk), "after_seq": 1})
        sync = await receive(ws, "sync")
        assert sync["conversation"] == str(old.pk)
        assert bodies(sync["messages"])[-1] == "Back to the kurti"
        await ws.disconnect()

    run(scenario)


def test_naming_another_visitors_conversation_reveals_nothing(shop: Tenant) -> None:
    alice, alice_token = new_guest(shop)
    bob, _bob_token = new_guest(shop)
    _, agent = staff(shop)
    visitor_says(shop, alice, "alice", "alice-key-1")
    bob_message = visitor_says(shop, bob, "bob secret", "bob-key-1")
    with tenant_context(shop):
        bob_conversation = Conversation.objects.get(pk=bob_message.conversation_id)
    reply = staff_says(shop, agent, bob_conversation, "reply to bob")

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": alice_token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await ws.send_json_to(
            {"type": "sync", "conversation": str(bob_conversation.pk), "after_seq": 0}
        )
        sync = await receive(ws, "sync")
        assert bodies(sync["messages"]) == ["alice"]
        await ws.send_json_to({"type": "read", "conversation": str(bob_conversation.pk), "seq": 2})
        await ws.send_json_to(
            {"type": "delivered", "conversation": str(bob_conversation.pk), "seq": 2}
        )
        await ws.send_json_to({"type": "sync", "after_seq": 1})
        await receive(ws, "sync")
        await ws.disconnect()

    run(scenario)
    client = APIClient()
    client.cookies["mc_chat"] = alice_token
    state = client.get(f"/api/chat/config/?conversation={bob_conversation.pk}&after_seq=0")
    assert bodies(state.data["messages"]) == ["alice"]
    with tenant_context(shop):
        bob_conversation.refresh_from_db()
        assert bob_conversation.visitor_read_seq == 0
        assert Message.objects.get(pk=reply.pk).delivery_status == DeliveryStatus.ACCEPTED
