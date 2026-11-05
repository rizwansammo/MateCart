"""Conversation operations (D-092 to D-098). Views and consumers stay thin; every rule is here.

Ordering and concurrency:

- Every message takes the next ``seq`` of its conversation under that conversation's row lock,
  so order is stable no matter when provider webhooks arrive. Provider timestamps are kept as
  facts but never reorder the thread.
- Inbound provider messages are unique per connection and provider id: replays and concurrent
  duplicate webhooks store one message and notify once.
- Finding or opening a participant's active conversation runs under the participant's row lock,
  and the database allows only one active conversation per participant.
- Staff state changes (assignment, status) carry the version the staff member saw; a stale
  version is refused instead of silently overwriting a newer change.
- Client keys make replies idempotent: a browser retry returns the stored message.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import record_audit_event
from commerce.core import ratelimit
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import AccountType, User
from commerce.orders.models import Order, OrderSource
from commerce.staff.models import MembershipStatus, StaffMembership
from commerce.staff.permissions import Permission, permissions_for
from commerce.tenancy.context import get_active_tenant_id

from . import policy, realtime
from .models import (
    ACTIVE_STATUSES,
    MAX_BODY,
    MAX_NOTE,
    STATUS_RANK,
    ChannelConnection,
    ChannelType,
    Conversation,
    ConversationEvent,
    ConversationNote,
    ConversationOrderLink,
    ConversationReadState,
    ConversationStatus,
    DeliveryStatus,
    EventKind,
    Message,
    MessageDirection,
    MessageKind,
    OrderLinkKind,
    Participant,
)

logger = logging.getLogger(__name__)

VISITOR_MESSAGES_PER_MINUTE = 20
VISITOR_CONVERSATIONS_PER_HOUR = 10
STAFF_MESSAGES_PER_MINUTE = 60
_CLIENT_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

TRANSITIONS: dict[str, tuple[str, ...]] = {
    ConversationStatus.OPEN: (
        ConversationStatus.PENDING,
        ConversationStatus.RESOLVED,
        ConversationStatus.CLOSED,
    ),
    ConversationStatus.PENDING: (
        ConversationStatus.OPEN,
        ConversationStatus.RESOLVED,
        ConversationStatus.CLOSED,
    ),
    ConversationStatus.RESOLVED: (ConversationStatus.OPEN, ConversationStatus.CLOSED),
    ConversationStatus.CLOSED: (ConversationStatus.OPEN,),
}
ORDER_SOURCES = {
    ChannelType.WEBSITE: OrderSource.LIVE_CHAT,
    ChannelType.MESSENGER: OrderSource.FACEBOOK,
    ChannelType.WHATSAPP: OrderSource.WHATSAPP,
    ChannelType.INSTAGRAM: OrderSource.INSTAGRAM,
}


def clean_text(value: Any, *, limit: int = MAX_BODY, field: str = "body") -> str:
    """Plain text only: control characters removed, surrounding space trimmed, bounded. Markup
    is kept as literal text and rendered escaped by every client (D-096)."""
    text = _CONTROL.sub("", str(value or "")).strip()
    if not text:
        raise ValidationError({field: ["Write a message."]})
    if len(text) > limit:
        raise ValidationError({field: [f"Messages can be at most {limit} characters."]})
    return text


def clean_client_key(value: Any) -> str:
    key = str(value or "").strip()
    if key and not _CLIENT_KEY.match(key):
        raise ValidationError({"client_key": ["Use 8-64 letters, digits, '-' or '_'."]})
    return key


def preview(message: Message) -> str:
    if message.kind == MessageKind.PRODUCT:
        return f"Product: {message.product_snapshot.get('name', '')}"[:160]
    if message.kind == MessageKind.IMAGE:
        return ("Photo: " + message.body)[:160] if message.body else "Photo"
    if message.kind == MessageKind.DOCUMENT:
        return "Document"
    if message.kind == MessageKind.UNSUPPORTED:
        return "Unsupported message"
    return " ".join(message.body.split())[:160]


# --- Conversations --------------------------------------------------------------------------------


def active_conversation(participant: Participant) -> Conversation | None:
    return (
        Conversation.objects.filter(participant=participant, status__in=ACTIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )


def latest_conversation(participant: Participant) -> Conversation | None:
    return Conversation.objects.filter(participant=participant).order_by("-created_at").first()


@dataclass(frozen=True)
class Opened:
    conversation: Conversation
    created: bool
    reopened: bool


def _open_for_inbound(participant: Participant) -> Opened:
    """Inside a transaction: the participant's active conversation, locked. A resolved one is
    reopened; a closed one stays closed and a new conversation starts (D-093)."""
    Participant.objects.select_for_update().filter(pk=participant.pk).first()
    current = (
        Conversation.objects.select_for_update()
        .filter(participant=participant, status__in=ACTIVE_STATUSES)
        .first()
    )
    if current is not None:
        return Opened(current, False, False)
    conversation = Conversation.objects.create(
        channel=participant.channel,
        connection_id=participant.connection_id,
        participant=participant,
        status=ConversationStatus.OPEN,
    )
    return Opened(Conversation.objects.select_for_update().get(pk=conversation.pk), True, False)


def _append(
    conversation: Conversation,
    *,
    direction: str,
    kind: str,
    body: str,
    delivery_status: str,
    sender_user: User | None = None,
    external_id: str = "",
    client_key: str = "",
    provider_timestamp: datetime | None = None,
    **extra: Any,
) -> Message:
    """Append to a conversation already locked by the caller (``select_for_update``)."""
    now = timezone.now()
    conversation.last_seq += 1
    message = Message.objects.create(
        conversation=conversation,
        connection_id=conversation.connection_id,
        seq=conversation.last_seq,
        direction=direction,
        kind=kind,
        body=body,
        sender_user=sender_user,
        external_id=external_id,
        client_key=client_key,
        provider_timestamp=provider_timestamp,
        delivery_status=delivery_status,
        status_changed_at=now,
        **extra,
    )
    conversation.last_message_at = now
    conversation.last_preview = preview(message)
    conversation.last_direction = direction
    fields = ["last_seq", "last_message_at", "last_preview", "last_direction", "updated_at"]
    if direction == MessageDirection.INBOUND:
        conversation.inbound_count += 1
        conversation.last_inbound_at = now
        fields += ["inbound_count", "last_inbound_at"]
        if conversation.first_inbound_at is None:
            conversation.first_inbound_at = now
            fields.append("first_inbound_at")
    elif sender_user is not None and conversation.first_response_at is None:
        if conversation.first_inbound_at is not None:
            conversation.first_response_at = now
            fields.append("first_response_at")
    conversation.save(update_fields=fields)
    return message


def _reopen_if_waiting(conversation: Conversation) -> str:
    """A customer message on a pending or resolved conversation needs attention again."""
    before = conversation.status
    if before in (ConversationStatus.PENDING, ConversationStatus.RESOLVED):
        conversation.status = ConversationStatus.OPEN
        conversation.resolved_at = None
        conversation.version += 1
        conversation.save(update_fields=["status", "resolved_at", "version", "updated_at"])
        ConversationEvent.objects.create(
            conversation=conversation,
            kind=EventKind.STATUS,
            from_status=before,
            to_status=ConversationStatus.OPEN,
        )
    return before


def _publish_message(conversation: Conversation, message: Message) -> None:
    from .serializers import conversation_event, message_payload

    staff = message_payload(message, staff=True)
    realtime.publish(
        realtime.staff_conversation_group(conversation.pk),
        {"type": "message", "conversation": str(conversation.pk), "message": staff},
    )
    realtime.publish(realtime.inbox_group(), conversation_event(conversation))
    if conversation.channel == ChannelType.WEBSITE:
        realtime.publish(
            realtime.visitor_group(conversation.participant_id),
            {"type": "message", "message": message_payload(message, staff=False)},
        )


def _alert_inbound(conversation: Conversation, opened: Opened, previous_status: str) -> None:
    from commerce.notifications import services as notifications

    participant = conversation.participant
    values = {
        "customer_name": participant.display_name or "A customer",
        "channel": ChannelType(conversation.channel).label,
    }
    link = f"/manage/inbox/{conversation.pk}"
    if opened.created:
        notifications.emit(
            "staff.conversation.new",
            event_id=str(conversation.pk),
            values=values,
            related=("conversation", str(conversation.pk)),
            staff_link_path=link,
        )
    elif previous_status in (ConversationStatus.PENDING, ConversationStatus.RESOLVED):
        watchers = (
            [conversation.assignee_id]
            if conversation.assignee_id
            and realtime.is_viewing(conversation.pk, conversation.assignee_id)
            else []
        )
        notifications.emit(
            "staff.conversation.reply",
            event_id=f"{conversation.pk}:{conversation.last_seq}",
            values=values,
            related=("conversation", str(conversation.pk)),
            staff_link_path=link,
            staff_users=[conversation.assignee] if conversation.assignee is not None else None,
            exclude_staff=watchers,
        )


def receive_inbound(
    *,
    participant: Participant,
    kind: str,
    body: str,
    external_id: str = "",
    provider_timestamp: datetime | None = None,
    client_key: str = "",
    before_publish: Callable[[Message], None] | None = None,
) -> tuple[Message, bool]:
    """Store one customer message. Returns (message, created); a duplicate provider id or client
    key returns the stored message with ``created=False`` and no side effects."""
    connection_id = participant.connection_id
    if external_id and connection_id is not None:
        existing = Message.objects.filter(
            connection_id=connection_id, external_id=external_id
        ).first()
        if existing is not None:
            return existing, False
    try:
        with transaction.atomic():
            opened = _open_for_inbound(participant)
            conversation = opened.conversation
            if client_key:
                existing = Message.objects.filter(
                    conversation=conversation, client_key=client_key
                ).first()
                if existing is not None:
                    return existing, False
            previous = _reopen_if_waiting(conversation)
            message = _append(
                conversation,
                direction=MessageDirection.INBOUND,
                kind=kind,
                body=body,
                delivery_status=DeliveryStatus.RECEIVED,
                external_id=external_id,
                client_key=client_key,
                provider_timestamp=provider_timestamp,
            )
            Participant.objects.filter(pk=participant.pk).update(last_seen_at=timezone.now())
            conversation.participant = participant
            if before_publish is not None:
                before_publish(message)
                message = Message.objects.select_related("attachment").get(pk=message.pk)
            _alert_inbound(conversation, opened, previous)
            _publish_message(conversation, message)
    except IntegrityError:
        # A concurrent delivery of the same provider message or client key won the race.
        duplicate = None
        if external_id and connection_id is not None:
            duplicate = Message.objects.filter(
                connection_id=connection_id, external_id=external_id
            ).first()
        elif client_key:
            duplicate = Message.objects.filter(
                conversation__participant=participant, client_key=client_key
            ).first()
        if duplicate is None:
            raise
        return duplicate, False
    return message, True


def visitor_send(*, participant: Participant, body: Any, client_key: Any) -> tuple[Message, bool]:
    """A website visitor's message (WebSocket or HTTP). Rate limited per visitor."""
    text = clean_text(body)
    key = clean_client_key(client_key)
    if not key:
        raise ValidationError({"client_key": ["A client key is required."]})
    ratelimit.consume(
        "chat-visitor-message",
        str(participant.pk),
        limit=VISITOR_MESSAGES_PER_MINUTE,
        window=60,
    )
    if active_conversation(participant) is None:
        ratelimit.consume(
            "chat-visitor-conversation",
            str(participant.pk),
            limit=VISITOR_CONVERSATIONS_PER_HOUR,
            window=3600,
        )
    return receive_inbound(
        participant=participant, kind=MessageKind.TEXT, body=text, client_key=key
    )


