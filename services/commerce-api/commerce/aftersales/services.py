"""Transactional reviews, returns, exchanges, warranty claims and support requests.

Customer submissions never create money movements, inventory movements or replacement shipments.
Those actions remain with the existing payment, inventory and fulfillment services.
"""

import hashlib
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from commerce.audit.services import record_audit_event
from commerce.catalog.models import ElectronicsDetails
from commerce.core.domain import ConflictError
from commerce.fulfillment.models import Shipment, ShipmentStatus
from commerce.identity.models import User
from commerce.orders.models import (
    Order,
    OrderItem,
    OrderPaymentStatus,
    OrderStatus,
    Refund,
    RefundStatus,
)
from commerce.stores.modules import enabled_modules

from .models import (
    AfterSalesCase,
    CaseKind,
    CaseMessage,
    CaseStatus,
    ProductReview,
    ReturnRefundEvidence,
    ReviewStatus,
)

ACTIVE_CLAIM_STATES = (
    CaseStatus.REQUESTED,
    CaseStatus.APPROVED,
    CaseStatus.RECEIVED,
    CaseStatus.INSPECTED,
    CaseStatus.AWAITING_REFUND,
    CaseStatus.REPLACEMENT_PENDING,
    CaseStatus.RESOLVED,
    CaseStatus.CLOSED,
)
TRANSITIONS: dict[str, frozenset[str]] = {
    CaseStatus.REQUESTED: frozenset({CaseStatus.APPROVED, CaseStatus.REJECTED}),
    CaseStatus.APPROVED: frozenset({CaseStatus.RECEIVED, CaseStatus.RESOLVED}),
    CaseStatus.REJECTED: frozenset(),
    CaseStatus.RECEIVED: frozenset({CaseStatus.INSPECTED}),
    CaseStatus.INSPECTED: frozenset(
        {CaseStatus.AWAITING_REFUND, CaseStatus.REPLACEMENT_PENDING, CaseStatus.RESOLVED}
    ),
    CaseStatus.AWAITING_REFUND: frozenset({CaseStatus.RESOLVED}),
    CaseStatus.REPLACEMENT_PENDING: frozenset({CaseStatus.RESOLVED}),
    CaseStatus.RESOLVED: frozenset({CaseStatus.CLOSED}),
    CaseStatus.CLOSED: frozenset(),
}


def _owned_item(item_id: Any, customer: User, *, lock: bool = False) -> OrderItem:
    query = OrderItem.objects.select_related("order", "variant")
    if lock:
        query = query.select_for_update(of=("self",))
    item = query.filter(pk=item_id, order__customer=customer).first()
    if item is None:
        raise NotFound("Purchase not found.")
    return item


def _verified_purchase(item: OrderItem) -> None:
    order = item.order
    if (
        order.status == OrderStatus.CANCELLED
        or order.amount_paid <= order.amount_refunded
        or order.payment_status
        not in (
            OrderPaymentStatus.PAID,
            OrderPaymentStatus.PARTIALLY_REFUNDED,
        )
    ):
        raise ConflictError("A verified, active purchase is required.", "purchase_not_verified")


def submit_review(*, customer: User, item_id: Any, rating: int, body: str) -> ProductReview:
    if "product_reviews" not in enabled_modules():
        raise ConflictError("Reviews are disabled for this store.", "reviews_unavailable")
    with transaction.atomic():
        item = _owned_item(item_id, customer, lock=True)
        _verified_purchase(item)
        if ProductReview.objects.filter(order_item=item).exists():
            raise ConflictError("This purchase has already been reviewed.", "review_exists")
        review = ProductReview.objects.create(
            customer=customer,
            order_item=item,
            rating=rating,
            body=body.strip(),
        )
        record_audit_event(
            action="review.submitted", resource=review, actor=customer, changes={"rating": rating}
        )
        return review


