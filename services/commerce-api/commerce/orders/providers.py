"""Payment provider boundary (D-072).

A deployment configures at most one online payment provider with ``MATECART_PAYMENT_PROVIDER``
(a dotted path to a class implementing ``PaymentProvider``). None is configured by default, and
online payment is then unavailable everywhere: checkout does not offer it and the
``online_payments`` module cannot be switched on. Production refuses test fakes (``tests.*``).

Adapters translate one gateway's API. They own its credentials, signature checks and any
server-to-server validation the gateway requires; the order domain never sees raw gateway data,
card details or secrets, and trusts nothing a browser sends.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import Literal, Protocol

from django.conf import settings
from django.utils.module_loading import import_string


class ProviderUnavailable(Exception):
    """The provider could not be reached or timed out. The outcome is unknown, not failed."""


class InvalidCallback(Exception):
    """A callback failed authentication or could not be decoded. It changes nothing."""


@dataclass(frozen=True)
class PaymentRequest:
    reference: str
    order_number: str
    amount: Decimal
    currency: str
    return_url: str


@dataclass(frozen=True)
class Initiation:
    provider_ref: str
    redirect_url: str


@dataclass(frozen=True)
class ProviderEvent:
    """An authenticated result. ``reference`` is the intent id given in ``PaymentRequest``."""

    event_id: str
    reference: str
    provider_ref: str
    transaction_ref: str
    status: Literal["succeeded", "failed"]
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class RefundOutcome:
    provider_ref: str
    status: Literal["succeeded", "pending", "failed"]


class PaymentProvider(Protocol):
    code: str
    supports_refunds: bool

    def initiate(self, request: PaymentRequest) -> Initiation:
        """Start a hosted payment. Raises ``ProviderUnavailable`` on timeouts."""
        ...

    def authenticate_callback(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent:
        """Verify and decode a callback (signature and any gateway validation call).
        Raises ``InvalidCallback`` when it is not genuine."""
        ...

    def refund(
        self, *, transaction_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> RefundOutcome:
        """Refund part or all of a payment, idempotently per key."""
        ...


@cache
def _load(path: str) -> PaymentProvider:
    provider: PaymentProvider = import_string(path)()
    return provider


def payment_provider() -> PaymentProvider | None:
    path: str = settings.MATECART_PAYMENT_PROVIDER
    return _load(path) if path else None


def reset_provider_cache() -> None:
    _load.cache_clear()