# --- Staff ----------------------------------------------------------------------------------------


def staff_can_message(user: User) -> bool:
    membership = StaffMembership.objects.filter(user=user, status=MembershipStatus.ACTIVE).first()
    return (
        user.is_active
        and user.account_type == AccountType.STAFF
        and membership is not None
        and Permission.MESSAGES_MANAGE in permissions_for(membership.role)
    )


def _locked(conversation: Conversation) -> Conversation:
    return (
        Conversation.objects.select_for_update()
        .select_related("participant")
        .get(pk=conversation.pk)
    )


def _require_version(conversation: Conversation, expected: int | None) -> None:
    if expected is not None and expected != conversation.version:
        raise ConflictError(
            "Someone else changed this conversation. Review the latest state and try again.",
            "conversation_changed",
        )


@dataclass(frozen=True)
class Outbound:
    kind: str = MessageKind.TEXT
    body: str = ""
    product_variant: Any = None
    product_snapshot: Mapping[str, Any] | None = None
    template_name: str = ""
    template_language: str = ""
    template_parameters: Sequence[str] = ()


def staff_send(
    *, conversation: Conversation, actor: User, outbound: Outbound, client_key: Any
) -> tuple[Message, bool]:
    """A staff reply. Checked against the provider's messaging rules before it is stored; an
    external message is queued for delivery after commit, never reported as delivered early."""
    key = clean_client_key(client_key)
    if not key:
        raise ValidationError({"client_key": ["A client key is required."]})
    ratelimit.consume(
        "chat-staff-message", str(actor.pk), limit=STAFF_MESSAGES_PER_MINUTE, window=60
    )
    with transaction.atomic():
        locked = _locked(conversation)
        existing = Message.objects.filter(conversation=locked, client_key=key).first()
        if existing is not None:
            return existing, False
        connection = (
            ChannelConnection.objects.filter(pk=locked.connection_id).first()
            if locked.connection_id
            else None
        )
        rules = policy.reply_policy(locked, connection, locked.participant)
        tag = ""
        if outbound.kind == MessageKind.TEMPLATE:
            if locked.channel != ChannelType.WHATSAPP:
                raise ValidationError({"template": ["Templates are only used on WhatsApp."]})
            problem = (
                "conversation_closed"
                if locked.status == ConversationStatus.CLOSED
                else policy.connection_problem(connection)
            )
            if problem:
                raise ConflictError(policy.REASONS[problem], problem)
            if not rules.templates_allowed:
                raise ConflictError(rules.template_reason, "template_not_allowed")
        elif not rules.can_reply:
            raise ConflictError(rules.message, rules.reason)
        else:
            tag = rules.tag
        if locked.channel == ChannelType.WEBSITE:
            status = DeliveryStatus.ACCEPTED
        else:
            status = DeliveryStatus.QUEUED
        message = _append(
            locked,
            direction=MessageDirection.OUTBOUND,
            kind=outbound.kind,
            body=outbound.body,
            delivery_status=status,
            sender_user=actor,
            client_key=key,
            product_variant=outbound.product_variant,
            product_snapshot=dict(outbound.product_snapshot or {}),
            template_name=outbound.template_name,
            template_language=outbound.template_language,
            reply_tag=tag,
        )
        _mark_read_locked(locked, actor)
        _publish_message(locked, message)
        if status == DeliveryStatus.QUEUED:
            from . import tasks

            message_id, params = message.pk, list(outbound.template_parameters)
            tenant = str(get_active_tenant_id())
            transaction.on_commit(
                lambda: tasks.enqueue_delivery(message_id, params, tenant_id=tenant)
            )
    return message, True


