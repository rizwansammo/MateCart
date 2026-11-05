"""API shapes for the inbox, the website chat widget and channel settings.

The same serializers build HTTP responses and WebSocket events, so both transports always agree.
Visitor shapes contain only customer-visible fields: no staff ids, no internal notes, no provider
ids, error codes or assignment data. No shape ever contains a provider token.
"""

from __future__ import annotations

import json
from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from commerce.orders.serializers import AssistedOrderInputSerializer

from . import policy
from .models import (
    AttachmentStatus,
    ChannelConnection,
    ChannelType,
    ChatLanguages,
    ConnectionStatus,
    Conversation,
    ConversationEvent,
    ConversationNote,
    ConversationStatus,
    LauncherPosition,
    Message,
    MessageAttachment,
    MessageKind,
    WhatsAppTemplate,
)

# Derived states first, then stored ones (D-094).
CONNECTION_STATES = [
    ("not_configured", "Not configured"),
    ("configuration_required", "Configuration required"),
    *ConnectionStatus.choices,
]


def _plain(data: Any) -> Any:
    """JSON-safe plain dicts for the channel layer (msgpack) and responses."""
    return json.loads(json.dumps(data, default=str))


def _first_name(user: Any) -> str:
    name = str(getattr(user, "full_name", "") or "").strip()
    return name.split()[0] if name else ""


# --- Attachments and messages ---------------------------------------------------------------------


class AttachmentSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=AttachmentStatus.choices)
    content_type = serializers.CharField()
    size_bytes = serializers.IntegerField()
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)
    filename = serializers.CharField()
    url = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_url(self, row: MessageAttachment) -> str | None:
        if row.status != AttachmentStatus.STORED:
            return None
        prefix = "/api/manage/inbox" if self.context.get("staff") else "/api/chat"
        return f"{prefix}/attachments/{row.pk}/"


class ProductCardSerializer(serializers.Serializer[Any]):
    product_id = serializers.UUIDField()
    variant_id = serializers.UUIDField()
    name = serializers.CharField()
    variant_title = serializers.CharField(allow_blank=True)
    sku = serializers.CharField()
    amount = serializers.DecimalField(max_digits=19, decimal_places=2)
    currency = serializers.CharField()
    in_stock = serializers.BooleanField()
    url = serializers.CharField(allow_blank=True)
    image_url = serializers.CharField(allow_null=True)


class VisitorMessageSerializer(serializers.Serializer[Any]):
    """What the website visitor sees of a message."""

    id = serializers.UUIDField()
    # The conversation that owns ``seq``: sequences restart at 1 in every conversation (D-100).
    conversation = serializers.UUIDField(source="conversation_id")
    seq = serializers.IntegerField()
    direction = serializers.CharField()
    kind = serializers.ChoiceField(choices=MessageKind.choices)
    body = serializers.CharField()
    created_at = serializers.DateTimeField()
    client_key = serializers.SerializerMethodField()
    sender_name = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    attachment = serializers.SerializerMethodField()

    def get_client_key(self, message: Message) -> str:
        # Visitors only get their own keys back (to settle optimistic messages).
        return message.client_key if message.direction == "inbound" else ""

    def get_sender_name(self, message: Message) -> str:
        return _first_name(message.sender_user) if message.sender_user_id else ""

    @extend_schema_field(ProductCardSerializer(allow_null=True))
    def get_product(self, message: Message) -> Any:
        return message.product_snapshot or None if message.kind == MessageKind.PRODUCT else None

    @extend_schema_field(AttachmentSerializer(allow_null=True))
    def get_attachment(self, message: Message) -> Any:
        row = (
            getattr(message, "attachment", None) if message.kind in ("image", "document") else None
        )
        return AttachmentSerializer(row, context=self.context).data if row is not None else None


class StaffMessageSerializer(VisitorMessageSerializer):
    delivery_status = serializers.CharField()
    status_changed_at = serializers.DateTimeField(allow_null=True)
    error_code = serializers.CharField()
    provider_timestamp = serializers.DateTimeField(allow_null=True)
    sender = serializers.SerializerMethodField()
    template_name = serializers.CharField()
    template_language = serializers.CharField()
    reply_tag = serializers.CharField()
    pricing_category = serializers.CharField()
    attempts = serializers.IntegerField()

    def get_client_key(self, message: Message) -> str:
        return message.client_key

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_sender(self, message: Message) -> dict[str, str] | None:
        user = message.sender_user
        return {"id": str(user.pk), "name": user.full_name} if user is not None else None


