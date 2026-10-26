"""Transactional shipment, courier, COD and fraud-review operations."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied

from commerce.audit.services import record_audit_event
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User
from commerce.orders import payments as order_payments
from commerce.orders import services as order_services
from commerce.orders.models import Order, OrderItem, OrderStatus, PaymentMethod
from commerce.stores.modules import enabled_modules

from .models import (
    PRE_DISPATCH_STATUSES,
    BookingStatus,
    CodReconciliationStatus,
    CourierBooking,
    CourierCallback,
    CourierCodRecord,
    FraudAssessment,
    FraudDecision,
    FraudFlag,
    FraudReview,
    FraudReviewStatus,
    Shipment,
    ShipmentItem,
    ShipmentStatus,
    TrackingEvent,
    TrackingSource,
)
from .providers import (
    BookingRejected,
    BookingRequest,
    CourierItem,
    InvalidCallback,
    ProviderUnavailable,
    courier_provider,
)


class CourierProviderUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The courier provider is temporarily unavailable."
    default_code = "courier_provider_unavailable"


class CourierUnavailable(ConflictError):
    default_detail = "No verified courier provider is configured."
    default_code = "courier_unavailable"


class InvalidShipmentTransition(ConflictError):
    default_detail = "That shipment transition is not allowed."
    default_code = "invalid_shipment_transition"


TRANSITIONS: dict[str, frozenset[str]] = {
    ShipmentStatus.READY: frozenset({ShipmentStatus.PICKING, ShipmentStatus.CANCELLED}),
    ShipmentStatus.PICKING: frozenset({ShipmentStatus.PACKED, ShipmentStatus.CANCELLED}),
    ShipmentStatus.PACKED: frozenset({ShipmentStatus.READY_FOR_DISPATCH, ShipmentStatus.CANCELLED}),
    ShipmentStatus.READY_FOR_DISPATCH: frozenset(
        {ShipmentStatus.DISPATCHED, ShipmentStatus.CANCELLED}
    ),
    ShipmentStatus.DISPATCHED: frozenset(
        {
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.DELIVERED,
            ShipmentStatus.DELIVERY_FAILED,
            ShipmentStatus.RETURN_TO_SENDER,
        }
    ),
    ShipmentStatus.IN_TRANSIT: frozenset(
        {
            ShipmentStatus.DELIVERED,
            ShipmentStatus.DELIVERY_FAILED,
            ShipmentStatus.RETURN_TO_SENDER,
        }
    ),
    ShipmentStatus.DELIVERY_FAILED: frozenset(
        {ShipmentStatus.IN_TRANSIT, ShipmentStatus.RETURN_TO_SENDER}
    ),
    ShipmentStatus.DELIVERED: frozenset(),
    ShipmentStatus.RETURN_TO_SENDER: frozenset(),
    ShipmentStatus.CANCELLED: frozenset(),
}


def _shipment_locks(shipment_id: Any) -> tuple[Order, Shipment]:
    row = Shipment.objects.filter(pk=shipment_id).values("order_id").first()
    if row is None:
        raise PermissionDenied("This shipment is not available.")
    order = Order.objects.select_for_update().get(pk=row["order_id"])
    shipment = Shipment.objects.select_for_update().get(pk=shipment_id)
    return order, shipment


def _tracking_event(
    shipment: Shipment,
    *,
    before: str,
    source: str,
    actor: User | None,
    occurred_at: datetime,
    note: str = "",
    provider_event_id: str = "",
    body_sha256: str = "",
) -> TrackingEvent:
    attempt = None
    if shipment.status == ShipmentStatus.DELIVERY_FAILED:
        attempt = (
            TrackingEvent.objects.filter(
                shipment=shipment, status=ShipmentStatus.DELIVERY_FAILED
            ).count()
            + 1
        )
    return TrackingEvent.objects.create(
        shipment=shipment,
        occurred_at=occurred_at,
        from_status=before,
        status=shipment.status,
        source=source,
        note=note[:300],
        attempt_number=attempt,
        provider_event_id=provider_event_id[:120],
        body_sha256=body_sha256,
        actor=actor,
    )


def _risk_flags(order: Order) -> list[tuple[str, str, int, dict[str, Any]]]:
    """Conservative store-history rules; evidence contains counts, never another buyer's PII."""
    if order.payment_method != PaymentMethod.CASH_ON_DELIVERY:
        return []
    contact = Q()
    has_contact = False
    if order.contact_phone:
        contact |= Q(contact_phone=order.contact_phone)
        has_contact = True
    if order.contact_email:
        contact |= Q(contact_email__iexact=order.contact_email)
        has_contact = True
    if not has_contact:
        return []
    seven_days = timezone.now() - timedelta(days=7)
    recent = Order.objects.filter(
        contact,
        payment_method=PaymentMethod.CASH_ON_DELIVERY,
        placed_at__gte=seven_days,
    ).exclude(pk=order.pk)
    flags: list[tuple[str, str, int, dict[str, Any]]] = []
    recent_count = recent.count()
    if recent_count >= 3:
        flags.append(
            (
                "repeated_recent_cod_contact",
                "Three or more recent COD orders use the same customer contact.",
                30,
                {"prior_orders": recent_count, "window_days": 7},
            )
        )
    failed_count = Shipment.objects.filter(
        order__in=recent,
        status__in=(ShipmentStatus.DELIVERY_FAILED, ShipmentStatus.RETURN_TO_SENDER),
    ).count()
    if failed_count >= 2:
        flags.append(
            (
                "repeated_delivery_failures",
                "Two or more recent COD shipments for this contact failed delivery.",
                40,
                {"failed_shipments": failed_count, "window_days": 7},
            )
        )
    variant_ids = list(OrderItem.objects.filter(order=order).values_list("variant_id", flat=True))
    same_item_count = (
        OrderItem.objects.filter(
            order__in=recent.filter(placed_at__gte=timezone.now() - timedelta(hours=24)),
            variant_id__in=variant_ids,
        )
        .values("order_id")
        .distinct()
        .count()
    )
    if same_item_count >= 2:
        flags.append(
            (
                "repeated_same_item_cod",
                "The same contact placed repeated COD orders for the same item within 24 hours.",
                25,
                {"prior_orders": same_item_count, "window_hours": 24},
            )
        )
    return flags


