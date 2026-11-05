"""Website live chat: settings, operating hours and visitor identities (D-093, D-095).

A guest visitor gets an opaque 256-bit chat credential in an HttpOnly cookie scoped to
``/api/chat`` (``mc_chat``). Only its SHA-256 hash is stored. It identifies one website
participant and nothing else: it is not a customer session, never reaches merchant routes and
cannot read another visitor's messages. Signed-in customers chat as themselves (their own customer
session); a previous guest chat joins their account only when they confirm it while holding both
credentials (``link_guest_to_customer``).
"""

from __future__ import annotations

import re
import zoneinfo
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.core.domain import ConflictError, DomainError
from commerce.core.security import hash_token, new_token
from commerce.identity.models import AccountType, User
from commerce.stores import modules
from commerce.stores.models import StoreSettings

from . import realtime
from .models import (
    ChannelType,
    ChatLanguages,
    LauncherPosition,
    LiveChatSettings,
    Participant,
)

COOKIE_NAME = "mc_chat"
COOKIE_PATH = "/api/chat"
GUEST_LIFETIME = timedelta(days=30)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
TEXT_FIELDS = (
    "welcome_en",
    "welcome_bn",
    "button_label_en",
    "button_label_bn",
    "away_en",
    "away_bn",
)
SETTINGS_FIELDS = (*TEXT_FIELDS, "hours", "languages", "position")
DEFAULTS = {
    "welcome_en": "Hi! Send us a message and our team will reply here.",
    "welcome_bn": "স্বাগতম! বার্তা পাঠান, আমাদের টিম এখানেই উত্তর দেবে।",
    "button_label_en": "Chat with us",
    "button_label_bn": "চ্যাট করুন",
    "away_en": "We are away right now. Leave a message and we will reply here.",
    "away_bn": "আমরা এখন অনুপস্থিত। বার্তা রেখে যান, আমরা এখানেই উত্তর দেব।",
}


class ChatUnavailable(DomainError):
    status_code = 403
    default_detail = "Live chat is not available in this store."
    default_code = "live_chat_disabled"


def enabled() -> bool:
    return "live_chat" in modules.enabled_modules()


def require_enabled() -> None:
    if not enabled():
        raise ChatUnavailable()


def current() -> LiveChatSettings:
    """The store's settings row, created with defaults on first use."""
    row = LiveChatSettings.objects.first()
    if row is not None:
        return row
    try:
        with transaction.atomic():
            return LiveChatSettings.objects.create(**DEFAULTS)
    except IntegrityError:
        return LiveChatSettings.objects.get()


def _clean_hours(value: Any) -> dict[str, list[list[str]]]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError({"hours": ["Use opening hours per weekday."]})
    hours: dict[str, list[list[str]]] = {}
    for day, ranges in value.items():
        if day not in DAYS or not isinstance(ranges, list) or len(ranges) > 3:
            raise ValidationError({"hours": ["Use up to three time ranges per weekday."]})
        cleaned: list[list[str]] = []
        for item in ranges:
            if (
                not isinstance(item, list | tuple)
                or len(item) != 2
                or not all(isinstance(part, str) and _HHMM.match(part) for part in item)
                or item[0] >= item[1]
            ):
                raise ValidationError(
                    {"hours": ["Each range needs a start before its end, as HH:MM."]}
                )
            cleaned.append([item[0], item[1]])
        if cleaned:
            hours[day] = sorted(cleaned)
    return hours


def save(*, data: Mapping[str, Any], actor: User) -> LiveChatSettings:
    with transaction.atomic():
        row = current()
        row = LiveChatSettings.objects.select_for_update().get(pk=row.pk)
        before = {field: getattr(row, field) for field in SETTINGS_FIELDS}
        for field in TEXT_FIELDS:
            if field in data:
                # Plain text only (D-063): no markup is interpreted anywhere it is shown.
                setattr(row, field, str(data[field] or "").strip())
        if "hours" in data:
            row.hours = _clean_hours(data["hours"])
        if "languages" in data:
            if data["languages"] not in ChatLanguages.values:
                raise ValidationError({"languages": ["Choose the chat languages."]})
            row.languages = data["languages"]
        if "position" in data:
            if data["position"] not in LauncherPosition.values:
                raise ValidationError({"position": ["Choose where the chat button sits."]})
            row.position = data["position"]
        row.full_clean()
        row.save()
        record_audit_event(
            action="communications.live_chat.updated",
            resource=row,
            actor=actor,
            changes=field_changes(
                before,
                {field: getattr(row, field) for field in SETTINGS_FIELDS},
                fields=["hours", "languages", "position"],
            ),
        )
    return row


def _zone() -> zoneinfo.ZoneInfo:
    name = StoreSettings.objects.values_list("timezone", flat=True).first() or "UTC"
    return zoneinfo.ZoneInfo(name)


def within_hours(row: LiveChatSettings, at: datetime | None = None) -> bool:
    """Inside the configured opening hours in the store's time zone; no hours = always."""
    if not row.hours:
        return True
    local = (at or timezone.now()).astimezone(_zone())
    day = DAYS[local.weekday()]
    clock = local.strftime("%H:%M")
    return any(start <= clock < end for start, end in row.hours.get(day, []))


