"""Controlled test doubles. Never configured outside tests: production settings refuse any
MATECART_SMS_PROVIDER under ``tests.``."""

import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Literal, cast

from commerce.fulfillment.providers import (
    BookingRequest,
    BookingResult,
    TrackingUpdate,
)
from commerce.fulfillment.providers import (
    InvalidCallback as InvalidCourierCallback,
)
from commerce.fulfillment.providers import (
    ProviderUnavailable as CourierProviderUnavailable,
)
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
            status=cast(Literal["succeeded", "failed"], data["status"]),
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


class FakeCourierProvider:
    """Signed, idempotent courier fake for isolated adapter and callback tests."""

    code = "fakecourier"
    display_name = "Fake Courier"
    secret = b"fake-courier-callback-key"
    booked: ClassVar[dict[str, BookingResult]] = {}
    unavailable: ClassVar[bool] = False

    @classmethod
    def reset(cls) -> None:
        cls.booked.clear()
        cls.unavailable = False

    @classmethod
    def signed(cls, payload: dict[str, str]) -> tuple[bytes, str]:
        body = json.dumps(payload).encode()
        return body, hmac.new(cls.secret, body, hashlib.sha256).hexdigest()

    def book(self, request: BookingRequest) -> BookingResult:
        if self.unavailable:
            raise CourierProviderUnavailable
        result = self.booked.get(request.reference)
        if result is None:
            result = BookingResult(
                provider_reference=f"fc_{request.reference}",
                tracking_number=f"TRACK-{len(self.booked) + 1:06d}",
                tracking_url=f"https://courier.example.test/track/{request.reference}",
            )
            self.booked[request.reference] = result
        return result

    def cancel(self, *, provider_reference: str, reference: str) -> None:
        del provider_reference, reference
        if self.unavailable:
            raise CourierProviderUnavailable

    def authenticate_tracking(self, body: bytes, headers: Mapping[str, str]) -> TrackingUpdate:
        expected = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("X-Fake-Courier-Signature", "")):
            raise InvalidCourierCallback
        data = json.loads(body)
        return TrackingUpdate(
            event_id=data["event_id"],
            booking_reference=data["booking_reference"],
            status=cast(
                Literal["in_transit", "delivered", "delivery_failed", "return_to_sender"],
                data["status"],
            ),
            occurred_at=datetime.fromisoformat(data["occurred_at"]),
            note=data.get("note", ""),
        )
