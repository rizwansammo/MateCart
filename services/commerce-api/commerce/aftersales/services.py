"""Transactional reviews, returns, exchanges, warranty claims and support requests.

Customer submissions never create money movements, inventory movements or replacement shipments.
Those actions remain with the existing payment, inventory and fulfillment services.
"""

import hashlib
from datetime import date
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
from commerce.orders.models import OrderItem, OrderPaymentStatus, OrderStatus
from .models import AfterSalesCase, CaseKind, CaseMessage, CaseStatus, ProductReview, ReviewStatus

ACTIVE_CLAIM_STATES = (
    CaseStatus.REQUESTED, CaseStatus.APPROVED, CaseStatus.RECEIVED,
    CaseStatus.INSPECTED, CaseStatus.AWAITING_REFUND,
    CaseStatus.REPLACEMENT_PENDING, CaseStatus.RESOLVED, CaseStatus.CLOSED,
)
TRANSITIONS: dict[str, frozenset[str]] = {
    CaseStatus.REQUESTED: frozenset({CaseStatus.APPROVED, CaseStatus.REJECTED}),
    CaseStatus.APPROVED: frozenset({CaseStatus.RECEIVED, CaseStatus.RESOLVED}),
    CaseStatus.REJECTED: frozenset(),
    CaseStatus.RECEIVED: frozenset({CaseStatus.INSPECTED}),
    CaseStatus.INSPECTED: frozenset({CaseStatus.AWAITING_REFUND, CaseStatus.REPLACEMENT_PENDING, CaseStatus.RESOLVED}),
    CaseStatus.AWAITING_REFUND: frozenset({CaseStatus.RESOLVED}),
    CaseStatus.REPLACEMENT_PENDING: frozenset(),
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
        or order.payment_status not in (
            OrderPaymentStatus.PAID,
            OrderPaymentStatus.PARTIALLY_REFUNDED,
        )
    ):
        raise ConflictError("A verified, active purchase is required.", "purchase_not_verified")


def submit_review(*, customer: User, item_id: Any, rating: int, body: str) -> ProductReview:
    with transaction.atomic():
        item = _owned_item(item_id, customer, lock=True)
        _verified_purchase(item)
        if ProductReview.objects.filter(order_item=item).exists():
            raise ConflictError("This purchase has already been reviewed.", "review_exists")
        review = ProductReview.objects.create(
            customer=customer, order_item=item, rating=rating, body=body.strip(),
        )
        record_audit_event(action="review.submitted", resource=review, actor=customer, changes={"rating": rating})
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
        record_audit_event(action="review.moderated", resource=review, actor=actor, changes={"status": status})
        return review


def _warranty_eligible(item: OrderItem) -> bool:
    details = ElectronicsDetails.objects.select_related("warranty").filter(product=item.variant.product).first()
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
    *, customer: User, kind: str, item_id: Any | None, quantity: Decimal,
    subject: str, reason: str, idempotency_key: str,
) -> AfterSalesCase:
    digest = hashlib.sha256(idempotency_key.strip().encode()).hexdigest()
    with transaction.atomic():
        # The order item is the serialization point for concurrent quantity claims.
        item = _owned_item(item_id, customer, lock=True) if item_id else None
        existing = AfterSalesCase.objects.filter(customer=customer, request_key_hash=digest).first()
        if existing:
            if (
                existing.kind != kind or existing.order_item_id != (item.pk if item else None)
                or existing.quantity != quantity or existing.subject != subject.strip()
                or existing.reason != reason.strip()
            ):
                raise ConflictError("The submission key belongs to another request.", "idempotency_conflict")
            return existing
        if kind != CaseKind.SUPPORT:
            if item is None:
                raise ValidationError({"order_item": ["Select a purchased item."]})
            _verified_purchase(item)
            if kind in (CaseKind.RETURN, CaseKind.EXCHANGE):
                if item.kind != "physical":
                    raise ConflictError("This item cannot be physically returned.", "return_unavailable")
                if not Shipment.objects.filter(order=item.order, status=ShipmentStatus.DELIVERED).exists():
                    raise ConflictError("A delivered shipment is required.", "not_delivered")
                used = AfterSalesCase.objects.filter(
                    order_item=item, kind__in=(CaseKind.RETURN, CaseKind.EXCHANGE),
                    status__in=ACTIVE_CLAIM_STATES,
                ).aggregate(total=Sum("quantity"))["total"] or Decimal(0)
                if quantity + used > item.quantity:
                    raise ConflictError("The requested quantity exceeds remaining purchased units.", "quantity_exceeded")
            elif kind == CaseKind.WARRANTY:
                if not _warranty_eligible(item):
                    raise ConflictError("No current purchase-linked warranty is available.", "warranty_unavailable")
                if quantity > item.quantity:
                    raise ConflictError("The requested quantity exceeds purchased units.", "quantity_exceeded")
        elif item is not None:
            _verified_purchase(item)
        case = AfterSalesCase.objects.create(
            customer=customer, kind=kind, order_item=item, quantity=quantity,
            subject=subject.strip(), reason=reason.strip(), request_key_hash=digest,
        )
        record_audit_event(action="aftersales.requested", resource=case, actor=customer, changes={"kind": kind})
        return case


def transition_case(*, case_id: Any, status: str, reason: str, actor: User) -> AfterSalesCase:
    with transaction.atomic():
        case = AfterSalesCase.objects.select_for_update().filter(pk=case_id).first()
        if case is None:
            raise NotFound("Request not found.")
        if status not in TRANSITIONS.get(case.status, frozenset()):
            raise ConflictError("This status transition is unavailable.", "invalid_case_transition")
        if status == CaseStatus.AWAITING_REFUND and case.kind != CaseKind.RETURN:
            raise ConflictError("Only returns can request a refund handoff.", "invalid_case_transition")
        if status == CaseStatus.REPLACEMENT_PENDING and case.kind != CaseKind.EXCHANGE:
            raise ConflictError("Only exchanges can await a replacement.", "invalid_case_transition")
        if status == CaseStatus.RESOLVED and case.kind == CaseKind.EXCHANGE:
            raise ConflictError("Replacement must be fulfilled through existing order operations.", "replacement_not_verified")
        if status == CaseStatus.RESOLVED and case.kind == CaseKind.RETURN:
            # A return is not resolved simply because it was inspected.
            if case.order_item is None or not case.order_item.order.refunds.filter(status="succeeded").exists():
                raise ConflictError("A verified refund is required before closing a return.", "refund_not_verified")
        if status in (CaseStatus.REJECTED, CaseStatus.RESOLVED) and not reason.strip():
            raise ValidationError({"reason": ["Record a decision reason."]})
        case.status = status
        case.decision_reason = reason.strip()
        case.save(update_fields=["status", "decision_reason", "updated_at"])
        record_audit_event(action="aftersales.status_changed", resource=case, actor=actor, changes={"status": status})
        return case


def send_message(*, case: AfterSalesCase, author: User, body: str) -> CaseMessage:
    with transaction.atomic():
        locked = AfterSalesCase.objects.select_for_update().get(pk=case.pk)
        if locked.status == CaseStatus.CLOSED:
            raise ConflictError("This request is closed.", "case_closed")
        message = CaseMessage.objects.create(case=locked, author=author, body=body.strip())
        record_audit_event(action="aftersales.message_added", resource=locked, actor=author)
        return message
