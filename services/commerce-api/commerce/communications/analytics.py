"""Inbox operational metrics (D-098). Computed from persisted rows only, over store-local days.

- Conversations, messages and unique participants are counted separately.
- First response time is from the conversation's first customer message to the first reply
  written by a person (``first_response_at``); system events never count.
- Resolution time is from the conversation's start to its resolution, for conversations
  resolved in the range.
- Linked orders are a reference, not attribution: a link does not prove the conversation caused
  the sale. Their value counts only paid orders (verified payment or recorded cash) and is shown
  only to staff who may see reports. Financial reports remain the authority for revenue.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from django.db.models import Count, Q, Sum

from commerce.identity.models import User
from commerce.orders.models import Order
from commerce.orders.reports import day_bounds, store_today, store_zone

from .models import (
    ACTIVE_STATUSES,
    Conversation,
    ConversationOrderLink,
    ConversationReadState,
    ConversationStatus,
    Message,
    MessageDirection,
)


def _seconds(values: list[float]) -> tuple[int | None, int | None]:
    if not values:
        return None, None
    return int(median(values)), int(sum(values) / len(values))


def report(*, days: int, viewer: User, include_value: bool) -> dict[str, Any]:
    end = store_today()
    start = end - timedelta(days=days - 1)
    since, until = day_bounds(start, end)
    created = Conversation.objects.filter(created_at__gte=since, created_at__lt=until)
    messages = Message.objects.filter(created_at__gte=since, created_at__lt=until)
    responded = list(
        Conversation.objects.filter(
            first_response_at__gte=since,
            first_response_at__lt=until,
            first_inbound_at__isnull=False,
        ).values_list("first_inbound_at", "first_response_at")
    )
    first = [(b - a).total_seconds() for a, b in responded if a and b and b >= a]
    resolved_rows = list(
        Conversation.objects.filter(resolved_at__gte=since, resolved_at__lt=until).values_list(
            "created_at", "resolved_at"
        )
    )
    resolution = [(b - a).total_seconds() for a, b in resolved_rows if a and b and b >= a]
    median_first, average_first = _seconds(first)
    _, average_resolution = _seconds(resolution)
    active = Conversation.objects.filter(status__in=ACTIVE_STATUSES)
    reads = dict(
        ConversationReadState.objects.filter(user=viewer).values_list(
            "conversation_id", "inbound_read"
        )
    )
    unread_now = sum(
        1 for pk, inbound in active.values_list("pk", "inbound_count") if inbound > reads.get(pk, 0)
    )
    links = ConversationOrderLink.objects.filter(created_at__gte=since, created_at__lt=until)
    paid = (
        Order.objects.filter(pk__in=links.values("order_id"), payment_status="paid")
        .values("currency")
        .annotate(total=Sum("grand_total"))
    )
    value: Decimal | None = None
    currency: str | None = None
    if include_value:
        totals = list(paid)
        if len(totals) == 1:
            value, currency = (
                Decimal(totals[0]["total"]).quantize(Decimal("0.01")),
                totals[0]["currency"],
            )
        elif not totals:
            value = Decimal("0.00")
    return {
        "days": days,
        "timezone": str(store_zone()),
        "start": start,
        "end": end,
        "new_conversations": created.count(),
        "unique_participants": created.values("participant_id").distinct().count(),
        "messages_received": messages.filter(direction=MessageDirection.INBOUND).count(),
        "messages_sent": messages.filter(
            direction=MessageDirection.OUTBOUND, sender_user__isnull=False
        ).count(),
        "resolved_conversations": len(resolved_rows),
        "open_now": active.filter(status=ConversationStatus.OPEN).count(),
        "pending_now": active.filter(status=ConversationStatus.PENDING).count(),
        "unassigned_now": active.exclude(status=ConversationStatus.RESOLVED)
        .filter(assignee__isnull=True)
        .count(),
        "unread_now": unread_now,
        "median_first_response_seconds": median_first,
        "average_first_response_seconds": average_first,
        "average_resolution_seconds": average_resolution,
        "by_channel": [
            {"channel": row["channel"], "conversations": row["n"]}
            for row in created.values("channel").annotate(n=Count("pk")).order_by("-n", "channel")
        ],
        "orders_linked": links.count(),
        "linked_order_value": value,
        "linked_order_currency": currency,
        "agents": _agents(since, until),
    }


def _agents(since: Any, until: Any) -> list[dict[str, Any]]:
    """A modest per-person view: workload and replies, no activity tracking (D-098)."""
    from commerce.notifications.services import staff_recipients

    people = staff_recipients("messages.manage")
    if not people:
        return []
    ids = [user.pk for user in people]
    open_by = dict(
        Conversation.objects.filter(assignee_id__in=ids, status__in=ACTIVE_STATUSES)
        .exclude(status=ConversationStatus.RESOLVED)
        .values("assignee_id")
        .annotate(n=Count("pk"))
        .values_list("assignee_id", "n")
    )
    resolved_by = dict(
        Conversation.objects.filter(
            assignee_id__in=ids, resolved_at__gte=since, resolved_at__lt=until
        )
        .values("assignee_id")
        .annotate(n=Count("pk"))
        .values_list("assignee_id", "n")
    )
    replies_by = dict(
        Message.objects.filter(sender_user_id__in=ids, created_at__gte=since, created_at__lt=until)
        .values("sender_user_id")
        .annotate(n=Count("pk"))
        .values_list("sender_user_id", "n")
    )
    firsts: dict[Any, list[float]] = {}
    rows = Conversation.objects.filter(
        assignee_id__in=ids,
        first_response_at__gte=since,
        first_response_at__lt=until,
        first_inbound_at__isnull=False,
    ).values_list("assignee_id", "first_inbound_at", "first_response_at")
    for assignee, a, b in rows:
        if a and b and b >= a:
            firsts.setdefault(assignee, []).append((b - a).total_seconds())
    return [
        {
            "id": user.pk,
            "name": user.full_name,
            "assigned_open": open_by.get(user.pk, 0),
            "resolved": resolved_by.get(user.pk, 0),
            "replies": replies_by.get(user.pk, 0),
            "median_first_response_seconds": int(median(firsts[user.pk]))
            if firsts.get(user.pk)
            else None,
        }
        for user in sorted(people, key=lambda u: (u.full_name, str(u.pk)))
    ]


def unread_conversations(viewer: User) -> int:
    reads = dict(
        ConversationReadState.objects.filter(user=viewer).values_list(
            "conversation_id", "inbound_read"
        )
    )
    return sum(
        1
        for pk, inbound in Conversation.objects.filter(status__in=ACTIVE_STATUSES)
        .filter(~Q(inbound_count=0))
        .values_list("pk", "inbound_count")
        if inbound > reads.get(pk, 0)
    )
