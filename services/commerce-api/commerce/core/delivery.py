"""Outbound email and SMS for authentication (D-041).

Messages are sent *after* the surrounding transaction commits, so a code is never delivered for a
challenge that rolled back, and never twice because of a retry. Codes and recipients are never
logged and never pass through the Celery broker. Queued delivery with retries, templates per
store and the managed masking SMS gateway arrive with the notification engine (Phase 13).
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


class SmsProvider(Protocol):
    """Boundary for SMS gateways. ``tenant_id`` and ``purpose`` let a provider attribute usage to
    the right store (included allowance first, then prepaid credits; Phase 13 and billing)."""

    def send(self, *, to: str, body: str, tenant_id: uuid.UUID, purpose: str) -> None: ...


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

    transaction.on_commit(deliver)


def send_email(*, to: str, subject: str, template: str, context: dict[str, Any]) -> None:
    def deliver() -> None:
        text = render_to_string(f"email/{template}.txt", context)
        html = render_to_string(f"email/{template}.html", context)
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