def add_note(*, conversation: Conversation, actor: User, body: Any) -> ConversationNote:
    text = clean_text(body, limit=MAX_NOTE)
    with transaction.atomic():
        locked = _locked(conversation)
        note = ConversationNote.objects.create(conversation=locked, author=actor, body=text)
        from .serializers import note_payload

        realtime.publish(
            realtime.staff_conversation_group(locked.pk),
            {"type": "note", "conversation": str(locked.pk), "note": note_payload(note)},
        )
    return note


def assign(
    *,
    conversation: Conversation,
    actor: User,
    assignee_id: uuid.UUID | str | None,
    expected_version: int | None,
) -> Conversation:
    with transaction.atomic():
        locked = _locked(conversation)
        _require_version(locked, expected_version)
        assignee: User | None = None
        if assignee_id is not None:
            assignee = User.objects.filter(pk=assignee_id).first()
            if assignee is None or not staff_can_message(assignee):
                raise ValidationError(
                    {"assignee": ["Choose an active team member who can answer messages."]}
                )
        if locked.assignee_id == (assignee.pk if assignee else None):
            return locked
        before = locked.assignee_id
        locked.assignee = assignee
        locked.assigned_at = timezone.now() if assignee else None
        locked.version += 1
        locked.save(update_fields=["assignee", "assigned_at", "version", "updated_at"])
        ConversationEvent.objects.create(
            conversation=locked,
            kind=EventKind.ASSIGNED if assignee else EventKind.UNASSIGNED,
            actor=actor,
            assignee=assignee,
        )
        record_audit_event(
            action="communications.conversation.assigned",
            resource=locked,
            actor=actor,
            changes={
                "assignee": {
                    "from": str(before) if before else None,
                    "to": str(assignee.pk) if assignee else None,
                }
            },
        )
        if assignee is not None and assignee.pk != actor.pk:
            from commerce.notifications import services as notifications

            notifications.emit(
                "staff.conversation.assigned",
                event_id=f"{locked.pk}:{locked.version}",
                values={
                    "customer_name": locked.participant.display_name or "a customer",
                    "channel": ChannelType(locked.channel).label,
                    "assigned_by": actor.full_name or "A team member",
                },
                related=("conversation", str(locked.pk)),
                staff_link_path=f"/manage/inbox/{locked.pk}",
                staff_users=[assignee],
            )
        _publish_state(locked)
    return locked


