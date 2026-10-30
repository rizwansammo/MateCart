"""Payments, provider callbacks, cash-on-delivery collection and refunds (D-072, D-073).

Only verified evidence changes money: an authenticated provider event, or a merchant recording
cash they collected or returned. Browser redirects and request bodies never mark anything paid.
Provider calls run outside database locks; their outcome is applied in a short locked transaction.
"""

import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from commerce.audit.services import record_audit_event
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User
from commerce.pricing import services as pricing

from .models import (
    IntentStatus,
    Order,
    OrderPaymentStatus,
    OrderStatus,
    PaymentIntent,
    PaymentMethod,
    PaymentTransaction,
    ProviderCallback,
    Refund,
    RefundMethod,
    RefundStatus,
    TransactionKind,
    TransactionStatus,
)
from .providers import (
    InvalidCallback,
    PaymentRequest,
    ProviderEvent,
    ProviderUnavailable,
    RefundOutcome,
    payment_provider,
)
from .services import _event, apply_transition, key_hash

logger = logging.getLogger(__name__)
COD_PROVIDER = "cod"


class PaymentUnavailable(ConflictError):
    default_code = "payment_unavailable"
    default_detail = "Online payment is not available for this order."


class PaymentProviderUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "payment_provider_unavailable"
    default_detail = "The payment service did not respond. Please try again."


class CallbackRejected(DomainError):
    default_code = "invalid_callback"
    default_detail = "The callback could not be verified."


def sync_payment_status(order: Order) -> None:
    """The order's payment status follows its verified amounts, never a flag set by hand."""
    if order.amount_paid <= 0:
        return
    if order.amount_refunded >= order.amount_paid:
        order.payment_status = OrderPaymentStatus.REFUNDED
    elif order.amount_refunded > 0:
        order.payment_status = OrderPaymentStatus.PARTIALLY_REFUNDED
    elif order.amount_paid >= order.grand_total:
        order.payment_status = OrderPaymentStatus.PAID


# --- Online payment -------------------------------------------------------------------------------


def start_online_payment(*, order: Order) -> PaymentIntent:
    """Create or reuse the order's open payment attempt and return its provider redirect."""
    provider = payment_provider()
    if provider is None or order.payment_method != PaymentMethod.ONLINE:
        raise PaymentUnavailable()
    with transaction.atomic():
        target = Order.objects.select_for_update().get(pk=order.pk)
        if (
            target.status != OrderStatus.PENDING_PAYMENT
            or target.payment_due_at is None
            or target.payment_due_at <= timezone.now()
        ):
            raise ConflictError("This order is no longer waiting for payment.", "payment_closed")
        intent = PaymentIntent.objects.filter(
            order=target, status__in=(IntentStatus.CREATED, IntentStatus.REQUIRES_ACTION)
        ).first()
        if intent is not None and intent.status == IntentStatus.REQUIRES_ACTION:
            return intent
        if intent is None:
            intent = PaymentIntent.objects.create(
                order=target,
                method=PaymentMethod.ONLINE,
                provider=provider.code,
                amount=target.grand_total,
                currency=target.currency,
                status=IntentStatus.CREATED,
            )
    # A CREATED intent is re-initiated with the same reference; providers treat it idempotently.
    try:
        initiation = provider.initiate(
            PaymentRequest(
                reference=str(intent.pk),
                order_number=order.number,
                amount=intent.amount,
                currency=intent.currency,
                return_url=f"{settings.MATECART_PUBLIC_BASE_URL}/orders/{order.pk}",
            )
        )
    except ProviderUnavailable as exc:
        raise PaymentProviderUnavailable() from exc
    with transaction.atomic():
        intent = PaymentIntent.objects.select_for_update().get(pk=intent.pk)
        if intent.status == IntentStatus.CREATED:
            intent.provider_ref = initiation.provider_ref[:120]
            intent.redirect_url = initiation.redirect_url
            intent.status = IntentStatus.REQUIRES_ACTION
            intent.save(update_fields=["provider_ref", "redirect_url", "status", "updated_at"])
        return intent


