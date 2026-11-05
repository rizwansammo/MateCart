"""Unified customer communications (Phase 14, D-092 to D-098). Every table is tenant-owned.

Concepts are kept apart on purpose:

- ``ChannelConnection``: one authorised external business account (a Facebook Page, a WhatsApp
  business phone number, an Instagram professional account). Website chat needs no connection.
- ``Participant``: the person on the other side, as the channel identifies them (a page-scoped
  id, a WhatsApp id, an Instagram-scoped id or a website visitor). A participant is never a
  customer account; ``customer`` is set only by an explicit, verified association (D-095).
- ``Conversation``: one thread with one participant on one channel connection. At most one
  conversation per participant is active (not closed); the database enforces it.
- ``Message``: a customer-visible message with a stable per-conversation sequence, its provider
  id (unique per connection, never assumed globally unique) and its delivery state.
- ``ConversationNote``: staff-only notes. A separate table, so no delivery path can reach them.
- ``ConversationEvent``: the append-only timeline (assignments, status changes, order links).

The database is the authoritative message store; Redis only distributes events (D-092).
"""

from __future__ import annotations

from typing import Any

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

from commerce.catalog.models import ProductVariant
from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.orders.models import Order
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.keys import tenant_storage_path
from commerce.tenancy.models import TenantOwnedModel

PROVIDER_ID = r"^[A-Za-z0-9_.:=+-]{1,128}$"
ACCOUNT_ID = r"^[A-Za-z0-9_.-]{1,64}$"
SHA256_HEX = r"^[0-9a-f]{64}$"
MAX_BODY = 4096
MAX_NOTE = 2000


class AppendOnlyError(RuntimeError):
    pass


class ChannelType(models.TextChoices):
    WEBSITE = "website", "Website chat"
    MESSENGER = "messenger", "Facebook Messenger"
    WHATSAPP = "whatsapp", "WhatsApp"
    INSTAGRAM = "instagram", "Instagram Direct"


EXTERNAL_CHANNELS = (ChannelType.MESSENGER, ChannelType.WHATSAPP, ChannelType.INSTAGRAM)


class ConnectionStatus(models.TextChoices):
    """Stored states. "Not configured" and "configuration required" are derived, never stored."""

    PENDING = "pending", "Authorization pending"
    CONNECTED = "connected", "Connected"
    PERMISSION_MISSING = "permission_missing", "Permission missing"
    REVOKED = "revoked", "Revoked"
    SUSPENDED = "suspended", "Suspended by the provider"
    ERROR = "error", "Error"
    DISCONNECTED = "disconnected", "Disconnected"


class ChannelConnection(TenantOwnedModel, TimestampedModel):
    """An authorised external business account. Tokens are Fernet-encrypted and never leave the
    server; the row keeps only safe identifiers for display."""

    channel = models.CharField(max_length=16, choices=ChannelType.choices)
    status = models.CharField(max_length=20, choices=ConnectionStatus.choices)
    is_enabled = models.BooleanField(default=True)
    # Development simulator (D-097): never reaches Meta and is labelled as such everywhere.
    is_simulated = models.BooleanField(default=False)
    # Page id (Messenger), phone number id (WhatsApp) or Instagram account id: the id webhooks
    # name, used to route events to this connection.
    external_account_id = models.CharField(max_length=64, validators=[RegexValidator(ACCOUNT_ID)])
    # WhatsApp Business Account id, or the Facebook Page that carries an Instagram account.
    business_account_id = models.CharField(max_length=64, blank=True)
    display_name = models.CharField(max_length=120, blank=True)
    display_identifier = models.CharField(max_length=60, blank=True)
    access_token_encrypted = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.CharField(max_length=500, blank=True)
    connected_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    connected_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=60, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="connected_by", name="comms_connection_actor_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "channel", "external_account_id"],
                name="comms_connection_account_unique",
            ),
            # One live binding per channel; disconnected rows stay for the history they own.
            models.UniqueConstraint(
                fields=["tenant", "channel"],
                condition=Q(disconnected_at__isnull=True),
                name="comms_connection_one_live",
            ),
            models.CheckConstraint(
                condition=Q(channel__in=EXTERNAL_CHANNELS), name="comms_connection_channel_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=ConnectionStatus.values),
                name="comms_connection_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(disconnected_at__isnull=True)
                | Q(status=ConnectionStatus.DISCONNECTED, access_token_encrypted=""),
                name="comms_connection_disconnected_clean",
            ),
        ]

    @property
    def can_send(self) -> bool:
        return (
            self.is_enabled
            and self.status == ConnectionStatus.CONNECTED
            and self.disconnected_at is None
        )


