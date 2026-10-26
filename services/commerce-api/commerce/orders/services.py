"""Order placement from a reviewed checkout, the order state machine and stock commitments.

Placement (D-071) is one transaction that locks, in this order, the cart, the checkout, the cart's
variants, the coupon, the delivery slot and the number sequence (the same order as checkout
review). It takes over the checkout's stock reservations without a new ledger movement, redeems
the coupon, confirms the slot booking, snapshots everything and completes the checkout. A business
rule that fails at that moment (expired stock, exhausted coupon, full slot, withdrawn item) rolls
the whole placement back and then releases the checkout hold so nothing stays reserved.
"""

import hashlib
import secrets
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from commerce.audit.services import record_audit_event
from commerce.catalog.models import ProductStatus, ProductVariant
from commerce.checkout import services as checkout
from commerce.checkout.models import (
    Cart,
    CartStatus,
    CheckoutLine,
    CheckoutReservation,
    CheckoutSession,
    CheckoutStatus,
    Coupon,
    DeliverySlot,
)
from commerce.core.domain import ConflictError
from commerce.identity.models import User
from commerce.inventory import services as inventory
from commerce.stores.models import StoreSettings
from commerce.stores.modules import enabled_modules
from commerce.tenancy.context import get_active_tenant_id

from .models import (
    OPEN_INTENT_STATUSES,
    AllocationStatus,
    CouponRedemption,
    DocumentSequence,
    IntentStatus,
    Invoice,
    Order,
    OrderAllocation,
    OrderItem,
    OrderPaymentStatus,
    OrderStatus,
    OrderStatusEvent,
    PaymentIntent,
    PaymentMethod,
    RedemptionStatus,
)
from .providers import payment_provider

PAYMENT_WINDOW = timedelta(minutes=30)
ORDER_COOKIE = "mc_order"
ORDER_COOKIE_LIFETIME = timedelta(days=30)

TRANSITIONS: dict[str, frozenset[str]] = {
    OrderStatus.PENDING_PAYMENT: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.PLACED: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PROCESSING, OrderStatus.CANCELLED}),
    OrderStatus.PROCESSING: frozenset({OrderStatus.CANCELLED}),
    OrderStatus.CANCELLED: frozenset(),
}


class PlacementBlocked(ConflictError):
    """A rule failed at placement. The checkout hold is released; the customer starts again."""

    default_code = "order_not_placed"


class StockExpired(PlacementBlocked):
    default_code = "stock_expired"
    default_detail = "Some reserved stock is no longer fresh enough to sell. Review your cart."


class CouponUnavailable(PlacementBlocked):
    default_code = "coupon_unavailable"
    default_detail = "The coupon can no longer be used. Review your checkout without it."


class SlotUnavailable(PlacementBlocked):
    default_code = "delivery_slot_unavailable"
    default_detail = "The delivery time is no longer available. Choose another time."


class ItemUnavailable(PlacementBlocked):
    default_code = "item_unavailable"
    default_detail = "An item is no longer available. Review your cart."


class ShippingUnavailable(PlacementBlocked):
    default_code = "shipping_unavailable"
    default_detail = "The delivery method is no longer available. Review your checkout again."


class InvalidTransition(ConflictError):
    default_code = "invalid_transition"
    default_detail = "The order cannot move to that status."


@dataclass(frozen=True)
class Placement:
    order: Order
    replayed: bool
    guest_token: str | None


def key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _clean_key(idempotency_key: str) -> str:
    key = idempotency_key.strip()
    if not 8 <= len(key) <= 100:
        raise ValidationError({"idempotency_key": ["Use an idempotency key of 8-100 characters."]})
    return key


def _actor_label(actor: User | None) -> str:
    return "guest" if actor is None else ""


# --- Payment methods ------------------------------------------------------------------------------