def change_status(
    *, conversation: Conversation, actor: User, status: str, expected_version: int | None
) -> Conversation:
    with transaction.atomic():
        locked = _locked(conversation)
        _require_version(locked, expected_version)
        if status == locked.status:
            return locked
        if status not in TRANSITIONS.get(locked.status, ()):
            raise ConflictError(
                f"A {locked.get_status_display().lower()} conversation cannot become {status}.",
                "invalid_transition",
            )
        if locked.status == ConversationStatus.CLOSED:
            newer = Conversation.objects.filter(
                participant_id=locked.participant_id, status__in=ACTIVE_STATUSES
            ).exists()
            if newer:
                raise ConflictError(
                    "This customer already has a newer active conversation. Continue there.",
                    "newer_conversation_active",
                )
        before = locked.status
        now = timezone.now()
        locked.status = status
        locked.resolved_at = now if status == ConversationStatus.RESOLVED else None
        locked.closed_at = now if status == ConversationStatus.CLOSED else None
        locked.version += 1
        locked.save(update_fields=["status", "resolved_at", "closed_at", "version", "updated_at"])
        ConversationEvent.objects.create(
            conversation=locked,
            kind=EventKind.STATUS,
            actor=actor,
            from_status=before,
            to_status=status,
        )
        record_audit_event(
            action="communications.conversation.status_changed",
            resource=locked,
            actor=actor,
            changes={"status": {"from": before, "to": status}},
        )
        _publish_state(locked)
        if locked.channel == ChannelType.WEBSITE:
            realtime.publish(
                realtime.visitor_group(locked.participant_id),
                {"type": "conversation", "status": locked.status},
            )
    return locked


