"""HTTP API for the unified inbox, social channel settings, the website chat widget and the
Meta webhook (Phase 14). WebSocket consumers live in ``consumers``.

- Inbox: staff realm, ``messages.manage``. Creating or linking orders also needs
  ``orders.manage``; viewing linked support requests needs ``orders.manage`` too.
- Settings: ``store.settings``. Connecting, selecting and disconnecting accounts also need a
  recent sign-in (D-048).
- Website chat: anonymous or signed-in customers, CSRF-protected writes, and only ever the
  visitor's own conversation (resolved from their own credential, never from an id in the URL).
- Webhook: no session; the signature over the raw body is the only authority.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any, cast

from django.conf import settings
from django.db.models import Exists, F, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commerce.aftersales.models import AfterSalesCase
from commerce.core.domain import ConflictError, found, found_in
from commerce.identity import sessions
from commerce.identity.auth import (
    CsrfProtectedAPIView,
    CustomerSessionAuthentication,
    HasStaffPermission,
)
from commerce.identity.models import User
from commerce.orders.models import Order
from commerce.orders.serializers import MerchantOrderDetailSerializer
from commerce.staff.models import MembershipStatus, StaffMembership
from commerce.staff.permissions import Permission, permissions_for
from commerce.staff.views import StaffView
from commerce.stores.models import StoreSettings

from . import (
    analytics,
    attachments,
    connections,
    inbound,
    livechat,
    meta,
    policy,
    products,
    services,
    tasks,
)
from .models import (
    ACTIVE_STATUSES,
    EXTERNAL_CHANNELS,
    ChannelConnection,
    ChannelType,
    Conversation,
    ConversationEvent,
    ConversationNote,
    ConversationOrderLink,
    ConversationReadState,
    Message,
    MessageAttachment,
    MessageKind,
    WhatsAppTemplate,
)
from .serializers import (
    AnalyticsQuerySerializer,
    AssignInputSerializer,
    ChannelConnectionSerializer,
    ChatLocaleQuerySerializer,
    ChatSendInputSerializer,
    ChatStateSerializer,
    ConversationDetailSerializer,
    ConversationOrderInputSerializer,
    ConversationPageSerializer,
    ConversationSummarySerializer,
    EnabledInputSerializer,
    EventSerializer,
    InboxAnalyticsSerializer,
    InboxProductSerializer,
    InboxQuerySerializer,
    LinkedOrderSerializer,
    LinkOrderInputSerializer,
    LiveChatSettingsSerializer,
    NoteInputSerializer,
    NoteSerializer,
    OptInInputSerializer,
    ProductQuerySerializer,
    ProductSearchSerializer,
    ReplyInputSerializer,
    SelectInputSerializer,
    SelectionSerializer,
    SocialSettingsSerializer,
    StaffMessageSerializer,
    StartConnectionInputSerializer,
    StartConnectionSerializer,
    StatusInputSerializer,
    SyncResultSerializer,
    TeamMemberSerializer,
    ThreadQuerySerializer,
    ThreadSerializer,
    VisitorMessageSerializer,
    WhatsAppTemplateSerializer,
    connection_card,
    message_payload,
)

INBOX_TAG = ["manage-inbox"]
SOCIAL_TAG = ["manage-social"]
CHAT_TAG = ["chat"]
WEBHOOK_TAG = ["webhooks"]
CanMessage = HasStaffPermission.of(Permission.MESSAGES_MANAGE)
CanSettings = HasStaffPermission.of(Permission.STORE_SETTINGS)
MAX_WEBHOOK_BYTES = 1024 * 1024


def _data(serializer: Any) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


# --- Inbox ----------------------------------------------------------------------------------------


class InboxView(StaffView):
    permission_classes: tuple[type[BasePermission], ...] = (IsAuthenticated, CanMessage)

    def conversation(self, conversation_id: uuid.UUID) -> Conversation:
        return found_in(
            Conversation.objects.select_related("participant", "assignee", "connection"),
            pk=conversation_id,
        )

    def can(self, permission: str) -> bool:
        return permission in self.staff.permissions


def _encode_cursor(row: Conversation) -> str:
    activity: datetime = row.activity  # type: ignore[attr-defined]
    raw = json.dumps([activity.isoformat(), str(row.pk)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        when, pk = json.loads(base64.urlsafe_b64decode(padded.encode()))
        parsed = parse_datetime(str(when))
        if parsed is None:
            raise ValueError
        return parsed, uuid.UUID(str(pk))
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise ValidationError({"cursor": ["This page link is not valid."]}) from None


class ConversationListView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_conversations",
        tags=INBOX_TAG,
        parameters=[InboxQuerySerializer],
        responses={200: ConversationPageSerializer},
    )
    def get(self, request: Request) -> Response:
        params = _data(InboxQuerySerializer(data=request.query_params))
        rows = Conversation.objects.select_related("participant", "assignee", "connection")
        status_value = params.get("status") or "active"
        if status_value == "active":
            rows = rows.filter(status__in=ACTIVE_STATUSES)
        else:
            rows = rows.filter(status=status_value)
        if params.get("channel"):
            rows = rows.filter(channel=params["channel"])
        view = params["view"]
        if view == "unassigned":
            rows = rows.filter(assignee__isnull=True)
        elif view == "mine":
            rows = rows.filter(assignee=self.user)
        text = (params.get("q") or "").strip()
        if text:
            orders = ConversationOrderLink.objects.filter(
                conversation=OuterRef("pk"), order__number__iexact=text
            )
            rows = rows.filter(
                Q(participant__display_name__icontains=text)
                | Q(participant__phone__icontains=text)
                | Exists(orders)
            )
        read = ConversationReadState.objects.filter(
            conversation=OuterRef("pk"), user=self.user
        ).values("inbound_read")[:1]
        annotated: Any = rows.annotate(
            read_count=Coalesce(Subquery(read, output_field=IntegerField()), Value(0)),
            activity=Coalesce("last_message_at", "created_at"),
        ).annotate(unread=F("inbound_count") - F("read_count"))
        rows = annotated
        if view == "unread":
            rows = rows.filter(unread__gt=0)
        if params.get("cursor"):
            when, pk = _decode_cursor(params["cursor"])
            rows = rows.filter(Q(activity__lt=when) | Q(activity=when, pk__lt=pk))
        limit = params["limit"]
        page = list(rows.order_by("-activity", "-pk")[: limit + 1])
        more = len(page) > limit
        page = page[:limit]
        return Response(
            {
                "results": ConversationSummarySerializer(page, many=True).data,
                "next_cursor": _encode_cursor(page[-1]) if more and page else None,
                "unread_conversations": analytics.unread_conversations(self.user),
            }
        )


class ConversationDetailView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_conversation",
        tags=INBOX_TAG,
        responses={200: ConversationDetailSerializer},
    )
    def get(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        return Response(_detail(conversation, self))


def _detail(conversation: Conversation, view: InboxView) -> dict[str, Any]:
    participant = conversation.participant
    connection = conversation.connection
    rules = policy.reply_policy(conversation, connection, participant)
    can_orders = view.can(Permission.ORDERS_MANAGE)
    links = (
        ConversationOrderLink.objects.filter(conversation=conversation)
        .select_related("order")
        .order_by("-created_at")
        if can_orders
        else ConversationOrderLink.objects.none()
    )
    cases: list[AfterSalesCase] = []
    if can_orders and participant.customer_id is not None:
        cases = list(
            AfterSalesCase.objects.filter(customer_id=participant.customer_id).order_by(
                "-created_at"
            )[:10]
        )
    recent = list(
        Conversation.objects.filter(participant=participant)
        .exclude(pk=conversation.pk)
        .select_related("participant", "assignee", "connection")
        .order_by("-created_at")[:5]
    )
    conversation.unread = services.unread_for(view.user, conversation)  # type: ignore[attr-defined]
    return {
        "conversation": ConversationSummarySerializer(conversation).data,
        "participant": {
            "id": participant.pk,
            "display_name": participant.display_name,
            "channel": participant.channel,
            "phone": participant.phone,
            "customer_id": participant.customer_id if view.can(Permission.CUSTOMERS_VIEW) else None,
            "customer_linked_at": participant.customer_linked_at,
            "template_opt_in_at": participant.template_opt_in_at,
            "opted_out_at": participant.opted_out_at,
        },
        "policy": {
            "can_reply": rules.can_reply,
            "reason": rules.reason,
            "message": rules.message,
            "tag": rules.tag,
            "window_ends_at": rules.window_ends_at,
            "templates_allowed": rules.templates_allowed,
            "template_reason": rules.template_reason,
        },
        "orders": LinkedOrderSerializer(links, many=True).data,
        "cases": [
            {"id": case.pk, "kind": case.kind, "status": case.status, "created_at": case.created_at}
            for case in cases
        ],
        "recent": ConversationSummarySerializer(recent, many=True).data,
        "can_create_orders": can_orders,
        "visitor_read_seq": conversation.visitor_read_seq,
    }


class ThreadView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_thread",
        tags=INBOX_TAG,
        parameters=[ThreadQuerySerializer],
        responses={200: ThreadSerializer},
    )
    def get(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        params = _data(ThreadQuerySerializer(data=request.query_params))
        limit = params["limit"]
        rows = Message.objects.filter(conversation=conversation).select_related(
            "sender_user", "attachment"
        )
        if "after_seq" in params:
            page = list(rows.filter(seq__gt=params["after_seq"]).order_by("seq")[: limit + 1])
            more = len(page) > limit
            page = page[:limit]
        else:
            if "before_seq" in params:
                rows = rows.filter(seq__lt=params["before_seq"])
            page = list(rows.order_by("-seq")[: limit + 1])
            more = len(page) > limit
            page = list(reversed(page[:limit]))
        notes = (
            ConversationNote.objects.filter(conversation=conversation)
            .select_related("author")
            .order_by("-created_at")[:200]
        )
        events = (
            ConversationEvent.objects.filter(conversation=conversation)
            .select_related("actor", "assignee", "order")
            .order_by("-created_at")[:200]
        )
        return Response(
            {
                "messages": StaffMessageSerializer(page, many=True, context={"staff": True}).data,
                "notes": NoteSerializer(reversed(list(notes)), many=True).data,
                "events": EventSerializer(reversed(list(events)), many=True).data,
                "has_more": more,
            }
        )


class ReplyView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_reply",
        tags=INBOX_TAG,
        request=ReplyInputSerializer,
        responses={201: StaffMessageSerializer, 200: StaffMessageSerializer},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        values = _data(ReplyInputSerializer(data=request.data))
        outbound = _outbound(conversation, values)
        message, created = services.staff_send(
            conversation=conversation,
            actor=self.user,
            outbound=outbound,
            client_key=values["client_key"],
        )
        return Response(
            message_payload(message, staff=True),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _outbound(conversation: Conversation, values: dict[str, Any]) -> services.Outbound:
    chosen = [key for key in ("product_variant", "template") if values.get(key)]
    if len(chosen) > 1:
        raise ValidationError({"non_field": ["Send a product or a template, not both."]})
    if values.get("product_variant"):
        variant, snapshot, text = products.snapshot(values["product_variant"], conversation.channel)
        return services.Outbound(
            kind=MessageKind.PRODUCT,
            body=text,
            product_variant=variant,
            product_snapshot=snapshot,
        )
    if values.get("template"):
        template = WhatsAppTemplate.objects.filter(
            pk=values["template"], connection_id=conversation.connection_id
        ).first()
        if template is None or not (
            template.status == "APPROVED" and template.category in policy.TEMPLATE_CATEGORIES
        ):
            raise ValidationError(
                {"template": ["Choose an approved utility template for this number."]}
            )
        parameters = [
            services.clean_text(p, limit=200, field="template_parameters")
            for p in values.get("template_parameters") or []
        ]
        if len(parameters) != template.parameter_count:
            raise ValidationError(
                {"template_parameters": [f"This template needs {template.parameter_count} values."]}
            )
        rendered = template.body_text
        for index, value in enumerate(parameters, start=1):
            rendered = rendered.replace(f"{{{{{index}}}}}", value)
        return services.Outbound(
            kind=MessageKind.TEMPLATE,
            body=rendered[:4096],
            template_name=template.name,
            template_language=template.language,
            template_parameters=parameters,
        )
    return services.Outbound(body=services.clean_text(values.get("body")))


class NoteView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_note",
        tags=INBOX_TAG,
        request=NoteInputSerializer,
        responses={201: NoteSerializer},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        values = _data(NoteInputSerializer(data=request.data))
        note = services.add_note(conversation=conversation, actor=self.user, body=values["body"])
        return Response(NoteSerializer(note).data, status=status.HTTP_201_CREATED)


class AssignView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_assign",
        tags=INBOX_TAG,
        request=AssignInputSerializer,
        responses={200: ConversationSummarySerializer},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        values = _data(AssignInputSerializer(data=request.data))
        updated = services.assign(
            conversation=conversation,
            actor=self.user,
            assignee_id=values["assignee"],
            expected_version=values["version"],
        )
        return Response(ConversationSummarySerializer(self.conversation(updated.pk)).data)


class StatusView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_status",
        tags=INBOX_TAG,
        request=StatusInputSerializer,
        responses={200: ConversationSummarySerializer},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        values = _data(StatusInputSerializer(data=request.data))
        updated = services.change_status(
            conversation=conversation,
            actor=self.user,
            status=values["status"],
            expected_version=values["version"],
        )
        return Response(ConversationSummarySerializer(self.conversation(updated.pk)).data)


class ReadView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_mark_read", tags=INBOX_TAG, request=None, responses={204: None}
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        services.mark_read(conversation=self.conversation(conversation_id), user=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrderInboxView(InboxView):
    """Order actions need ``orders.manage`` in addition to ``messages.manage``."""

    permission_classes = (
        IsAuthenticated,
        CanMessage,
        HasStaffPermission.of(Permission.ORDERS_MANAGE),
    )


class ConversationOrderView(OrderInboxView):
    @extend_schema(
        operation_id="manage_inbox_create_order",
        tags=INBOX_TAG,
        request=ConversationOrderInputSerializer,
        responses={201: MerchantOrderDetailSerializer, 200: MerchantOrderDetailSerializer},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        values = _data(ConversationOrderInputSerializer(data=request.data))
        placement = services.create_order(conversation=conversation, data=values, actor=self.user)
        return Response(
            MerchantOrderDetailSerializer(placement.order).data,
            status=status.HTTP_200_OK if placement.replayed else status.HTTP_201_CREATED,
        )


class LinkOrderView(OrderInboxView):
    @extend_schema(
        operation_id="manage_inbox_link_order",
        tags=INBOX_TAG,
        request=LinkOrderInputSerializer,
        responses={200: LinkedOrderSerializer},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        values = _data(LinkOrderInputSerializer(data=request.data))
        order = Order.objects.filter(pk=values["order"]).first()
        if order is None:
            raise ValidationError({"order": ["This order does not exist in the store."]})
        link = services.link_order(conversation=conversation, order=order, actor=self.user)
        link = ConversationOrderLink.objects.select_related("order").get(pk=link.pk)
        return Response(LinkedOrderSerializer(link).data)


class OptInView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_opt_in",
        tags=INBOX_TAG,
        request=OptInInputSerializer,
        responses={204: None},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        values = _data(OptInInputSerializer(data=request.data))
        services.record_template_opt_in(
            conversation=self.conversation(conversation_id),
            actor=self.user,
            confirmed=values["confirmed"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class OptOutView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_opt_out", tags=INBOX_TAG, request=None, responses={204: None}
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        services.record_opt_out(conversation=self.conversation(conversation_id), actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ResendView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_resend",
        tags=INBOX_TAG,
        request=None,
        responses={200: StaffMessageSerializer},
    )
    def post(self, request: Request, message_id: uuid.UUID) -> Response:
        message = found(Message, pk=message_id)
        updated = tasks.resend(message=message, actor=self.user)
        return Response(message_payload(updated, staff=True))


class StaffAttachmentUploadView(InboxView):
    """Website chat only: staff send an image or PDF to the visitor."""

    parser_classes = (MultiPartParser,)

    @extend_schema(
        operation_id="manage_inbox_attachment_upload",
        tags=INBOX_TAG,
        request={"multipart/form-data": {"type": "object"}},
        responses={201: StaffMessageSerializer, 200: StaffMessageSerializer},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        conversation = self.conversation(conversation_id)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": ["Choose a file."]})
        message, created = attachments.staff_upload(
            conversation=conversation,
            actor=self.user,
            stream=upload,
            filename=str(upload.name or ""),
            client_key=request.data.get("client_key"),
        )
        return Response(
            message_payload(message, staff=True),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class StaffAttachmentView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_attachment",
        tags=INBOX_TAG,
        responses={(200, "application/octet-stream"): OpenApiResponse(description="The file.")},
    )
    def get(self, request: Request, attachment_id: uuid.UUID) -> FileResponse:
        row = found(MessageAttachment, pk=attachment_id)
        return attachments.file_response(row)


class ProductSearchView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_products",
        tags=INBOX_TAG,
        parameters=[ProductQuerySerializer],
        responses={200: ProductSearchSerializer},
    )
    def get(self, request: Request) -> Response:
        text = _data(ProductQuerySerializer(data=request.query_params)).get("q") or ""
        return Response(
            {
                "results": InboxProductSerializer(products.search(text), many=True).data,
                "links_available": products.links_available(),
            }
        )


class TeamView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_team",
        tags=INBOX_TAG,
        responses={200: TeamMemberSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        rows = StaffMembership.objects.filter(status=MembershipStatus.ACTIVE).select_related("user")
        members = [
            {"id": row.user.pk, "name": row.user.full_name, "role": row.role}
            for row in rows
            if row.user.is_active and Permission.MESSAGES_MANAGE in permissions_for(row.role)
        ]
        members.sort(key=lambda item: (str(item["name"]).lower(), str(item["id"])))
        return Response(TeamMemberSerializer(members, many=True).data)


class TemplatesView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_templates",
        tags=INBOX_TAG,
        responses={200: WhatsAppTemplateSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        connection = connections.live(ChannelType.WHATSAPP)
        rows = (
            WhatsAppTemplate.objects.filter(connection=connection).order_by("name", "language")
            if connection
            else WhatsAppTemplate.objects.none()
        )
        return Response(WhatsAppTemplateSerializer(rows, many=True).data)


class AnalyticsView(InboxView):
    @extend_schema(
        operation_id="manage_inbox_analytics",
        tags=INBOX_TAG,
        parameters=[AnalyticsQuerySerializer],
        responses={200: InboxAnalyticsSerializer},
    )
    def get(self, request: Request) -> Response:
        params = _data(AnalyticsQuerySerializer(data=request.query_params))
        data = analytics.report(
            days=int(params["days"]),
            viewer=self.user,
            include_value=self.can(Permission.REPORTS_VIEW),
        )
        return Response(InboxAnalyticsSerializer(data).data)


# --- Social channel settings ----------------------------------------------------------------------


class SocialView(StaffView):
    permission_classes = (IsAuthenticated, CanSettings)


class SensitiveSocialView(SocialView):
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        sessions.require_recent_authentication(self.staff.session)


def _settings_payload() -> dict[str, Any]:
    row = livechat.current()
    state = livechat.availability(row)
    zone = StoreSettings.objects.values_list("timezone", flat=True).first() or "UTC"
    return {
        "live_chat_enabled": livechat.enabled(),
        "live_chat": {field: getattr(row, field) for field in livechat.SETTINGS_FIELDS},
        "agents_online": state.agents_online,
        "within_hours": state.within_hours,
        "timezone": zone,
        "connections": [
            connection_card(channel, connections.live(channel)) for channel in EXTERNAL_CHANNELS
        ],
        "simulator_available": bool(settings.MATECART_CHANNEL_SIMULATOR),
        "webhook_url": f"{settings.MATECART_PUBLIC_BASE_URL}/api/webhooks/meta/",
        "human_agent_approved": bool(settings.META_HUMAN_AGENT_APPROVED),
        "product_links_available": products.links_available(),
    }


class SocialSettingsView(SocialView):
    @extend_schema(
        operation_id="manage_social_settings",
        tags=SOCIAL_TAG,
        responses={200: SocialSettingsSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(SocialSettingsSerializer(_settings_payload()).data)


class LiveChatSettingsView(SocialView):
    @extend_schema(
        operation_id="manage_social_live_chat",
        tags=SOCIAL_TAG,
        request=LiveChatSettingsSerializer,
        responses={200: SocialSettingsSerializer},
    )
    def put(self, request: Request) -> Response:
        values = _data(LiveChatSettingsSerializer(data=request.data))
        livechat.save(data=values, actor=self.user)
        return Response(SocialSettingsSerializer(_settings_payload()).data)


class StartConnectionView(SensitiveSocialView):
    @extend_schema(
        operation_id="manage_social_connect_start",
        tags=SOCIAL_TAG,
        request=StartConnectionInputSerializer,
        responses={200: StartConnectionSerializer},
    )
    def post(self, request: Request) -> Response:
        values = _data(StartConnectionInputSerializer(data=request.data))
        url = connections.start(
            channel=values["channel"], actor=self.user, session=self.staff.session
        )
        return Response({"authorize_url": url})


class OAuthCallbackView(SocialView):
    """Meta's redirect target. Answers with a redirect to the dashboard, never with data."""

    @extend_schema(
        operation_id="manage_social_oauth_callback", tags=SOCIAL_TAG, responses={302: None}
    )
    def get(self, request: Request) -> HttpResponse:
        target = connections.callback(
            state=str(request.query_params.get("state") or "")[:100],
            code=str(request.query_params.get("code") or "")[:2000],
            error=str(request.query_params.get("error") or "")[:100],
            actor=self.user,
            session=self.staff.session,
        )
        return HttpResponseRedirect(target)