def available_payment_methods(session: CheckoutSession) -> list[str]:
    """Methods that can genuinely complete this checkout, from modules and configuration only."""
    if session.grand_total <= 0:
        return []
    modules = enabled_modules()
    kinds = set(CheckoutLine.objects.filter(session=session).values_list("kind", flat=True))
    methods: list[str] = []
    # Cash is collected when goods arrive, so only all-physical orders qualify.
    if "cash_on_delivery" in modules and session.requires_shipping and kinds == {"physical"}:
        methods.append(PaymentMethod.CASH_ON_DELIVERY)
    if "online_payments" in modules and payment_provider() is not None:
        methods.append(PaymentMethod.ONLINE)
    return methods


# --- Numbers --------------------------------------------------------------------------------------


def _next_number(kind: str, prefix: str) -> str:
    DocumentSequence.objects.bulk_create([DocumentSequence(kind=kind)], ignore_conflicts=True)
    sequence = DocumentSequence.objects.select_for_update().get(kind=kind)
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    return f"{prefix}-{sequence.last_value:06d}"


# --- Placement ------------------------------------------------------------------------------------


def _lock_and_check_items(session: CheckoutSession) -> list[CheckoutLine]:
    lines = list(CheckoutLine.objects.filter(session=session).order_by("variant_id"))
    variants = {
        variant.pk: variant
        for variant in ProductVariant.objects.select_for_update()
        .filter(pk__in=[line.variant_id for line in lines])
        .order_by("pk")
    }
    for line in lines:
        variant = ProductVariant.objects.select_related("product").get(pk=line.variant_id)
        if (
            line.variant_id not in variants
            or variant.product.status != ProductStatus.ACTIVE
            or not variant.is_active
        ):
            raise ItemUnavailable()
    return lines


def _convert_reservations(
    session: CheckoutSession, order: Order, items: Mapping[Any, OrderItem]
) -> None:
    """Make the checkout's holds the order's own. The reserved quantity already sits in the
    ledger, so nothing is reserved twice; batch stock is re-checked against today's date."""
    holds = list(
        CheckoutReservation.objects.select_for_update()
        .filter(session=session, released_at__isnull=True)
        .select_related("variant__product", "location")
        .order_by("variant_id", "location_id")
    )
    held: dict[Any, Decimal] = defaultdict(Decimal)
    for hold in holds:
        held[hold.variant_id] += hold.quantity
    for item in items.values():
        variant = ProductVariant.objects.select_related("product").get(pk=item.variant_id)
        needs_stock = inventory.is_tracked(variant) and not checkout._preorder_allowed(variant)
        if needs_stock and held[item.variant_id] != item.quantity:
            raise ItemUnavailable()
    for hold in holds:
        if hold.variant.product.tracking_mode == "batch":
            # Orders already placed come first; a lot that expired since review is never counted.
            fresh = sum(
                (row.on_hand for row in inventory.batches_by_expiry(hold.variant, hold.location)),
                Decimal(0),
            )
            committed = OrderAllocation.objects.filter(
                variant=hold.variant, location=hold.location, status=AllocationStatus.ALLOCATED
            ).aggregate(total=Sum("quantity"))["total"] or Decimal(0)
            if committed + hold.quantity > fresh:
                raise StockExpired()
        OrderAllocation.objects.create(
            order=order,
            item=items[hold.variant_id],
            variant=hold.variant,
            location=hold.location,
            source_reservation=hold,
            quantity=hold.quantity,
            reserve_operation_id=hold.operation_id,
        )


def _check_shipping(session: CheckoutSession) -> None:
    """The reviewed delivery method must still pass the checkout's own eligibility rules (active,
    currency, country, minimum subtotal). Its reviewed price is kept, like item prices."""
    if not session.requires_shipping:
        return
    method = session.shipping_method
    if method is None or method not in checkout.available_shipping_methods(session):
        raise ShippingUnavailable()


def _redeem_coupon(session: CheckoutSession, order: Order) -> None:
    if session.coupon_id is None:
        return
    coupon = Coupon.objects.select_for_update().filter(pk=session.coupon_id).first()
    if coupon is None or coupon.code != session.coupon_code:
        raise CouponUnavailable()
    try:
        checkout._validate_coupon(session, coupon)
    except ValidationError as exc:
        raise CouponUnavailable() from exc
    coupon.times_redeemed += 1
    coupon.save(update_fields=["times_redeemed", "updated_at"])
    CouponRedemption.objects.create(
        coupon=coupon,
        order=order,
        code=coupon.code,
        discount=session.discount_total,
        currency=session.currency,
    )