def moderate_review(*, review_id: Any, status: str, reason: str, actor: User) -> ProductReview:
    if status not in (ReviewStatus.PUBLISHED, ReviewStatus.REJECTED):
        raise ValidationError({"status": ["Choose published or rejected."]})
    with transaction.atomic():
        review = ProductReview.objects.select_for_update().filter(pk=review_id).first()
        if review is None:
            raise NotFound("Review not found.")
        if review.status != ReviewStatus.PENDING:
            raise ConflictError("The review already has a moderation decision.", "review_decided")
        review.status = status
        review.decision_reason = reason.strip()
        review.save(update_fields=["status", "decision_reason", "updated_at"])
        record_audit_event(
            action="review.moderated", resource=review, actor=actor, changes={"status": status}
        )
        return review


def _warranty_eligible(item: OrderItem) -> bool:
    details = (
        ElectronicsDetails.objects.select_related("warranty")
        .filter(product=item.variant.product)
        .first()
    )
    if details is None or details.warranty is None:
        return False
    months = details.warranty.duration_months
    if not months:
        return False
    purchased = item.order.placed_at.date()
    today = timezone.localdate()
    age_months = (today.year - purchased.year) * 12 + (today.month - purchased.month)
    return age_months < months or (age_months == months and today.day <= purchased.day)


def submit_case(
    *,
    customer: User,
    kind: str,
    item_id: Any | None,
    quantity: Decimal,
    subject: str,
    reason: str,
    idempotency_key: str,
) -> AfterSalesCase:
    if (
        kind in (CaseKind.RETURN, CaseKind.EXCHANGE)
        and "returns_exchanges" not in enabled_modules()
    ):
        raise ConflictError("Returns and exchanges are unavailable.", "returns_unavailable")
    digest = hashlib.sha256(idempotency_key.strip().encode()).hexdigest()
    with transaction.atomic():
        # Lock each customer's requests before locking a purchased item. This
        # serializes support idempotency retries as well as quantity claims.
        User.objects.select_for_update().get(pk=customer.pk)
        item = _owned_item(item_id, customer, lock=True) if item_id else None
        existing = AfterSalesCase.objects.filter(customer=customer, request_key_hash=digest).first()
        if existing:
            if (
                existing.kind != kind
                or existing.order_item_id != (item.pk if item else None)
                or existing.quantity != quantity
                or existing.subject != subject.strip()
                or existing.reason != reason.strip()
            ):
                raise ConflictError(
                    "The submission key belongs to another request.", "idempotency_conflict"
                )
            return existing
        if kind != CaseKind.SUPPORT:
            if item is None:
                raise ValidationError({"order_item": ["Select a purchased item."]})
            _verified_purchase(item)
            if kind in (CaseKind.RETURN, CaseKind.EXCHANGE):
                if item.kind != "physical":
                    raise ConflictError(
                        "This item cannot be physically returned.", "return_unavailable"
                    )
                if not Shipment.objects.filter(
                    order=item.order, status=ShipmentStatus.DELIVERED
                ).exists():
                    raise ConflictError("A delivered shipment is required.", "not_delivered")
                used = AfterSalesCase.objects.filter(
                    order_item=item,
                    kind__in=(CaseKind.RETURN, CaseKind.EXCHANGE),
                    status__in=ACTIVE_CLAIM_STATES,
                ).aggregate(total=Sum("quantity"))["total"] or Decimal(0)
                if quantity + used > item.quantity:
                    raise ConflictError(
                        "The requested quantity exceeds remaining purchased units.",
                        "quantity_exceeded",
                    )
            elif kind == CaseKind.WARRANTY:
                if not _warranty_eligible(item):
                    raise ConflictError(
                        "No current purchase-linked warranty is available.", "warranty_unavailable"
                    )
                if quantity > item.quantity:
                    raise ConflictError(
                        "The requested quantity exceeds purchased units.", "quantity_exceeded"
                    )
        elif item is not None:
            _verified_purchase(item)
        case = AfterSalesCase.objects.create(
            customer=customer,
            kind=kind,
            order_item=item,
            quantity=quantity,
            subject=subject.strip(),
            reason=reason.strip(),
            request_key_hash=digest,
        )
        record_audit_event(
            action="aftersales.requested", resource=case, actor=customer, changes={"kind": kind}
        )
        return case