def assess_order(*, order_id: Any) -> FraudAssessment:
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise PermissionDenied("This order is not available.")
        existing = FraudAssessment.objects.filter(order=order).first()
        if existing is not None:
            return existing
        flags = _risk_flags(order)
        assessment = FraudAssessment.objects.create(
            order=order,
            score=min(100, sum(flag[2] for flag in flags)),
            status=FraudReviewStatus.PENDING if flags else FraudReviewStatus.CLEAR,
        )
        for code, description, weight, evidence in flags:
            FraudFlag.objects.create(
                assessment=assessment,
                code=code,
                description=description,
                weight=weight,
                evidence=evidence,
            )
        record_audit_event(
            action="fulfillment.fraud.assessed",
            resource=assessment,
            actor_label="system",
            changes={"score": assessment.score, "flags": [flag[0] for flag in flags]},
        )
        return assessment


def review_assessment(
    *, assessment_id: Any, decision: str, reason: str, actor: User
) -> FraudAssessment:
    status_by_decision: dict[str, str] = {
        FraudDecision.CLEAR: FraudReviewStatus.CLEARED,
        FraudDecision.MONITOR: FraudReviewStatus.MONITOR,
        FraudDecision.CONFIRM_RISK: FraudReviewStatus.CONFIRMED_RISK,
    }
    if decision not in status_by_decision:
        raise DomainError("Unknown fraud review decision.", "invalid_fraud_decision")
    with transaction.atomic():
        assessment = FraudAssessment.objects.select_for_update().filter(pk=assessment_id).first()
        if assessment is None:
            raise PermissionDenied("This fraud assessment is not available.")
        before = assessment.status
        review = FraudReview.objects.create(
            assessment=assessment, decision=decision, reason=reason, reviewer=actor
        )
        assessment.status = status_by_decision[decision]
        assessment.reviewed_at = review.occurred_at
        assessment.reviewed_by = actor
        assessment.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
        record_audit_event(
            action="fulfillment.fraud.reviewed",
            resource=assessment,
            actor=actor,
            changes={"status": {"from": before, "to": assessment.status}, "reason": reason},
        )
        return assessment


