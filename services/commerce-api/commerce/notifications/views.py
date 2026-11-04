"""Notification HTTP endpoints (Phase 13).

Merchant (staff realm, CSRF):
- inbox: every staff member, only their own rows and only rows whose permission they still hold;
- email connections, SMS, templates, delivery history and retries: ``store.settings``
  (Owner, Manager); credential and transport changes also need a recent sign-in;
- own email preferences: every staff member.
Customer (customer realm): own inbox and preferences. Public: signed unsubscribe links.
Responses never contain passwords, tokens, codes or message bodies of other users.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.audit.services import record_audit_event
from commerce.core import ratelimit
from commerce.core.delivery import sms_provider
from commerce.core.http import client_ip
from commerce.core.pagination import PageQuerySerializer, paginate, paginated
from commerce.identity import sessions
from commerce.identity.auth import (
    CsrfProtectedAPIView,
    CustomerSessionAuthentication,
    HasStaffPermission,
)
from commerce.identity.models import OtpChannel, User
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView

from . import connections, providers, rendering, services
from .models import (
    Category,
    Channel,
    ConnectionStatus,
    DeliveryAttempt,
    MailProvider,
    Notification,
    NotificationStatus,
    NotificationTemplate,
    SenderStatus,
    SmtpSecurity,
)
from .registry import BY_KEY, CUSTOMER_TOPICS, KINDS, STAFF_TOPICS

TAG = ["manage-notifications"]
ACCOUNT_TAG = ["account-notifications"]
CanChangeSettings = HasStaffPermission.of(Permission.STORE_SETTINGS)
LOCALES = ("en", "bn")
CHANNELS = Channel.choices
STATUSES = NotificationStatus.choices
CATEGORIES = Category.choices


def _valid(serializer: serializers.Serializer[Any]) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


# --- Shapes ---------------------------------------------------------------------------------------


class InboxItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    category = serializers.ChoiceField(choices=CATEGORIES)
    title = serializers.CharField(source="subject")
    body = serializers.CharField(source="body_text")
    link = serializers.CharField(source="link_path")
    created_at = serializers.DateTimeField()
    read_at = serializers.DateTimeField(allow_null=True)


class InboxQuerySerializer(PageQuerySerializer):
    unread = serializers.BooleanField(required=False, allow_null=True, default=None)
    category = serializers.ChoiceField(choices=CATEGORIES, required=False)


class ReadSerializer(serializers.Serializer[Any]):
    ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=200)
    all = serializers.BooleanField(default=False)


class UnreadSerializer(serializers.Serializer[Any]):
    unread = serializers.IntegerField()


class DeliverySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    category = serializers.ChoiceField(choices=CATEGORIES)
    channel = serializers.ChoiceField(choices=CHANNELS)
    audience = serializers.CharField()
    recipient = serializers.SerializerMethodField()
    subject = serializers.CharField()
    status = serializers.ChoiceField(choices=STATUSES)
    attempts = serializers.IntegerField()
    provider = serializers.CharField()
    failure_code = serializers.CharField()
    created_at = serializers.DateTimeField()
    last_attempt_at = serializers.DateTimeField(allow_null=True)
    sent_at = serializers.DateTimeField(allow_null=True)
    can_retry = serializers.SerializerMethodField()

    def get_recipient(self, row: Notification) -> str:
        """Masked: enough to recognise, not to harvest (``r***@example.com``, ``+880***78``)."""
        address = row.recipient_address
        if "@" in address:
            local, _, domain = address.partition("@")
            return f"{local[:1]}***@{domain}"
        if len(address) > 6:
            return f"{address[:4]}***{address[-2:]}"
        return address

    def get_can_retry(self, row: Notification) -> bool:
        return row.status == NotificationStatus.FAILED and row.content_cleared_at is None


class AttemptSerializer(serializers.Serializer[Any]):
    number = serializers.IntegerField()
    provider = serializers.CharField()
    outcome = serializers.CharField()
    error_code = serializers.CharField()
    next_retry_at = serializers.DateTimeField(allow_null=True)
    occurred_at = serializers.DateTimeField()


class DeliveryQuerySerializer(PageQuerySerializer):
    status = serializers.ChoiceField(choices=STATUSES, required=False)
    channel = serializers.ChoiceField(choices=CHANNELS, required=False)
    kind = serializers.CharField(max_length=60, required=False)
    failed = serializers.BooleanField(required=False, allow_null=True, default=None)


class DeliverySummarySerializer(serializers.Serializer[Any]):
    pending = serializers.IntegerField()
    sent = serializers.IntegerField()
    failed = serializers.IntegerField()
    suppressed = serializers.IntegerField()


class ConnectionSerializer(serializers.Serializer[Any]):
    provider = serializers.ChoiceField(choices=MailProvider.choices)
    is_enabled = serializers.BooleanField()
    status = serializers.ChoiceField(choices=ConnectionStatus.choices)
    display_name = serializers.CharField()
    smtp_host = serializers.CharField()
    smtp_port = serializers.IntegerField(allow_null=True)
    smtp_security = serializers.ChoiceField(choices=SmtpSecurity.choices)
    smtp_username = serializers.CharField()
    smtp_password_set = serializers.BooleanField()
    from_email = serializers.CharField()
    from_name = serializers.CharField()
    reply_to = serializers.CharField()
    sender_verified = serializers.BooleanField()
    sender_code_pending = serializers.BooleanField()
    imap_host = serializers.CharField()
    imap_port = serializers.IntegerField(allow_null=True)
    imap_security = serializers.ChoiceField(choices=SmtpSecurity.choices)
    imap_username = serializers.CharField()
    imap_password_set = serializers.BooleanField()
    imap_status = serializers.ChoiceField(choices=ConnectionStatus.choices)
    imap_verified_at = serializers.DateTimeField(allow_null=True)
    imap_error = serializers.CharField()
    matemail_tenant_id = serializers.CharField()
    matemail_authorization_url = serializers.CharField()
    matemail_request_expires_at = serializers.DateTimeField(allow_null=True)
    matemail_mailbox = serializers.CharField()
    matemail_scopes = serializers.ListField(child=serializers.CharField())
    matemail_token_set = serializers.BooleanField()
    last_verified_at = serializers.DateTimeField(allow_null=True)
    last_error = serializers.CharField()


class EmailSettingsSerializer(serializers.Serializer[Any]):
    active_provider = serializers.CharField(allow_null=True)
    default_sender_available = serializers.BooleanField()
    matemail_available = serializers.BooleanField()
    smtp = ConnectionSerializer()
    matemail = ConnectionSerializer()


class SmtpInputSerializer(serializers.Serializer[Any]):
    display_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    smtp_host = serializers.CharField(max_length=253)
    smtp_port = serializers.IntegerField(min_value=1, max_value=65535)
    smtp_security = serializers.ChoiceField(choices=SmtpSecurity.choices)
    smtp_username = serializers.CharField(max_length=254, required=False, allow_blank=True)
    smtp_password = serializers.CharField(
        max_length=500, required=False, allow_blank=True, write_only=True
    )
    from_email = serializers.EmailField()
    from_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    reply_to = serializers.EmailField(required=False, allow_blank=True)


class ImapInputSerializer(serializers.Serializer[Any]):
    imap_host = serializers.CharField(max_length=253, allow_blank=True)
    imap_port = serializers.IntegerField(min_value=1, max_value=65535, allow_null=True)
    imap_security = serializers.ChoiceField(choices=SmtpSecurity.choices)
    imap_username = serializers.CharField(max_length=254, allow_blank=True)
    imap_password = serializers.CharField(
        max_length=500, required=False, allow_blank=True, write_only=True
    )


class TestInputSerializer(serializers.Serializer[Any]):
    send_to_self = serializers.BooleanField(default=False)


class CodeInputSerializer(serializers.Serializer[Any]):
    code = serializers.RegexField(r"^\s*\d{6}\s*$")


class MateMailStartSerializer(serializers.Serializer[Any]):
    tenant_id = serializers.UUIDField()
    integration_secret = serializers.CharField(min_length=8, max_length=300, write_only=True)


class SelectSerializer(serializers.Serializer[Any]):
    provider = serializers.ChoiceField(choices=MailProvider.choices, allow_null=True)


class SmsBalanceSerializer(serializers.Serializer[Any]):
    allowance = serializers.IntegerField()
    used = serializers.IntegerField()
    remaining = serializers.IntegerField()
    prepaid_credits = serializers.IntegerField()


class SmsSerializer(serializers.Serializer[Any]):
    provider_configured = serializers.BooleanField()
    is_enabled = serializers.BooleanField()
    requested_sender = serializers.CharField()
    approved_sender = serializers.CharField()
    sender_status = serializers.ChoiceField(choices=SenderStatus.choices)
    order_updates = serializers.BooleanField()
    shipping_updates = serializers.BooleanField()
    ready = serializers.BooleanField()
    balance = SmsBalanceSerializer()


class SmsInputSerializer(serializers.Serializer[Any]):
    is_enabled = serializers.BooleanField(required=False)
    requested_sender = serializers.RegexField(
        r"^[A-Za-z0-9 ]{3,11}$|^\+?[0-9]{6,15}$", required=False
    )
    order_updates = serializers.BooleanField(required=False)
    shipping_updates = serializers.BooleanField(required=False)


class TemplateTextSerializer(serializers.Serializer[Any]):
    subject = serializers.CharField(max_length=160, allow_blank=True, required=False)
    heading = serializers.CharField(max_length=120, allow_blank=True, required=False)
    body = serializers.CharField(max_length=4000, allow_blank=True, required=False)
    sms = serializers.CharField(max_length=480, allow_blank=True, required=False)
    in_app_title = serializers.CharField(max_length=120, allow_blank=True, required=False)
    in_app_body = serializers.CharField(max_length=300, allow_blank=True, required=False)


class TemplateInputSerializer(TemplateTextSerializer):
    is_enabled = serializers.BooleanField(default=True)


class TemplateLocaleSerializer(serializers.Serializer[Any]):
    locale = serializers.ChoiceField(choices=LOCALES)
    customized = serializers.BooleanField()
    is_enabled = serializers.BooleanField()
    version = serializers.IntegerField()
    subject = serializers.CharField()
    heading = serializers.CharField()
    body = serializers.CharField()
    sms = serializers.CharField()
    in_app_title = serializers.CharField()
    in_app_body = serializers.CharField()
    default_subject = serializers.CharField()
    default_heading = serializers.CharField()
    default_body = serializers.CharField()
    default_sms = serializers.CharField()


class TemplateSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    title = serializers.CharField()
    category = serializers.ChoiceField(choices=CATEGORIES)
    audience = serializers.CharField()
    variables = serializers.ListField(child=serializers.CharField())
    supports_sms = serializers.BooleanField()
    locales = TemplateLocaleSerializer(many=True)


class TemplatePreviewSerializer(serializers.Serializer[Any]):
    subject = serializers.CharField()
    text = serializers.CharField()
    html = serializers.CharField()
    sms = serializers.CharField()
    sms_segments = serializers.IntegerField()
    in_app_title = serializers.CharField()
    in_app_body = serializers.CharField()


class PreferenceSerializer(serializers.Serializer[Any]):
    topic = serializers.CharField()
    title = serializers.CharField()
    channel = serializers.ChoiceField(choices=CHANNELS)
    enabled = serializers.BooleanField()
    locked = serializers.BooleanField(help_text="Essential messages that cannot be switched off.")


class PreferenceInputSerializer(serializers.Serializer[Any]):
    topic = serializers.CharField(max_length=40)
    channel = serializers.ChoiceField(choices=OtpChannel.choices)
    enabled = serializers.BooleanField()


class UnsubscribeSerializer(serializers.Serializer[Any]):
    token = serializers.CharField(max_length=600)


class UnsubscribeResultSerializer(serializers.Serializer[Any]):
    unsubscribed = serializers.BooleanField()


# --- Inbox (staff and customers) ------------------------------------------------------------------


def _inbox_response(request: Request, rows: Any) -> Response:
    params = _valid(InboxQuerySerializer(data=request.query_params))
    if params.get("unread") is True:
        rows = rows.filter(read_at__isnull=True)
    if params.get("category"):
        rows = rows.filter(category=params["category"])
    items, meta = paginate(rows.order_by("-created_at", "-pk"), request)
    return Response({**meta, "results": InboxItemSerializer(items, many=True).data})


class StaffInboxView(StaffView):
    @extend_schema(
        operation_id="manage_inbox",
        tags=TAG,
        parameters=[InboxQuerySerializer],
        responses={200: paginated(InboxItemSerializer)},
    )
    def get(self, request: Request) -> Response:
        return _inbox_response(request, services.inbox(self.user, self.staff.permissions))


class StaffUnreadView(StaffView):
    @extend_schema(operation_id="manage_inbox_unread", tags=TAG, responses={200: UnreadSerializer})
    def get(self, request: Request) -> Response:
        rows = services.inbox(self.user, self.staff.permissions).filter(read_at__isnull=True)
        return Response({"unread": rows.count()})


class StaffReadView(StaffView):
    @extend_schema(
        operation_id="manage_inbox_read",
        tags=TAG,
        request=ReadSerializer,
        responses={200: UnreadSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _valid(ReadSerializer(data=request.data))
        ids = None if data["all"] else data.get("ids", [])
        services.mark_read(self.user, ids, self.staff.permissions)
        unread = services.inbox(self.user, self.staff.permissions).filter(read_at__isnull=True)
        return Response({"unread": unread.count()})


class CustomerView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @property
    def customer(self) -> User:
        user: User = self.request.user  # type: ignore[assignment]
        return user


class CustomerInboxView(CustomerView):
    @extend_schema(
        operation_id="account_notifications",
        tags=ACCOUNT_TAG,
        parameters=[InboxQuerySerializer],
        responses={200: paginated(InboxItemSerializer)},
    )
    def get(self, request: Request) -> Response:
        return _inbox_response(request, services.inbox(self.customer))


class CustomerReadView(CustomerView):
    @extend_schema(
        operation_id="account_notifications_read",
        tags=ACCOUNT_TAG,
        request=ReadSerializer,
        responses={200: UnreadSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _valid(ReadSerializer(data=request.data))
        services.mark_read(self.customer, None if data["all"] else data.get("ids", []), None)
        unread = services.inbox(self.customer).filter(read_at__isnull=True).count()
        return Response({"unread": unread})


# --- Preferences ----------------------------------------------------------------------------------


def _customer_preferences(user: User) -> list[dict[str, Any]]:
    rows = []
    for topic, spec in CUSTOMER_TOPICS.items():
        for channel in (Channel.EMAIL, Channel.SMS):
            setting = spec.get(channel)
            if setting is False and topic != "marketing":
                continue
            if topic == "marketing":
                enabled = services.marketing_allowed(user, channel)
                locked = False
            elif setting == "essential":
                enabled, locked = True, True
            else:
                enabled = services.preference(user, topic, channel, True)
                locked = False
            rows.append(
                {
                    "topic": topic,
                    "title": spec["label"],
                    "channel": channel,
                    "enabled": enabled,
                    "locked": locked,
                }
            )
    return rows


def _staff_preferences(user: User) -> list[dict[str, Any]]:
    defaults = {kind.topic: kind.email_default for kind in KINDS if kind.audience == "staff"}
    return [
        {
            "topic": topic,
            "title": label,
            "channel": Channel.EMAIL,
            "enabled": services.preference(user, topic, Channel.EMAIL, defaults.get(topic, False)),
            "locked": False,
        }
        for topic, label in STAFF_TOPICS.items()
    ]


class CustomerPreferencesView(CustomerView):
    @extend_schema(
        operation_id="account_notification_preferences",
        tags=ACCOUNT_TAG,
        responses={200: PreferenceSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        return Response(PreferenceSerializer(_customer_preferences(self.customer), many=True).data)

    @extend_schema(
        operation_id="account_notification_preferences_update",
        tags=ACCOUNT_TAG,
        request=PreferenceInputSerializer,
        responses={200: PreferenceSerializer(many=True)},
    )
    def put(self, request: Request) -> Response:
        data = _valid(PreferenceInputSerializer(data=request.data))
        if data["topic"] not in CUSTOMER_TOPICS:
            raise ValidationError({"topic": ["Unknown topic."]})
        services.set_preference(
            user=self.customer,
            topic=data["topic"],
            channel=data["channel"],
            enabled=data["enabled"],
            source="account",
        )
        return Response(PreferenceSerializer(_customer_preferences(self.customer), many=True).data)


class StaffPreferencesView(StaffView):
    @extend_schema(
        operation_id="manage_notification_preferences",
        tags=TAG,
        responses={200: PreferenceSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        return Response(PreferenceSerializer(_staff_preferences(self.user), many=True).data)

    @extend_schema(
        operation_id="manage_notification_preferences_update",
        tags=TAG,
        request=PreferenceInputSerializer,
        responses={200: PreferenceSerializer(many=True)},
    )
    def put(self, request: Request) -> Response:
        data = _valid(PreferenceInputSerializer(data=request.data))
        if data["topic"] not in STAFF_TOPICS or data["channel"] != Channel.EMAIL:
            raise ValidationError({"topic": ["Unknown topic."]})
        services.set_preference(
            user=self.user,
            topic=data["topic"],
            channel=Channel.EMAIL,
            enabled=data["enabled"],
            source="staff",
        )
        return Response(PreferenceSerializer(_staff_preferences(self.user), many=True).data)


class UnsubscribeView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="notifications_unsubscribe",
        tags=ACCOUNT_TAG,
        request=UnsubscribeSerializer,
        responses={200: UnsubscribeResultSerializer},
    )
    def post(self, request: Request) -> Response:
        ratelimit.consume("unsubscribe", client_ip(request), limit=30, window=3600)
        data = _valid(UnsubscribeSerializer(data=request.data))
        # The same answer shape either way; a bad token reveals nothing about any account.
        return Response({"unsubscribed": services.unsubscribe(data["token"])})


# --- Delivery history -----------------------------------------------------------------------------


class SettingsView(StaffView):
    permission_classes = (IsAuthenticated, CanChangeSettings)


class SensitiveSettingsView(SettingsView):
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sessions.require_recent_authentication(self.staff.session)


class DeliveryListView(SettingsView):
    @extend_schema(
        operation_id="manage_deliveries",
        tags=TAG,
        parameters=[DeliveryQuerySerializer],
        responses={200: paginated(DeliverySerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(DeliveryQuerySerializer(data=request.query_params))
        rows = Notification.objects.exclude(channel=Channel.IN_APP)
        if params.get("status"):
            rows = rows.filter(status=params["status"])
        if params.get("failed") is True:
            rows = rows.filter(status=NotificationStatus.FAILED)
        if params.get("channel"):
            rows = rows.filter(channel=params["channel"])
        if params.get("kind"):
            rows = rows.filter(kind=params["kind"])
        items, meta = paginate(rows.order_by("-created_at", "-pk"), request)
        return Response({**meta, "results": DeliverySerializer(items, many=True).data})


class DeliverySummaryView(SettingsView):
    @extend_schema(
        operation_id="manage_delivery_summary", tags=TAG, responses={200: DeliverySummarySerializer}
    )
    def get(self, request: Request) -> Response:
        rows = Notification.objects.exclude(channel=Channel.IN_APP)
        return Response(
            {
                "pending": rows.filter(
                    status__in=(
                        NotificationStatus.PENDING,
                        NotificationStatus.QUEUED,
                        NotificationStatus.PROCESSING,
                    )
                ).count(),
                "sent": rows.filter(
                    status__in=(NotificationStatus.SENT, NotificationStatus.DELIVERED)
                ).count(),
                "failed": rows.filter(status=NotificationStatus.FAILED).count(),
                "suppressed": rows.filter(status=NotificationStatus.SUPPRESSED).count(),
            }
        )


class DeliveryAttemptsView(SettingsView):
    @extend_schema(
        operation_id="manage_delivery_attempts",
        tags=TAG,
        responses={200: AttemptSerializer(many=True)},
    )
    def get(self, request: Request, notification_id: Any) -> Response:
        row = (
            Notification.objects.filter(pk=notification_id).exclude(channel=Channel.IN_APP).first()
        )
        if row is None:
            raise NotFound("Notification not found.")
        attempts = DeliveryAttempt.objects.filter(notification=row).order_by("number")
        return Response(AttemptSerializer(attempts, many=True).data)


class DeliveryRetryView(SettingsView):
    @extend_schema(
        operation_id="manage_delivery_retry",
        tags=TAG,
        request=None,
        responses={200: DeliverySerializer},
    )
    def post(self, request: Request, notification_id: Any) -> Response:
        row = services.manual_retry(notification_id=notification_id, actor=self.user)
        return Response(DeliverySerializer(row).data)


# --- Email connections ----------------------------------------------------------------------------


def _email_payload() -> dict[str, Any]:
    smtp = connections.connection(MailProvider.SMTP)
    matemail = connections.connection(MailProvider.MATEMAIL)
    active = connections.active()
    return {
        "active_provider": active.provider if active is not None else None,
        "default_sender_available": providers.default_sender_available(),
        "matemail_available": providers.matemail_available(),
        "smtp": connections.public_state(smtp),
        "matemail": connections.public_state(matemail),
    }


class EmailSettingsView(SettingsView):
    @extend_schema(
        operation_id="manage_email_settings", tags=TAG, responses={200: EmailSettingsSerializer}
    )
    def get(self, request: Request) -> Response:
        return Response(EmailSettingsSerializer(_email_payload()).data)


Handler = Callable[..., Response]


def _email_action(
    operation_id: str, request_serializer: Any = None
) -> Callable[[Handler], Handler]:
    return extend_schema(
        operation_id=operation_id,
        tags=TAG,
        request=request_serializer,
        responses={200: EmailSettingsSerializer},
    )


class SmtpView(SensitiveSettingsView):
    @_email_action("manage_email_smtp_update", SmtpInputSerializer)
    def put(self, request: Request) -> Response:
        data = _valid(SmtpInputSerializer(data=request.data))
        connections.save_smtp(data=data, actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class SmtpTestView(SettingsView):
    @_email_action("manage_email_smtp_test", TestInputSerializer)
    def post(self, request: Request) -> Response:
        data = _valid(TestInputSerializer(data=request.data))
        connections.test_smtp(actor=self.user, send_to_self=data["send_to_self"])
        return Response(EmailSettingsSerializer(_email_payload()).data)


class SenderCodeView(SettingsView):
    @_email_action("manage_email_sender_code")
    def post(self, request: Request) -> Response:
        connections.send_sender_code(actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class SenderConfirmView(SettingsView):
    @_email_action("manage_email_sender_confirm", CodeInputSerializer)
    def post(self, request: Request) -> Response:
        data = _valid(CodeInputSerializer(data=request.data))
        connections.confirm_sender(code=data["code"], actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class ImapView(SensitiveSettingsView):
    @_email_action("manage_email_imap_update", ImapInputSerializer)
    def put(self, request: Request) -> Response:
        data = _valid(ImapInputSerializer(data=request.data))
        connections.save_imap(data=data, actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class ImapVerifyView(SettingsView):
    @_email_action("manage_email_imap_verify")
    def post(self, request: Request) -> Response:
        connections.verify_imap(actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class MateMailStartView(SensitiveSettingsView):
    @_email_action("manage_email_matemail_start", MateMailStartSerializer)
    def post(self, request: Request) -> Response:
        data = _valid(MateMailStartSerializer(data=request.data))
        connections.matemail_start(
            tenant_id=str(data["tenant_id"]), secret=data["integration_secret"], actor=self.user
        )
        return Response(EmailSettingsSerializer(_email_payload()).data)


class MateMailCheckView(SettingsView):
    @_email_action("manage_email_matemail_check")
    def post(self, request: Request) -> Response:
        connections.matemail_check(actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class MateMailVerifyView(SettingsView):
    @_email_action("manage_email_matemail_verify")
    def post(self, request: Request) -> Response:
        connections.matemail_verify(actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class MateMailDisconnectView(SensitiveSettingsView):
    @_email_action("manage_email_matemail_disconnect")
    def post(self, request: Request) -> Response:
        connections.matemail_disconnect(actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


class SelectProviderView(SensitiveSettingsView):
    @_email_action("manage_email_select", SelectSerializer)
    def post(self, request: Request) -> Response:
        data = _valid(SelectSerializer(data=request.data))
        connections.select(provider=data["provider"], actor=self.user)
        return Response(EmailSettingsSerializer(_email_payload()).data)


# --- SMS ------------------------------------------------------------------------------------------


def _sms_payload() -> dict[str, Any]:
    row = services.sms_settings()
    return {
        "provider_configured": sms_provider() is not None,
        "is_enabled": row.is_enabled,
        "requested_sender": row.requested_sender,
        "approved_sender": row.approved_sender,
        "sender_status": row.sender_status,
        "order_updates": row.order_updates,
        "shipping_updates": row.shipping_updates,
        "ready": services.sms_ready(),
        "balance": services.sms_balance(),
    }


class SmsView(SettingsView):
    @extend_schema(operation_id="manage_sms", tags=TAG, responses={200: SmsSerializer})
    def get(self, request: Request) -> Response:
        return Response(SmsSerializer(_sms_payload()).data)

    @extend_schema(
        operation_id="manage_sms_update",
        tags=TAG,
        request=SmsInputSerializer,
        responses={200: SmsSerializer},
    )
    def patch(self, request: Request) -> Response:
        data = _valid(SmsInputSerializer(data=request.data))
        if "requested_sender" in data:
            sessions.require_recent_authentication(self.staff.session)
        with transaction.atomic():
            row = services.sms_settings()
            row = type(row).objects.select_for_update().get(pk=row.pk)
            before = {
                "is_enabled": row.is_enabled,
                "requested_sender": row.requested_sender,
                "order_updates": row.order_updates,
                "shipping_updates": row.shipping_updates,
            }
            for field in ("is_enabled", "order_updates", "shipping_updates"):
                if field in data:
                    setattr(row, field, data[field])
            if "requested_sender" in data:
                sender = data["requested_sender"].strip()
                if sender != row.approved_sender:
                    # A new sender id is only a request until NetaMate records the provider's
                    # approval; until then nothing is sent with it.
                    row.requested_sender = sender
                    row.sender_status = SenderStatus.PENDING
                    row.approved_sender = ""
            if row.is_enabled and row.sender_status != SenderStatus.APPROVED:
                row.is_enabled = False
                if data.get("is_enabled"):
                    raise ValidationError(
                        {"is_enabled": ["SMS can be turned on once a sender id is approved."]}
                    )
            row.save()
            record_audit_event(
                action="notifications.sms.updated",
                resource=row,
                actor=self.user,
                changes={
                    key: {"from": value, "to": getattr(row, key)}
                    for key, value in before.items()
                    if getattr(row, key) != value
                },
            )
        return Response(SmsSerializer(_sms_payload()).data)


# --- Templates ------------------------------------------------------------------------------------

SAMPLE = {
    "customer_name": "Rahim Uddin",
    "order_number": "ORD-000123",
    "order_status": "Confirmed",
    "order_total": "BDT 1,250.00",
    "payment_method": "Cash on delivery",
    "amount": "BDT 1,250.00",
    "tracking_reference": "TRK-4821",
    "case_kind": "return",
    "case_status": "approved",
    "product_name": "Cotton Panjabi",
    "sku": "PNJ-M",
    "available": "2",
    "rating": "5",
    "score": "40",
    "order_source": "Facebook",
    "date": "2026-11-01",
    "orders": "12",
    "order_value": "BDT 18,400.00",
    "collected": "BDT 9,200.00",
    "to_prepare": "3",
    "points": "350",
    "change": "authenticator app turned on",
    "role": "Manager",
}


def _template_payload(key: str) -> dict[str, Any]:
    kind = BY_KEY[key]
    overrides = {row.locale: row for row in NotificationTemplate.objects.filter(key=key)}
    locales = []
    for locale in LOCALES:
        row = overrides.get(locale)
        locales.append(
            {
                "locale": locale,
                "customized": row is not None,
                "is_enabled": row.is_enabled if row is not None else True,
                "version": row.version if row is not None else 0,
                "subject": row.subject if row is not None else "",
                "heading": row.heading if row is not None else "",
                "body": row.body if row is not None else "",
                "sms": row.sms if row is not None else "",
                "in_app_title": row.in_app_title if row is not None else "",
                "in_app_body": row.in_app_body if row is not None else "",
                "default_subject": kind.text("subject", locale),
                "default_heading": kind.text("heading", locale),
                "default_body": kind.text("body", locale),
                "default_sms": kind.text("sms", locale) if kind.sms else "",
            }
        )
    return {
        "key": kind.key,
        "title": kind.label,
        "category": kind.category,
        "audience": kind.audience,
        "variables": list(kind.variables),
        "supports_sms": bool(kind.sms),
        "locales": locales,
    }


def _kind(key: str) -> Any:
    kind = BY_KEY.get(key)
    if kind is None:
        raise NotFound("Template not found.")
    return kind


def _check_locale(locale: str) -> None:
    if locale not in LOCALES:
        raise NotFound("Language not found.")


class TemplateListView(SettingsView):
    @extend_schema(
        operation_id="manage_templates", tags=TAG, responses={200: TemplateSerializer(many=True)}
    )
    def get(self, request: Request) -> Response:
        return Response(
            TemplateSerializer([_template_payload(kind.key) for kind in KINDS], many=True).data
        )


class TemplateDetailView(SettingsView):
    @extend_schema(
        operation_id="manage_template_update",
        tags=TAG,
        request=TemplateInputSerializer,
        responses={200: TemplateSerializer},
    )
    def put(self, request: Request, key: str, locale: str) -> Response:
        kind = _kind(key)
        _check_locale(locale)
        data = _valid(TemplateInputSerializer(data=request.data))
        for field in ("subject", "heading", "body", "sms", "in_app_title", "in_app_body"):
            rendering.validate_text(kind, field, str(data.get(field, "")))
        if data.get("sms") and not kind.sms:
            raise ValidationError({"sms": ["This message is not sent by SMS."]})
        if kind.category == Category.SECURITY and not data.get("is_enabled", True):
            raise ValidationError({"is_enabled": ["Security notifications cannot be turned off."]})
        with transaction.atomic():
            row = (
                NotificationTemplate.objects.select_for_update()
                .filter(key=key, locale=locale)
                .first()
            )
            if row is None:
                row = NotificationTemplate(key=key, locale=locale, version=0)
            for field in ("subject", "heading", "body", "sms", "in_app_title", "in_app_body"):
                setattr(row, field, str(data.get(field, "")).strip())
            row.is_enabled = data.get("is_enabled", True)
            row.version += 1
            row.updated_by = self.user
            row.save()
            record_audit_event(
                action="notifications.template.updated",
                resource=row,
                actor=self.user,
                changes={
                    "key": key,
                    "locale": locale,
                    "version": row.version,
                    "enabled": row.is_enabled,
                },
            )
        return Response(TemplateSerializer(_template_payload(key)).data)

    @extend_schema(
        operation_id="manage_template_reset", tags=TAG, responses={200: TemplateSerializer}
    )
    def delete(self, request: Request, key: str, locale: str) -> Response:
        _kind(key)
        _check_locale(locale)
        with transaction.atomic():
            row = NotificationTemplate.objects.filter(key=key, locale=locale).first()
            if row is not None:
                record_audit_event(
                    action="notifications.template.reset",
                    resource=row,
                    actor=self.user,
                    changes={"key": key, "locale": locale},
                )
                row.delete()
        return Response(TemplateSerializer(_template_payload(key)).data)


class TemplatePreviewView(SettingsView):
    @extend_schema(
        operation_id="manage_template_preview",
        tags=TAG,
        request=TemplateTextSerializer,
        responses={200: TemplatePreviewSerializer},
    )
    def post(self, request: Request, key: str, locale: str) -> Response:
        kind = _kind(key)
        _check_locale(locale)
        data = _valid(TemplateTextSerializer(data=request.data))
        for field, value in data.items():
            rendering.validate_text(kind, field, str(value))

        class Draft:
            pass

        draft = Draft()
        for field in ("subject", "heading", "body", "sms", "in_app_title", "in_app_body"):
            setattr(draft, field, str(data.get(field, "")))
        content = rendering.render(
            kind=kind,
            locale=locale,
            values=SAMPLE,
            override=draft,
            branding=services.branding(),
            link_path="/account",
        )
        segments = providers.sms_segments(content.sms)[0] if content.sms else 0
        return Response(
            {
                "subject": content.subject,
                "text": content.text,
                "html": content.html,
                "sms": content.sms,
                "sms_segments": segments,
                "in_app_title": content.title,
                "in_app_body": content.summary,
            }
        )