class SelectionView(SocialView):
    @extend_schema(
        operation_id="manage_social_selection",
        tags=SOCIAL_TAG,
        responses={200: SelectionSerializer},
    )
    def get(self, request: Request) -> Response:
        data = connections.choices(
            selection=str(request.query_params.get("selection") or "")[:100],
            actor=self.user,
            session=self.staff.session,
        )
        return Response(SelectionSerializer(data).data)


class SelectView(SensitiveSocialView):
    @extend_schema(
        operation_id="manage_social_select",
        tags=SOCIAL_TAG,
        request=SelectInputSerializer,
        responses={200: ChannelConnectionSerializer},
    )
    def post(self, request: Request) -> Response:
        values = _data(SelectInputSerializer(data=request.data))
        row = connections.select(
            selection=values["selection"],
            account_id=values["account"],
            actor=self.user,
            session=self.staff.session,
        )
        return Response(ChannelConnectionSerializer(connection_card(row.channel, row)).data)


class _ConnectionAction(SocialView):
    def connection(self, connection_id: uuid.UUID) -> ChannelConnection:
        return found(ChannelConnection, pk=connection_id, disconnected_at__isnull=True)


class VerifyConnectionView(_ConnectionAction):
    @extend_schema(
        operation_id="manage_social_verify",
        tags=SOCIAL_TAG,
        request=None,
        responses={200: ChannelConnectionSerializer},
    )
    def post(self, request: Request, connection_id: uuid.UUID) -> Response:
        row = connections.verify(connection=self.connection(connection_id), actor=self.user)
        return Response(ChannelConnectionSerializer(connection_card(row.channel, row)).data)