def prepare_shipment(*, order_id: Any, actor: User, operational_notes: str = "") -> Shipment:
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise PermissionDenied("This order is not available.")
        existing = Shipment.objects.filter(order=order).first()
        if existing is not None:
            assess_order(order_id=order.pk)
            return existing
        if not order.requires_shipping:
            raise ConflictError(
                "Digital-only orders do not have physical shipments.", "shipment_not_required"
            )
        if order.status not in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING):
            raise ConflictError(
                "Only confirmed physical orders can be prepared.", "order_not_confirmed"
            )
        physical_items = list(OrderItem.objects.filter(order=order, kind="physical"))
        if not physical_items:
            raise ConflictError("This order has no physical items.", "shipment_not_required")
        shipment = Shipment.objects.create(
            order=order,
            reference=f"SHP-{order.number.removeprefix('ORD-')}",
            recipient_name=order.recipient_name,
            address_line_1=order.address_line_1,
            address_line_2=order.address_line_2,
            city=order.city,
            region=order.region,
            postal_code=order.postal_code,
            country=order.country,
            operational_notes=operational_notes,
        )
        ShipmentItem.objects.bulk_create(
            [
                ShipmentItem(shipment=shipment, order_item=item, quantity=item.quantity)
                for item in physical_items
            ]
        )
        _tracking_event(
            shipment,
            before="",
            source=TrackingSource.STAFF,
            actor=actor,
            occurred_at=timezone.now(),
            note="Shipment prepared",
        )
        record_audit_event(
            action="fulfillment.shipment.prepared",
            resource=shipment,
            actor=actor,
            changes={"reference": shipment.reference, "order_number": order.number},
        )
        assess_order(order_id=order.pk)
    return shipment


def update_manual_details(
    *,
    shipment_id: Any,
    actor: User,
    courier_name: str,
    tracking_number: str = "",
    tracking_url: str = "",
) -> Shipment:
    with transaction.atomic():
        _order, shipment = _shipment_locks(shipment_id)
        if shipment.status not in PRE_DISPATCH_STATUSES:
            raise ConflictError(
                "Courier details cannot be changed after dispatch.", "shipment_already_dispatched"
            )
        if CourierBooking.objects.filter(
            shipment=shipment,
            status__in=(BookingStatus.PENDING, BookingStatus.BOOKED, BookingStatus.CANCELLING),
        ).exists():
            raise ConflictError(
                "This shipment is managed by its courier booking.", "courier_booking_active"
            )
        before = {
            "courier_name": shipment.courier_name,
            "tracking_number": shipment.tracking_number,
        }
        shipment.courier_code = ""
        shipment.courier_name = courier_name
        shipment.tracking_number = tracking_number
        shipment.tracking_url = tracking_url
        shipment.full_clean()
        shipment.save(
            update_fields=[
                "courier_code",
                "courier_name",
                "tracking_number",
                "tracking_url",
                "updated_at",
            ]
        )
        record_audit_event(
            action="fulfillment.shipment.courier_recorded",
            resource=shipment,
            actor=actor,
            changes={
                "courier_name": {"from": before["courier_name"], "to": courier_name},
                "tracking_number_recorded": bool(tracking_number),
            },
        )
        return shipment