def _book_slot(session: CheckoutSession, order: Order) -> None:
    if session.delivery_slot_id is None:
        return
    slot = DeliverySlot.objects.select_for_update().filter(pk=session.delivery_slot_id).first()
    # This checkout's own hold is part of the capacity it was given at review; converting it is
    # bookings + 1, and the hold stops counting because the checkout leaves the reviewed state.
    if (
        slot is None
        or not slot.is_active
        or slot.starts_at <= timezone.now()
        or (slot.capacity is not None and slot.bookings >= slot.capacity)
    ):
        raise SlotUnavailable()
    slot.bookings += 1
    slot.save(update_fields=["bookings", "updated_at"])
    order.delivery_slot = slot
    order.delivery_slot_label = slot.label
    order.delivery_slot_starts_at = slot.starts_at
    order.delivery_slot_ends_at = slot.ends_at
    order.delivery_slot_booked = True


def _issue_invoice(order: Order) -> Invoice:
    store = StoreSettings.objects.first()
    seller = {
        "name": store.name if store else "",
        "legal_name": store.legal_name if store else "",
        "email": store.contact_email if store else "",
        "phone": store.contact_phone if store else "",
        "country": store.country if store else "",
    }
    buyer = {
        "name": order.recipient_name,
        "email": order.contact_email,
        "phone": order.contact_phone,
        "address_line_1": order.address_line_1,
        "address_line_2": order.address_line_2,
        "city": order.city,
        "region": order.region,
        "postal_code": order.postal_code,
        "country": order.country,
    }
    return Invoice.objects.create(
        order=order,
        number=_next_number("invoice", "INV"),
        currency=order.currency,
        item_subtotal=order.item_subtotal,
        discount_total=order.discount_total,
        shipping_total=order.shipping_total,
        tax_total=order.tax_total,
        grand_total=order.grand_total,
        seller=seller,
        buyer=buyer,
    )


def _event(
    order: Order,
    event: str,
    *,
    from_status: str,
    actor: User | None,
    actor_label: str = "",
    reason: str = "",
) -> None:
    OrderStatusEvent.objects.create(
        order=order,
        event=event,
        from_status=from_status,
        to_status=order.status,
        payment_status=order.payment_status,
        reason=reason,
        actor=actor,
        actor_label=actor_label if actor is None else "",
    )