class Participant(TenantOwnedModel, TimestampedModel):
    """The other side of a conversation, as the channel identifies them (never a login)."""

    channel = models.CharField(max_length=16, choices=ChannelType.choices)
    connection = models.ForeignKey(
        ChannelConnection, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Provider-scoped id (PSID, WhatsApp id, IGSID) or the website visitor id.
    external_id = models.CharField(max_length=128, validators=[RegexValidator(PROVIDER_ID)])
    display_name = models.CharField(max_length=120, blank=True)
    # WhatsApp only: the number the provider reports. Contact data, not a verified identity.
    phone = models.CharField(max_length=20, blank=True)
    # Verified association only (signed-in session or the visitor's own confirmation, D-095).
    customer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    customer_linked_at = models.DateTimeField(null=True, blank=True)
    # Website guests: the hashed opaque chat credential (cookie ``mc_chat``).
    token_hash = models.CharField(
        max_length=64, blank=True, validators=[RegexValidator(SHA256_HEX)]
    )
    token_expires_at = models.DateTimeField(null=True, blank=True)
    # Messaging permissions (D-096). Starting a support chat is not consent to anything else.
    template_opt_in_at = models.DateTimeField(null=True, blank=True)
    template_opt_in_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    opted_out_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="connection", name="comms_participant_conn_tenant"),
            TenantForeignKeyConstraint(field="customer", name="comms_participant_cust_tenant"),
            TenantForeignKeyConstraint(
                field="template_opt_in_by", name="comms_participant_optin_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "connection", "external_id"],
                condition=Q(connection__isnull=False),
                name="comms_participant_provider_id",
            ),
            models.UniqueConstraint(
                fields=["tenant", "external_id"],
                condition=Q(channel="website"),
                name="comms_participant_visitor_id",
            ),
            models.UniqueConstraint(
                fields=["tenant", "token_hash"],
                condition=~Q(token_hash=""),
                name="comms_participant_token_unique",
            ),
            models.CheckConstraint(
                condition=(Q(channel="website") & Q(connection__isnull=True))
                | (Q(channel__in=EXTERNAL_CHANNELS) & Q(connection__isnull=False)),
                name="comms_participant_channel_conn",
            ),
            models.CheckConstraint(
                condition=Q(channel="website") | Q(token_hash=""),
                name="comms_participant_token_website",
            ),
        ]


class ConversationStatus(models.TextChoices):
    OPEN = "open", "Open"
    PENDING = "pending", "Pending"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


ACTIVE_STATUSES = (ConversationStatus.OPEN, ConversationStatus.PENDING, ConversationStatus.RESOLVED)