class EnableConnectionView(_ConnectionAction):
    @extend_schema(
        operation_id="manage_social_enable",
        tags=SOCIAL_TAG,
        request=EnabledInputSerializer,
        responses={200: ChannelConnectionSerializer},
    )
    def post(self, request: Request, connection_id: uuid.UUID) -> Response:
        values = _data(EnabledInputSerializer(data=request.data))
        row = connections.set_enabled(
            connection=self.connection(connection_id), enabled=values["enabled"], actor=self.user
        )
        return Response(ChannelConnectionSerializer(connection_card(row.channel, row)).data)


class DisconnectView(_ConnectionAction):
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        sessions.require_recent_authentication(self.staff.session)

    @extend_schema(
        operation_id="manage_social_disconnect",
        tags=SOCIAL_TAG,
        request=None,
        responses={200: ChannelConnectionSerializer},
    )
    def post(self, request: Request, connection_id: uuid.UUID) -> Response:
        row = connections.disconnect(connection=self.connection(connection_id), actor=self.user)
        return Response(ChannelConnectionSerializer(connection_card(row.channel, None)).data)


class SyncTemplatesView(_ConnectionAction):
    @extend_schema(
        operation_id="manage_social_sync_templates",
        tags=SOCIAL_TAG,
        request=None,
        responses={200: SyncResultSerializer},
    )
    def post(self, request: Request, connection_id: uuid.UUID) -> Response:
        count = connections.sync_templates(
            connection=self.connection(connection_id), actor=self.user
        )
        return Response({"count": count})


