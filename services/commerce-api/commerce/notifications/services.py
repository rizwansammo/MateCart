"""The notification engine (D-087, D-088).

``emit()`` turns one committed business event into notification rows (the durable outbox): one
per recipient and channel, unique by ``event_key``. It runs inside the caller's transaction in a
savepoint, so it commits or rolls back with the business change but its own failure can never
undo an order, payment or refund. After commit the rows are queued; the beat sweeper re-queues
anything a broker outage left behind.

Delivery (``deliver``) claims a row, sends outside any lock through the store's single email
transport, records an append-only attempt and moves the row to sent, a retry or a final state.
A message whose outcome is unknown is never resent automatically.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import re
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from commerce.core import ratelimit
from commerce.identity.models import AccountType, User
from commerce.staff.models import StaffMembership
from commerce.staff.permissions import permissions_for
from commerce.tenancy.context import get_active_tenant_id

from . import connections, providers, rendering
from .models import (
    Audience,
    Category,
    Channel,
    CommunicationConsent,
    DeliveryAttempt,
    MailConnection,
    MailProvider,
    Notification,
    NotificationPreference,
    NotificationStatus,
    NotificationTemplate,
    SenderStatus,
    SmsSettings,
    SmsUsage,
    SmsUsageState,
)
from .registry import BY_KEY, CUSTOMER_TOPICS, Kind

logger = logging.getLogger(__name__)
RETRY_DELAYS = (60, 300, 900, 1800)
STALE_CLAIM = timedelta(minutes=10)
REQUEUE_AFTER = timedelta(minutes=5)
UNSUBSCRIBE_SALT = "matecart.notifications.unsubscribe"
UNSUBSCRIBE_MAX_AGE = 60 * 60 * 24 * 90
SMS_PER_RECIPIENT_HOUR = 6
SMS_PER_STORE_HOUR = 500
SMS_COUNTED = (SmsUsageState.RESERVED, SmsUsageState.ACCEPTED, SmsUsageState.HELD)
CONTENT_RETENTION = timedelta(days=90)
READ_IN_APP_RETENTION = timedelta(days=180)
RECORD_RETENTION = timedelta(days=400)
FINAL = (
    NotificationStatus.SENT,
    NotificationStatus.DELIVERED,
    NotificationStatus.FAILED,
    NotificationStatus.CANCELLED,
    NotificationStatus.SUPPRESSED,
)


@dataclass(frozen=True)
class Recipient:
    user: User | None
    email: str = ""
    phone: str = ""
    name: str = ""
    locale: str = ""  # unknown: the store's default language is used


def recipient_key(channel: str, recipient: Recipient) -> str:
    identity = (
        f"user:{recipient.user.pk}"
        if recipient.user is not None
        else f"{channel}:{recipient.email or recipient.phone}".lower()
    )
    return hashlib.sha256(f"{channel}|{identity}".encode()).hexdigest()


# --- Branding and preferences ---------------------------------------------------------------------


def branding() -> rendering.Branding:
    from commerce.stores.appearance import current_theme, store_settings

    store = store_settings()
    theme = current_theme()
    logo = ""
    if theme.logo_id:
        from commerce.catalog.serializers import public_media_url

        logo = rendering.public_url(public_media_url(theme.logo_id))
    return rendering.Branding(
        store_name=store.name,
        primary_color=theme.primary_color or "",
        logo_url=logo,
        contact_email=store.contact_email or "",
        contact_phone=store.contact_phone or "",
        footer_text=theme.footer_text or "",
    )


def preference(user: User, topic: str, channel: str, default: bool) -> bool:
    row = (
        NotificationPreference.objects.filter(user=user, topic=topic, channel=channel)
        .values_list("enabled", flat=True)
        .first()
    )
    return default if row is None else bool(row)


def set_preference(*, user: User, topic: str, channel: str, enabled: bool, source: str) -> None:
    essential = (
        user.account_type == AccountType.CUSTOMER
        and CUSTOMER_TOPICS.get(topic, {}).get(channel) == "essential"
    )
    if essential:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"topic": ["Essential order and security email stays on."]})
    with transaction.atomic():
        NotificationPreference.objects.update_or_create(
            user=user, topic=topic, channel=channel, defaults={"enabled": enabled}
        )
        if topic == "marketing":
            CommunicationConsent.objects.create(
                user=user,
                channel=channel,
                granted=enabled,
                source=source,
                recorded_at=timezone.now(),
            )


def marketing_allowed(user: User, channel: str) -> bool:
    """Only an explicit, unwithdrawn opt-in. Phase 12 tracking consent never counts (D-090)."""
    latest = (
        CommunicationConsent.objects.filter(user=user, channel=channel)
        .order_by("-recorded_at", "-pk")
        .values_list("granted", flat=True)
        .first()
    )
    return bool(latest)


def unsubscribe_token(user: User, channel: str) -> str:
    return signing.dumps(
        {"u": str(user.pk), "c": channel, "t": str(get_active_tenant_id())}, salt=UNSUBSCRIBE_SALT
    )


def unsubscribe(token: str) -> bool:
    """Withdraw marketing consent from a signed link (no sign-in needed). Idempotent."""
    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT, max_age=UNSUBSCRIBE_MAX_AGE)
    except signing.BadSignature:
        return False
    if data.get("t") != str(get_active_tenant_id()) or data.get("c") not in Channel.values:
        return False
    user = User.objects.filter(pk=data.get("u"), account_type=AccountType.CUSTOMER).first()
    if user is None:
        return False
    if marketing_allowed(user, data["c"]):
        set_preference(
            user=user,
            topic="marketing",
            channel=data["c"],
            enabled=False,
            source="unsubscribe_link",
        )
    return True


def staff_recipients(permission: str) -> list[User]:
    """Active staff members whose fixed role grants ``permission`` (D-044)."""
    rows = StaffMembership.objects.filter(status="active").select_related("user")
    return [
        row.user
        for row in rows
        if row.user.is_active and (not permission or permission in permissions_for(row.role))
    ]


# --- Emission -------------------------------------------------------------------------------------


def _template(kind: Kind, locale: str) -> NotificationTemplate | None:
    row = NotificationTemplate.objects.filter(key=kind.key, locale=locale).first()
    if row is None and locale != "en":
        row = NotificationTemplate.objects.filter(key=kind.key, locale="en").first()
    return row


def _store_locale() -> str:
    from commerce.stores.models import StoreSettings

    return StoreSettings.objects.values_list("default_language", flat=True).first() or "en"


def emit(
    kind_key: str,
    *,
    event_id: str,
    values: dict[str, Any],
    customer: Recipient | None = None,
    related: tuple[str, str] = ("", ""),
    link_path: str = "",
    staff_link_path: str = "",
    staff_in_app: bool = True,
    staff_users: Sequence[User] | None = None,
    exclude_staff: Iterable[Any] = (),
) -> list[Notification]:
    """Create the notifications for one business event. Never raises into the caller.

    ``staff_users`` narrows a staff event to those members (for example the assignee of a
    conversation); each must still hold the kind's permission. ``exclude_staff`` (user ids)
    skips members who already see the change live (Phase 14)."""
    kind = BY_KEY[kind_key]
    try:
        with transaction.atomic():
            created = _emit(
                kind,
                event_id,
                values,
                customer,
                related,
                link_path,
                staff_link_path,
                staff_in_app,
                staff_users,
                frozenset(str(pk) for pk in exclude_staff),
            )
    except Exception:
        logger.exception("Notification not recorded (kind=%s)", kind_key)
        return []
    if created:
        _queue_after_commit([row.pk for row in created if row.channel != Channel.IN_APP])
    return created


def _emit(
    kind: Kind,
    event_id: str,
    values: dict[str, Any],
    customer: Recipient | None,
    related: tuple[str, str],
    link_path: str,
    staff_link_path: str,
    staff_in_app: bool = True,
    staff_users: Sequence[User] | None = None,
    exclude_staff: frozenset[str] = frozenset(),
) -> list[Notification]:
    brand = branding()
    now = timezone.now()
    event_key = f"{kind.key}:{event_id}"[:160]
    created: list[Notification] = []
    targets: list[tuple[str, Recipient, str]] = []
    if kind.audience == Audience.CUSTOMER and customer is not None:
        targets.extend(_customer_channels(kind, customer))
    elif kind.audience == Audience.STAFF:
        if kind.category == Category.SECURITY and customer is not None:
            targets.extend(
                (channel, customer, "")
                for channel in (Channel.EMAIL, Channel.IN_APP)
                if channel != Channel.EMAIL or customer.email
            )
        else:
            members = staff_recipients(kind.permission)
            if staff_users is not None:
                wanted = {user.pk for user in staff_users}
                members = [user for user in members if user.pk in wanted]
            for user in members:
                if str(user.pk) in exclude_staff:
                    continue
                staff = Recipient(user=user, email=user.email or "", name=user.full_name)
                if staff_in_app:
                    targets.append((Channel.IN_APP, staff, kind.permission))
                if user.email and preference(user, kind.topic, Channel.EMAIL, kind.email_default):
                    targets.append((Channel.EMAIL, staff, kind.permission))
    store_locale = _store_locale()
    for channel, recipient, permission in targets:
        locale = recipient.locale if recipient.locale in ("en", "bn") else store_locale
        override = _template(kind, locale)
        if override is not None and not override.is_enabled:
            continue
        is_staff = kind.audience == Audience.STAFF and kind.category != Category.SECURITY
        path = staff_link_path if is_staff else link_path
        unsubscribe_path = ""
        if kind.category == Category.MARKETING and recipient.user is not None:
            unsubscribe_path = f"/unsubscribe?token={unsubscribe_token(recipient.user, channel)}"
        content = rendering.render(
            kind=kind,
            locale=locale,
            values={**values, "customer_name": recipient.name or values.get("customer_name", "")},
            override=override,
            branding=brand,
            link_path=path,
            unsubscribe_path=unsubscribe_path,
        )
        address = (
            recipient.email
            if channel == Channel.EMAIL
            else (recipient.phone if channel == Channel.SMS else "")
        )
        row = Notification(
            kind=kind.key,
            category=kind.category,
            channel=channel,
            audience=kind.audience,
            event_key=event_key,
            recipient_key=recipient_key(channel, recipient),
            recipient_user=recipient.user,
            recipient_address=address,
            required_permission=permission,
            locale=locale,
            template_version=override.version if override is not None else 0,
            related_type=related[0],
            related_id=related[1],
            link_path=path,
            subject=content.subject if channel == Channel.EMAIL else content.title,
            body_text=content.text
            if channel == Channel.EMAIL
            else content.sms
            if channel == Channel.SMS
            else content.summary,
            body_html=content.html if channel == Channel.EMAIL else "",
            scheduled_at=now,
            status=NotificationStatus.SENT
            if channel == Channel.IN_APP
            else NotificationStatus.PENDING,
            sent_at=now if channel == Channel.IN_APP else None,
            provider="in_app" if channel == Channel.IN_APP else "",
        )
        if channel == Channel.SMS and not content.sms:
            continue
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            continue  # this event was already recorded for this recipient and channel
        created.append(row)
    return created


def _customer_channels(kind: Kind, customer: Recipient) -> Iterable[tuple[str, Recipient, str]]:
    user = customer.user
    if kind.category == Category.MARKETING:
        if user is None:
            return
        if customer.email and marketing_allowed(user, Channel.EMAIL):
            yield Channel.EMAIL, customer, ""
        return
    if customer.email:
        yield Channel.EMAIL, customer, ""
    if user is not None:
        yield Channel.IN_APP, customer, ""
    wants_sms = user is None or preference(user, kind.topic, Channel.SMS, True)
    if kind.sms_setting and customer.phone and sms_enabled_for(kind.sms_setting) and wants_sms:
        yield Channel.SMS, customer, ""


def _queue_after_commit(ids: Sequence[Any]) -> None:
    if not ids:
        return
    tenant_id = str(get_active_tenant_id())
    keys = [str(pk) for pk in ids]

    def enqueue() -> None:
        # After commit the request's tenant context may be gone: carry the tenant explicitly.
        from commerce.tenancy.context import tenant_context

        from .tasks import deliver_notification

        for key in keys:
            try:
                deliver_notification.delay(key, tenant_id=tenant_id)
            except Exception:
                logger.warning("Notification queueing deferred to the sweeper")
                return
        with tenant_context(uuid.UUID(tenant_id)):
            Notification.objects.filter(pk__in=keys, status=NotificationStatus.PENDING).update(
                status=NotificationStatus.QUEUED, queued_at=timezone.now()
            )

    transaction.on_commit(enqueue)


# --- Delivery -------------------------------------------------------------------------------------


def _attempt(row: Notification, result: providers.Result, next_retry: datetime | None) -> None:
    DeliveryAttempt.objects.create(
        notification=row,
        number=row.attempts,
        provider=result.provider or row.channel,
        outcome=result.outcome,
        error_code=result.code[:40],
        provider_reference=result.reference[:200],
        next_retry_at=next_retry,
        occurred_at=timezone.now(),
    )


def deliver(notification_id: Any) -> str:
    """Send one notification. Idempotent: anything not due and pending is left alone."""
    now = timezone.now()
    with transaction.atomic():
        row = (
            Notification.objects.select_for_update(skip_locked=True)
            .filter(pk=notification_id)
            .first()
        )
        if row is None:
            return "busy_or_missing"
        if (
            row.status == NotificationStatus.PROCESSING
            and row.claimed_at
            and (now - row.claimed_at > STALE_CLAIM)
        ):
            # A worker died while sending: the provider may have accepted it. Never resend, and
            # keep any SMS units reserved for it until someone reconciles them (D-091).
            row.status = NotificationStatus.FAILED
            row.failure_code = "outcome_unknown"
            row.save(update_fields=["status", "failure_code", "updated_at"])
            if row.channel == Channel.SMS:
                _settle_sms(row, providers.Result(providers.UNKNOWN, "worker_lost"))
            return row.status
        if row.status not in (NotificationStatus.PENDING, NotificationStatus.QUEUED):
            return str(row.status)
        if row.scheduled_at > now:
            return "not_due"
        blocked = _suppression(row)
        if not blocked and row.channel == Channel.SMS:
            # Reserved in this transaction, together with the claim (D-091).
            blocked = _reserve_sms(row, attempt=row.attempts + 1, now=now)
        if blocked:
            row.status = NotificationStatus.SUPPRESSED
            row.failure_code = blocked
            row.save(update_fields=["status", "failure_code", "updated_at"])
            return row.status
        transport = connections.active() if row.channel == Channel.EMAIL else None
        row.status = NotificationStatus.PROCESSING
        row.claimed_at = now
        row.attempts += 1
        row.last_attempt_at = now
        row.save(
            update_fields=["status", "claimed_at", "attempts", "last_attempt_at", "updated_at"]
        )
    result = _send(row, transport)
    with transaction.atomic():
        row = Notification.objects.select_for_update().get(pk=row.pk)
        next_retry = None
        if result.outcome == providers.ACCEPTED:
            row.status = NotificationStatus.SENT
            row.sent_at = timezone.now()
            row.failure_code = ""
            row.provider_reference = result.reference[:200]
        elif result.outcome == providers.RETRYABLE and row.attempts < row.max_attempts:
            delay = RETRY_DELAYS[min(row.attempts - 1, len(RETRY_DELAYS) - 1)]
            next_retry = timezone.now() + timedelta(seconds=delay)
            row.status = NotificationStatus.PENDING
            row.scheduled_at = next_retry
            row.failure_code = result.code[:40]
        else:
            row.status = NotificationStatus.FAILED
            row.failure_code = (
                "outcome_unknown"
                if result.outcome == providers.UNKNOWN
                else ("retries_exhausted" if result.outcome == providers.RETRYABLE else result.code)
            )[:40]
        row.provider = (result.provider or row.provider)[:20]
        row.claimed_at = None
        row.save()
        _attempt(row, result, next_retry)
        if row.channel == Channel.SMS:
            _settle_sms(row, result)
        if result.code == "connection_revoked" and transport is not None:
            connections.mark_revoked(transport)
    if next_retry is not None:
        _schedule(row.pk, next_retry)
    return str(row.status)


def _schedule(pk: Any, when: datetime) -> None:
    tenant_id = str(get_active_tenant_id())

    def enqueue() -> None:
        from .tasks import deliver_notification

        try:
            deliver_notification.apply_async(
                args=(str(pk),), kwargs={"tenant_id": tenant_id}, eta=when
            )
        except Exception:
            logger.warning("Retry scheduling deferred to the sweeper")

    transaction.on_commit(enqueue)


def _suppression(row: Notification) -> str:
    if row.category == Category.MARKETING and (
        row.recipient_user is None or not marketing_allowed(row.recipient_user, row.channel)
    ):
        return "consent_withdrawn"
    if row.channel == Channel.SMS:
        return _sms_block(row)
    return ""


def _send(row: Notification, transport: MailConnection | None) -> providers.Result:
    if row.channel == Channel.EMAIL:
        email = providers.Email(
            to=row.recipient_address,
            subject=row.subject,
            text=row.body_text,
            html=row.body_html,
            idempotency_key=f"matecart-{row.pk}"[:100],
        )
        try:
            if transport is None:
                return providers.send_default(email, branding().store_name)
            if transport.status != "connected":
                return providers.Result(
                    providers.PERMANENT, "connection_not_ready", provider=transport.provider
                )
            if transport.provider == MailProvider.MATEMAIL:
                return providers.send_matemail(transport, email)
            return providers.send_smtp(transport, email)
        except Exception:
            logger.warning("Email delivery error (notification=%s)", row.pk)
            return providers.Result(providers.UNKNOWN, "unexpected_error", provider="email")
    if row.channel == Channel.SMS:
        return _send_sms(row)
    return providers.Result(providers.ACCEPTED, provider="in_app")


# --- SMS ------------------------------------------------------------------------------------------


def sms_settings() -> SmsSettings:
    row = SmsSettings.objects.first()
    if row is not None:
        return row
    with transaction.atomic():
        SmsSettings.objects.get_or_create()
    return SmsSettings.objects.get()


def sms_ready() -> bool:
    from commerce.core.delivery import sms_provider

    settings_row = SmsSettings.objects.first()
    return bool(
        settings_row is not None
        and settings_row.is_enabled
        and settings_row.sender_status == SenderStatus.APPROVED
        and sms_provider() is not None
    )


def sms_enabled_for(setting: str) -> bool:
    row = SmsSettings.objects.first()
    return bool(sms_ready() and row is not None and getattr(row, setting, False))


def period(at: datetime | None = None) -> str:
    from commerce.orders.reports import store_zone

    return (at or timezone.now()).astimezone(store_zone()).strftime("%Y-%m")


def _allowance_used(month: str) -> int:
    from django.db.models import Sum

    total = SmsUsage.objects.filter(period=month, state__in=SMS_COUNTED).aggregate(
        total=Sum("allowance_segments")
    )["total"]
    return int(total or 0)


def sms_balance(at: datetime | None = None) -> dict[str, int]:
    """The store-local month's allowance. ``used`` counts reserved, accepted and held units:
    a message whose outcome is unknown keeps its units until it is reconciled (D-091)."""
    row = sms_settings()
    used = _allowance_used(period(at))
    return {
        "allowance": row.monthly_allowance,
        "used": used,
        "remaining": max(0, row.monthly_allowance - used),
        "prepaid_credits": row.prepaid_credits,
    }


def _sms_block(row: Notification) -> str:
    if not sms_ready():
        return "sms_unavailable"
    if ratelimit.count("sms-recipient", row.recipient_key) >= SMS_PER_RECIPIENT_HOUR:
        return "sms_rate_limited"
    if ratelimit.count("sms-store", "all") >= SMS_PER_STORE_HOUR:
        return "sms_rate_limited"
    return ""


def _reserve_sms(row: Notification, *, attempt: int, now: datetime) -> str:
    """Reserve this attempt's segments before the provider is called (D-091).

    Runs inside ``deliver()``'s claim transaction, under a row lock on the store's
    ``SmsSettings``: concurrent deliveries reserve one after another and each sees the units the
    others committed, so the last units go to exactly one message. The included allowance of the
    store-local month is used first, then prepaid credits (deducted now; the column cannot go
    negative). When the two together cannot cover every segment the message is not sent: there
    is no overage. Returns a suppression code, or ``""`` once the units are reserved.
    """
    settings_row = SmsSettings.objects.select_for_update().first()
    if settings_row is None:
        return "sms_unavailable"
    segments, encoding = providers.sms_segments(row.body_text)
    month = period(now)
    from_allowance = min(segments, max(0, settings_row.monthly_allowance - _allowance_used(month)))
    from_prepaid = segments - from_allowance
    if from_prepaid > settings_row.prepaid_credits:
        return "sms_allowance_exhausted"
    if from_prepaid:
        SmsSettings.objects.filter(pk=settings_row.pk).update(
            prepaid_credits=F("prepaid_credits") - from_prepaid
        )
    SmsUsage.objects.create(
        notification=row,
        attempt=attempt,
        purpose=row.kind[:40],
        period=month,
        segments=segments,
        encoding=encoding,
        accepted=False,
        state=SmsUsageState.RESERVED,
        funded_by=(
            "allowance" if not from_prepaid else ("prepaid" if not from_allowance else "mixed")
        ),
        allowance_segments=from_allowance,
        prepaid_segments=from_prepaid,
        occurred_at=now,
    )
    return ""


def _resolve(usage: SmsUsage, state: str, resolution: str) -> None:
    """Move a reservation to its outcome. Releasing returns the prepaid units it deducted (the
    allowance units return by no longer being counted). Caller holds the usage row lock."""
    if state == SmsUsageState.RELEASED and usage.prepaid_segments:
        SmsSettings.objects.all().update(
            prepaid_credits=F("prepaid_credits") + usage.prepaid_segments
        )
    usage.state = state
    usage.accepted = state == SmsUsageState.ACCEPTED
    usage.resolution = resolution[:40]
    usage.resolved_at = timezone.now()
    usage.save(update_fields=["state", "accepted", "resolution", "resolved_at"])


def _settle_sms(row: Notification, result: providers.Result) -> None:
    """Finalise, release or hold the reservation of ``row``'s current attempt (D-091).

    Accepted: the units are spent. Retryable or permanent: the gateway definitely did not accept
    the message, so the units return. Unknown: it may have been accepted, so the units stay held
    until reconciled (``reconcile_sms``). Idempotent: only a still-reserved row is changed.
    """
    usage = (
        SmsUsage.objects.select_for_update()
        .filter(notification=row, attempt=row.attempts, state=SmsUsageState.RESERVED)
        .first()
    )
    if usage is None:
        return
    if result.outcome == providers.ACCEPTED:
        _resolve(usage, SmsUsageState.ACCEPTED, "accepted")
    elif result.outcome in (providers.RETRYABLE, providers.PERMANENT):
        _resolve(usage, SmsUsageState.RELEASED, result.code or "not_accepted")
    else:
        _resolve(usage, SmsUsageState.HELD, result.code or "outcome_unknown")


def sms_idempotency_key(row: Notification) -> str:
    """Stable for the notification across every attempt, including a deliberate resend, so a
    gateway that deduplicates by key never delivers the same notification twice."""
    return f"matecart-sms-{row.pk}"


def _accepts(function: Callable[..., object], names: Iterable[str]) -> bool:
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return True
    return all(name in parameters for name in names)


def _rejection_code(code: str) -> str:
    """A provider's machine-readable reason, kept only if it cannot carry a phone number."""
    cleaned = re.sub(r"[^a-z0-9_]", "", code.lower())[:40]
    return cleaned if cleaned and not re.search(r"\d{5,}", cleaned) else "sms_rejected"


