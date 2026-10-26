"""Minimal courier adapter boundary. No real provider is configured by default."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import cache
from typing import Literal, Protocol

from django.conf import settings
from django.utils.module_loading import import_string


class ProviderUnavailable(Exception):
    """The provider timed out or could not be reached; the remote outcome may be unknown."""


class BookingRejected(Exception):
    """The provider definitively refused the request without creating a booking."""

    def __init__(self, code: str = "rejected") -> None:
        self.code = code
        super().__init__(code)


class InvalidCallback(Exception):
    """The callback could not be authenticated and must change nothing."""


@dataclass(frozen=True)
class CourierItem:
    sku: str
    name: str
    quantity: Decimal


@dataclass(frozen=True)
class BookingRequest:
    reference: str
    order_number: str
    amount_to_collect: Decimal
    currency: str
    recipient_name: str
    contact_phone: str
    address_lines: tuple[str, ...]
    city: str
    region: str
    postal_code: str
    country: str
    notes: str
    items: tuple[CourierItem, ...]


@dataclass(frozen=True)
class BookingResult:
    provider_reference: str
    tracking_number: str = ""
    tracking_url: str = ""


@dataclass(frozen=True)
class TrackingUpdate:
    event_id: str
    booking_reference: str
    status: Literal["in_transit", "delivered", "delivery_failed", "return_to_sender"]
    occurred_at: datetime
    note: str = ""


class CourierProvider(Protocol):
    code: str
    display_name: str

    def book(self, request: BookingRequest) -> BookingResult:
        """Book idempotently using ``request.reference`` as the provider idempotency key."""
        ...

    def cancel(self, *, provider_reference: str, reference: str) -> None:
        """Cancel an eligible booking idempotently. ``reference`` is the local booking id."""
        ...

    def authenticate_tracking(self, body: bytes, headers: Mapping[str, str]) -> TrackingUpdate:
        """Authenticate and decode one tracking callback."""
        ...


@cache
def _load(path: str) -> CourierProvider:
    provider: CourierProvider = import_string(path)()
    return provider


def courier_provider() -> CourierProvider | None:
    path: str = settings.MATECART_COURIER_PROVIDER
    return _load(path) if path else None


def reset_provider_cache() -> None:
    _load.cache_clear()