class SimulatorView(SocialView):
    @extend_schema(
        operation_id="manage_social_simulator",
        tags=SOCIAL_TAG,
        request=StartConnectionInputSerializer,
        responses={200: ChannelConnectionSerializer},
    )
    def post(self, request: Request) -> Response:
        values = _data(StartConnectionInputSerializer(data=request.data))
        row = connections.connect_simulator(channel=values["channel"], actor=self.user)
        return Response(ChannelConnectionSerializer(connection_card(row.channel, row)).data)


# --- Website chat (visitor) -----------------------------------------------------------------------


class ChatView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (AllowAny,)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        livechat.require_enabled()

    def customer(self, request: Request) -> User | None:
        return request.user if isinstance(request.user, User) else None

    def visitor(self, request: Request, *, create: bool = False) -> livechat.Visitor:
        return livechat.resolve_visitor(
            guest_token=request.COOKIES.get(livechat.COOKIE_NAME),
            customer=self.customer(request),
            create=create,
        )


def _locale(request: Request) -> str:
    return "bn" if request.query_params.get("locale") == "bn" else "en"


class ChatConfigView(ChatView):
    @extend_schema(
        operation_id="chat_config",
        tags=CHAT_TAG,
        parameters=[ChatLocaleQuerySerializer],
        responses={200: ChatStateSerializer},
    )
    def get(self, request: Request) -> Response:
        params = _data(ChatLocaleQuerySerializer(data=request.query_params))
        visitor = self.visitor(request)
        conversation = (
            services.latest_conversation(visitor.participant) if visitor.participant else None
        )
        messages: list[Message] = []
        if conversation is not None:
            rows = Message.objects.filter(conversation=conversation).select_related(
                "sender_user", "attachment"
            )
            if "after_seq" in params:
                messages = list(rows.filter(seq__gt=params["after_seq"]).order_by("seq")[:200])
            else:
                messages = list(reversed(list(rows.order_by("-seq")[:100])))
        guest = livechat._guest(request.COOKIES.get(livechat.COOKIE_NAME))
        return Response(
            {
                "config": livechat.public_settings(_locale(request)),
                "conversation_status": conversation.status if conversation else None,
                "messages": VisitorMessageSerializer(
                    messages, many=True, context={"staff": False}
                ).data,
                "signed_in": visitor.customer is not None,
                "guest_chat_linkable": bool(
                    visitor.customer is not None and guest is not None and guest.customer_id is None
                ),
            }
        )


