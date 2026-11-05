"""Real-time event distribution (D-092).

PostgreSQL is the record; the channel layer only tells connected browsers that something changed.
Every publish happens after the transaction commits, so no browser ever sees a message that was
rolled back, and a broker failure never breaks the business operation: clients reconcile from the
database when they reconnect.

Groups are tenant-qualified and split by audience. Visitor groups carry only customer-visible
messages; internal notes, assignments and staff typing are published to staff groups only.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.db import transaction

from commerce.tenancy.context import get_active_tenant_id
from commerce.tenancy.keys import tenant_cache_key

logger = logging.getLogger(__name__)

PRESENCE_TTL = 75  # seconds; inbox sockets refresh it every heartbeat (25 s)


def _t() -> str:
    return get_active_tenant_id().hex


def inbox_group() -> str:
    """Every staff socket with ``messages.manage`` in this store."""
    return f"t{_t()}.inbox"


def staff_conversation_group(conversation_id: uuid.UUID | str) -> str:
    return f"t{_t()}.c{uuid.UUID(str(conversation_id)).hex}.staff"


def visitor_group(participant_id: uuid.UUID | str) -> str:
    """The participant's own widget sockets (all tabs of one visitor or customer)."""
    return f"t{_t()}.p{uuid.UUID(str(participant_id)).hex}.visitor"


def user_group(user_id: uuid.UUID | str) -> str:
    """Every socket of one staff member (session revocation, personal alerts)."""
    return f"t{_t()}.u{uuid.UUID(str(user_id)).hex}"


def send_now(group: str, event: dict[str, Any]) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, {"type": "relay", "event": event})
    except Exception:  # the record is committed; browsers recover on reconnect
        logger.warning("Real-time event not distributed (group=%s)", group.split(".")[-1])


def publish(group: str, event: dict[str, Any]) -> None:
    """Distribute ``event`` to ``group`` once the current transaction commits."""
    transaction.on_commit(lambda: send_now(group, event))


# --- Presence -------------------------------------------------------------------------------------


def _agents_key() -> str:
    return tenant_cache_key("chat", "agents")


def _viewer_key(conversation_id: uuid.UUID | str, user_id: uuid.UUID | str) -> str:
    return tenant_cache_key("chat", "viewing", str(conversation_id), str(user_id))


def mark_agent_present(channel_name: str, user_id: uuid.UUID | str) -> None:
    """An inbox socket is open. Stored per socket ({channel: [user, last heartbeat]}), so closing
    one of two tabs does not make an agent look away."""
    try:
        sockets: dict[str, list[Any]] = cache.get(_agents_key()) or {}
        now = time.time()
        sockets = {k: v for k, v in sockets.items() if now - float(v[1]) < PRESENCE_TTL}
        sockets[channel_name] = [str(user_id), now]
        cache.set(_agents_key(), sockets, PRESENCE_TTL)
    except Exception:
        logger.warning("Agent presence not recorded")


def clear_agent(channel_name: str) -> None:
    try:
        sockets: dict[str, list[Any]] = cache.get(_agents_key()) or {}
        sockets.pop(channel_name, None)
        cache.set(_agents_key(), sockets, PRESENCE_TTL)
    except Exception:
        logger.warning("Agent presence not cleared")


def agents_online() -> bool:
    """True only while at least one staff member has the inbox open (a live heartbeat)."""
    try:
        sockets: dict[str, list[Any]] = cache.get(_agents_key()) or {}
    except Exception:
        return False
    now = time.time()
    return any(now - float(seen) < PRESENCE_TTL for _, seen in sockets.values())


def mark_viewing(conversation_id: uuid.UUID | str, user_id: uuid.UUID | str, on: bool) -> None:
    try:
        if on:
            cache.set(_viewer_key(conversation_id, user_id), 1, PRESENCE_TTL)
        else:
            cache.delete(_viewer_key(conversation_id, user_id))
    except Exception:
        logger.warning("Conversation viewer state not recorded")


def is_viewing(conversation_id: uuid.UUID | str, user_id: uuid.UUID | str) -> bool:
    try:
        return bool(cache.get(_viewer_key(conversation_id, user_id)))
    except Exception:
        return False
