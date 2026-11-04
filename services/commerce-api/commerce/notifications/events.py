"""Business-event hooks (D-087). Called by the order, payment, fulfillment, after-sales, review,
fraud and account services at the moment their change is made, inside their transaction.

Each hook only observes: it reads committed state and calls ``services.emit``, which never raises
and never changes the business record. Event keys come from the business record (order id,
refund id, shipment status, case status), so repeated callbacks, retries and replays produce the
same key and therefore no second notification.

Links: customers get the order page (which still checks ownership) or their account; staff get
the dashboard page that needs the same permission as the notification. Merchant-assisted orders
have no customer link (they have no browser credential, D-077).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import services
from .services import Recipient, money

if TYPE_CHECKING:
    from commerce.aftersales.models import AfterSalesCase, CaseMessage, ProductReview
    from commerce.fulfillment.models import FraudAssessment, Shipment
    from commerce.identity.models import User
    from commerce.orders.models import Order, Refund

PAYMENT_LABELS = {"cod": "Cash on delivery", "online": "Online payment"}


def _customer(order: Order) -> Recipient:
    user = order.customer
    return Recipient(
        user=user,
        email=order.contact_email,
        phone=order.contact_phone,
        name=order.recipient_name or (user.full_name if user is not None else ""),
    )


def _order_values(order: Order) -> dict[str, Any]:
    return {
        "order_number": order.number,
        "order_status": order.get_status_display(),
        "order_total": money(order.grand_total, order.currency),
        "customer_name": order.recipient_name,
    }


def _order_link(order: Order) -> str:
    from commerce.orders.models import OrderSource

    return f"/orders/{order.pk}" if order.source == OrderSource.STOREFRONT else ""


def _staff_order_link(order: Order) -> str:
    return f"/manage/orders/{order.pk}"


def order_placed(order: Order) -> None:
    values = {
        **_order_values(order),
        "payment_method": PAYMENT_LABELS.get(order.payment_method, ""),
    }
    services.emit(
        "order.placed",
        event_id=str(order.pk),
        values=values,
        customer=_customer(order),
        related=("order", str(order.pk)),
        link_path=_order_link(order),
    )
    services.emit(
        "staff.order.new",
        event_id=str(order.pk),
        values={**values, "order_source": order.get_source_display()},
        related=("order", str(order.pk)),
        staff_link_path=_staff_order_link(order),
    )


def order_status_changed(order: Order, *, to_status: str, reason: str) -> None:
    values = _order_values(order)
    related = ("order", str(order.pk))
    if to_status == "confirmed":
        services.emit(
            "order.confirmed",
            event_id=str(order.pk),
            values=values,
            customer=_customer(order),
            related=related,
            link_path=_order_link(order),
        )
        if order.requires_shipping:
            services.emit(
                "staff.order.to_fulfil",
                event_id=str(order.pk),
                values=values,
                related=related,
                staff_link_path=_staff_order_link(order),
            )
    elif to_status == "cancelled":
        if reason == "payment_expired":
            # A meaningful failure: the payment window passed without a verified payment.
            services.emit(
                "payment.failed",
                event_id=str(order.pk),
                values=values,
                customer=_customer(order),
                related=related,
                link_path=_order_link(order),
            )
            services.emit(
                "staff.payment.problem",
                event_id=str(order.pk),
                values=values,
                related=related,
                staff_link_path=_staff_order_link(order),
            )
        else:
            services.emit(
                "order.cancelled",
                event_id=str(order.pk),
                values=values,
                customer=_customer(order),
                related=related,
                link_path=_order_link(order),
            )
        services.emit(
            "staff.order.cancelled",
            event_id=str(order.pk),
            values=values,
            related=related,
            staff_link_path=_staff_order_link(order),
        )


def payment_verified(order: Order) -> None:
    """After a verified online payment or recorded cash. Never on COD placement (D-087)."""
    if order.payment_status != "paid":
        return
    values = {**_order_values(order), "amount": money(order.amount_paid, order.currency)}
    services.emit(
        "payment.succeeded",
        event_id=str(order.pk),
        values=values,
        customer=_customer(order),
        related=("order", str(order.pk)),
        link_path=_order_link(order),
    )
    if order.payment_method == "cod":
        services.emit(
            "staff.cod.collected",
            event_id=str(order.pk),
            values=values,
            related=("order", str(order.pk)),
            staff_link_path=_staff_order_link(order),
        )


def refund_changed(order: Order, refund: Refund) -> None:
    kind = "refund.completed" if refund.status == "succeeded" else "refund.initiated"
    if refund.status not in ("succeeded", "pending"):
        return
    services.emit(
        kind,
        event_id=str(refund.pk),
        values={**_order_values(order), "amount": money(refund.amount, refund.currency)},
        customer=_customer(order),
        related=("refund", str(refund.pk)),
        link_path=_order_link(order),
    )


def shipment_changed(order: Order, shipment: Shipment, *, to_status: str) -> None:
    values = {
        **_order_values(order),
        "tracking_reference": shipment.tracking_number or "—",
    }
    related = ("shipment", str(shipment.pk))
    if to_status == "dispatched":
        kind, event = "shipment.dispatched", str(shipment.pk)
    elif to_status == "delivered":
        kind, event = "shipment.delivered", str(shipment.pk)
    elif to_status == "delivery_failed":
        stamp = shipment.failed_at.isoformat() if shipment.failed_at else ""
        kind, event = "shipment.failed", f"{shipment.pk}:{stamp}"
        services.emit(
            "staff.delivery.failed",
            event_id=event,
            values=values,
            related=related,
            staff_link_path=f"/manage/shipments/{shipment.pk}",
        )
    else:
        return
    services.emit(
        kind,
        event_id=event,
        values=values,
        customer=_customer(order),
        related=related,
        link_path=_order_link(order),
    )


def _case_values(case: AfterSalesCase) -> dict[str, Any]:
    item = case.order_item
    return {
        **(_order_values(item.order) if item is not None else {}),
        "case_kind": case.get_kind_display().lower(),
        "case_status": case.get_status_display().lower(),
        "customer_name": case.customer.full_name,
    }


def _case_customer(case: AfterSalesCase) -> Recipient:
    user = case.customer
    return Recipient(user=user, email=user.email or "", phone=user.phone or "", name=user.full_name)


def case_submitted(case: AfterSalesCase) -> None:
    values = _case_values(case)
    services.emit(
        "case.submitted",
        event_id=str(case.pk),
        values=values,
        customer=_case_customer(case),
        related=("case", str(case.pk)),
        link_path="/account",
    )
    services.emit(
        "staff.case.new",
        event_id=str(case.pk),
        values=values,
        related=("case", str(case.pk)),
        staff_link_path="/manage/aftersales",
    )


def case_status_changed(case: AfterSalesCase) -> None:
    if case.status in ("requested",):
        return
    services.emit(
        "case.updated",
        event_id=f"{case.pk}:{case.status}",
        values=_case_values(case),
        customer=_case_customer(case),
        related=("case", str(case.pk)),
        link_path="/account",
    )


def case_message(case: AfterSalesCase, message: CaseMessage) -> None:
    values = _case_values(case)
    if message.author_id == case.customer_id:
        services.emit(
            "staff.case.message",
            event_id=str(message.pk),
            values=values,
            related=("case", str(case.pk)),
            staff_link_path="/manage/aftersales",
        )
    else:
        services.emit(
            "case.reply",
            event_id=str(message.pk),
            values=values,
            customer=_case_customer(case),
            related=("case", str(case.pk)),
            link_path="/account",
        )


def review_submitted(review: ProductReview) -> None:
    if review.status != "pending":
        return
    services.emit(
        "staff.review.pending",
        event_id=str(review.pk),
        values={"product_name": review.order_item.product_name, "rating": str(review.rating)},
        related=("review", str(review.pk)),
        staff_link_path="/manage/aftersales",
    )


def fraud_flagged(order: Order, assessment: FraudAssessment) -> None:
    if assessment.status != "pending":
        return
    services.emit(
        "staff.fraud.review",
        event_id=str(assessment.pk),
        values={**_order_values(order), "score": str(assessment.score)},
        related=("order", str(order.pk)),
        staff_link_path=_staff_order_link(order),
    )


def _account_recipient(user: User) -> Recipient:
    return Recipient(user=user, email=user.email or "", name=user.full_name)


def password_changed(user: User) -> None:
    from django.utils import timezone

    services.emit(
        "security.password_changed",
        event_id=f"{user.pk}:{timezone.now().isoformat()}",
        values={},
        customer=_account_recipient(user),
        related=("user", str(user.pk)),
        link_path="/account" if user.account_type == "customer" else "/manage/account/security",
    )


def mfa_changed(user: User, change: str) -> None:
    from django.utils import timezone

    services.emit(
        "security.mfa_changed",
        event_id=f"{user.pk}:{change}:{timezone.now().isoformat()}",
        values={"change": change},
        customer=_account_recipient(user),
        related=("user", str(user.pk)),
        link_path="/account" if user.account_type == "customer" else "/manage/account/security",
    )


def role_changed(user: User, role_label: str) -> None:
    from django.utils import timezone

    services.emit(
        "security.role_changed",
        event_id=f"{user.pk}:{role_label}:{timezone.now().isoformat()}",
        values={"role": role_label},
        customer=_account_recipient(user),
        related=("user", str(user.pk)),
        link_path="/manage",
    )