def _place(
    session: CheckoutSession, method: str, digest: str, actor: User | None
) -> tuple[Order, bool]:
    Cart.objects.select_for_update().get(pk=session.cart_id)
    target = CheckoutSession.objects.select_for_update().get(pk=session.pk)
    if target.status == CheckoutStatus.COMPLETED:
        order = Order.objects.get(checkout=target)
        if order.placement_key_hash != digest:
            raise ConflictError("This checkout has already become an order.", "checkout_completed")
        if order.payment_method != method:
            # A replay must be the same request: the key is bound to the original payment method.
            raise ConflictError(
                "This request was already used with a different payment method.",
                "idempotency_conflict",
            )
        return order, True
    if target.status == CheckoutStatus.DRAFT and target.expires_at > timezone.now():
        raise ConflictError(
            "Review the checkout before placing the order.", "checkout_not_reviewed"
        )
    if target.status != CheckoutStatus.REVIEWED or target.expires_at <= timezone.now():
        raise checkout.CheckoutExpired()
    if method not in available_payment_methods(target):
        raise ValidationError({"payment_method": ["This payment method is not available."]})
    _check_shipping(target)
    lines = _lock_and_check_items(target)
    online = method == PaymentMethod.ONLINE
    order = Order(
        checkout=target,
        placement_key_hash=digest,
        customer=target.customer,
        status=OrderStatus.PENDING_PAYMENT if online else OrderStatus.PLACED,
        payment_method=method,
        payment_due_at=timezone.now() + PAYMENT_WINDOW if online else None,
        currency=target.currency,
        item_subtotal=target.item_subtotal,
        discount_total=target.discount_total,
        shipping_total=target.shipping_total,
        tax_total=target.tax_total,
        grand_total=target.grand_total,
        contact_email=target.contact_email,
        contact_phone=target.contact_phone,
        requires_shipping=target.requires_shipping,
        recipient_name=target.recipient_name,
        address_line_1=target.address_line_1,
        address_line_2=target.address_line_2,
        city=target.city,
        region=target.region,
        postal_code=target.postal_code,
        country=target.country,
        delivery_notes=target.delivery_notes,
        shipping_method=target.shipping_method,
        shipping_method_name=target.shipping_method.name if target.shipping_method else "",
        coupon_code=target.coupon_code,
        guest_access_hash="" if target.customer_id else key_hash(secrets.token_urlsafe(32)),
    )
    _book_slot(target, order)
    order.number = _next_number("order", "ORD")
    order.save()
    items = {
        line.variant_id: OrderItem.objects.create(
            order=order,
            variant_id=line.variant_id,
            product_name=line.product_name,
            variant_title=line.variant_title,
            sku=line.sku,
            kind=line.kind,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_total=line.line_subtotal,
            price_id=line.price_id,
            position=position,
        )
        for position, line in enumerate(lines)
    }
    _convert_reservations(target, order, items)
    _redeem_coupon(target, order)
    if not online:
        PaymentIntent.objects.create(
            order=order,
            method=PaymentMethod.CASH_ON_DELIVERY,
            amount=order.grand_total,
            currency=order.currency,
            status=IntentStatus.AWAITING_COLLECTION,
        )
    _issue_invoice(order)
    target.status = CheckoutStatus.COMPLETED
    target.save(update_fields=["status", "updated_at"])
    Cart.objects.filter(pk=target.cart_id).update(status=CartStatus.CONVERTED)
    _event(order, "placed", from_status="", actor=actor, actor_label=_actor_label(actor))
    record_audit_event(
        action="order.placed",
        resource=order,
        actor=actor,
        actor_label=_actor_label(actor),
        changes={
            "number": order.number,
            "status": order.status,
            "payment_method": method,
            "currency": order.currency,
            "grand_total": order.grand_total,
        },
    )
    if online:
        _schedule_payment_expiry(order)
    return order, False


def place_order(
    *, session: CheckoutSession, payment_method: str, idempotency_key: str, actor: User | None
) -> Placement:
    """Turn a reviewed checkout into one order. A replay with the same key returns that order."""
    digest = key_hash(_clean_key(idempotency_key))
    try:
        with transaction.atomic():
            order, replayed = _place(session, payment_method, digest, actor)
            token = None
            if order.customer_id is None:
                # The guest's order credential is issued (or, on a replay by the same checkout
                # owner, re-issued) here and only ever kept as a digest.
                token = secrets.token_urlsafe(32)
                order.guest_access_hash = key_hash(token)
                order.save(update_fields=["guest_access_hash", "updated_at"])
    except checkout.CheckoutExpired:
        checkout.release_checkout(session=session, expired=True)
        raise
    except PlacementBlocked:
        checkout.release_checkout(session=session)
        raise
    return Placement(order=order, replayed=replayed, guest_token=token)


def set_order_cookie(response: Any, placement: Placement) -> None:
    if placement.guest_token:
        response.set_cookie(
            ORDER_COOKIE,
            placement.guest_token,
            max_age=int(ORDER_COOKIE_LIFETIME.total_seconds()),
            path="/",
            secure=settings.MATECART_SECURE_COOKIES,
            httponly=True,
            samesite="Lax",
        )


def order_for_checkout(session: CheckoutSession) -> Order | None:
    if session.status != CheckoutStatus.COMPLETED:
        return None
    return Order.objects.filter(checkout=session).first()


# --- Access ---------------------------------------------------------------------------------------