def _send_sms(row: Notification) -> providers.Result:
    """Submit one SMS and classify the outcome (D-091). Only ``SmsRejected`` proves the gateway
    did not accept the message; any other exception leaves the outcome unknown. Neither the
    recipient nor the provider's exception text is logged."""
    from commerce.core.delivery import SmsRejected, sms_provider

    provider = sms_provider()
    settings_row = SmsSettings.objects.first()
    if (
        provider is None
        or settings_row is None
        or settings_row.sender_status != (SenderStatus.APPROVED)
    ):
        return providers.Result(providers.PERMANENT, "sms_unavailable", provider="sms")
    extra: dict[str, str] = {}
    if getattr(provider, "supports_idempotency_key", False) is True:
        extra["idempotency_key"] = sms_idempotency_key(row)
    send: Callable[..., object] = provider.send
    if not _accepts(send, ("sender", *extra)):
        return providers.Result(providers.PERMANENT, "sms_provider_incompatible", provider="sms")
    ratelimit.hit("sms-recipient", row.recipient_key, window=3600)
    ratelimit.hit("sms-store", "all", window=3600)
    try:
        send(
            to=row.recipient_address,
            body=row.body_text,
            tenant_id=get_active_tenant_id(),
            purpose=row.kind,
            sender=settings_row.approved_sender,
            **extra,
        )
    except SmsRejected as rejected:
        code = _rejection_code(rejected.code)
        logger.warning("SMS not accepted (notification=%s, reason=%s)", row.pk, code)
        outcome = providers.RETRYABLE if rejected.retryable else providers.PERMANENT
        return providers.Result(outcome, code, provider="sms")
    except Exception:
        logger.warning("SMS outcome unknown (notification=%s)", row.pk)
        return providers.Result(providers.UNKNOWN, "sms_outcome_unknown", provider="sms")
    return providers.Result(providers.ACCEPTED, provider="sms")