def message_payload(message: Message, *, staff: bool) -> dict[str, Any]:
    serializer = StaffMessageSerializer if staff else VisitorMessageSerializer
    data: dict[str, Any] = _plain(serializer(message, context={"staff": staff}).data)
    return data


# --- Conversations --------------------------------------------------------------------------------


class PersonSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class ParticipantSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    display_name = serializers.CharField()
    channel = serializers.ChoiceField(choices=ChannelType.choices)
    phone = serializers.CharField()
    customer_id = serializers.UUIDField(allow_null=True)
    customer_linked_at = serializers.DateTimeField(allow_null=True)
    template_opt_in_at = serializers.DateTimeField(allow_null=True)
    opted_out_at = serializers.DateTimeField(allow_null=True)


class ConversationSummarySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    channel = serializers.ChoiceField(choices=ChannelType.choices)
    status = serializers.ChoiceField(choices=ConversationStatus.choices)
    version = serializers.IntegerField()
    display_name = serializers.SerializerMethodField()
    connection_name = serializers.SerializerMethodField()
    is_simulated = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    last_message_at = serializers.DateTimeField(allow_null=True)
    last_preview = serializers.CharField()
    last_direction = serializers.CharField()
    inbound_count = serializers.IntegerField()
    unread = serializers.SerializerMethodField()
    customer_linked = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_display_name(self, row: Conversation) -> str:
        participant = row.participant
        if participant.display_name:
            return participant.display_name
        if participant.phone:
            return participant.phone
        return "Website visitor" if row.channel == ChannelType.WEBSITE else "Customer"

    def get_connection_name(self, row: Conversation) -> str:
        connection = getattr(row, "connection", None)
        return connection.display_name if connection is not None else ""

    def get_is_simulated(self, row: Conversation) -> bool:
        connection = getattr(row, "connection", None)
        return bool(connection is not None and connection.is_simulated)

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_assignee(self, row: Conversation) -> dict[str, str] | None:
        user = row.assignee
        return {"id": str(user.pk), "name": user.full_name} if user is not None else None

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_unread(self, row: Conversation) -> int | None:
        value = getattr(row, "unread", None)
        return max(int(value), 0) if value is not None else None

    def get_customer_linked(self, row: Conversation) -> bool:
        return row.participant.customer_id is not None


def conversation_event(conversation: Conversation) -> dict[str, Any]:
    """A list-row update for every inbox. ``unread`` is per staff member, so it is omitted:
    clients add the new inbound messages to their own count (and refetch to reconcile)."""
    row = (
        Conversation.objects.select_related("participant", "assignee", "connection")
        .filter(pk=conversation.pk)
        .first()
    )
    data = ConversationSummarySerializer(row or conversation).data
    return {"type": "conversation", "conversation": _plain(data)}


class PolicySerializer(serializers.Serializer[Any]):
    can_reply = serializers.BooleanField()
    reason = serializers.CharField()
    message = serializers.CharField()
    tag = serializers.CharField()
    window_ends_at = serializers.DateTimeField(allow_null=True)
    templates_allowed = serializers.BooleanField()
    template_reason = serializers.CharField()


class NoteSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()
    author = serializers.SerializerMethodField()

    @extend_schema_field(PersonSerializer())
    def get_author(self, note: ConversationNote) -> dict[str, str]:
        return {"id": str(note.author_id), "name": note.author.full_name}


def note_payload(note: ConversationNote) -> dict[str, Any]:
    data: dict[str, Any] = _plain(NoteSerializer(note).data)
    return data


class EventSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    created_at = serializers.DateTimeField()
    from_status = serializers.CharField()
    to_status = serializers.CharField()
    actor = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    order = serializers.SerializerMethodField()

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_actor(self, row: ConversationEvent) -> dict[str, str] | None:
        return {"id": str(row.actor.pk), "name": row.actor.full_name} if row.actor else None

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_assignee(self, row: ConversationEvent) -> dict[str, str] | None:
        user = row.assignee
        return {"id": str(user.pk), "name": user.full_name} if user else None

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_order(self, row: ConversationEvent) -> dict[str, str] | None:
        return {"id": str(row.order.pk), "number": row.order.number} if row.order else None


class LinkedOrderSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(source="order.id")
    number = serializers.CharField(source="order.number")
    status = serializers.CharField(source="order.status")
    payment_status = serializers.CharField(source="order.payment_status")
    grand_total = serializers.DecimalField(
        source="order.grand_total", max_digits=19, decimal_places=4
    )
    currency = serializers.CharField(source="order.currency")
    order_source = serializers.CharField(source="order.source")
    kind = serializers.CharField()
    created_at = serializers.DateTimeField()


class CaseSummarySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


class ConversationDetailSerializer(serializers.Serializer[Any]):
    conversation = ConversationSummarySerializer()
    participant = ParticipantSerializer()
    policy = PolicySerializer()
    orders = LinkedOrderSerializer(many=True)
    cases = CaseSummarySerializer(many=True)
    recent = ConversationSummarySerializer(many=True)
    can_create_orders = serializers.BooleanField()
    visitor_read_seq = serializers.IntegerField()


class ThreadSerializer(serializers.Serializer[Any]):
    messages = StaffMessageSerializer(many=True)
    notes = NoteSerializer(many=True)
    events = EventSerializer(many=True)
    has_more = serializers.BooleanField()


class ConversationPageSerializer(serializers.Serializer[Any]):
    results = ConversationSummarySerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    unread_conversations = serializers.IntegerField()


# --- Requests -------------------------------------------------------------------------------------

INBOX_FILTERS = ("all", "unassigned", "mine", "unread")


class InboxQuerySerializer(serializers.Serializer[Any]):
    view = serializers.ChoiceField(choices=[(v, v) for v in INBOX_FILTERS], default="all")
    status = serializers.ChoiceField(
        choices=[("active", "active"), *ConversationStatus.choices], required=False
    )
    channel = serializers.ChoiceField(choices=ChannelType.choices, required=False)
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)
    cursor = serializers.CharField(max_length=200, required=False, allow_blank=True)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=30)


class ThreadQuerySerializer(serializers.Serializer[Any]):
    before_seq = serializers.IntegerField(min_value=1, required=False)
    after_seq = serializers.IntegerField(min_value=0, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=200, default=50)


class ReplyInputSerializer(serializers.Serializer[Any]):
    client_key = serializers.CharField(max_length=64)
    body = serializers.CharField(max_length=4096, required=False, allow_blank=True)
    product_variant = serializers.UUIDField(required=False, allow_null=True)
    template = serializers.UUIDField(required=False, allow_null=True)
    template_parameters = serializers.ListField(
        child=serializers.CharField(max_length=200), max_length=10, required=False
    )


class NoteInputSerializer(serializers.Serializer[Any]):
    body = serializers.CharField(max_length=2000)


class AssignInputSerializer(serializers.Serializer[Any]):
    assignee = serializers.UUIDField(allow_null=True)
    version = serializers.IntegerField(min_value=1)


class StatusInputSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=ConversationStatus.choices)
    version = serializers.IntegerField(min_value=1)


class LinkOrderInputSerializer(serializers.Serializer[Any]):
    order = serializers.UUIDField()


class OptInInputSerializer(serializers.Serializer[Any]):
    confirmed = serializers.BooleanField()


class ConversationOrderInputSerializer(AssistedOrderInputSerializer):
    """The assisted order request without its source: the conversation's channel decides it."""

    source = None  # type: ignore[assignment]
    source_reference = None  # type: ignore[assignment]


class ProductQuerySerializer(serializers.Serializer[Any]):
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)