def _publish_state(conversation: Conversation) -> None:
    from .serializers import conversation_event

    event = conversation_event(conversation)
    realtime.publish(realtime.inbox_group(), event)
    realtime.publish(realtime.staff_conversation_group(conversation.pk), event)


def _mark_read_locked(conversation: Conversation, user: User) -> None:
    now = timezone.now()
    updated = ConversationReadState.objects.filter(conversation=conversation, user=user).update(
        inbound_read=conversation.inbound_count, read_at=now
    )
    if not updated:
        try:
            with transaction.atomic():
                ConversationReadState.objects.create(
                    conversation=conversation,
                    user=user,
                    inbound_read=conversation.inbound_count,
                    read_at=now,
                )
        except IntegrityError:
            ConversationReadState.objects.filter(conversation=conversation, user=user).update(
                inbound_read=conversation.inbound_count, read_at=now
            )


def mark_read(*, conversation: Conversation, user: User) -> None:
    """Only this staff member's unread count changes; everyone else's stays as it was."""
    with transaction.atomic():
        current = Conversation.objects.get(pk=conversation.pk)
        _mark_read_locked(current, user)
        realtime.publish(
            realtime.user_group(user.pk),
            {"type": "read", "conversation": str(current.pk), "unread": 0},
        )


def visitor_mark_read(*, participant: Participant, seq: Any) -> None:
    """The visitor's widget displayed messages up to ``seq`` (website chat only): genuine read
    receipts for the store's replies."""
    try:
        upto = int(seq)
    except (TypeError, ValueError):
        return
    with transaction.atomic():
        conversation = (
            Conversation.objects.select_for_update()
            .filter(participant=participant)
            .order_by("-created_at")
            .first()
        )
        if conversation is None:
            return
        upto = min(max(upto, 0), conversation.last_seq)
        if upto <= conversation.visitor_read_seq:
            return
        conversation.visitor_read_seq = upto
        conversation.save(update_fields=["visitor_read_seq", "updated_at"])
        now = timezone.now()
        Message.objects.filter(
            conversation=conversation,
            direction=MessageDirection.OUTBOUND,
            seq__lte=upto,
            delivery_status__in=(DeliveryStatus.ACCEPTED, DeliveryStatus.DELIVERED),
        ).update(delivery_status=DeliveryStatus.READ, status_changed_at=now)
        realtime.publish(
            realtime.staff_conversation_group(conversation.pk),
            {"type": "visitor_read", "conversation": str(conversation.pk), "seq": upto},
        )