def owned_order(*, order_id: Any, user: Any, order_token: str | None) -> Order:
    """The order, when the browser is its customer or holds its guest credential."""
    customer = checkout._customer(user)
    if customer is not None:
        order = Order.objects.filter(pk=order_id, customer=customer).first()
    elif order_token:
        order = Order.objects.filter(
            pk=order_id, customer__isnull=True, guest_access_hash=key_hash(order_token)
        ).first()
    else:
        order = None
    if order is None:
        raise PermissionDenied("This order is not available.")
    if (
        order.status == OrderStatus.PENDING_PAYMENT
        and order.payment_due_at is not None
        and order.payment_due_at <= timezone.now()
    ):
        expire_unpaid(order)
        order = Order.objects.get(pk=order.pk)
    return order


# --- State machine --------------------------------------------------------------------------------


def _release_commitments(order: Order) -> None:
    """Return everything the order holds: stock, the coupon use and the delivery booking.
    Each release is guarded by state, so it happens exactly once."""
    allocations = list(
        OrderAllocation.objects.select_for_update()
        .filter(order=order, status=AllocationStatus.ALLOCATED)
        .select_related("variant", "location")
        .order_by("variant_id", "location_id")
    )
    for allocation in allocations:
        operation = inventory.release(
            variant=allocation.variant,
            location=allocation.location,
            quantity=allocation.quantity,
            request=inventory.StockRequest(
                reason="Order cancelled",
                reference_type="order",
                reference_id=str(order.pk),
                idempotency_key=f"order-release:{allocation.pk}",
            ),
        )
        allocation.status = AllocationStatus.RELEASED
        allocation.release_operation_id = operation.operation_id
        allocation.save(update_fields=["status", "release_operation_id", "updated_at"])
    redemption = (
        CouponRedemption.objects.select_for_update()
        .filter(order=order, status=RedemptionStatus.REDEEMED)
        .first()
    )
    if redemption is not None:
        coupon = Coupon.objects.select_for_update().get(pk=redemption.coupon_id)
        coupon.times_redeemed = max(0, coupon.times_redeemed - 1)
        coupon.save(update_fields=["times_redeemed", "updated_at"])
        redemption.status = RedemptionStatus.VOIDED
        redemption.voided_at = timezone.now()
        redemption.save(update_fields=["status", "voided_at", "updated_at"])
    if order.delivery_slot_booked and order.delivery_slot_id is not None:
        slot = DeliverySlot.objects.select_for_update().get(pk=order.delivery_slot_id)
        # A booking is given back only while its window is still ahead.
        if slot.starts_at > timezone.now() and slot.bookings > 0:
            slot.bookings -= 1
            slot.save(update_fields=["bookings", "updated_at"])
            order.delivery_slot_booked = False
    PaymentIntent.objects.filter(order=order, status__in=OPEN_INTENT_STATUSES).update(
        status=IntentStatus.CANCELLED, updated_at=timezone.now()
    )


def apply_transition(
    order: Order,
    to_status: str,
    *,
    event: str,
    actor: User | None,
    actor_label: str = "",
    reason: str = "",
) -> Order:
    """Move a locked order along an allowed edge, with its side effects, history and audit."""
    if to_status not in TRANSITIONS.get(order.status, frozenset()):
        raise InvalidTransition()
    before = order.status
    now = timezone.now()
    if to_status == OrderStatus.CANCELLED:
        # Phase 09 shipment state is locked after the order (the same order -> shipment ordering
        # used by dispatch), so cancellation cannot race a physical dispatch.
        from commerce.fulfillment.services import cancel_for_order_locked

        cancel_for_order_locked(order=order, actor=actor)
        if OrderAllocation.objects.filter(order=order, status=AllocationStatus.FULFILLED).exists():
            raise InvalidTransition("Fulfilled goods cannot be cancelled here.")
        _release_commitments(order)
        order.cancelled_at = now
        order.cancel_reason = reason
    if to_status == OrderStatus.CONFIRMED:
        order.confirmed_at = now
    order.status = to_status
    order.save()
    _event(order, event, from_status=before, actor=actor, actor_label=actor_label, reason=reason)
    record_audit_event(
        action="order.status.changed",
        resource=order,
        actor=actor,
        actor_label=actor_label if actor is None else "",
        changes={
            "number": order.number,
            "status": {"from": before, "to": to_status},
            "reason": reason,
        },
    )
    return order