def transition_shipment(
    *,
    shipment_id: Any,
    to_status: str,
    actor: User | None,
    source: str = TrackingSource.STAFF,
    occurred_at: datetime | None = None,
    note: str = "",
    serials: Mapping[str, Sequence[str]] | None = None,
    provider_event_id: str = "",
    body_sha256: str = "",
) -> Shipment:
    at = occurred_at or timezone.now()
    if at > timezone.now() + timedelta(minutes=5):
        raise DomainError("Tracking time cannot be in the future.", "invalid_tracking_time")
    with transaction.atomic():
        order, shipment = _shipment_locks(shipment_id)
        if shipment.status == to_status:
            return shipment
        if to_status not in TRANSITIONS.get(shipment.status, frozenset()):
            raise InvalidShipmentTransition()
        if source == TrackingSource.PROVIDER and shipment.status in PRE_DISPATCH_STATUSES:
            raise InvalidShipmentTransition("A courier callback cannot dispatch store inventory.")
        if to_status == ShipmentStatus.PICKING and order.status == OrderStatus.CONFIRMED:
            order_services.apply_transition(
                order,
                OrderStatus.PROCESSING,
                event="processing",
                actor=actor,
                actor_label="provider" if actor is None else "",
                reason="fulfillment_started",
            )
        if to_status == ShipmentStatus.DISPATCHED:
            if CourierBooking.objects.filter(
                shipment=shipment, status__in=(BookingStatus.PENDING, BookingStatus.CANCELLING)
            ).exists():
                raise ConflictError(
                    "Finish the courier operation before dispatch.", "courier_operation_pending"
                )
            order_services.fulfil_order_stock(order=order, serials=serials, actor=actor)
            shipment.dispatched_at = at
        elif to_status == ShipmentStatus.DELIVERED:
            shipment.delivered_at = at
        elif to_status == ShipmentStatus.DELIVERY_FAILED:
            shipment.failed_at = at
        elif to_status == ShipmentStatus.RETURN_TO_SENDER:
            shipment.returned_at = at
        elif to_status == ShipmentStatus.CANCELLED:
            if CourierBooking.objects.filter(
                shipment=shipment,
                status__in=(BookingStatus.PENDING, BookingStatus.BOOKED, BookingStatus.CANCELLING),
            ).exists():
                raise ConflictError("Cancel the courier booking first.", "courier_booking_active")
            shipment.cancelled_at = at
        before = shipment.status
        shipment.status = to_status
        shipment.save()
        _tracking_event(
            shipment,
            before=before,
            source=source,
            actor=actor,
            occurred_at=at,
            note=note,
            provider_event_id=provider_event_id,
            body_sha256=body_sha256,
        )
        record_audit_event(
            action="fulfillment.shipment.status_changed",
            resource=shipment,
            actor=actor,
            actor_label="courier_provider" if actor is None else "",
            changes={"status": {"from": before, "to": to_status}, "note": note},
        )
        return shipment


def cancel_for_order_locked(*, order: Order, actor: User | None) -> None:
    """Cancel an undispatched shipment while the caller holds the order lock."""
    shipment = Shipment.objects.select_for_update().filter(order=order).first()
    if shipment is None or shipment.status == ShipmentStatus.CANCELLED:
        return
    if shipment.status not in PRE_DISPATCH_STATUSES:
        raise ConflictError("Dispatched goods cannot be cancelled here.", "shipment_dispatched")
    if CourierBooking.objects.filter(
        shipment=shipment,
        status__in=(BookingStatus.PENDING, BookingStatus.BOOKED, BookingStatus.CANCELLING),
    ).exists():
        raise ConflictError("Cancel the courier booking first.", "courier_booking_active")
    before = shipment.status
    shipment.status = ShipmentStatus.CANCELLED
    shipment.cancelled_at = timezone.now()
    shipment.save(update_fields=["status", "cancelled_at", "updated_at"])
    _tracking_event(
        shipment,
        before=before,
        source=TrackingSource.STAFF if actor else TrackingSource.SYSTEM,
        actor=actor,
        occurred_at=shipment.cancelled_at,
        note="Order cancelled",
    )
    record_audit_event(
        action="fulfillment.shipment.cancelled",
        resource=shipment,
        actor=actor,
        actor_label="system" if actor is None else "",
        changes={"status": {"from": before, "to": ShipmentStatus.CANCELLED}},
    )


def _booking_request(booking: CourierBooking, shipment: Shipment, order: Order) -> BookingRequest:
    items = tuple(
        CourierItem(
            sku=item.order_item.sku, name=item.order_item.product_name, quantity=item.quantity
        )
        for item in ShipmentItem.objects.filter(shipment=shipment).select_related("order_item")
    )
    address_lines = tuple(
        line for line in (shipment.address_line_1, shipment.address_line_2) if line
    )
    return BookingRequest(
        reference=str(booking.pk),
        order_number=order.number,
        amount_to_collect=order.grand_total
        if order.payment_method == PaymentMethod.CASH_ON_DELIVERY
        else Decimal(0),
        currency=order.currency,
        recipient_name=shipment.recipient_name,
        contact_phone=order.contact_phone,
        address_lines=address_lines,
        city=shipment.city,
        region=shipment.region,
        postal_code=shipment.postal_code,
        country=shipment.country,
        notes=shipment.operational_notes,
        items=items,
    )