@dataclass(frozen=True)
class Availability:
    within_hours: bool
    agents_online: bool

    @property
    def online(self) -> bool:
        return self.within_hours and self.agents_online


def availability(row: LiveChatSettings | None = None) -> Availability:
    """Genuine state only: "online" needs opening hours *and* a staff member with the inbox
    open right now. Otherwise the widget shows the away message (D-093)."""
    return Availability(within_hours(row or current()), realtime.agents_online())


# --- Visitor identity -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Visitor:
    participant: Participant | None
    customer: User | None
    new_token: str | None = None


def _guest(token: str | None) -> Participant | None:
    if not token or len(token) > 128:
        return None
    row = (
        Participant.objects.filter(
            channel=ChannelType.WEBSITE,
            token_hash=hash_token(token),
            token_expires_at__gt=timezone.now(),
        )
        .select_related("customer")
        .first()
    )
    return row


def _customer_participant(customer: User, *, create: bool) -> Participant | None:
    external_id = f"customer-{customer.pk.hex}"
    row = Participant.objects.filter(channel=ChannelType.WEBSITE, external_id=external_id).first()
    if row is not None or not create:
        return row
    try:
        with transaction.atomic():
            return Participant.objects.create(
                channel=ChannelType.WEBSITE,
                external_id=external_id,
                display_name=customer.full_name[:120],
                customer=customer,
                customer_linked_at=timezone.now(),
            )
    except IntegrityError:
        return Participant.objects.get(channel=ChannelType.WEBSITE, external_id=external_id)


def resolve_visitor(
    *, guest_token: str | None, customer: User | None, create: bool = False
) -> Visitor:
    """The participant behind a widget request. A signed-in customer always chats as their own
    participant; otherwise the guest credential decides. ``create`` issues a new guest identity
    (only when the visitor actually starts a conversation)."""
    if customer is not None and customer.account_type == AccountType.CUSTOMER:
        return Visitor(_customer_participant(customer, create=create), customer)
    guest = _guest(guest_token)
    if guest is not None:
        if guest.token_expires_at and guest.token_expires_at - timezone.now() < GUEST_LIFETIME / 2:
            Participant.objects.filter(pk=guest.pk).update(
                token_expires_at=timezone.now() + GUEST_LIFETIME
            )
        return Visitor(guest, None)
    if not create:
        return Visitor(None, None)
    token = new_token()
    row = Participant.objects.create(
        channel=ChannelType.WEBSITE,
        external_id=f"visitor-{new_token()[:22]}",
        token_hash=hash_token(token),
        token_expires_at=timezone.now() + GUEST_LIFETIME,
    )
    return Visitor(row, None, token)


def set_cookie(response: HttpResponse, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(GUEST_LIFETIME.total_seconds()),
        path=COOKIE_PATH,
        secure=settings.MATECART_SECURE_COOKIES,
        httponly=True,
        samesite="Lax",
    )


def clear_cookie(response: HttpResponse) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, samesite="Lax")


def link_guest_to_customer(*, guest_token: str | None, customer: User) -> Participant:
    """The visitor, signed in, confirms that the guest chat in this browser is theirs. Proof is
    holding both credentials at once; matching names, emails or phone numbers is never used."""
    guest = _guest(guest_token)
    if guest is None:
        raise ConflictError("There is no guest chat in this browser to link.", "no_guest_chat")
    if guest.customer_id is not None and guest.customer_id != customer.pk:
        raise ConflictError("This chat belongs to another account.", "chat_owned")
    from .models import ConversationEvent, EventKind
    from .services import active_conversation

    with transaction.atomic():
        locked = Participant.objects.select_for_update().get(pk=guest.pk)
        if locked.customer_id is None:
            locked.customer = customer
            locked.customer_linked_at = timezone.now()
            if not locked.display_name:
                locked.display_name = customer.full_name[:120]
            locked.save(update_fields=["customer", "customer_linked_at", "display_name"])
            conversation = active_conversation(locked)
            if conversation is not None:
                ConversationEvent.objects.create(
                    conversation=conversation, kind=EventKind.CUSTOMER_LINKED, actor=customer
                )
            record_audit_event(
                action="communications.participant.customer_linked",
                resource=locked,
                actor=customer,
                changes={"method": "visitor_confirmed"},
            )
    return locked


def public_settings(locale: str) -> dict[str, Any]:
    row = current()
    state = availability(row)
    lang = "bn" if locale == "bn" else "en"

    def text(field: str) -> str:
        value = str(getattr(row, f"{field}_{lang}") or getattr(row, f"{field}_en") or "")
        return value or DEFAULTS[f"{field}_{lang}"]

    return {
        "enabled": True,
        "welcome": text("welcome"),
        "button_label": text("button_label"),
        "away_message": text("away"),
        "languages": row.languages.split(","),
        "position": row.position,
        "online": state.online,
        "within_hours": state.within_hours,
    }
