"""Tracking settings, the Meta Pixel configuration and the Conversions API (D-084).

- Everything external is off by default. The browser Pixel loads only when the merchant enabled
  Meta with a valid Pixel id **and** the visitor granted marketing consent.
- The Conversions API sends one server event: ``Purchase``. Its ``event_id`` is
  ``purchase.<order id>``, shared with the browser Pixel for deduplication.
- Timing is the merchant's choice: when payment is verified or cash is collected (default), or
  when the order is placed. A placed cash-on-delivery order is never reported as paid; it is
  reported only in "order placed" mode, with ``payment_status`` ``unpaid`` in the event data.
- An event is queued only for a storefront order whose visitor had marketing consent at
  placement; it is discarded if consent is withdrawn before sending. Contact details are sent
  only when the merchant enabled hashed matching, and only SHA-256 hashed at send time.
- The access token comes from the deployment environment (``META_CAPI_ACCESS_TOKEN``). It is
  never stored, returned or logged. Provider calls run in a Celery task, outside database locks,
  with at most five attempts; a failure never affects the order.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.core.domain import ConflictError
from commerce.identity.models import User
from commerce.orders.models import (
    Order,
    OrderItem,
    OrderPaymentStatus,
    OrderSource,
    OrderStatus,
)
from commerce.tenancy.context import get_active_tenant_id

from . import consent as consent_module
from .models import (
    PIXEL_ID_PATTERN,
    DeliveryStatus,
    MetaEventDelivery,
    PurchaseTiming,
    TrackingSettings,
)

logger = logging.getLogger(__name__)
SETTINGS_FIELDS = [
    "analytics_enabled",
    "meta_enabled",
    "meta_pixel_id",
    "meta_capi_enabled",
    "meta_send_hashed_contact",
    "meta_purchase_timing",
    "meta_test_event_code",
]
MAX_ATTEMPTS = 5
STALE_CLAIM = timedelta(minutes=10)
_FBP = re.compile(r"^fb\.[0-9]\.[0-9]{10,13}\.[0-9]{1,30}$")
_FBC = re.compile(r"^fb\.[0-9]\.[0-9]{10,13}\.[A-Za-z0-9_-]{1,160}$")
_TEST_CODE = re.compile(r"^[A-Z0-9]{0,40}$")


def current() -> TrackingSettings:
    found = TrackingSettings.objects.first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return TrackingSettings.objects.create()
    except IntegrityError:
        existing = TrackingSettings.objects.first()
        if existing is None:
            raise
        return existing


def token_configured() -> bool:
    return bool(settings.META_CAPI_ACCESS_TOKEN)


def source_url_configured() -> bool:
    return str(settings.MATECART_PUBLIC_BASE_URL).startswith("https://")


def capi_active(row: TrackingSettings | None = None) -> bool:
    row = row or current()
    return bool(
        row.meta_enabled
        and row.meta_capi_enabled
        and bool(row.meta_pixel_id)
        and token_configured()
        and source_url_configured()
    )


def status(row: TrackingSettings | None = None) -> dict[str, Any]:
    row = row or current()
    return {
        "browser_pixel": "active" if row.meta_enabled and row.meta_pixel_id else "off",
        "server_events": (
            "active"
            if capi_active(row)
            else "missing_credentials"
            if row.meta_capi_enabled and not (token_configured() and source_url_configured())
            else "off"
        ),
        "token_configured": token_configured(),
        "public_url_configured": source_url_configured(),
        "pending_events": MetaEventDelivery.objects.filter(
            status__in=(DeliveryStatus.PENDING, DeliveryStatus.SENDING)
        ).count(),
        "failed_events": MetaEventDelivery.objects.filter(status=DeliveryStatus.FAILED).count(),
        "sent_events": MetaEventDelivery.objects.filter(status=DeliveryStatus.SENT).count(),
    }


def save(*, data: Mapping[str, Any], actor: User) -> TrackingSettings:
    with transaction.atomic():
        row = TrackingSettings.objects.select_for_update().get(pk=current().pk)
        before = {field: getattr(row, field) for field in SETTINGS_FIELDS}
        for field in SETTINGS_FIELDS:
            if field in data:
                setattr(row, field, data[field])
        row.meta_pixel_id = row.meta_pixel_id.strip()
        row.meta_test_event_code = row.meta_test_event_code.strip().upper()
        if row.meta_pixel_id and not re.match(PIXEL_ID_PATTERN, row.meta_pixel_id):
            raise ValidationError({"meta_pixel_id": ["A Pixel id is 5-20 digits."]})
        if row.meta_enabled and not row.meta_pixel_id:
            raise ValidationError({"meta_pixel_id": ["Enter the Pixel id to turn Meta on."]})
        if not _TEST_CODE.match(row.meta_test_event_code):
            raise ValidationError({"meta_test_event_code": ["Use the code Meta shows (A-Z, 0-9)."]})
        if row.meta_capi_enabled and not row.meta_enabled:
            raise ValidationError({"meta_capi_enabled": ["Turn on Meta tracking first."]})
        if row.meta_capi_enabled and not (token_configured() and source_url_configured()):
            raise ConflictError(
                "Server-side events need the deployment's Conversions API token and public "
                "HTTPS address. NetaMate configures them for live stores.",
                "capi_not_configured",
            )
        row.save()
        if not row.meta_capi_enabled or not row.meta_enabled:
            MetaEventDelivery.objects.filter(
                status__in=(DeliveryStatus.PENDING, DeliveryStatus.WAITING)
            ).update(
                status=DeliveryStatus.DISCARDED,
                fbp="",
                fbc="",
                user_agent="",
                last_error="integration_disabled",
                updated_at=timezone.now(),
            )
        after = {field: getattr(row, field) for field in SETTINGS_FIELDS}
        record_audit_event(
            action="marketing.tracking.updated",
            resource=row,
            actor=actor,
            changes=field_changes(before, after, fields=SETTINGS_FIELDS),
        )
        return row


def public_config() -> dict[str, Any]:
    """What the storefront may know: never tokens, never server-side state."""
    row = current()
    return {
        "analytics": row.analytics_enabled,
        "meta_pixel_id": row.meta_pixel_id if row.meta_enabled and row.meta_pixel_id else None,
        "purchase_timing": row.meta_purchase_timing,
        "consent_version": consent_module.POLICY_VERSION,
    }


def purchase_event_id(order: Order) -> str:
    return f"purchase.{order.pk}"


# --- Queueing -------------------------------------------------------------------------------------


def _browser_ids(request: Any) -> tuple[str, str, str]:
    fbp = request.COOKIES.get("_fbp", "")
    fbc = request.COOKIES.get("_fbc", "")
    agent = str(request.META.get("HTTP_USER_AGENT", ""))[:400]
    return (fbp if _FBP.match(fbp) else "", fbc if _FBC.match(fbc) else "", agent)


def on_order_placed(*, request: Any, order: Order) -> None:
    """After a storefront placement commits: analytics conversion, attribution and, with
    marketing consent, the Purchase event (now or once payment is verified). Never raises."""
    try:
        _on_order_placed(request, order)
    except Exception:
        logger.warning("Order tracking not recorded", exc_info=True)


def _on_order_placed(request: Any, order: Order) -> None:
    from . import analytics

    if order.source != OrderSource.STOREFRONT:
        return
    row = current()
    granted = consent_module.from_request(request)
    if row.analytics_enabled:
        visitor = consent_module.visitor_id(request, granted)
        utm = analytics.parse_source(request.COOKIES.get(consent_module.SOURCE_COOKIE))
        with transaction.atomic():
            analytics.record_order(order=order, visitor=visitor, utm=utm)
    if granted is None or not granted.marketing or not capi_active(row):
        return
    fbp, fbc, agent = _browser_ids(request)
    immediate = (
        row.meta_purchase_timing == PurchaseTiming.ORDER_PLACED
        and order.status != OrderStatus.PENDING_PAYMENT
    ) or order.payment_status == OrderPaymentStatus.PAID
    with transaction.atomic():
        delivery, created = MetaEventDelivery.objects.get_or_create(
            event_id=purchase_event_id(order),
            defaults={
                "event_name": "Purchase",
                "order": order,
                "consent_digest": granted.digest,
                "fbp": fbp,
                "fbc": fbc,
                "user_agent": agent,
                "source_url": f"{settings.MATECART_PUBLIC_BASE_URL}/orders/{order.pk}",
                "event_time": timezone.now(),
                "status": DeliveryStatus.PENDING if immediate else DeliveryStatus.WAITING,
            },
        )
        if created and immediate:
            _enqueue(delivery)


def on_payment_verified(order: Order) -> None:
    """Inside the payment transaction: release a waiting Purchase once the order is fully paid."""
    if order.payment_status != OrderPaymentStatus.PAID:
        return
    with transaction.atomic():
        delivery = (
            MetaEventDelivery.objects.select_for_update()
            .filter(event_id=purchase_event_id(order), status=DeliveryStatus.WAITING)
            .first()
        )
        if delivery is None:
            return
        delivery.status = DeliveryStatus.PENDING
        delivery.event_time = timezone.now()
        delivery.save(update_fields=["status", "event_time", "updated_at"])
        _enqueue(delivery)


def _enqueue(delivery: MetaEventDelivery) -> None:
    tenant_id = str(get_active_tenant_id())
    delivery_id = str(delivery.pk)

    def send() -> None:
        from .tasks import deliver_meta_event

        deliver_meta_event.delay(delivery_id, tenant_id=tenant_id)

    transaction.on_commit(send)


# --- Provider adapter -----------------------------------------------------------------------------


class MetaDeliveryError(Exception):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class MetaCapiClient(Protocol):
    def send(
        self, *, pixel_id: str, events: Sequence[Mapping[str, Any]], test_event_code: str
    ) -> None: ...


@dataclass
class GraphApiClient:
    """Meta's documented endpoint: ``POST https://graph.facebook.com/<version>/<pixel>/events``.
    The token travels in the request body, never in a URL that could be logged."""

    token: str
    version: str
    timeout: float = 10.0

    def send(
        self, *, pixel_id: str, events: Sequence[Mapping[str, Any]], test_event_code: str
    ) -> None:
        body: dict[str, Any] = {"data": list(events), "access_token": self.token}
        if test_event_code:
            body["test_event_code"] = test_event_code
        request = urllib.request.Request(
            f"https://graph.facebook.com/{self.version}/{pixel_id}/events",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise MetaDeliveryError(f"http_{response.status}", retryable=True)
        except urllib.error.HTTPError as exc:
            # 4xx means Meta refused the event (bad pixel, token or payload): retrying won't help,
            # except for rate limiting. The response body is never logged.
            raise MetaDeliveryError(
                f"http_{exc.code}", retryable=exc.code == 429 or exc.code >= 500
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise MetaDeliveryError("network", retryable=True) from None


def capi_client() -> MetaCapiClient:
    """The live adapter. Tests replace this function; they never reach Meta."""
    return GraphApiClient(
        token=settings.META_CAPI_ACCESS_TOKEN, version=settings.META_GRAPH_API_VERSION
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def build_event(delivery: MetaEventDelivery, row: TrackingSettings) -> dict[str, Any]:
    order = Order.objects.get(pk=delivery.order_id)
    items = list(OrderItem.objects.filter(order=order).order_by("position"))
    user_data: dict[str, Any] = {}
    if delivery.fbp:
        user_data["fbp"] = delivery.fbp
    if delivery.fbc:
        user_data["fbc"] = delivery.fbc
    if delivery.user_agent:
        user_data["client_user_agent"] = delivery.user_agent
    if row.meta_send_hashed_contact:
        if order.contact_email:
            user_data["em"] = [_sha256(order.contact_email.strip().lower())]
        if order.contact_phone:
            user_data["ph"] = [_sha256(order.contact_phone.lstrip("+"))]
    return {
        "event_name": delivery.event_name,
        "event_time": int(delivery.event_time.timestamp()),
        "event_id": delivery.event_id,
        "action_source": "website",
        "event_source_url": delivery.source_url,
        "user_data": user_data,
        "custom_data": {
            "currency": order.currency,
            # Meta's wire format is a JSON number; MateCart keeps the Decimal.
            "value": float(order.grand_total),
            "content_type": "product",
            "content_ids": [str(item.variant_id) for item in items],
            "num_items": len(items),
            "order_id": order.number,
            "payment_status": order.payment_status,
        },
    }


def _finish(delivery: MetaEventDelivery, status_value: str, error: str = "") -> None:
    delivery.status = status_value
    delivery.last_error = error
    if status_value == DeliveryStatus.SENT:
        delivery.sent_at = timezone.now()
    if status_value != DeliveryStatus.PENDING:
        delivery.fbp = delivery.fbc = delivery.user_agent = ""
    delivery.save(
        update_fields=[
            "status",
            "last_error",
            "sent_at",
            "fbp",
            "fbc",
            "user_agent",
            "updated_at",
        ]
    )


def deliver(delivery_id: Any) -> str:
    """Send one event. Returns the resulting status; 'retry' asks the task to try again."""
    now = timezone.now()
    with transaction.atomic():
        delivery = (
            MetaEventDelivery.objects.select_for_update(skip_locked=True)
            .filter(pk=delivery_id)
            .first()
        )
        if delivery is None:
            return "missing"
        stale = delivery.status == DeliveryStatus.SENDING and (
            delivery.claimed_at is None or now - delivery.claimed_at > STALE_CLAIM
        )
        if delivery.status != DeliveryStatus.PENDING and not stale:
            return str(delivery.status)
        row = current()
        if not capi_active(row):
            _finish(delivery, DeliveryStatus.DISCARDED, "integration_disabled")
            return DeliveryStatus.DISCARDED
        if not consent_module.still_authorised(delivery.consent_digest):
            _finish(delivery, DeliveryStatus.DISCARDED, "consent_withdrawn")
            return DeliveryStatus.DISCARDED
        delivery.status = DeliveryStatus.SENDING
        delivery.attempts += 1
        delivery.claimed_at = now
        delivery.save(update_fields=["status", "attempts", "claimed_at", "updated_at"])
        event = build_event(delivery, row)
        pixel_id, test_code = row.meta_pixel_id, row.meta_test_event_code
    try:
        capi_client().send(pixel_id=pixel_id, events=[event], test_event_code=test_code)
    except MetaDeliveryError as exc:
        with transaction.atomic():
            delivery = MetaEventDelivery.objects.select_for_update().get(pk=delivery_id)
            if exc.retryable and delivery.attempts < MAX_ATTEMPTS:
                _finish(delivery, DeliveryStatus.PENDING, exc.code)
                return "retry"
            _finish(delivery, DeliveryStatus.FAILED, exc.code)
            logger.warning("Meta Conversions API event failed (%s)", exc.code)
            return DeliveryStatus.FAILED
    with transaction.atomic():
        delivery = MetaEventDelivery.objects.select_for_update().get(pk=delivery_id)
        _finish(delivery, DeliveryStatus.SENT)
    return DeliveryStatus.SENT
