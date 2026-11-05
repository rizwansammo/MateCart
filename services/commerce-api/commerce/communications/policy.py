"""Who may be messaged, how, and until when (D-094).

Checked on the server before every outbound message, and returned to the inbox so the composer
shows only what the provider genuinely permits:

- Website chat: replies while the conversation is not closed.
- Messenger and Instagram: standard replies within 24 hours of the customer's last message.
  Up to 7 days only with the HUMAN_AGENT tag, and only when the deployment's Meta app has that
  feature approved (``META_HUMAN_AGENT_APPROVED``). Never anything later; never bulk messages.
- WhatsApp: free-form replies inside the 24-hour customer service window. Outside it, only an
  approved utility template, only for a participant whose opt-in staff recorded, and never after
  an opt-out. Marketing templates are never offered.

The same rules run twice for external channels (D-099): when staff send (``reply_policy``) and
again immediately before the provider call (``delivery_check``), because a queued message can
wait (broker outage, retries) while the window closes, the customer opts out, the conversation
is closed or the connection changes. Allowed when queued is not enough; it must still be allowed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .models import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
    Conversation,
    ConversationStatus,
    Message,
    MessageKind,
    Participant,
    WhatsAppTemplate,
)

STANDARD_WINDOW = timedelta(hours=24)
HUMAN_AGENT_WINDOW = timedelta(days=7)
HUMAN_AGENT_TAG = "HUMAN_AGENT"
TEMPLATE_CATEGORIES = ("UTILITY",)

REASONS = {
    "conversation_closed": "This conversation is closed. Reopen it to reply.",
    "channel_disabled": "This channel is switched off in Social channels settings.",
    "connection_unavailable": "This channel is not connected. Reconnect it in Social channels.",
    "connection_revoked": "The store's access to this account was revoked. Reconnect it.",
    "permission_missing": "The connection is missing a messaging permission. Reconnect it.",
    "connection_suspended": "The provider suspended this account. Check it in Meta Business.",
    "window_closed": (
        "The customer's last message is older than the provider allows for a reply. "
        "Wait for the customer to message again."
    ),
    "template_required": (
        "The 24-hour WhatsApp service window has ended. Send an approved template instead."
    ),
    "no_customer_message": "The customer has not messaged on this channel yet.",
    # Delivery-time refusals (``delivery_check``), stored as the message's error code.
    "opted_out": "The customer asked not to be contacted on WhatsApp.",
    "opt_in_missing": "The customer's agreement to WhatsApp updates is no longer recorded.",
    "template_unavailable": "This template is no longer an approved utility template.",
    "template_parameters_invalid": "This template's values no longer match the template.",
}


@dataclass(frozen=True)
class ReplyPolicy:
    can_reply: bool
    reason: str = ""
    tag: str = ""
    window_ends_at: datetime | None = None
    templates_allowed: bool = False
    template_reason: str = ""

    @property
    def message(self) -> str:
        return REASONS.get(self.reason, "")


def connection_problem(connection: ChannelConnection | None) -> str:
    if connection is None or connection.disconnected_at is not None:
        return "connection_unavailable"
    if not connection.is_enabled:
        return "channel_disabled"
    problems: dict[str, str] = {
        ConnectionStatus.CONNECTED: "",
        ConnectionStatus.REVOKED: "connection_revoked",
        ConnectionStatus.PERMISSION_MISSING: "permission_missing",
        ConnectionStatus.SUSPENDED: "connection_suspended",
    }
    return problems.get(str(connection.status), "connection_unavailable")


def _template_state(participant: Participant) -> tuple[bool, str]:
    if participant.opted_out_at is not None:
        return False, "The customer asked not to be contacted on WhatsApp."
    if participant.template_opt_in_at is None:
        return False, "Record the customer's agreement to WhatsApp updates before using templates."
    return True, ""


def reply_policy(
    conversation: Conversation,
    connection: ChannelConnection | None,
    participant: Participant,
    now: datetime | None = None,
) -> ReplyPolicy:
    now = now or timezone.now()
    if conversation.status == ConversationStatus.CLOSED:
        return ReplyPolicy(False, "conversation_closed")
    if conversation.channel == ChannelType.WEBSITE:
        return ReplyPolicy(True)
    problem = connection_problem(connection)
    if problem:
        return ReplyPolicy(False, problem)
    last = conversation.last_inbound_at
    if conversation.channel == ChannelType.WHATSAPP:
        allowed, why = _template_state(participant)
        if last is not None and now - last < STANDARD_WINDOW:
            return ReplyPolicy(
                True,
                window_ends_at=last + STANDARD_WINDOW,
                templates_allowed=allowed,
                template_reason=why,
            )
        return ReplyPolicy(
            False,
            "template_required" if last is not None else "no_customer_message",
            templates_allowed=allowed,
            template_reason=why,
        )
    # Messenger and Instagram Direct.
    if last is None:
        return ReplyPolicy(False, "no_customer_message")
    if now - last < STANDARD_WINDOW:
        return ReplyPolicy(True, window_ends_at=last + STANDARD_WINDOW)
    if settings.META_HUMAN_AGENT_APPROVED and now - last < HUMAN_AGENT_WINDOW:
        return ReplyPolicy(True, tag=HUMAN_AGENT_TAG, window_ends_at=last + HUMAN_AGENT_WINDOW)
    return ReplyPolicy(False, "window_closed")


@dataclass(frozen=True)
class DeliveryDecision:
    """``reason`` is a ``REASONS`` key when refused; ``tag`` is the Messenger tag to use now."""

    allowed: bool
    reason: str = ""
    tag: str = ""


def delivery_check(
    message: Message,
    conversation: Conversation,
    connection: ChannelConnection | None,
    parameters: Sequence[str],
    now: datetime | None = None,
) -> DeliveryDecision:
    """Whether a queued external message may be sent to the provider right now.

    A refusal is permanent for this message (it is failed, never retried); staff can send again
    once the situation allows. Free-form messages re-run ``reply_policy`` (window, Human Agent
    approval, closed conversation, connection state). Templates re-check the WhatsApp rules:
    open conversation, usable connection, no opt-out, recorded opt-in, a template that is still
    an approved utility template, and exactly the values it needs.
    """
    participant = conversation.participant
    if connection is None or conversation.channel == ChannelType.WEBSITE:
        return DeliveryDecision(False, "connection_unavailable")
    if message.kind != MessageKind.TEMPLATE:
        rules = reply_policy(conversation, connection, participant, now)
        if not rules.can_reply:
            return DeliveryDecision(False, rules.reason)
        return DeliveryDecision(True, tag=rules.tag)
    if conversation.channel != ChannelType.WHATSAPP:
        return DeliveryDecision(False, "template_unavailable")
    if conversation.status == ConversationStatus.CLOSED:
        return DeliveryDecision(False, "conversation_closed")
    problem = connection_problem(connection)
    if problem:
        return DeliveryDecision(False, problem)
    if participant.opted_out_at is not None:
        return DeliveryDecision(False, "opted_out")
    if participant.template_opt_in_at is None:
        return DeliveryDecision(False, "opt_in_missing")
    template = WhatsAppTemplate.objects.filter(
        connection=connection,
        name=message.template_name,
        language=message.template_language,
        status="APPROVED",
        category__in=TEMPLATE_CATEGORIES,
    ).first()
    if template is None:
        return DeliveryDecision(False, "template_unavailable")
    if template.parameter_count != len(parameters):
        return DeliveryDecision(False, "template_parameters_invalid")
    return DeliveryDecision(True)