def visitor_delivered(*, participant: Participant, seq: Any) -> None:
    """The visitor's socket received store messages up to ``seq``."""
    try:
        upto = int(seq)
    except (TypeError, ValueError):
        return
    conversation = latest_conversation(participant)
    if conversation is None:
        return
    count = Message.objects.filter(
        conversation=conversation,
        direction=MessageDirection.OUTBOUND,
        seq__lte=min(upto, conversation.last_seq),
        delivery_status=DeliveryStatus.ACCEPTED,
    ).update(delivery_status=DeliveryStatus.DELIVERED, status_changed_at=timezone.now())
    if count:
        realtime.send_now(
            realtime.staff_conversation_group(conversation.pk),
            {"type": "visitor_delivered", "conversation": str(conversation.pk), "seq": upto},
        )


# --- Provider receipts ----------------------------------------------------------------------------


def apply_status(
    *,
    connection: ChannelConnection,
    status: str,
    message_ids: Sequence[str] = (),
    watermark: datetime | None = None,
    recipient_id: str = "",
    error_code: str = "",
    pricing_category: str = "",
) -> int:
    """Advance outbound delivery states from provider receipts. Monotonic: a late or duplicate
    receipt never moves a message backwards, and "read" is set only when reported."""
    rows = Message.objects.filter(connection=connection, direction=MessageDirection.OUTBOUND)
    if message_ids:
        rows = rows.filter(external_id__in=[m for m in message_ids if m])
    elif watermark is not None and recipient_id:
        rows = rows.filter(
            conversation__participant__external_id=recipient_id,
            created_at__lte=watermark,
            delivery_status__in=(DeliveryStatus.ACCEPTED, DeliveryStatus.DELIVERED),
        )
    else:
        return 0
    changed = 0
    with transaction.atomic():
        for message in rows.select_for_update().order_by("pk")[:500]:
            current = message.delivery_status
            fields = []
            if status == DeliveryStatus.FAILED:
                if current in (
                    DeliveryStatus.ACCEPTED,
                    DeliveryStatus.UNKNOWN,
                    DeliveryStatus.SENDING,
                ):
                    message.delivery_status = DeliveryStatus.FAILED
                    message.error_code = error_code[:60] or "provider_failed"
                    fields += ["delivery_status", "error_code"]
            elif status in STATUS_RANK and STATUS_RANK[status] > STATUS_RANK.get(current, -1):
                message.delivery_status = status
                fields.append("delivery_status")
            if pricing_category and not message.pricing_category:
                message.pricing_category = pricing_category
                fields.append("pricing_category")
            if fields:
                message.status_changed_at = timezone.now()
                message.save(update_fields=[*fields, "status_changed_at"])
                changed += 1
                realtime.publish(
                    realtime.staff_conversation_group(message.conversation_id),
                    {
                        "type": "delivery",
                        "message": str(message.pk),
                        "status": message.delivery_status,
                        "error_code": message.error_code,
                    },
                )
    return changed


# --- Orders ---------------------------------------------------------------------------------------


def link_order(
    *, conversation: Conversation, order: Order, actor: User, kind: str = OrderLinkKind.LINKED
) -> ConversationOrderLink:
    """Reference an existing order of this store (the scoped manager found it). The link never
    exposes the order to the conversation's participant."""
    with transaction.atomic():
        locked = _locked(conversation)
        link = ConversationOrderLink.objects.filter(conversation=locked, order=order).first()
        if link is not None:
            return link
        link = ConversationOrderLink.objects.create(
            conversation=locked, order=order, kind=kind, created_by=actor
        )
        ConversationEvent.objects.create(
            conversation=locked, kind=EventKind.ORDER_LINKED, actor=actor, order=order
        )
        record_audit_event(
            action="communications.conversation.order_linked",
            resource=locked,
            actor=actor,
            changes={"order": str(order.pk), "kind": kind},
        )
        _publish_state(locked)
    return link