class ChatSendView(ChatView):
    @extend_schema(
        operation_id="chat_send",
        tags=CHAT_TAG,
        request=ChatSendInputSerializer,
        responses={201: VisitorMessageSerializer, 200: VisitorMessageSerializer},
    )
    def post(self, request: Request) -> Response:
        values = _data(ChatSendInputSerializer(data=request.data))
        visitor = self.visitor(request, create=True)
        if visitor.participant is None:
            raise livechat.ChatUnavailable()
        message, created = services.visitor_send(
            participant=visitor.participant, body=values["body"], client_key=values["client_key"]
        )
        response = Response(
            message_payload(message, staff=False),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
        if visitor.new_token:
            livechat.set_cookie(response, visitor.new_token)
        return response


class ChatUploadView(ChatView):
    parser_classes = (MultiPartParser,)

    @extend_schema(
        operation_id="chat_upload",
        tags=CHAT_TAG,
        request={"multipart/form-data": {"type": "object"}},
        responses={201: VisitorMessageSerializer, 200: VisitorMessageSerializer},
    )
    def post(self, request: Request) -> Response:
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": ["Choose a file."]})
        visitor = self.visitor(request, create=True)
        if visitor.participant is None:
            raise livechat.ChatUnavailable()
        message, created = attachments.visitor_upload(
            participant=visitor.participant,
            stream=upload,
            filename=str(upload.name or ""),
            client_key=request.data.get("client_key"),
        )
        response = Response(
            message_payload(message, staff=False),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
        if visitor.new_token:
            livechat.set_cookie(response, visitor.new_token)
        return response


class ChatAttachmentView(ChatView):
    @extend_schema(
        operation_id="chat_attachment",
        tags=CHAT_TAG,
        responses={(200, "application/octet-stream"): OpenApiResponse(description="The file.")},
    )
    def get(self, request: Request, attachment_id: uuid.UUID) -> FileResponse:
        visitor = self.visitor(request)
        if visitor.participant is None:
            raise Http404
        row = (
            MessageAttachment.objects.select_related("message")
            .filter(pk=attachment_id, message__conversation__participant=visitor.participant)
            .first()
        )
        if row is None:
            raise Http404
        return attachments.file_response(row)


class ChatLinkAccountView(ChatView):
    @extend_schema(
        operation_id="chat_link_account", tags=CHAT_TAG, request=None, responses={204: None}
    )
    def post(self, request: Request) -> Response:
        customer = self.customer(request)
        if customer is None:
            raise ConflictError("Sign in to link this chat to your account.", "sign_in_required")
        livechat.link_guest_to_customer(
            guest_token=request.COOKIES.get(livechat.COOKIE_NAME), customer=customer
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Webhook --------------------------------------------------------------------------------------


class MetaWebhookView(APIView):
    """Meta's webhook for Pages (Messenger), Instagram and WhatsApp Business Accounts."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="webhook_meta_verify", tags=WEBHOOK_TAG, responses={200: str})
    def get(self, request: Request) -> HttpResponse:
        import hmac

        expected = settings.META_WEBHOOK_VERIFY_TOKEN
        token = str(request.query_params.get("hub.verify_token") or "")
        if (
            not expected
            or request.query_params.get("hub.mode") != "subscribe"
            or not hmac.compare_digest(expected.encode(), token.encode())
        ):
            return HttpResponse(status=403)
        challenge = str(request.query_params.get("hub.challenge") or "")[:200]
        return HttpResponse(challenge, content_type="text/plain")

    @extend_schema(
        operation_id="webhook_meta", tags=WEBHOOK_TAG, request=None, responses={200: None}
    )
    def post(self, request: Request) -> HttpResponse:
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > MAX_WEBHOOK_BYTES:
            return HttpResponse(status=413)
        raw = request._request.body  # raw bytes: the signature covers them exactly
        if len(raw) > MAX_WEBHOOK_BYTES:
            return HttpResponse(status=413)
        if not meta.valid_signature(raw, request.META.get("HTTP_X_HUB_SIGNATURE_256")):
            return HttpResponse(status=401)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return HttpResponse(status=400)
        if not isinstance(payload, dict):
            return HttpResponse(status=400)
        result = inbound.process(payload)
        return HttpResponse(
            json.dumps({"stored": result.stored, "duplicates": result.duplicates}),
            content_type="application/json",
        )