class InboxProductSerializer(serializers.Serializer[Any]):
    variant_id = serializers.UUIDField(source="variant.pk")
    product_id = serializers.UUIDField(source="variant.product_id")
    name = serializers.CharField(source="variant.product.name")
    variant_title = serializers.CharField(source="variant.title")
    sku = serializers.CharField(source="variant.sku")
    amount = serializers.DecimalField(max_digits=19, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_null=True)
    in_stock = serializers.BooleanField()
    available_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, allow_null=True)
    image_url = serializers.CharField(allow_null=True)


class ProductSearchSerializer(serializers.Serializer[Any]):
    results = InboxProductSerializer(many=True)
    links_available = serializers.BooleanField()


class TeamMemberSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()


class WhatsAppTemplateSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    language = serializers.CharField()
    category = serializers.CharField()
    status = serializers.CharField()
    body_text = serializers.CharField()
    parameter_count = serializers.IntegerField()
    sendable = serializers.SerializerMethodField()

    def get_sendable(self, row: WhatsAppTemplate) -> bool:
        return row.status == "APPROVED" and row.category in policy.TEMPLATE_CATEGORIES


# --- Settings -------------------------------------------------------------------------------------


class ChannelConnectionSerializer(serializers.Serializer[Any]):
    channel = serializers.ChoiceField(choices=ChannelType.choices)
    state = serializers.ChoiceField(choices=CONNECTION_STATES)
    id = serializers.UUIDField(allow_null=True)
    is_enabled = serializers.BooleanField()
    is_simulated = serializers.BooleanField()
    display_name = serializers.CharField()
    display_identifier = serializers.CharField()
    account_id = serializers.CharField()
    connected_at = serializers.DateTimeField(allow_null=True)
    last_verified_at = serializers.DateTimeField(allow_null=True)
    last_error = serializers.CharField()
    requirements = serializers.ListField(child=serializers.CharField())
    templates = serializers.IntegerField()


def connection_card(channel: str, row: ChannelConnection | None) -> dict[str, Any]:
    from . import connections

    requirements: list[str] = []
    if not connections.meta_configured():
        requirements.append("A Meta app configured for this store by NetaMate (app id and secret).")
    if not connections.webhook_configured():
        requirements.append("Webhook verification token and app secret for incoming messages.")
    if channel == ChannelType.INSTAGRAM:
        requirements.append(
            "An Instagram professional account linked to a Facebook Page, with Meta's "
            "instagram_manage_messages permission approved for the app."
        )
    if channel == ChannelType.WHATSAPP:
        requirements.append(
            "A WhatsApp Business Account and phone number added through Meta's Embedded Signup."
        )
    if row is None:
        state = "not_configured" if connections.meta_configured() else "configuration_required"
    else:
        state = row.status
    return {
        "channel": channel,
        "state": state,
        "id": str(row.pk) if row else None,
        "is_enabled": bool(row and row.is_enabled),
        "is_simulated": bool(row and row.is_simulated),
        "display_name": row.display_name if row else "",
        "display_identifier": row.display_identifier if row else "",
        "account_id": row.external_account_id if row else "",
        "connected_at": row.connected_at if row else None,
        "last_verified_at": row.last_verified_at if row else None,
        "last_error": row.last_error if row else "",
        "requirements": [] if row is not None and row.status == "connected" else requirements,
        "templates": WhatsAppTemplate.objects.filter(connection=row).count() if row else 0,
    }


class LiveChatSettingsSerializer(serializers.Serializer[Any]):
    welcome_en = serializers.CharField(max_length=300, allow_blank=True)
    welcome_bn = serializers.CharField(max_length=300, allow_blank=True)
    button_label_en = serializers.CharField(max_length=30, allow_blank=True)
    button_label_bn = serializers.CharField(max_length=30, allow_blank=True)
    away_en = serializers.CharField(max_length=300, allow_blank=True)
    away_bn = serializers.CharField(max_length=300, allow_blank=True)
    hours = serializers.JSONField()
    languages = serializers.ChoiceField(choices=ChatLanguages.choices)
    position = serializers.ChoiceField(choices=LauncherPosition.choices)


class SocialSettingsSerializer(serializers.Serializer[Any]):
    live_chat_enabled = serializers.BooleanField()
    live_chat = LiveChatSettingsSerializer()
    agents_online = serializers.BooleanField()
    within_hours = serializers.BooleanField()
    timezone = serializers.CharField()
    connections = ChannelConnectionSerializer(many=True)
    simulator_available = serializers.BooleanField()
    webhook_url = serializers.CharField()
    human_agent_approved = serializers.BooleanField()
    product_links_available = serializers.BooleanField()