def _intent_id(reference: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(reference)
    except ValueError:
        return None


def _apply_event(code: str, event: ProviderEvent) -> str:
    intent_id = _intent_id(event.reference)
    found = PaymentIntent.objects.filter(pk=intent_id, provider=code).first() if intent_id else None
    if found is None:
        return "unknown_intent"
    order = Order.objects.select_for_update().get(pk=found.order_id)
    intent = PaymentIntent.objects.select_for_update().get(pk=found.pk)
    if intent.provider_ref and event.provider_ref != intent.provider_ref:
        return "reference_mismatch"
    if PaymentTransaction.objects.filter(
        provider=code, provider_ref=event.transaction_ref
    ).exists():
        return "duplicate_transaction"
    if event.status == "failed":
        if intent.status == IntentStatus.SUCCEEDED:
            return "ignored_after_success"
        if event.transaction_ref and event.amount > 0:
            PaymentTransaction.objects.create(
                order=order,
                intent=intent,
                kind=TransactionKind.PAYMENT,
                status=TransactionStatus.FAILED,
                provider=code,
                provider_ref=event.transaction_ref[:120],
                amount=event.amount,
                currency=event.currency,
                evidence={"event_id": event.event_id, "status": event.status},
            )
        if intent.status in (IntentStatus.CREATED, IntentStatus.REQUIRES_ACTION):
            intent.status = IntentStatus.FAILED
            intent.failure_code = "provider_declined"
            intent.provider_ref = intent.provider_ref or event.provider_ref[:120]
            intent.save(update_fields=["status", "failure_code", "provider_ref", "updated_at"])
            _event(
                order,
                "payment_failed",
                from_status=order.status,
                actor=None,
                actor_label="provider",
            )
        return "payment_failed"
    if event.amount != intent.amount or event.currency != intent.currency:
        record_audit_event(
            action="order.payment.mismatch",
            resource=intent,
            actor_label="provider",
            changes={"number": order.number, "provider": code},
        )
        return "amount_mismatch"
    PaymentTransaction.objects.create(
        order=order,
        intent=intent,
        kind=TransactionKind.PAYMENT,
        status=TransactionStatus.SUCCEEDED,
        provider=code,
        provider_ref=event.transaction_ref[:120],
        amount=event.amount,
        currency=event.currency,
        evidence={"event_id": event.event_id, "status": event.status},
    )
    intent.status = IntentStatus.SUCCEEDED
    intent.failure_code = ""
    intent.provider_ref = intent.provider_ref or event.provider_ref[:120]
    intent.save(update_fields=["status", "failure_code", "provider_ref", "updated_at"])
    order.amount_paid += event.amount
    sync_payment_status(order)
    record_audit_event(
        action="order.payment.succeeded",
        resource=order,
        actor_label="provider",
        changes={"number": order.number, "amount": event.amount, "currency": event.currency},
    )
    if order.status == OrderStatus.PENDING_PAYMENT:
        apply_transition(
            order,
            OrderStatus.CONFIRMED,
            event="payment_confirmed",
            actor=None,
            actor_label="provider",
        )
    else:
        # A late payment never revives a cancelled order; it is kept on record for a refund.
        order.save(update_fields=["amount_paid", "payment_status", "updated_at"])
        _event(
            order,
            "payment_after_cancellation"
            if order.status == OrderStatus.CANCELLED
            else "payment_received",
            from_status=order.status,
            actor=None,
            actor_label="provider",
        )
    from commerce.aftersales.digital import issue_for_order

    issue_for_order(order)
    return "payment_succeeded"


def process_callback(*, provider_code: str, body: bytes, headers: Any) -> str:
    """Authenticate, deduplicate and apply one provider callback. Returns its outcome."""
    provider = payment_provider()
    if provider is None or provider.code != provider_code:
        raise NotFound("This payment provider is not configured.")
    try:
        event = provider.authenticate_callback(body, headers)
    except InvalidCallback as exc:
        logger.warning("payment.callback.rejected", extra={"provider": provider_code})
        raise CallbackRejected() from exc
    with transaction.atomic():
        callback = ProviderCallback(
            provider=provider.code,
            event_id=event.event_id[:120],
            body_sha256=hashlib.sha256(body).hexdigest(),
            summary={
                "status": event.status,
                "amount": str(event.amount),
                "currency": event.currency,
            },
        )
        try:
            with transaction.atomic():
                callback.save()
        except IntegrityError:
            return "duplicate"
        outcome = _apply_event(provider.code, event)
        callback.outcome = outcome
        callback.save(update_fields=["outcome"])
        return outcome


# --- Cash on delivery -----------------------------------------------------------------------------


def record_cod_collection(*, order_id: Any, actor: User) -> Order:
    """The merchant confirms the cash was received. Idempotent: the collection is recorded once."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise PermissionDenied("This order is not available.")
        if order.payment_method != PaymentMethod.CASH_ON_DELIVERY:
            raise ConflictError("This order is not cash on delivery.", "not_cash_on_delivery")
        if PaymentTransaction.objects.filter(
            order=order, kind=TransactionKind.COD_COLLECTION
        ).exists():
            return order
        if order.status not in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING):
            raise ConflictError("Record cash only for confirmed orders.", "order_not_confirmed")
        intent = PaymentIntent.objects.select_for_update().get(
            order=order, status=IntentStatus.AWAITING_COLLECTION
        )
        PaymentTransaction.objects.create(
            order=order,
            intent=intent,
            kind=TransactionKind.COD_COLLECTION,
            status=TransactionStatus.SUCCEEDED,
            provider=COD_PROVIDER,
            provider_ref=f"collection:{order.pk}",
            amount=intent.amount,
            currency=intent.currency,
            recorded_by=actor,
        )
        intent.status = IntentStatus.SUCCEEDED
        intent.save(update_fields=["status", "updated_at"])
        order.amount_paid += intent.amount
        sync_payment_status(order)
        order.save(update_fields=["amount_paid", "payment_status", "updated_at"])
        _event(order, "cod_collected", from_status=order.status, actor=actor)
        record_audit_event(
            action="order.cod.collected",
            resource=order,
            actor=actor,
            changes={"number": order.number, "amount": intent.amount, "currency": intent.currency},
        )
        return order


# --- Refunds --------------------------------------------------------------------------------------


def _refundable(payment: PaymentTransaction) -> Decimal:
    used: Decimal = Refund.objects.filter(
        payment=payment, status__in=(RefundStatus.PENDING, RefundStatus.SUCCEEDED)
    ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
    return Decimal(payment.amount) - used


def _complete_refund(order: Order, refund: Refund, outcome_ref: str, actor: User | None) -> None:
    refund.status = RefundStatus.SUCCEEDED
    refund.completed_at = timezone.now()
    refund.provider_ref = outcome_ref[:120]
    refund.save(update_fields=["status", "completed_at", "provider_ref", "updated_at"])
    PaymentTransaction.objects.create(
        order=order,
        kind=TransactionKind.REFUND,
        status=TransactionStatus.SUCCEEDED,
        provider=refund.payment.provider,
        provider_ref=f"refund:{refund.pk}",
        amount=refund.amount,
        currency=refund.currency,
        evidence={"method": refund.method, "provider_ref": refund.provider_ref},
        recorded_by=actor,
    )
    order.amount_refunded += refund.amount
    sync_payment_status(order)
    order.save(update_fields=["amount_refunded", "payment_status", "updated_at"])
    _event(order, "refunded", from_status=order.status, actor=actor, actor_label="provider")
    record_audit_event(
        action="order.refund.succeeded",
        resource=refund,
        actor=actor,
        actor_label="provider" if actor is None else "",
        changes={"number": order.number, "amount": refund.amount, "method": refund.method},
    )
    from commerce.aftersales.digital import revoke_for_order

    revoke_for_order(order)


def _apply_refund_outcome(refund_id: Any, outcome: RefundOutcome) -> Refund:
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund_id)
        order = Order.objects.select_for_update().get(pk=refund.order_id)
        refund = Refund.objects.select_related("payment").get(pk=refund.pk)
        if refund.status != RefundStatus.PENDING:
            return refund
        if outcome.status == "succeeded":
            _complete_refund(order, refund, outcome.provider_ref, None)
        elif outcome.status == "failed":
            refund.status = RefundStatus.FAILED
            refund.failure_code = "provider_declined"
            refund.completed_at = timezone.now()
            refund.save(update_fields=["status", "failure_code", "completed_at", "updated_at"])
            record_audit_event(
                action="order.refund.failed",
                resource=refund,
                actor_label="provider",
                changes={"number": order.number, "amount": refund.amount},
            )
        else:
            refund.provider_ref = outcome.provider_ref[:120]
            refund.save(update_fields=["provider_ref", "updated_at"])
        return refund


def request_refund(
    *,
    order_id: Any,
    amount: Decimal,
    reason: str,
    idempotency_key: str,
    actor: User,
    return_case_id: Any | None = None,
) -> Refund:
    """Refund verified money, never more than was paid or collected.

    Online payments are refunded through the provider that took them; the refund stays pending
    until the provider confirms it. Cash collected on delivery is refunded by hand and recorded
    by the merchant as a manual refund. A retry with the same key never refunds twice.
    """
    key = idempotency_key.strip()
    if not 8 <= len(key) <= 100:
        raise ValidationError({"idempotency_key": ["Use an idempotency key of 8-100 characters."]})
    digest = key_hash(key)
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise PermissionDenied("This order is not available.")
        existing = (
            Refund.objects.select_related("payment").filter(idempotency_key_hash=digest).first()
        )
        if existing is not None:
            if existing.order_id != order.pk or existing.amount != amount:
                raise ConflictError(
                    "This idempotency key was already used for a different refund.",
                    "idempotency_conflict",
                )
            from commerce.aftersales.services import assert_refund_replay

            assert_refund_replay(refund=existing, case_id=return_case_id)
            refund = existing
        else:
            from commerce.aftersales.services import (
                link_requested_refund,
                validate_refund_attribution,
            )

            return_case = (
                validate_refund_attribution(case_id=return_case_id, order=order, amount=amount)
                if return_case_id is not None
                else None
            )
            precision = pricing.currency_precision(order.currency)
            if amount <= 0 or amount != amount.quantize(Decimal(1).scaleb(-precision)):
                raise ValidationError(
                    {"amount": ["Enter a positive amount in the order currency."]}
                )
            payment = next(
                (
                    row
                    for row in PaymentTransaction.objects.filter(
                        order=order,
                        status=TransactionStatus.SUCCEEDED,
                        kind__in=(TransactionKind.PAYMENT, TransactionKind.COD_COLLECTION),
                    ).order_by("occurred_at", "pk")
                    if _refundable(row) >= amount
                ),
                None,
            )
            if payment is None:
                raise ConflictError(
                    "The refund is more than the verified amount still refundable.",
                    "refund_exceeds_paid",
                )
            manual = payment.kind == TransactionKind.COD_COLLECTION
            provider = payment_provider()
            if not manual and (
                provider is None
                or provider.code != payment.provider
                or not provider.supports_refunds
            ):
                raise ConflictError(
                    "Refunds through this payment provider are not available.",
                    "refund_unavailable",
                )
            refund = Refund.objects.create(
                order=order,
                payment=payment,
                method=RefundMethod.MANUAL if manual else RefundMethod.PROVIDER,
                amount=amount,
                currency=order.currency,
                status=RefundStatus.PENDING,
                reason=reason.strip()[:300],
                idempotency_key_hash=digest,
                requested_by=actor,
            )
            if return_case is not None:
                link_requested_refund(case=return_case, refund=refund, actor=actor)
            record_audit_event(
                action="order.refund.requested",
                resource=refund,
                actor=actor,
                changes={"number": order.number, "amount": amount, "method": refund.method},
            )
            if manual:
                # The merchant attests the cash was handed back: that is the verified event.
                _complete_refund(order, refund, "", actor)
                return refund
        if refund.status != RefundStatus.PENDING or refund.method == RefundMethod.MANUAL:
            return refund
    provider = payment_provider()
    if provider is None or provider.code != refund.payment.provider:
        return refund
    try:
        outcome = provider.refund(
            transaction_ref=refund.payment.provider_ref,
            amount=refund.amount,
            currency=refund.currency,
            idempotency_key=str(refund.pk),
        )
    except ProviderUnavailable:
        # Unknown outcome: it stays pending, and a retry with the same key asks again.
        return refund
    return _apply_refund_outcome(refund.pk, outcome)