def held_sms() -> list[SmsUsage]:
    return list(SmsUsage.objects.filter(state=SmsUsageState.HELD).order_by("occurred_at"))


def reconcile_sms(usage_id: Any, *, accepted: bool, actor_label: str) -> SmsUsage:
    """Resolve a held reservation after checking the gateway's records (D-091): accepted spends
    the units, not accepted returns them. Audited; nothing is resent."""
    from commerce.audit.services import record_audit_event
    from commerce.core.domain import ConflictError

    with transaction.atomic():
        usage = SmsUsage.objects.select_for_update().filter(pk=usage_id).first()
        if usage is None or usage.state != SmsUsageState.HELD:
            raise ConflictError("Only held SMS reservations can be reconciled.", "not_held")
        _resolve(
            usage,
            SmsUsageState.ACCEPTED if accepted else SmsUsageState.RELEASED,
            "reconciled_accepted" if accepted else "reconciled_not_accepted",
        )
        record_audit_event(
            action="notifications.sms.reconciled",
            resource=usage,
            actor_label=actor_label,
            changes={
                "state": usage.state,
                "period": usage.period,
                "segments": usage.segments,
                "allowance_segments": usage.allowance_segments,
                "prepaid_segments": usage.prepaid_segments,
            },
        )
    return usage