class StartConnectionInputSerializer(serializers.Serializer[Any]):
    channel = serializers.ChoiceField(
        choices=[(c, c) for c in ("messenger", "whatsapp", "instagram")]
    )


class StartConnectionSerializer(serializers.Serializer[Any]):
    authorize_url = serializers.CharField()


class SelectionAccountSerializer(serializers.Serializer[Any]):
    id = serializers.CharField()
    name = serializers.CharField()
    identifier = serializers.CharField()


class SelectionSerializer(serializers.Serializer[Any]):
    channel = serializers.CharField()
    accounts = SelectionAccountSerializer(many=True)


class SelectInputSerializer(serializers.Serializer[Any]):
    selection = serializers.CharField(max_length=100)
    account = serializers.CharField(max_length=64)


class EnabledInputSerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField()


class SyncResultSerializer(serializers.Serializer[Any]):
    count = serializers.IntegerField()


# --- Website chat (visitor) -----------------------------------------------------------------------


class ChatConfigSerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField()
    welcome = serializers.CharField()
    button_label = serializers.CharField()
    away_message = serializers.CharField()
    languages = serializers.ListField(child=serializers.CharField())
    position = serializers.CharField()
    online = serializers.BooleanField()
    within_hours = serializers.BooleanField()


class ChatStateSerializer(serializers.Serializer[Any]):
    """The visitor's own conversation (or none yet)."""

    config = ChatConfigSerializer()
    conversation_id = serializers.UUIDField(allow_null=True)
    conversation_status = serializers.CharField(allow_null=True)
    messages = VisitorMessageSerializer(many=True)
    signed_in = serializers.BooleanField()
    guest_chat_linkable = serializers.BooleanField()


class ChatSendInputSerializer(serializers.Serializer[Any]):
    client_key = serializers.CharField(max_length=64)
    body = serializers.CharField(max_length=4096)


class ChatLocaleQuerySerializer(serializers.Serializer[Any]):
    locale = serializers.ChoiceField(choices=[("en", "en"), ("bn", "bn")], default="en")
    after_seq = serializers.IntegerField(min_value=0, required=False)
    # The conversation ``after_seq`` belongs to; another conversation returns the current one.
    conversation = serializers.UUIDField(required=False)


# --- Analytics ------------------------------------------------------------------------------------


class AnalyticsQuerySerializer(serializers.Serializer[Any]):
    days = serializers.ChoiceField(choices=[(7, "7"), (30, "30"), (90, "90")], default=30)


class ChannelCountSerializer(serializers.Serializer[Any]):
    channel = serializers.CharField()
    conversations = serializers.IntegerField()


class AgentActivitySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    assigned_open = serializers.IntegerField()
    resolved = serializers.IntegerField()
    replies = serializers.IntegerField()
    median_first_response_seconds = serializers.IntegerField(allow_null=True)


class InboxAnalyticsSerializer(serializers.Serializer[Any]):
    days = serializers.IntegerField()
    timezone = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    new_conversations = serializers.IntegerField()
    unique_participants = serializers.IntegerField()
    messages_received = serializers.IntegerField()
    messages_sent = serializers.IntegerField()
    resolved_conversations = serializers.IntegerField()
    open_now = serializers.IntegerField()
    pending_now = serializers.IntegerField()
    unassigned_now = serializers.IntegerField()
    unread_now = serializers.IntegerField()
    median_first_response_seconds = serializers.IntegerField(allow_null=True)
    average_first_response_seconds = serializers.IntegerField(allow_null=True)
    average_resolution_seconds = serializers.IntegerField(allow_null=True)
    by_channel = ChannelCountSerializer(many=True)
    orders_linked = serializers.IntegerField()
    linked_order_value = serializers.DecimalField(max_digits=19, decimal_places=2, allow_null=True)
    linked_order_currency = serializers.CharField(allow_null=True)
    agents = AgentActivitySerializer(many=True)
