"""Phase 14 WebSockets: real handshakes through the ASGI application (Channels test
communicator), Origin checks, cookie authentication, durable acknowledgements, recovery after
reconnecting, audience separation, limits, revocation and Redis fan-out between workers."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from pytest_django.fixtures import Settings

from commerce.communications import consumers, livechat, services
from commerce.communications.models import Conversation, Message, Participant
from commerce.identity import sessions
from commerce.identity.models import AuthSession
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from config.asgi import application

from .comms_helpers import enable_live_chat, staff
from .factories import make_store_settings
from .ws_client import WebsocketCommunicator

pytestmark = pytest.mark.django_db(transaction=True)
ORIGIN = "http://testserver"


def run(scenario: Callable[[], Awaitable[None]]) -> None:
    async_to_sync(scenario)()


def socket(
    path: str, cookies: dict[str, str], origin: str | None = ORIGIN
) -> WebsocketCommunicator:
    headers = [(b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return WebsocketCommunicator(application, path, headers=headers)


async def receive(ws: WebsocketCommunicator, kind: str, timeout: float = 3) -> dict[str, Any]:
    """The next event of ``kind`` (other events, like presence or typing, are skipped)."""
    while True:
        data: dict[str, Any] = json.loads(await ws.receive_from(timeout=timeout))
        if data.get("type") == kind:
            return data


@pytest.fixture
def shop(deployment_tenant: Tenant) -> Tenant:
    make_store_settings(deployment_tenant)
    enable_live_chat(deployment_tenant)
    return deployment_tenant


def new_guest(tenant: Tenant) -> tuple[Participant, str]:
    with tenant_context(tenant):
        visitor = livechat.resolve_visitor(guest_token=None, customer=None, create=True)
    assert visitor.participant is not None
    assert visitor.new_token
    return visitor.participant, visitor.new_token


def staff_cookie(tenant: Tenant, role: str = StaffRole.OWNER) -> tuple[dict[str, str], Any]:
    client, user = staff(tenant, role)
    return {"mc_staff": client.cookies["mc_staff"].value}, user


# --- Handshake ------------------------------------------------------------------------------------


def test_handshake_requires_a_store_origin(shop: Tenant) -> None:
    _, token = new_guest(shop)

    async def scenario() -> None:
        for origin in (None, "https://evil.example", "http://testserver.evil.example"):
            ws = socket("/api/chat/ws/", {"mc_chat": token}, origin)
            connected, code = await ws.connect()
            assert not connected
            assert code == consumers.CLOSE_FORBIDDEN, origin

    run(scenario)


def test_visitor_socket_needs_a_valid_credential_and_live_chat(shop: Tenant) -> None:
    _, token = new_guest(shop)

    async def scenario() -> None:
        for cookies in ({}, {"mc_chat": "forged-token"}, {"mc_staff": "x"}):
            ws = socket("/api/chat/ws/", cookies)
            connected, code = await ws.connect()
            assert not connected
            assert code == consumers.CLOSE_UNAUTHORIZED
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        connected, _ = await ws.connect()
        assert connected
        await ws.disconnect()

    run(scenario)
    enable_live_chat(shop, on=False)

    async def disabled() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        connected, _ = await ws.connect()
        assert not connected

    run(disabled)


def test_staff_socket_needs_messages_permission(shop: Tenant) -> None:
    editor, _ = staff_cookie(shop, StaffRole.CONTENT_EDITOR)
    handler, _ = staff_cookie(shop, StaffRole.ORDER_HANDLER)
    _, guest_token = new_guest(shop)

    async def scenario() -> None:
        for cookies in (editor, {"mc_chat": guest_token}, {}):
            ws = socket("/api/manage/ws/inbox/", cookies)
            connected, _ = await ws.connect()
            assert not connected
        ws = socket("/api/manage/ws/inbox/", handler)
        connected, _ = await ws.connect()
        assert connected
        assert (await receive(ws, "ready"))["type"] == "ready"
        await ws.disconnect()

    run(scenario)


# --- Messaging ------------------------------------------------------------------------------------


def test_visitor_message_is_acknowledged_after_commit_and_reaches_staff(shop: Tenant) -> None:
    participant, token = new_guest(shop)
    with tenant_context(shop):
        services.visitor_send(participant=participant, body="first", client_key="first-key-1")
        conversation = Conversation.objects.get()
    agent, _ = staff_cookie(shop)

    async def scenario() -> None:
        inbox = socket("/api/manage/ws/inbox/", agent)
        assert (await inbox.connect())[0]
        await receive(inbox, "ready")
        await inbox.send_json_to({"type": "watch", "conversation": str(conversation.pk)})
        await receive(inbox, "watching")
        visitor = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await visitor.connect())[0]
        ready = await receive(visitor, "ready")
        assert ready["online"] is True  # a staff member has the inbox open right now
        await visitor.send_json_to({"type": "message", "client_key": "ws-key-0001", "body": "Hi!"})
        ack = await receive(visitor, "ack")
        assert ack["client_key"] == "ws-key-0001"
        assert ack["message"]["seq"] == 2
        # The acknowledged message is already committed.
        stored = await asyncio.to_thread(_stored_bodies, shop)
        assert stored == ["first", "Hi!"]
        event = await receive(inbox, "message")
        assert event["message"]["body"] == "Hi!"
        assert event["message"]["direction"] == "inbound"
        listing = await receive(inbox, "conversation")
        assert listing["conversation"]["id"] == str(conversation.pk)
        # Retrying the same frame does not store a second message.
        await visitor.send_json_to({"type": "message", "client_key": "ws-key-0001", "body": "Hi!"})
        again = await receive(visitor, "ack")
        assert again["message"]["id"] == ack["message"]["id"]
        await visitor.disconnect()
        await inbox.disconnect()

    run(scenario)


def _stored_bodies(tenant: Tenant) -> list[str]:
    from django.db import connections

    try:
        with tenant_context(tenant):
            return list(Message.objects.order_by("seq").values_list("body", flat=True))
    finally:
        connections.close_all()


def test_reconnecting_visitor_recovers_missed_replies(shop: Tenant) -> None:
    participant, token = new_guest(shop)
    _, agent = staff(shop)
    with tenant_context(shop):
        services.visitor_send(participant=participant, body="Hello", client_key="hello-key-1")
        conversation = Conversation.objects.get()
        for index in range(3):
            services.staff_send(
                conversation=conversation,
                actor=agent,
                outbound=services.Outbound(body=f"reply {index}"),
                client_key=f"reply-key-{index}",
            )

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await ws.send_json_to({"type": "sync", "after_seq": 1})
        sync = await receive(ws, "sync")
        assert [m["body"] for m in sync["messages"]] == ["reply 0", "reply 1", "reply 2"]
        assert all("delivery_status" not in m for m in sync["messages"])  # visitor shape
        assert sync["messages"][0]["sender_name"]
        await ws.send_json_to({"type": "read", "seq": 4})
        await ws.disconnect()

    run(scenario)
    with tenant_context(shop):
        conversation.refresh_from_db()
        assert conversation.visitor_read_seq == 4
        assert set(
            Message.objects.filter(direction="outbound").values_list("delivery_status", flat=True)
        ) == {"read"}


def test_visitors_never_see_each_other_or_internal_notes(shop: Tenant) -> None:
    alice, alice_token = new_guest(shop)
    bob, bob_token = new_guest(shop)
    _, agent = staff(shop)
    with tenant_context(shop):
        services.visitor_send(participant=alice, body="alice", client_key="alice-key-1")
        services.visitor_send(participant=bob, body="bob", client_key="bob-key-001")
        alice_conv = Conversation.objects.get(participant=alice)

    async def scenario() -> None:
        bob_ws = socket("/api/chat/ws/", {"mc_chat": bob_token})
        alice_ws = socket("/api/chat/ws/", {"mc_chat": alice_token})
        assert (await bob_ws.connect())[0]
        assert (await alice_ws.connect())[0]
        await receive(bob_ws, "ready")
        await receive(alice_ws, "ready")

        def act() -> None:
            from django.db import connections

            try:
                with tenant_context(shop):
                    services.add_note(conversation=alice_conv, actor=agent, body="VIP note")
                    services.staff_send(
                        conversation=alice_conv,
                        actor=agent,
                        outbound=services.Outbound(body="for alice"),
                        client_key="for-alice-01",
                    )
            finally:
                connections.close_all()

        await asyncio.to_thread(act)
        got = await receive(alice_ws, "message")
        assert got["message"]["body"] == "for alice"
        assert await bob_ws.receive_nothing(timeout=0.5)
        assert await alice_ws.receive_nothing(timeout=0.3)  # the note never arrives
        await bob_ws.send_json_to({"type": "sync", "after_seq": 0})
        sync = await receive(bob_ws, "sync")
        assert [m["body"] for m in sync["messages"]] == ["bob"]
        await bob_ws.disconnect()
        await alice_ws.disconnect()

    run(scenario)


def test_staff_cannot_watch_unknown_or_foreign_conversations(
    shop: Tenant, tenant_b: Tenant
) -> None:
    make_store_settings(tenant_b)
    enable_live_chat(tenant_b)
    with tenant_context(tenant_b):
        other = livechat.resolve_visitor(guest_token=None, customer=None, create=True).participant
        assert other is not None
        services.visitor_send(participant=other, body="b", client_key="b-key-0001")
        foreign = Conversation.objects.get()
    agent, _ = staff_cookie(shop)

    async def scenario() -> None:
        ws = socket("/api/manage/ws/inbox/", agent)
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        for target in (str(foreign.pk), str(uuid.uuid4()), "not-a-uuid"):
            await ws.send_json_to({"type": "watch", "conversation": target})
            error = await receive(ws, "error")
            assert error["code"] == "not_found"
        await ws.disconnect()

    run(scenario)


def test_typing_is_relayed_between_visitor_and_agent(shop: Tenant) -> None:
    participant, token = new_guest(shop)
    with tenant_context(shop):
        services.visitor_send(participant=participant, body="Hi", client_key="typing-key-1")
        conversation = Conversation.objects.get()
    agent, _ = staff_cookie(shop)

    async def scenario() -> None:
        inbox = socket("/api/manage/ws/inbox/", agent)
        visitor = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await inbox.connect())[0]
        assert (await visitor.connect())[0]
        await receive(inbox, "ready")
        await receive(visitor, "ready")
        await inbox.send_json_to({"type": "watch", "conversation": str(conversation.pk)})
        await receive(inbox, "watching")
        await visitor.send_json_to({"type": "typing", "on": True})
        assert (await receive(inbox, "typing"))["who"] == "visitor"
        await inbox.send_json_to({"type": "typing", "on": True})
        typing = await receive(visitor, "typing")
        assert typing["who"] == "agent"
        assert "user" not in typing
        await inbox.disconnect()
        await visitor.disconnect()

    run(scenario)


# --- Limits and revocation ------------------------------------------------------------------------


def test_oversized_frames_and_floods_close_the_socket(shop: Tenant) -> None:
    _, token = new_guest(shop)

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await ws.send_to(text_data="x" * (consumers.MAX_FRAME + 1))
        closed = await ws.receive_output(timeout=2)
        assert closed == {"type": "websocket.close", "code": consumers.CLOSE_TOO_BIG}
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        for _ in range(consumers.FRAMES_PER_WINDOW + 1):
            await ws.send_json_to({"type": "ping"})
        while True:
            output = await ws.receive_output(timeout=2)
            if output["type"] == "websocket.close":
                assert output["code"] == consumers.CLOSE_RATE
                break

    run(scenario)


def test_invalid_visitor_messages_are_never_acknowledged(shop: Tenant) -> None:
    _, token = new_guest(shop)

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await ws.send_json_to({"type": "message", "client_key": "empty-key-1", "body": "   "})
        nack = await receive(ws, "nack")
        assert nack["client_key"] == "empty-key-1"
        await ws.send_to(text_data="{not json")
        assert (await receive(ws, "error"))["code"] == "invalid_json"
        await ws.disconnect()

    run(scenario)
    with tenant_context(shop):
        assert Message.objects.count() == 0


def test_revoked_staff_session_closes_the_socket(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(consumers, "WATCH_INTERVAL", 0.05)
    monkeypatch.setattr(consumers, "RECHECK_EVERY", 0.0)
    cookies, user = staff_cookie(shop)

    async def scenario() -> None:
        ws = socket("/api/manage/ws/inbox/", cookies)
        assert (await ws.connect())[0]
        await receive(ws, "ready")

        def revoke() -> None:
            from django.db import connections

            try:
                with tenant_context(shop):
                    sessions.revoke_all(user)
                    assert not AuthSession.objects.filter(
                        user=user, revoked_at__isnull=True
                    ).exists()
            finally:
                connections.close_all()

        await asyncio.to_thread(revoke)
        while True:
            output = await ws.receive_output(timeout=3)
            if output["type"] == "websocket.close":
                assert output["code"] == consumers.CLOSE_UNAUTHORIZED
                break

    run(scenario)


def test_switching_live_chat_off_closes_visitor_sockets(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(consumers, "WATCH_INTERVAL", 0.05)
    monkeypatch.setattr(consumers, "RECHECK_EVERY", 0.0)
    _, token = new_guest(shop)

    async def scenario() -> None:
        ws = socket("/api/chat/ws/", {"mc_chat": token})
        assert (await ws.connect())[0]
        await receive(ws, "ready")
        await asyncio.to_thread(enable_live_chat, shop, False)
        while True:
            output = await ws.receive_output(timeout=3)
            if output["type"] == "websocket.close":
                break

    run(scenario)


def test_redis_channel_layer_fans_out_between_workers(settings: Settings) -> None:
    """Two layer instances on the same Redis behave like two ASGI workers."""
    from channels_redis.core import RedisChannelLayer

    base = os.environ.get("REDIS_URL", "")
    if not base:
        pytest.skip("No Redis configured")
    url = base.rsplit("/", 1)[0] + "/5"

    async def scenario() -> None:
        worker_a = RedisChannelLayer(hosts=[url], prefix=f"t-{uuid.uuid4().hex[:6]}")
        worker_b = RedisChannelLayer(hosts=[url], prefix=worker_a.prefix)
        channel = await worker_a.new_channel()
        await worker_a.group_add("tenant.inbox", channel)
        await worker_b.group_send("tenant.inbox", {"type": "relay", "event": {"type": "message"}})
        received = await asyncio.wait_for(worker_a.receive(channel), 3)
        assert received["event"] == {"type": "message"}
        await worker_a.flush()
        await worker_a.close_pools()
        await worker_b.close_pools()

    run(scenario)