def required_return_refund(case: AfterSalesCase) -> Decimal:
    """Net value of returned units from immutable order snapshots, not live prices.

    Allocate the order-level discount and tax across purchased lines in proportion
    to their historical line totals. Shipping is not part of the returned goods.
    """
    from commerce.pricing import services as pricing

    item = case.order_item
    if item is None or item.quantity <= 0:
        raise ConflictError("Return purchase evidence is missing.", "return_purchase_missing")
    order = item.order
    if order.item_subtotal <= 0:
        raise ConflictError("Order has no refundable item value.", "return_value_unavailable")
    net_goods = max(
        Decimal("0"),
        order.item_subtotal - order.discount_total + order.tax_total,
    )
    precision = pricing.currency_precision(order.currency)
    quantum = Decimal(1).scaleb(-precision)
    total = net_goods * item.line_total / order.item_subtotal * case.quantity / item.quantity
    expected = total.quantize(quantum)
    if expected <= 0 or expected > order.grand_total:
        raise ConflictError("Return has no refundable paid amount.", "return_value_unavailable")
    return expected


def validate_refund_attribution(
    *,
    case_id: Any,
    order: Order,
    amount: Decimal,
) -> AfterSalesCase:
    """Called only while the payment service holds the source order lock."""
    case = (
        AfterSalesCase.objects.select_for_update()
        .select_related("order_item__order")
        .filter(pk=case_id, kind=CaseKind.RETURN, status=CaseStatus.AWAITING_REFUND)
        .first()
    )
    if (
        case is None
        or case.order_item is None
        or case.order_item.order_id != order.pk
        or case.customer_id != order.customer_id
    ):
        raise ConflictError("A matching inspected return is required.", "return_case_invalid")
    expected = required_return_refund(case)
    committed = ReturnRefundEvidence.objects.filter(
        case=case,
        refund__status__in=(RefundStatus.PENDING, RefundStatus.SUCCEEDED),
    ).aggregate(total=Sum("refund__amount"))["total"] or Decimal(0)
    if amount <= 0 or committed + amount > expected:
        raise ConflictError("Refund exceeds this return's eligible value.", "return_refund_exceeds")
    return case


def link_requested_refund(*, case: AfterSalesCase, refund: Refund, actor: User) -> None:
    """The attribution is committed before any manual or provider refund succeeds."""
    ReturnRefundEvidence.objects.create(case=case, refund=refund)
    record_audit_event(
        action="aftersales.refund_attributed",
        resource=case,
        actor=actor,
        changes={"refund": str(refund.pk), "amount": str(refund.amount)},
    )


def assert_refund_replay(*, refund: Refund, case_id: Any | None) -> None:
    existing = ReturnRefundEvidence.objects.filter(refund=refund).first()
    if (existing.case_id if existing else None) != case_id:
        raise ConflictError(
            "This refund key already belongs to a different purpose.",
            "idempotency_conflict",
        )


def _matching_replacement(case: AfterSalesCase, replacement: Order | None) -> bool:
    item = case.order_item
    if (
        item is None
        or replacement is None
        or replacement.pk == item.order_id
        or replacement.customer_id != case.customer_id
        or replacement.status == OrderStatus.CANCELLED
        or replacement.placed_at < case.created_at
    ):
        return False
    return OrderItem.objects.filter(
        order=replacement,
        variant_id=item.variant_id,
        quantity__gte=case.quantity,
        kind="physical",
    ).exists()


