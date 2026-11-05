"""WebSocket consumers (D-092, D-093).

``/api/chat/ws/`` (visitor) and ``/api/manage/ws/inbox/`` (staff). Both:

- accept only browser origins of this store (exact scheme, host and port; no Origin = refused),
  which stops cross-site WebSocket hijacking with the visitor's cookies;
- authenticate during the handshake from HttpOnly cookies (``mc_chat`` or ``mc_customer`` for
  visitors, ``mc_staff`` plus an active membership with ``messages.manage`` for staff); nothing
  sensitive is ever read from the query string;
- run every database access inside this deployment's tenant context;
- bound frame size, frame rate and concurrent sockets per identity, close idle sockets, and
  re-check the session and permissions at least every 30 seconds (a revoked session, removed
  membership or switched-off module closes the socket);
- choose subscriptions on the server: a visitor only ever joins their own participant group,
  and a staff socket joins a conversation only after the server found it in this store.

Acknowledgements are sent only after the message is committed (the service's transaction has
closed), so an acknowledged message is durable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.core.cache import cache
from django.http.cookie import parse_cookie

from commerce.core import ratelimit
from commerce.tenancy.context import tenant_context
from commerce.tenancy.keys import tenant_cache_key
from commerce.tenancy.resolution import configured_tenant_id

from . import livechat, realtime, services

logger = logging.getLogger(__name__)
T = TypeVar("T")

MAX_FRAME = 16 * 1024
FRAMES_PER_WINDOW = 40
FRAME_WINDOW = 10.0
IDLE_TIMEOUT = 90.0
RECHECK_EVERY = 30.0
WATCH_INTERVAL = 10.0
TYPING_INTERVAL = 3.0
MAX_VISITOR_SOCKETS = 8
MAX_STAFF_SOCKETS = 12
# Per client address: generous, because many real customers share one carrier-grade NAT address;
# per-identity socket caps and frame limits bound each visitor separately.
CONNECTS_PER_MINUTE = 120

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_IDLE = 4408
CLOSE_RATE = 4429
CLOSE_TOO_BIG = 1009


def allowed_origins() -> set[str]:
    origins = {str(settings.MATECART_PUBLIC_BASE_URL).rstrip("/")}
    origins.update(o.rstrip("/") for o in settings.MATECART_WEBSOCKET_ORIGINS)
    origins.update(o.rstrip("/") for o in settings.CSRF_TRUSTED_ORIGINS)
    return {o for o in origins if o}


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _client_ip(scope: dict[str, Any], headers: dict[str, str]) -> str:
    hops = int(settings.MATECART_TRUSTED_PROXY_COUNT)
    forwarded = [
        part.strip() for part in headers.get("x-forwarded-for", "").split(",") if part.strip()
    ]
    if hops and len(forwarded) >= hops:
        return forwarded[-hops]
    client = scope.get("client") or ("", 0)
    return str(client[0])


class _Base(AsyncWebsocketConsumer):
    kind = "base"
    max_sockets = MAX_VISITOR_SOCKETS

    tenant_id: uuid.UUID | None = None
    identity = ""
    groups_joined: set[str]

    async def db(self, fn: Callable[[], T]) -> T:
        tenant_id = self.tenant_id
        if tenant_id is None:
            raise RuntimeError("WebSocket used before its tenant was resolved.")

        def run() -> T:
            with tenant_context(tenant_id):
                return fn()

        result: T = await database_sync_to_async(run)()
        return result

    async def connect(self) -> None:
        self.groups_joined = set()
        self.frames: list[float] = []
        self.last_seen = time.monotonic()
        self.last_check = time.monotonic()
        self.watchdog: asyncio.Task[None] | None = None
        headers = _headers(self.scope)
        origin = headers.get("origin", "").rstrip("/")
        if not origin or origin not in allowed_origins():
            await self.refuse(CLOSE_FORBIDDEN, "origin")
            return
        self.tenant_id = configured_tenant_id()
        if self.tenant_id is None:
            await self.refuse(CLOSE_FORBIDDEN, "tenant")
            return
        self.cookies = parse_cookie(headers.get("cookie", ""))
        ip = _client_ip(self.scope, headers)
        try:
            await self.db(
                lambda: ratelimit.consume(
                    f"ws-connect-{self.kind}", ip, limit=CONNECTS_PER_MINUTE, window=60
                )
            )
        except ratelimit.RateLimited:
            await self.refuse(CLOSE_RATE, "connect_rate")
            return
        try:
            ok = await self.authenticate()
        except Exception:
            logger.exception("WebSocket authentication failed (%s)", self.kind)
            ok = False
        if not ok:
            await self.refuse(CLOSE_UNAUTHORIZED, "credential")
            return
        if not await self.db(lambda: self._register(True)):
            await self.refuse(CLOSE_RATE, "socket_cap")
            return
        await self.accept()
        await self.joined()
        self.watchdog = asyncio.ensure_future(self._watch())

    # Concurrent socket cap per identity (stale entries expire after the idle timeout).
    def _register(self, on: bool) -> bool:
        key = tenant_cache_key("ws", self.kind, self.identity)
        now = time.time()
        try:
            sockets: dict[str, float] = cache.get(key) or {}
            sockets = {k: v for k, v in sockets.items() if now - v < IDLE_TIMEOUT}
            if on:
                if self.channel_name not in sockets and len(sockets) >= self.max_sockets:
                    return False
                sockets[self.channel_name] = now
            else:
                sockets.pop(self.channel_name, None)
            cache.set(key, sockets, int(IDLE_TIMEOUT))
        except Exception:
            logger.warning("WebSocket registry unavailable")
        return True

    async def refuse(self, code: int, reason: str) -> None:
        """Close with ``code``. Logged without identifiers or message content."""
        logger.info("WebSocket closed by server (%s, %s, code=%s)", self.kind, reason, code)
        await self.close(code=code)

    async def join(self, group: str) -> None:
        await self.channel_layer.group_add(group, self.channel_name)
        self.groups_joined.add(group)

    async def leave(self, group: str) -> None:
        await self.channel_layer.group_discard(group, self.channel_name)
        self.groups_joined.discard(group)

    async def disconnect(self, code: int) -> None:
        if self.watchdog is not None:
            self.watchdog.cancel()
        for group in list(getattr(self, "groups_joined", set())):
            await self.leave(group)
        if self.tenant_id is not None and self.identity:
            await self.db(lambda: self._register(False))
            await self.left()

    async def _watch(self) -> None:
        try:
            while True:
                await asyncio.sleep(WATCH_INTERVAL)
                now = time.monotonic()
                if now - self.last_seen > IDLE_TIMEOUT:
                    await self.refuse(CLOSE_IDLE, "idle")
                    return
                if now - self.last_check > RECHECK_EVERY:
                    self.last_check = now
                    if not await self.still_allowed():
                        await self.refuse(CLOSE_UNAUTHORIZED, "revoked")
                        return
                    await self.db(lambda: self._register(True))
        except asyncio.CancelledError:
            return

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        if bytes_data is not None or text_data is None:
            await self.refuse(CLOSE_FORBIDDEN, "binary_frame")
            return
        if len(text_data.encode()) > MAX_FRAME:
            await self.refuse(CLOSE_TOO_BIG, "frame_size")
            return
        now = time.monotonic()
        self.frames = [t for t in self.frames if now - t < FRAME_WINDOW]
        self.frames.append(now)
        if len(self.frames) > FRAMES_PER_WINDOW:
            await self.refuse(CLOSE_RATE, "frame_rate")
            return
        self.last_seen = now
        try:
            data = json.loads(text_data)
        except ValueError:
            await self.send_json({"type": "error", "code": "invalid_json"})
            return
        if not isinstance(data, dict) or not isinstance(data.get("type"), str):
            await self.send_json({"type": "error", "code": "invalid_frame"})
            return
        if data["type"] == "ping":
            await self.send_json({"type": "pong"})
            await self.heartbeat()
            return
        await self.handle(data)

    async def send_json(self, data: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(data))

    async def relay(self, event: dict[str, Any]) -> None:
        payload = event.get("event")
        if isinstance(payload, dict) and self.should_relay(payload):
            await self.send_json(payload)

    # Hooks.
    async def authenticate(self) -> bool:
        raise NotImplementedError

    async def joined(self) -> None:
        return None

    async def left(self) -> None:
        return None

    async def still_allowed(self) -> bool:
        return await self.authenticate()

    async def heartbeat(self) -> None:
        return None

    async def handle(self, data: dict[str, Any]) -> None:
        await self.send_json({"type": "error", "code": "unknown_type"})

    def should_relay(self, payload: dict[str, Any]) -> bool:
        return True


class ChatConsumer(_Base):
    """A website visitor's (or signed-in customer's) own conversation."""

    kind = "visitor"
    max_sockets = MAX_VISITOR_SOCKETS
    participant_id: uuid.UUID | None = None
    last_typing = 0.0

    def _resolve(self) -> livechat.Visitor | None:
        from commerce.identity import sessions
        from commerce.identity.models import AccountType

        if not livechat.enabled():
            return None
        customer = None
        token = self.cookies.get(sessions.cookie_spec(AccountType.CUSTOMER)["name"])
        if token:
            session = sessions.resolve(token, AccountType.CUSTOMER)
            customer = session.user if session else None
        visitor = livechat.resolve_visitor(
            guest_token=self.cookies.get(livechat.COOKIE_NAME), customer=customer
        )
        return visitor if visitor.participant is not None else None

    async def authenticate(self) -> bool:
        visitor = await self.db(self._resolve)
        if visitor is None or visitor.participant is None:
            return False
        if self.participant_id is not None and visitor.participant.pk != self.participant_id:
            return False  # the credential now names someone else (sign-out, other account)
        self.participant_id = visitor.participant.pk
        self.identity = visitor.participant.pk.hex
        return True

    async def joined(self) -> None:
        participant_id = self.participant_id
        if participant_id is None:
            return
        group = await self.db(lambda: realtime.visitor_group(participant_id))
        await self.join(group)
        online = await self.db(lambda: livechat.availability().online)
        await self.send_json({"type": "ready", "online": online})

    async def handle(self, data: dict[str, Any]) -> None:
        participant_id = self.participant_id
        if participant_id is None:
            return
        kind = data["type"]
        if kind == "message":
            await self._message(participant_id, data)
        elif kind == "sync":
            await self.send_json(
                await self.db(lambda: _visitor_sync(participant_id, data.get("after_seq")))
            )
        elif kind == "read":
            await self.db(lambda: _visitor_read(participant_id, data.get("seq")))
        elif kind == "delivered":
            await self.db(lambda: _visitor_delivered(participant_id, data.get("seq")))
        elif kind == "typing":
            now = time.monotonic()
            if now - self.last_typing >= TYPING_INTERVAL or not data.get("on"):
                self.last_typing = now
                await self.db(lambda: _visitor_typing(participant_id, bool(data.get("on"))))
        else:
            await self.send_json({"type": "error", "code": "unknown_type"})

    async def _message(self, participant_id: uuid.UUID, data: dict[str, Any]) -> None:
        client_key = str(data.get("client_key") or "")[:64]

        def send() -> dict[str, Any]:
            from .models import Participant
            from .serializers import message_payload

            participant = Participant.objects.get(pk=participant_id)
            message, _ = services.visitor_send(
                participant=participant, body=data.get("body"), client_key=client_key
            )
            return message_payload(message, staff=False)

        try:
            message = await self.db(send)
        except ratelimit.RateLimited:
            await self.send_json({"type": "nack", "client_key": client_key, "code": "rate_limited"})
            return
        except Exception as exc:  # validation or a database failure: never acknowledged
            code = getattr(exc, "default_code", "") or "not_stored"
            if code == "not_stored":
                logger.exception("Visitor message not stored")
            await self.send_json({"type": "nack", "client_key": client_key, "code": str(code)})
            return
        await self.send_json({"type": "ack", "client_key": client_key, "message": message})

    def should_relay(self, payload: dict[str, Any]) -> bool:
        # Visitor groups only ever receive customer-visible events; belt and braces.
        return payload.get("type") in ("message", "conversation", "typing", "presence")


def _visitor_sync(participant_id: uuid.UUID, after: Any) -> dict[str, Any]:
    from .models import Message, Participant
    from .serializers import VisitorMessageSerializer, _plain

    participant = Participant.objects.get(pk=participant_id)
    conversation = services.latest_conversation(participant)
    try:
        after_seq = max(int(after), 0)
    except (TypeError, ValueError):
        after_seq = 0
    rows: list[Message] = []
    if conversation is not None:
        rows = list(
            Message.objects.filter(conversation=conversation, seq__gt=after_seq)
            .select_related("sender_user", "attachment")
            .order_by("seq")[:200]
        )
    return {
        "type": "sync",
        "status": conversation.status if conversation else None,
        "messages": _plain(
            VisitorMessageSerializer(rows, many=True, context={"staff": False}).data
        ),
        "online": livechat.availability().online,
    }


def _visitor_read(participant_id: uuid.UUID, seq: Any) -> None:
    from .models import Participant

    services.visitor_mark_read(participant=Participant.objects.get(pk=participant_id), seq=seq)


def _visitor_delivered(participant_id: uuid.UUID, seq: Any) -> None:
    from .models import Participant

    services.visitor_delivered(participant=Participant.objects.get(pk=participant_id), seq=seq)


def _visitor_typing(participant_id: uuid.UUID, on: bool) -> None:
    from .models import Participant

    conversation = services.active_conversation(Participant.objects.get(pk=participant_id))
    if conversation is not None:
        realtime.send_now(
            realtime.staff_conversation_group(conversation.pk),
            {"type": "typing", "who": "visitor", "conversation": str(conversation.pk), "on": on},
        )


class InboxConsumer(_Base):
    """A staff member's live inbox: list updates, the watched conversation, typing."""

    kind = "staff"
    max_sockets = MAX_STAFF_SOCKETS
    user_id: uuid.UUID | None = None
    user_name = ""
    session_id: uuid.UUID | None = None
    watching: uuid.UUID | None = None
    last_typing = 0.0

    def _resolve(self) -> tuple[uuid.UUID, str, uuid.UUID] | None:
        from commerce.identity import sessions
        from commerce.identity.models import AccountType

        token = self.cookies.get(sessions.cookie_spec(AccountType.STAFF)["name"])
        session = sessions.resolve(token, AccountType.STAFF) if token else None
        if session is None or not services.staff_can_message(session.user):
            return None
        return session.user.pk, session.user.full_name, session.pk

    async def authenticate(self) -> bool:
        found = await self.db(self._resolve)
        if found is None:
            return False
        user_id, name, session_id = found
        if self.session_id is not None and session_id != self.session_id:
            return False
        self.user_id, self.user_name, self.session_id = user_id, name, session_id
        self.identity = user_id.hex
        return True

    async def joined(self) -> None:
        user_id = self.user_id
        if user_id is None:
            return
        inbox, mine = await self.db(lambda: (realtime.inbox_group(), realtime.user_group(user_id)))
        await self.join(inbox)
        await self.join(mine)
        await self.heartbeat()
        await self.send_json({"type": "ready"})

    async def heartbeat(self) -> None:
        user_id, channel, watching = self.user_id, self.channel_name, self.watching
        if user_id is None:
            return

        def beat() -> None:
            realtime.mark_agent_present(channel, user_id)
            if watching is not None:
                realtime.mark_viewing(watching, user_id, True)

        await self.db(beat)

    async def left(self) -> None:
        user_id, channel, watching = self.user_id, self.channel_name, self.watching
        if user_id is None:
            return

        def gone() -> None:
            realtime.clear_agent(channel)
            if watching is not None:
                realtime.mark_viewing(watching, user_id, False)

        await self.db(gone)

    async def handle(self, data: dict[str, Any]) -> None:
        kind = data["type"]
        if kind == "watch":
            await self._watch_conversation(data.get("conversation"))
        elif kind == "unwatch":
            await self._watch_conversation(None)
        elif kind == "typing":
            now = time.monotonic()
            if self.watching is not None and (
                now - self.last_typing >= TYPING_INTERVAL or not data.get("on")
            ):
                self.last_typing = now
                conversation_id, on, name, user_id = (
                    self.watching,
                    bool(data.get("on")),
                    self.user_name.split(" ")[0] if self.user_name else "",
                    self.user_id,
                )
                await self.db(lambda: _staff_typing(conversation_id, on, name, user_id))
        else:
            await self.send_json({"type": "error", "code": "unknown_type"})

    async def _watch_conversation(self, raw: Any) -> None:
        user_id = self.user_id
        if user_id is None:
            return
        previous = self.watching
        if previous is not None:
            group = await self.db(lambda: realtime.staff_conversation_group(previous))
            await self.leave(group)
            await self.db(lambda: realtime.mark_viewing(previous, user_id, False))
            self.watching = None
        if raw is None:
            return
        try:
            conversation_id = uuid.UUID(str(raw))
        except ValueError:
            await self.send_json({"type": "error", "code": "not_found"})
            return

        def check() -> str:
            from .models import Conversation

            if not Conversation.objects.filter(pk=conversation_id).exists():
                return ""
            realtime.mark_viewing(conversation_id, user_id, True)
            return realtime.staff_conversation_group(conversation_id)

        group = await self.db(check)
        if not group:
            await self.send_json({"type": "error", "code": "not_found"})
            return
        await self.join(group)
        self.watching = conversation_id
        await self.send_json({"type": "watching", "conversation": str(conversation_id)})

    def should_relay(self, payload: dict[str, Any]) -> bool:
        # Never echo a member's own typing back to them.
        return not (payload.get("type") == "typing" and payload.get("user") == str(self.user_id))


def _staff_typing(
    conversation_id: uuid.UUID, on: bool, name: str, user_id: uuid.UUID | None
) -> None:
    from .models import ChannelType, Conversation

    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        return
    event = {"type": "typing", "who": "agent", "conversation": str(conversation.pk), "on": on}
    realtime.send_now(
        realtime.staff_conversation_group(conversation.pk),
        {**event, "name": name, "user": str(user_id)},
    )
    if conversation.channel == ChannelType.WEBSITE:
        realtime.send_now(realtime.visitor_group(conversation.participant_id), event)