class Conversation(TenantOwnedModel, TimestampedModel):
    channel = models.CharField(max_length=16, choices=ChannelType.choices)
    connection = models.ForeignKey(
        ChannelConnection, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    participant = models.ForeignKey(
        Participant, on_delete=models.PROTECT, related_name="conversations"
    )
    status = models.CharField(
        max_length=12, choices=ConversationStatus.choices, default=ConversationStatus.OPEN
    )
    assignee = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    # Optimistic concurrency for staff changes: a stale version is refused, never overwritten.
    version = models.PositiveIntegerField(default=1)
    # Last allocated message sequence (allocated under this row's lock).
    last_seq = models.PositiveIntegerField(default=0)
    # Cumulative inbound messages; a staff member's unread count is this minus what they read.
    inbound_count = models.PositiveIntegerField(default=0)
    # Website chat: the highest sequence the visitor's own widget has displayed.
    visitor_read_seq = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_preview = models.CharField(max_length=160, blank=True)
    last_direction = models.CharField(max_length=8, blank=True)
    first_inbound_at = models.DateTimeField(null=True, blank=True)
    # First human reply after the first inbound message (analytics, D-098).
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    # Retention (D-096): bodies and files are removed, the row and its counts remain.
    redacted_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="connection", name="comms_conv_connection_tenant"),
            TenantForeignKeyConstraint(field="participant", name="comms_conv_participant_tenant"),
            TenantForeignKeyConstraint(field="assignee", name="comms_conv_assignee_tenant"),
            # Concurrent webhooks or tabs can never open two active threads for one participant.
            models.UniqueConstraint(
                fields=["tenant", "participant"],
                condition=~Q(status="closed"),
                name="comms_conv_one_active",
            ),
            models.CheckConstraint(
                condition=Q(status__in=ConversationStatus.values), name="comms_conv_status_valid"
            ),
            models.CheckConstraint(
                condition=(Q(channel="website") & Q(connection__isnull=True))
                | (Q(channel__in=EXTERNAL_CHANNELS) & Q(connection__isnull=False)),
                name="comms_conv_channel_conn",
            ),
            models.CheckConstraint(
                condition=Q(status="closed") | Q(closed_at__isnull=True),
                name="comms_conv_closed_at",
            ),
            models.CheckConstraint(
                condition=Q(visitor_read_seq__lte=models.F("last_seq")),
                name="comms_conv_read_le_seq",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "-last_message_at"], name="comms_conv_inbox"),
            models.Index(fields=["tenant", "assignee", "status"], name="comms_conv_assignee"),
            models.Index(fields=["tenant", "-created_at"], name="comms_conv_created"),
        ]


class MessageDirection(models.TextChoices):
    INBOUND = "inbound", "From the customer"
    OUTBOUND = "outbound", "From the store"


class MessageKind(models.TextChoices):
    TEXT = "text", "Text"
    IMAGE = "image", "Image"
    DOCUMENT = "document", "Document"
    PRODUCT = "product", "Product"
    TEMPLATE = "template", "WhatsApp template"
    UNSUPPORTED = "unsupported", "Unsupported content"


class DeliveryStatus(models.TextChoices):
    """Outbound states are only advanced by evidence: provider acceptance is not delivery, and
    delivered/read are set only when the provider (or the website widget) reports them."""

    RECEIVED = "received", "Received"
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    ACCEPTED = "accepted", "Accepted by provider"
    DELIVERED = "delivered", "Delivered"
    READ = "read", "Read"
    FAILED = "failed", "Failed"
    UNKNOWN = "unknown", "Outcome unknown"


OUTBOUND_STATUSES = [s for s in DeliveryStatus.values if s != DeliveryStatus.RECEIVED]
# Receipt order: a late "delivered" never overwrites "read".
STATUS_RANK = {"queued": 0, "sending": 1, "accepted": 2, "unknown": 2, "delivered": 3, "read": 4}