def book_courier(*, shipment_id: Any, idempotency_key: str, actor: User) -> CourierBooking:
    provider = courier_provider()
    if provider is None or "courier_booking" not in enabled_modules():
        raise CourierUnavailable()
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    with transaction.atomic():
        order, shipment = _shipment_locks(shipment_id)
        if shipment.status not in PRE_DISPATCH_STATUSES:
            raise ConflictError(
                "Only an undispatched shipment can be booked.", "shipment_dispatched"
            )
        existing_key = CourierBooking.objects.filter(idempotency_key_hash=key_hash).first()
        if existing_key is not None:
            if existing_key.shipment_id != shipment.pk or existing_key.provider != provider.code:
                raise ConflictError(
                    "That idempotency key was already used.", "idempotency_conflict"
                )
            if existing_key.status != BookingStatus.PENDING:
                return existing_key
            booking = existing_key
        else:
            active = CourierBooking.objects.filter(
                shipment=shipment,
                status__in=(BookingStatus.PENDING, BookingStatus.BOOKED, BookingStatus.CANCELLING),
            ).first()
            if active is not None:
                raise ConflictError(
                    "This shipment already has a courier booking.", "booking_exists"
                )
            booking = CourierBooking.objects.create(
                shipment=shipment,
                provider=provider.code,
                idempotency_key_hash=key_hash,
                status=BookingStatus.PENDING,
                requested_by=actor,
            )
            record_audit_event(
                action="fulfillment.courier.booking_requested",
                resource=booking,
                actor=actor,
                changes={"shipment": shipment.reference, "provider": provider.code},
            )
        request = _booking_request(booking, shipment, order)
    try:
        result = provider.book(request)
        if not result.provider_reference or (
            result.tracking_url and not result.tracking_url.startswith("https://")
        ):
            raise BookingRejected("invalid_response")
    except ProviderUnavailable as exc:
        raise CourierProviderUnavailable() from exc
    except BookingRejected as exc:
        with transaction.atomic():
            target = CourierBooking.objects.select_for_update().get(pk=booking.pk)
            if target.status == BookingStatus.PENDING:
                target.status = BookingStatus.FAILED
                target.failure_code = exc.code[:40]
                target.save(update_fields=["status", "failure_code", "updated_at"])
                record_audit_event(
                    action="fulfillment.courier.booking_failed",
                    resource=target,
                    actor=actor,
                    changes={"failure_code": target.failure_code},
                )
        return target
    with transaction.atomic():
        target = CourierBooking.objects.select_for_update().get(pk=booking.pk)
        shipment = Shipment.objects.select_for_update().get(pk=target.shipment_id)
        if target.status == BookingStatus.BOOKED:
            return target
        if target.status != BookingStatus.PENDING or shipment.status not in PRE_DISPATCH_STATUSES:
            raise ConflictError("The shipment changed while booking.", "booking_state_changed")
        target.status = BookingStatus.BOOKED
        target.provider_reference = result.provider_reference[:120]
        target.save(update_fields=["status", "provider_reference", "updated_at"])
        shipment.courier_code = provider.code
        shipment.courier_name = provider.display_name[:100]
        shipment.tracking_number = result.tracking_number[:120]
        shipment.tracking_url = result.tracking_url[:1000]
        shipment.full_clean()
        shipment.save(
            update_fields=[
                "courier_code",
                "courier_name",
                "tracking_number",
                "tracking_url",
                "updated_at",
            ]
        )
        record_audit_event(
            action="fulfillment.courier.booked",
            resource=target,
            actor=actor,
            changes={
                "provider": provider.code,
                "provider_reference_recorded": True,
                "tracking_number_recorded": bool(result.tracking_number),
            },
        )
        return target


