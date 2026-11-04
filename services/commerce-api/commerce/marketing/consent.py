"""Visitor consent (D-083).

Categories: strictly necessary (always on: sign-in, cart, checkout, security and the consent
cookie itself), analytics and marketing (both off until the visitor opts in).

The browser keeps its decision in the ``mc_consent`` cookie, ``1:<a><m>:<id>`` where ``a`` and
``m`` are 0/1 and ``id`` is a random 128-bit consent id. Every decision is also appended to
``ConsentRecord`` under the SHA-256 of that id, so withdrawals can be honoured server-side (for
example by discarding unsent Conversions API events). The record holds no IP address, user
agent or account link. The cookie is readable by page scripts on purpose: they need it to decide
whether optional scripts may load.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ConsentRecord, DeliveryStatus, MetaEventDelivery, VisitorPresence

COOKIE = "mc_consent"
VISITOR_COOKIE = "mc_vid"
SOURCE_COOKIE = "mc_src"
POLICY_VERSION = "1"
COOKIE_MAX_AGE = int(timedelta(days=180).total_seconds())
_VALUE = re.compile(r"^1:([01])([01]):([0-9a-f]{32})$")
_VISITOR = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class Consent:
    analytics: bool
    marketing: bool
    consent_id: str

    @property
    def digest(self) -> str:
        return digest(self.consent_id)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def parse(value: str | None) -> Consent | None:
    match = _VALUE.match(value or "")
    if match is None:
        return None
    return Consent(match.group(1) == "1", match.group(2) == "1", match.group(3))


def from_request(request: Any) -> Consent | None:
    return parse(request.COOKIES.get(COOKIE))


def visitor_id(request: Any, consent: Consent | None) -> str | None:
    """The browser's random visitor id, only when analytics consent was given."""
    if consent is None or not consent.analytics:
        return None
    value = request.COOKIES.get(VISITOR_COOKIE, "")
    return value if _VISITOR.match(value) else None


def record(*, request: Any, analytics: bool, marketing: bool, source: str) -> Consent:
    previous = from_request(request)
    consent = Consent(
        analytics, marketing, previous.consent_id if previous else secrets.token_hex(16)
    )
    with transaction.atomic():
        ConsentRecord.objects.create(
            subject_digest=consent.digest,
            analytics=analytics,
            marketing=marketing,
            policy_version=POLICY_VERSION,
            source=source,
            recorded_at=timezone.now(),
        )
        if not marketing:
            # Withdrawal: server-side marketing events not yet sent lose their authorisation.
            MetaEventDelivery.objects.filter(
                consent_digest=consent.digest,
                status__in=(DeliveryStatus.PENDING, DeliveryStatus.WAITING),
            ).update(
                status=DeliveryStatus.DISCARDED,
                fbp="",
                fbc="",
                user_agent="",
                last_error="consent_withdrawn",
                updated_at=timezone.now(),
            )
        if not analytics:
            from .analytics import visitor_digest

            visitor = request.COOKIES.get(VISITOR_COOKIE, "")
            if _VISITOR.match(visitor):
                VisitorPresence.objects.filter(visitor_digest=visitor_digest(visitor)).delete()
    return consent


def still_authorised(consent_digest: str) -> bool:
    """Whether the latest decision for this consent id still allows marketing."""
    latest = (
        ConsentRecord.objects.filter(subject_digest=consent_digest)
        .order_by("-recorded_at", "-pk")
        .values_list("marketing", flat=True)
        .first()
    )
    return bool(latest)


def set_cookie(response: Any, consent: Consent) -> None:
    response.set_cookie(
        COOKIE,
        f"1:{int(consent.analytics)}{int(consent.marketing)}:{consent.consent_id}",
        max_age=COOKIE_MAX_AGE,
        path="/",
        secure=settings.MATECART_SECURE_COOKIES,
        httponly=False,
        samesite="Lax",
    )
    if not consent.analytics:
        response.delete_cookie(VISITOR_COOKIE, path="/", samesite="Lax")
        response.delete_cookie(SOURCE_COOKIE, path="/", samesite="Lax")
    if not consent.marketing:
        response.delete_cookie("_fbp", path="/", samesite="Lax")
        response.delete_cookie("_fbc", path="/", samesite="Lax")