def record_auth_sms(*, segments: int, encoding: str, purpose: str) -> None:
    """Usage record for an authentication SMS the provider accepted. Authentication codes are
    exempt from the store's notification allowance and prepaid credits (D-091): sign-in must
    never stop because marketing or order messages used the units. No message or number is
    stored."""
    now = timezone.now()
    SmsUsage.objects.create(
        notification=None,
        attempt=0,
        purpose=purpose[:40],
        period=period(now),
        segments=segments,
        encoding=encoding,
        accepted=True,
        state=SmsUsageState.ACCEPTED,
        funded_by="auth",
        occurred_at=now,
        resolved_at=now,
        resolution="accepted",
    )


# --- Sweeper, retries and retention ---------------------------------------------------------------


def due_ids(limit: int = 200) -> list[Any]:
    now = timezone.now()
    return list(
        Notification.objects.filter(
            Q(status=NotificationStatus.PENDING)
            | Q(status=NotificationStatus.QUEUED, queued_at__lt=now - REQUEUE_AFTER),
            scheduled_at__lte=now,
        )
        .exclude(channel=Channel.IN_APP)
        .order_by("scheduled_at", "pk")
        .values_list("pk", flat=True)[:limit]
    ) + list(
        Notification.objects.filter(
            status=NotificationStatus.PROCESSING, claimed_at__lt=now - STALE_CLAIM
        ).values_list("pk", flat=True)[:limit]
    )