def cancel_courier_booking(*, shipment_id: Any, actor: User) -> CourierBooking:
    """Cancel an external booking without holding database locks during the provider call."""
    provider = courier_provider()
    if provider is None:
        raise CourierUnavailable()
    with transaction.atomic():
        _order, shipment = _shipment_locks(shipment_id)
        if shipment.status not in PRE_DISPATCH_STATUSES:
            raise ConflictError(
                "A dispatched shipment booking cannot be cancelled.", "shipment_dispatched"
            )
        booking = (
            CourierBooking.objects.select_for_update()
            .filter(
                shipment=shipment,
                provider=provider.code,
                status__in=(BookingStatus.BOOKED, BookingStatus.CANCELLING),
            )
            .first()
        )
        if booking is None:
            cancelled = CourierBooking.objects.filter(
                shipment=shipment, provider=provider.code, status=BookingStatus.CANCELLED
            ).first()
            if cancelled is not None:
                return cancelled
            raise ConflictError("This shipment has no cancellable booking.", "booking_not_found")
        if booking.status == BookingStatus.BOOKED:
            booking.status = BookingStatus.CANCELLING
            booking.save(update_fields=["status", "updated_at"])
            record_audit_event(
                action="fulfillment.courier.cancellation_requested",
                resource=booking,
                actor=actor,
                changes={"provider": booking.provider},
            )
        provider_reference = booking.provider_reference
        reference = str(booking.pk)
    try:
        provider.cancel(provider_reference=provider_reference, reference=reference)
    except ProviderUnavailable as exc:
        # The provider operation is idempotent. Keeping CANCELLING blocks dispatch until a safe
        # retry determines the outcome instead of claiming either success or failure.
        raise CourierProviderUnavailable() from exc
    with transaction.atomic():
        target = CourierBooking.objects.select_for_update().get(pk=booking.pk)
        shipment = Shipment.objects.select_for_update().get(pk=target.shipment_id)
        if target.status == BookingStatus.CANCELLED:
            return target
        if target.status != BookingStatus.CANCELLING:
            raise ConflictError("The booking changed while cancelling.", "booking_state_changed")
        target.status = BookingStatus.CANCELLED
        target.cancelled_at = timezone.now()
        target.save(update_fields=["status", "cancelled_at", "updated_at"])
        shipment.courier_code = ""
        shipment.courier_name = ""
        shipment.tracking_number = ""
        shipment.tracking_url = ""
        shipment.save(
            update_fields=[
                "courier_code",
                "courier_name",
                "tracking_number",
                "tracking_url",
                "updated_at",
            ]
        )
        record_audit_event(
            action="fulfillment.courier.cancelled",
            resource=target,
            actor=actor,
            changes={"provider": target.provider, "cancelled": True},
        )
        return target


def process_tracking_callback(
    *, provider_code: str, body: bytes, headers: Mapping[str, str]
) -> str:
    provider = courier_provider()
    if provider is None or provider.code != provider_code:
        raise CourierUnavailable()
    try:
        update = provider.authenticate_tracking(body, headers)
    except InvalidCallback as exc:
        raise PermissionDenied("The courier callback could not be verified.") from exc
    callback = CourierCallback(
        provider=provider.code,
        event_id=update.event_id[:120],
        booking_reference=update.booking_reference[:120],
        body_sha256=hashlib.sha256(body).hexdigest(),
    )
    try:
        with transaction.atomic():
            callback.save()
    except IntegrityError:
        return "duplicate"
    booking = CourierBooking.objects.filter(
        provider=provider.code,
        provider_reference=update.booking_reference,
        status=BookingStatus.BOOKED,
    ).first()
    if booking is None:
        callback.outcome = "unknown_reference"
        callback.save(update_fields=["outcome"])
        return callback.outcome
    latest = (
        TrackingEvent.objects.filter(shipment=booking.shipment)
        .order_by("-occurred_at", "-pk")
        .values_list("occurred_at", flat=True)
        .first()
    )
    if latest is not None and update.occurred_at < latest:
        callback.outcome = "stale"
        callback.save(update_fields=["outcome"])
        return callback.outcome
    try:
        transition_shipment(
            shipment_id=booking.shipment_id,
            to_status=update.status,
            actor=None,
            source=TrackingSource.PROVIDER,
            occurred_at=update.occurred_at,
            note=update.note,
            provider_event_id=update.event_id,
            body_sha256=callback.body_sha256,
        )
    except InvalidShipmentTransition:
        callback.outcome = "invalid_transition"
    else:
        callback.outcome = "applied"
    callback.save(update_fields=["outcome"])
    return callback.outcome