def create_order(*, conversation: Conversation, data: Mapping[str, Any], actor: User) -> Any:
    """Place a merchant-assisted COD order for this conversation (D-077 engine, unchanged).

    The source follows the conversation's channel; the attestation, reviewed totals, stock,
    pricing and idempotency rules are the assisted order's own. Nothing here creates or links a
    customer account."""
    from commerce.orders import assisted

    if conversation.status == ConversationStatus.CLOSED:
        raise ConflictError(
            "Reopen the conversation before creating an order.", "conversation_closed"
        )
    payload = dict(data)
    payload["source"] = ORDER_SOURCES[ChannelType(conversation.channel)]
    payload["source_reference"] = f"Conversation {conversation.pk.hex[:12]}"
    with transaction.atomic():
        placement = assisted.place_assisted_order(data=payload, actor=actor)
        link_order(
            conversation=conversation,
            order=placement.order,
            actor=actor,
            kind=OrderLinkKind.CREATED,
        )
    return placement


# --- Consent --------------------------------------------------------------------------------------


def record_template_opt_in(
    *, conversation: Conversation, actor: User, confirmed: bool
) -> Participant:
    if conversation.channel != ChannelType.WHATSAPP:
        raise DomainError("WhatsApp updates apply to WhatsApp conversations.", "not_whatsapp")
    if confirmed is not True:
        raise ValidationError({"confirmed": ["Confirm the customer agreed to WhatsApp updates."]})
    with transaction.atomic():
        locked = _locked(conversation)
        participant = Participant.objects.select_for_update().get(pk=locked.participant_id)
        participant.template_opt_in_at = timezone.now()
        participant.template_opt_in_by = actor
        participant.opted_out_at = None
        participant.save(update_fields=["template_opt_in_at", "template_opt_in_by", "opted_out_at"])
        ConversationEvent.objects.create(conversation=locked, kind=EventKind.OPT_IN, actor=actor)
        record_audit_event(
            action="communications.participant.opt_in",
            resource=participant,
            actor=actor,
            changes={"channel": locked.channel},
        )
        _publish_state(locked)
    return participant


def record_opt_out(*, conversation: Conversation, actor: User) -> Participant:
    with transaction.atomic():
        locked = _locked(conversation)
        participant = Participant.objects.select_for_update().get(pk=locked.participant_id)
        participant.opted_out_at = timezone.now()
        participant.template_opt_in_at = None
        participant.save(update_fields=["opted_out_at", "template_opt_in_at"])
        ConversationEvent.objects.create(conversation=locked, kind=EventKind.OPT_OUT, actor=actor)
        record_audit_event(
            action="communications.participant.opt_out",
            resource=participant,
            actor=actor,
            changes={"channel": locked.channel},
        )
        _publish_state(locked)
    return participant


# --- Housekeeping ---------------------------------------------------------------------------------

AUTO_CLOSE_AFTER = timedelta(days=7)


def auto_close_resolved(now: datetime | None = None) -> int:
    """Resolved conversations with no activity for 7 days close, so a later message starts a
    new conversation (history stays with the old one)."""
    now = now or timezone.now()
    ids = list(
        Conversation.objects.filter(
            status=ConversationStatus.RESOLVED,
            updated_at__lt=now - AUTO_CLOSE_AFTER,
        ).values_list("pk", flat=True)[:500]
    )
    closed = 0
    for pk in ids:
        with transaction.atomic():
            row = (
                Conversation.objects.select_for_update()
                .filter(pk=pk, status=ConversationStatus.RESOLVED)
                .first()
            )
            if row is None:
                continue
            row.status = ConversationStatus.CLOSED
            row.closed_at = now
            row.version += 1
            row.save(update_fields=["status", "closed_at", "version", "updated_at"])
            ConversationEvent.objects.create(
                conversation=row,
                kind=EventKind.STATUS,
                from_status=ConversationStatus.RESOLVED,
                to_status=ConversationStatus.CLOSED,
            )
            closed += 1
    return closed


def unread_for(user: User, conversation: Conversation) -> int:
    state = ConversationReadState.objects.filter(conversation=conversation, user=user).first()
    return max(conversation.inbound_count - (state.inbound_read if state else 0), 0)