def manual_retry(*, notification_id: Any, actor: User) -> Notification:
    """Staff may resend a failed message once more (including an unknown outcome, knowingly)."""
    from commerce.audit.services import record_audit_event
    from commerce.core.domain import ConflictError

    ratelimit.consume("notification-retry", str(actor.pk), limit=30, window=3600)
    with transaction.atomic():
        row = Notification.objects.select_for_update().filter(pk=notification_id).first()
        if row is None or row.channel == Channel.IN_APP:
            raise ConflictError("This notification cannot be resent.", "not_retryable")
        if row.status != NotificationStatus.FAILED or row.content_cleared_at is not None:
            raise ConflictError("Only failed messages can be resent.", "not_retryable")
        row.status = NotificationStatus.PENDING
        row.scheduled_at = timezone.now()
        row.max_attempts = row.attempts + 1
        row.failure_code = ""
        row.save()
        record_audit_event(
            action="notifications.retried",
            resource=row,
            actor=actor,
            changes={"kind": row.kind, "channel": row.channel},
        )
    _queue_after_commit([row.pk])
    return row


def cleanup(now: datetime | None = None) -> dict[str, int]:
    """Retention (D-088): message content 90 days; read in-app rows 180 days; records 400 days.
    SMS accounting and consent evidence are kept as financial and legal records."""
    now = now or timezone.now()
    cleared = (
        Notification.objects.filter(
            created_at__lt=now - CONTENT_RETENTION, content_cleared_at__isnull=True
        )
        .exclude(channel=Channel.IN_APP)
        .filter(status__in=FINAL)
    )
    content = cleared.update(
        subject="", body_text="", body_html="", recipient_address="—", content_cleared_at=now
    )
    read = Notification.objects.filter(
        channel=Channel.IN_APP, read_at__lt=now - READ_IN_APP_RETENTION
    ).delete()[0]
    old = Notification.objects.filter(created_at__lt=now - RECORD_RETENTION, status__in=FINAL)
    removed = old.delete()[0]
    return {"content_cleared": content, "read_in_app": read, "records": removed}


# --- In-app inbox ---------------------------------------------------------------------------------


def inbox(user: User, permissions: Iterable[str] | None = None) -> Any:
    rows = Notification.objects.filter(recipient_user=user, channel=Channel.IN_APP)
    if permissions is not None:
        rows = rows.filter(Q(required_permission="") | Q(required_permission__in=list(permissions)))
    return rows


def mark_read(user: User, ids: Sequence[Any] | None, permissions: Iterable[str] | None) -> int:
    rows = inbox(user, permissions).filter(read_at__isnull=True)
    if ids is not None:
        rows = rows.filter(pk__in=ids)
    updated: int = rows.update(read_at=timezone.now())
    return updated


def money(amount: Decimal | None, currency: str) -> str:
    from commerce.pricing.services import currency_precision

    if amount is None:
        return ""
    places = currency_precision(currency)
    return f"{currency} {amount:,.{places}f}"


def public_settings() -> dict[str, Any]:
    return {"base_url_configured": bool(settings.MATECART_PUBLIC_BASE_URL)}