def record_cod_collection(
    *,
    shipment_id: Any,
    collected_amount: Decimal,
    courier_fees: Decimal,
    deductions: Decimal,
    deduction_note: str,
    collection_reference: str,
    actor: User,
) -> CourierCodRecord:
    with transaction.atomic():
        order, shipment = _shipment_locks(shipment_id)
        if order.payment_method != PaymentMethod.CASH_ON_DELIVERY:
            raise ConflictError("This is not a COD order.", "not_cash_on_delivery")
        if shipment.status != ShipmentStatus.DELIVERED:
            raise ConflictError(
                "Verified collection evidence is accepted after delivery.", "shipment_not_delivered"
            )
        expected = collected_amount - courier_fees - deductions
        if expected < 0:
            raise DomainError("Fees and deductions cannot exceed collection.", "invalid_deductions")
        existing = CourierCodRecord.objects.filter(shipment=shipment).first()
        fingerprint = (
            collected_amount,
            courier_fees,
            deductions,
            collection_reference,
        )
        if existing is not None:
            current = (
                existing.collected_amount,
                existing.courier_fees,
                existing.deductions,
                existing.collection_reference,
            )
            if current == fingerprint:
                return existing
            raise ConflictError("Collection evidence is already recorded.", "cod_collection_exists")
        record = CourierCodRecord.objects.create(
            shipment=shipment,
            order=order,
            courier_code=shipment.courier_code or "manual",
            currency=order.currency,
            amount_due=order.grand_total,
            collected_amount=collected_amount,
            courier_fees=courier_fees,
            deductions=deductions,
            deduction_note=deduction_note,
            expected_remittance=expected,
            collection_reference=collection_reference,
            recorded_by=actor,
        )
        # Exact, attributable evidence is sufficient for the existing COD payment operation.
        # Under/over-collection stays an operational discrepancy and never invents a payment.
        if collected_amount == order.grand_total:
            order_payments.record_cod_collection(order_id=order.pk, actor=actor)
        record_audit_event(
            action="fulfillment.cod.collection_recorded",
            resource=record,
            actor=actor,
            changes={
                "amount_due": order.grand_total,
                "collected_amount": collected_amount,
                "courier_fees": courier_fees,
                "deductions": deductions,
                "collection_reference": collection_reference,
            },
        )
        return record


def reconcile_cod(
    *,
    record_id: Any,
    actual_remittance: Decimal,
    remittance_reference: str,
    remitted_at: datetime,
    actor: User,
) -> CourierCodRecord:
    with transaction.atomic():
        record = CourierCodRecord.objects.select_for_update().filter(pk=record_id).first()
        if record is None:
            raise PermissionDenied("This COD record is not available.")
        if record.actual_remittance is not None:
            if (
                record.actual_remittance == actual_remittance
                and record.remittance_reference == remittance_reference
            ):
                return record
            raise ConflictError("This COD remittance is already recorded.", "cod_remittance_exists")
        record.actual_remittance = actual_remittance
        record.remittance_reference = remittance_reference
        record.remitted_at = remitted_at
        record.reconciled_by = actor
        record.status = (
            CodReconciliationStatus.RECONCILED
            if actual_remittance == record.expected_remittance
            else CodReconciliationStatus.DISPUTED
        )
        record.full_clean()
        record.save(
            update_fields=[
                "actual_remittance",
                "remittance_reference",
                "remitted_at",
                "reconciled_by",
                "status",
                "updated_at",
            ]
        )
        record_audit_event(
            action="fulfillment.cod.reconciled",
            resource=record,
            actor=actor,
            changes={
                "expected_remittance": record.expected_remittance,
                "actual_remittance": actual_remittance,
                "remittance_reference": remittance_reference,
                "status": record.status,
            },
        )
        return record