class Message(TenantOwnedModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="+")
    # Denormalised from the conversation so provider ids are unique per connection.
    connection = models.ForeignKey(
        ChannelConnection, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    seq = models.PositiveIntegerField()
    direction = models.CharField(max_length=8, choices=MessageDirection.choices)
    kind = models.CharField(max_length=12, choices=MessageKind.choices, default=MessageKind.TEXT)
    body = models.TextField(max_length=MAX_BODY, blank=True)
    sender_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    external_id = models.CharField(max_length=128, blank=True)
    client_key = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    provider_timestamp = models.DateTimeField(null=True, blank=True)
    delivery_status = models.CharField(max_length=10, choices=DeliveryStatus.choices)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=60, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    product_variant = models.ForeignKey(
        ProductVariant, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Authoritative values at send time (name, price, link): what the customer actually saw.
    product_snapshot = models.JSONField(default=dict, blank=True)
    template_name = models.CharField(max_length=512, blank=True)
    template_language = models.CharField(max_length=15, blank=True)
    # Messenger/Instagram message tag used outside the standard window (HUMAN_AGENT).
    reply_tag = models.CharField(max_length=30, blank=True)
    # As reported by WhatsApp status webhooks; display only, never billing (D-094).
    pricing_category = models.CharField(max_length=30, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="conversation", name="comms_msg_conv_tenant"),
            TenantForeignKeyConstraint(field="connection", name="comms_msg_connection_tenant"),
            TenantForeignKeyConstraint(field="sender_user", name="comms_msg_sender_tenant"),
            TenantForeignKeyConstraint(field="product_variant", name="comms_msg_variant_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "conversation", "seq"], name="comms_msg_seq_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant", "connection", "external_id"],
                condition=~Q(external_id="") & Q(connection__isnull=False),
                name="comms_msg_provider_id_unique",
            ),
            models.UniqueConstraint(
                fields=["tenant", "conversation", "client_key"],
                condition=~Q(client_key=""),
                name="comms_msg_client_key_unique",
            ),
            models.CheckConstraint(
                condition=Q(direction__in=MessageDirection.values), name="comms_msg_direction"
            ),
            models.CheckConstraint(condition=Q(kind__in=MessageKind.values), name="comms_msg_kind"),
            models.CheckConstraint(
                condition=(Q(direction="inbound") & Q(delivery_status="received"))
                | (Q(direction="outbound") & Q(delivery_status__in=OUTBOUND_STATUSES)),
                name="comms_msg_status_direction",
            ),
            models.CheckConstraint(
                condition=Q(direction="outbound") | Q(sender_user__isnull=True),
                name="comms_msg_inbound_no_staff",
            ),
            models.CheckConstraint(condition=Q(seq__gt=0), name="comms_msg_seq_positive"),
        ]
        indexes = [
            models.Index(fields=["tenant", "conversation", "seq"], name="comms_msg_thread"),
            models.Index(fields=["tenant", "delivery_status"], name="comms_msg_status"),
        ]


class AttachmentStatus(models.TextChoices):
    PENDING = "pending", "Downloading"
    STORED = "stored", "Stored"
    FAILED = "failed", "Could not be downloaded"
    UNSUPPORTED = "unsupported", "Unsupported type"
    REMOVED = "removed", "Removed by retention"


class MessageAttachment(TenantOwnedModel, TimestampedModel):
    """A file in a message. Never public: served only through checked conversation views."""

    message = models.OneToOneField(Message, on_delete=models.PROTECT, related_name="attachment")
    status = models.CharField(max_length=12, choices=AttachmentStatus.choices)
    file = models.FileField(upload_to=tenant_storage_path, max_length=255, blank=True)
    content_type = models.CharField(max_length=64, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    # Display only, sanitised; the stored name is generated (tenant_storage_path).
    filename = models.CharField(max_length=120, blank=True)
    # Provider reference to fetch the bytes (WhatsApp media id or a temporary Meta CDN URL).
    # Cleared once stored or failed; it never carries an access token.
    provider_media_id = models.CharField(max_length=128, blank=True)
    source_url = models.CharField(max_length=2000, blank=True)
    error_code = models.CharField(max_length=40, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="message", name="comms_attachment_msg_tenant"),
            models.CheckConstraint(
                condition=Q(status__in=AttachmentStatus.values), name="comms_attachment_status"
            ),
            models.CheckConstraint(
                condition=~Q(status="stored") | (~Q(file="") & Q(size_bytes__gt=0)),
                name="comms_attachment_stored_file",
            ),
        ]


class DeliveryAttempt(TenantOwnedModel):
    """Append-only record of each provider call for an outbound message."""

    message = models.ForeignKey(Message, on_delete=models.PROTECT, related_name="+")
    attempt = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=12)
    error_code = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="message", name="comms_attempt_msg_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "message", "attempt"], name="comms_attempt_unique"
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Delivery attempts are append-only.")
        super().save(*args, **kwargs)


class ConversationNote(TenantOwnedModel):
    """Staff-only. No serializer or delivery path for customers ever reads this table."""

    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="+")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    body = models.TextField(max_length=MAX_NOTE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="conversation", name="comms_note_conv_tenant"),
            TenantForeignKeyConstraint(field="author", name="comms_note_author_tenant"),
        ]


class EventKind(models.TextChoices):
    ASSIGNED = "assigned", "Assigned"
    UNASSIGNED = "unassigned", "Unassigned"
    STATUS = "status", "Status changed"
    ORDER_LINKED = "order_linked", "Order linked"
    OPT_IN = "opt_in", "WhatsApp updates allowed"
    OPT_OUT = "opt_out", "Asked not to be contacted"
    CUSTOMER_LINKED = "customer_linked", "Linked to a customer account"


