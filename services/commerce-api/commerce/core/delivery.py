"""Outbound email and SMS for authentication (D-041).

Messages are sent *after* the surrounding transaction commits, so a code is never delivered for a
challenge that rolled back, and never twice because of a retry. Codes and recipients are never
logged and never pass through the Celery broker. Phase 13 keeps this path for authentication
(it is never queued behind other notifications) and only chooses the store's email transport.
"""

import logging
import uuid
from functools import cache
from typing import Any, Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class SmsUnavailable(Exception):
    """No SMS provider is configured; SMS sign-in must stay disabled."""


class SmsDeliveryFailed(APIException):
    status_code = 503
    default_code = "delivery_unavailable"
    default_detail = "We couldn't send a text message right now. Please try again later."


class SmsRejected(Exception):
    """Raised by an SMS provider only when the gateway *definitely did not accept* the message
    (D-091): the request never left, or the gateway answered with a documented rejection.

    ``retryable``: a later attempt may succeed (throttled, gateway temporarily unavailable).
    Otherwise the rejection is permanent (invalid number, sender not registered). ``code`` is a
    short machine-readable reason; never put the recipient, message or credentials in it.

    Any *other* exception from ``send`` (a timeout after the request was written, a dropped
    connection, a malformed response) means the outcome is unknown: the message may have been
    accepted, so notifications never resend it automatically.
    """

    def __init__(self, code: str = "sms_rejected", *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class SmsProvider(Protocol):
    """Boundary for SMS gateways. ``tenant_id`` and ``purpose`` let a provider attribute usage to
    the right store (included allowance first, then prepaid credits; Phase 13 and billing).

    Optional idempotency (D-091): a provider whose gateway deduplicates by a client key sets the
    class attribute ``supports_idempotency_key = True`` and accepts an ``idempotency_key``
    keyword argument. Notifications then pass one stable key per notification, so a resend of a
    message the gateway already accepted is not delivered twice. Providers without the attribute
    are never given the argument.
    """

    def send(
        self, *, to: str, body: str, tenant_id: uuid.UUID, purpose: str, sender: str = ""
    ) -> None:
        """``sender``: the provider-approved sender id (Phase 13); empty = provider default.
        Returning normally means the gateway accepted the message."""


@cache
def _provider(path: str) -> SmsProvider:
    provider: SmsProvider = import_string(path)()
    return provider


def sms_provider() -> SmsProvider | None:
    path: str = settings.MATECART_SMS_PROVIDER
    return _provider(path) if path else None


def send_sms(*, to: str, body: str, tenant_id: uuid.UUID, purpose: str) -> None:
    provider = sms_provider()
    if provider is None:
        raise SmsUnavailable("SMS delivery is not configured.")

    def deliver() -> None:
        try:
            provider.send(to=to, body=body, tenant_id=tenant_id, purpose=purpose)
        except Exception:
            logger.error("SMS delivery failed (purpose=%s)", purpose)
            raise SmsDeliveryFailed() from None
        logger.info("SMS sent (purpose=%s)", purpose)
        # Phase 13 (D-089): usage accounting only (segments, never the message or number).
        from commerce.notifications.transport import count_auth_sms

        count_auth_sms(body=body, purpose=purpose, tenant_id=tenant_id)

    transaction.on_commit(deliver)


def send_email(*, to: str, subject: str, template: str, context: dict[str, Any]) -> None:
    # Phase 13 (D-086): the store's connected transport, chosen now while the request's tenant
    # is active; ``None`` keeps the deployment default. Still sent synchronously after commit:
    # codes and links never enter Celery or a stored record.
    from commerce.notifications.transport import auth_transport, send_now

    transport = auth_transport()

    def deliver() -> None:
        text = render_to_string(f"email/{template}.txt", context)
        html = render_to_string(f"email/{template}.html", context)
        if transport is not None:
            send_now(transport, to=to, subject=subject, text=text, html=html)
            return
        message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
        message.attach_alternative(html, "text/html")
        try:
            message.send()
        except Exception:
            # Provider exception strings may contain recipients or credentials. Public email
            # flows remain enumeration-safe; operators receive a payload-free failure signal.
            logger.error("Email delivery failed (template=%s)", template)
            return
        logger.info("Email sent (template=%s)", template)

    transaction.on_commit(deliver, robust=True)
