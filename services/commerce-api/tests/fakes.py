"""Controlled test doubles. Never configured outside tests: production settings refuse any
MATECART_SMS_PROVIDER under ``tests.``."""

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Literal, cast

from commerce.orders.providers import (
    Initiation,
    InvalidCallback,
    PaymentRequest,
    ProviderEvent,
    ProviderUnavailable,
    RefundOutcome,
)


@dataclass(frozen=True)
class SentSms:
    to: str
    body: str
    tenant_id: uuid.UUID
    purpose: str


class FakeSmsProvider:
    outbox: ClassVar[list[SentSms]] = []

    def send(self, *, to: str, body: str, tenant_id: uuid.UUID, purpose: str) -> None:
        self.outbox.append(SentSms(to=to, body=body, tenant_id=tenant_id, purpose=purpose))


class FakePaymentProvider:
    """A controlled online payment provider for tests (never configured outside them: production
    refuses ``MATECART_PAYMENT_PROVIDER`` under ``tests.``). Callbacks are HMAC-signed JSON."""

    code = "fakepay"
    supports_refunds = True
    secret = b"fake-callback-signing-key"
    initiated: ClassVar[list[str]] = []
    unavailable: ClassVar[bool] = False
    refund_status: ClassVar[str] = "succeeded"
    refunds: ClassVar[list[str]] = []

    @classmethod
    def reset(cls) -> None:
        cls.initiated.clear()
        cls.refunds.clear()
        cls.unavailable = False
        cls.refund_status = "succeeded"

    @classmethod
    def signed(cls, payload: dict[str, str]) -> tuple[bytes, str]:
        body = json.dumps(payload).encode()
        return body, hmac.new(cls.secret, body, hashlib.sha256).hexdigest()

    def initiate(self, request: PaymentRequest) -> Initiation:
        if self.unavailable:
            raise ProviderUnavailable
        self.initiated.append(request.reference)
        return Initiation(
            provider_ref=f"fp_{request.reference}",
            redirect_url=f"https://pay.example.test/checkout/{request.reference}",
        )

    def authenticate_callback(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent:
        expected = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("X-Fake-Signature", "")):
            raise InvalidCallback
        data = json.loads(body)
        return ProviderEvent(
            event_id=data["event_id"],
            reference=data["reference"],
            provider_ref=data["provider_ref"],
            transaction_ref=data["transaction_ref"],
            status=data["status"],
            amount=Decimal(data["amount"]),
            currency=data["currency"],
        )

    def refund(
        self, *, transaction_ref: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> RefundOutcome:
        if self.unavailable:
            raise ProviderUnavailable
        self.refunds.append(idempotency_key)
        status = cast(Literal["succeeded", "pending", "failed"], self.refund_status)
        return RefundOutcome(provider_ref=f"rf_{idempotency_key}", status=status)