class ConversationEvent(TenantOwnedModel):
    """Append-only timeline: who assigned, changed status or linked an order, and when."""

    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=EventKind.choices)
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    assignee = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    from_status = models.CharField(max_length=12, blank=True)
    to_status = models.CharField(max_length=12, blank=True)
    order = models.ForeignKey(
        Order, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="conversation", name="comms_event_conv_tenant"),
            TenantForeignKeyConstraint(field="actor", name="comms_event_actor_tenant"),
            TenantForeignKeyConstraint(field="assignee", name="comms_event_assignee_tenant"),
            TenantForeignKeyConstraint(field="order", name="comms_event_order_tenant"),
            models.CheckConstraint(condition=Q(kind__in=EventKind.values), name="comms_event_kind"),
        ]
        indexes = [models.Index(fields=["tenant", "conversation", "created_at"], name="comms_ev")]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Conversation events are append-only.")
        super().save(*args, **kwargs)


class ConversationReadState(TenantOwnedModel):
    """Per staff member: how many inbound messages they have read. Reading never changes another
    staff member's unread count."""

    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    inbound_read = models.PositiveIntegerField(default=0)
    read_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="conversation", name="comms_read_conv_tenant"),
            TenantForeignKeyConstraint(field="user", name="comms_read_user_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "conversation", "user"], name="comms_read_unique"
            ),
        ]


class OrderLinkKind(models.TextChoices):
    CREATED = "created", "Created from this conversation"
    LINKED = "linked", "Linked by staff"


class ConversationOrderLink(TenantOwnedModel):
    """A conversation's related order. A link is a reference, not proof the chat caused a sale."""

    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=OrderLinkKind.choices)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="conversation", name="comms_link_conv_tenant"),
            TenantForeignKeyConstraint(field="order", name="comms_link_order_tenant"),
            TenantForeignKeyConstraint(field="created_by", name="comms_link_actor_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "conversation", "order"], name="comms_link_unique"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=OrderLinkKind.values), name="comms_link_kind"
            ),
        ]


class WhatsAppTemplate(TenantOwnedModel, TimestampedModel):
    """A template as the provider reports it (synchronised, never authored here). Only
    ``APPROVED`` utility templates can be sent (D-094)."""

    connection = models.ForeignKey(ChannelConnection, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=512)
    language = models.CharField(max_length=15)
    category = models.CharField(max_length=30)
    status = models.CharField(max_length=30)
    body_text = models.TextField(max_length=1024, blank=True)
    parameter_count = models.PositiveSmallIntegerField(default=0)
    synced_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="connection", name="comms_template_conn_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "connection", "name", "language"], name="comms_template_unique"
            ),
        ]


class LauncherPosition(models.TextChoices):
    RIGHT = "right", "Bottom right"
    LEFT = "left", "Bottom left"


class ChatLanguages(models.TextChoices):
    BOTH = "en,bn", "English and Bangla"
    ENGLISH = "en", "English"
    BANGLA = "bn", "Bangla"


class LiveChatSettings(TenantOwnedModel, TimestampedModel):
    """Website chat configuration (one row per store). Plain text only (D-063); the widget's
    colours come from the store theme tokens, never from merchant CSS (D-061)."""

    welcome_en = models.CharField(max_length=300, blank=True)
    welcome_bn = models.CharField(max_length=300, blank=True)
    button_label_en = models.CharField(max_length=30, blank=True)
    button_label_bn = models.CharField(max_length=30, blank=True)
    away_en = models.CharField(max_length=300, blank=True)
    away_bn = models.CharField(max_length=300, blank=True)
    # {"mon": [["09:00", "18:00"]], ...} in the store's time zone; empty = no set hours.
    hours = models.JSONField(default=dict, blank=True)
    languages = models.CharField(
        max_length=5, choices=ChatLanguages.choices, default=ChatLanguages.BOTH
    )
    position = models.CharField(
        max_length=5, choices=LauncherPosition.choices, default=LauncherPosition.RIGHT
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant"], name="comms_livechat_one_per_store"),
        ]