def transition_case(
    *,
    case_id: Any,
    status: str,
    reason: str,
    actor: User,
    refund_id: Any | None = None,
    replacement_order_id: Any | None = None,
) -> AfterSalesCase:
    """Resolve only with pre-attributed refund or bound replacement evidence."""
    with transaction.atomic():
        case = (
            AfterSalesCase.objects.select_for_update()
            .select_related("order_item__order")
            .filter(pk=case_id)
            .first()
        )
        if case is None:
            raise NotFound("Request not found.")
        if status not in TRANSITIONS.get(case.status, frozenset()):
            raise ConflictError("This status transition is unavailable.", "invalid_case_transition")
        if status == CaseStatus.AWAITING_REFUND and case.kind != CaseKind.RETURN:
            raise ConflictError(
                "Only returns can request a refund handoff.", "invalid_case_transition"
            )
        if status == CaseStatus.REPLACEMENT_PENDING:
            if case.kind != CaseKind.EXCHANGE or replacement_order_id is None:
                raise ConflictError(
                    "Bind the replacement order before fulfillment.", "replacement_binding_required"
                )
            replacement = Order.objects.select_for_update().filter(pk=replacement_order_id).first()
            if not _matching_replacement(case, replacement):
                raise ConflictError(
                    "Replacement does not match this exchange.", "replacement_not_verified"
                )
            if (
                Shipment.objects.filter(order=replacement)
                .exclude(
                    status__in=(
                        ShipmentStatus.READY,
                        ShipmentStatus.PICKING,
                        ShipmentStatus.PACKED,
                        ShipmentStatus.READY_FOR_DISPATCH,
                    )
                )
                .exists()
            ):
                raise ConflictError(
                    "Replacement must be linked before dispatch.", "replacement_already_dispatched"
                )
            if (
                AfterSalesCase.objects.filter(replacement_order=replacement)
                .exclude(pk=case.pk)
                .exists()
            ):
                raise ConflictError(
                    "Replacement order is already assigned.", "replacement_already_used"
                )
            case.replacement_order = replacement
        elif status == CaseStatus.RESOLVED and case.kind == CaseKind.RETURN:
            if case.status != CaseStatus.AWAITING_REFUND or case.order_item is None:
                raise ConflictError("Inspect the return before resolution.", "return_not_inspected")
            evidence = (
                ReturnRefundEvidence.objects.select_related("refund")
                .filter(case=case, refund_id=refund_id, refund__status=RefundStatus.SUCCEEDED)
                .first()
                if refund_id is not None
                else None
            )
            succeeded = ReturnRefundEvidence.objects.filter(
                case=case, refund__status=RefundStatus.SUCCEEDED
            ).aggregate(total=Sum("refund__amount"))["total"] or Decimal(0)
            if evidence is None or succeeded < required_return_refund(case):
                raise ConflictError(
                    "Verified refunds attributed to this return are insufficient.",
                    "refund_not_verified",
                )
            case.refund = evidence.refund
        elif status == CaseStatus.RESOLVED and case.kind == CaseKind.EXCHANGE:
            if case.status != CaseStatus.REPLACEMENT_PENDING or case.order_item is None:
                raise ConflictError("Receive and inspect the item first.", "exchange_not_inspected")
            replacement = case.replacement_order
            if (
                not _matching_replacement(case, replacement)
                or (
                    replacement_order_id is not None
                    and replacement_order_id != case.replacement_order_id
                )
                or not Shipment.objects.filter(
                    order=replacement, status=ShipmentStatus.DELIVERED
                ).exists()
            ):
                raise ConflictError(
                    "A bound and delivered replacement is required.",
                    "replacement_not_verified",
                )
        elif refund_id is not None or replacement_order_id is not None:
            raise ValidationError(
                {"evidence": ["Evidence must match its specific claim transition."]}
            )
        if refund_id is not None and case.kind != CaseKind.RETURN:
            raise ValidationError({"refund": ["Only returns accept refund evidence."]})
        if status in (CaseStatus.REJECTED, CaseStatus.RESOLVED) and not reason.strip():
            raise ValidationError({"reason": ["Record a decision reason."]})
        case.status = status
        case.decision_reason = reason.strip()
        case.save(
            update_fields=[
                "status",
                "decision_reason",
                "refund",
                "replacement_order",
                "updated_at",
            ]
        )
        changes = {"status": status}
        if status == CaseStatus.REPLACEMENT_PENDING:
            changes["replacement_order"] = str(case.replacement_order_id)
        if status == CaseStatus.RESOLVED and case.refund_id is not None:
            changes["refund"] = str(case.refund_id)
        record_audit_event(
            action="aftersales.status_changed",
            resource=case,
            actor=actor,
            changes=changes,
        )
        return case


def send_message(*, case: AfterSalesCase, author: User, body: str) -> CaseMessage:
    with transaction.atomic():
        locked = AfterSalesCase.objects.select_for_update().get(pk=case.pk)
        if locked.status == CaseStatus.CLOSED:
            raise ConflictError("This request is closed.", "case_closed")
        message = CaseMessage.objects.create(case=locked, author=author, body=body.strip())
        record_audit_event(action="aftersales.message_added", resource=locked, actor=author)
        return message