def merchant_transition(*, order_id: Any, to_status: str, actor: User, reason: str = "") -> Order:
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise PermissionDenied("This order is not available.")
        if order.status == to_status:
            return order
        if to_status == OrderStatus.CONFIRMED and order.status == OrderStatus.PENDING_PAYMENT:
            raise ConflictError(
                "Online orders are confirmed by a verified payment.", "payment_required"
            )
        if to_status not in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.CANCELLED):
            raise InvalidTransition()
        # Merchant events are named after the status they reach.
        return apply_transition(
            order, to_status, event=str(to_status), actor=actor, reason=reason or "merchant"
        )


def customer_cancel(*, order: Order, actor: User | None) -> Order:
    with transaction.atomic():
        target = Order.objects.select_for_update().get(pk=order.pk)
        if target.status == OrderStatus.CANCELLED:
            return target
        if (
            target.status not in (OrderStatus.PLACED, OrderStatus.PENDING_PAYMENT)
            or target.amount_paid > 0
        ):
            raise ConflictError(
                "This order can no longer be cancelled online. Contact the store.",
                "cancel_not_allowed",
            )
        return apply_transition(
            target,
            OrderStatus.CANCELLED,
            event="cancelled",
            actor=actor,
            actor_label=_actor_label(actor),
            reason="customer",
        )


def expire_unpaid(order: Order) -> Order:
    """Cancel an online order whose payment window passed without a verified payment."""
    with transaction.atomic():
        target = Order.objects.select_for_update().get(pk=order.pk)
        if (
            target.status != OrderStatus.PENDING_PAYMENT
            or target.payment_due_at is None
            or target.payment_due_at > timezone.now()
        ):
            return target
        if target.amount_paid == 0:
            target.payment_status = OrderPaymentStatus.FAILED
        return apply_transition(
            target,
            OrderStatus.CANCELLED,
            event="payment_expired",
            actor=None,
            actor_label="system",
            reason="payment_expired",
        )


def _schedule_payment_expiry(order: Order) -> None:
    tenant_id = str(get_active_tenant_id())
    due = order.payment_due_at
    if due is None:
        return

    def enqueue() -> None:
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            return
        from .tasks import expire_order_payment

        expire_order_payment.apply_async(
            args=(str(order.pk),), kwargs={"tenant_id": tenant_id}, eta=due
        )

    transaction.on_commit(enqueue)


# --- Fulfilment handoff ---------------------------------------------------------------------------


def fulfil_order_stock(
    *,
    order: Order,
    serials: Mapping[str, Sequence[str]] | None = None,
    actor: User | None = None,
) -> Order:
    """Deduct the order's reserved stock once (the Phase 09 dispatch entry point).

    Batch stock leaves first-expired-first-out from unexpired lots at that moment; serial-tracked
    allocations need the shipped serials, keyed by allocation id. Fulfilled allocations are never
    deducted again, and a retry replays the same ledger operations.
    """
    with transaction.atomic():
        target = Order.objects.select_for_update().get(pk=order.pk)
        if target.status not in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING):
            raise ConflictError("Only confirmed orders can be fulfilled.", "order_not_confirmed")
        allocations = list(
            OrderAllocation.objects.select_for_update()
            .filter(order=target, status=AllocationStatus.ALLOCATED)
            .select_related("variant", "location")
            .order_by("variant_id", "location_id")
        )
        for allocation in allocations:
            operation = inventory.fulfil(
                variant=allocation.variant,
                location=allocation.location,
                quantity=allocation.quantity,
                serials=(serials or {}).get(str(allocation.pk), ()),
                request=inventory.StockRequest(
                    reason="Order fulfilled",
                    reference_type="order",
                    reference_id=str(target.pk),
                    idempotency_key=f"order-fulfil:{allocation.pk}",
                    actor=actor,
                ),
            )
            allocation.status = AllocationStatus.FULFILLED
            allocation.fulfil_operation_id = operation.operation_id
            allocation.save(update_fields=["status", "fulfil_operation_id", "updated_at"])
        return target
