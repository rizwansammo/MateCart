"""Loyalty points (D-082).

- Only registered customers with a verified email or phone take part. Guest and
  merchant-assisted orders (which have no customer) never earn, and accounts are never matched by
  email or phone.
- At placement an order gets a pending award, fixed from its immutable totals: ``earn_points``
  per ``earn_spend`` of items after discounts (delivery and tax excluded), rounded down.
- Points become usable when the order is paid in full (verified online payment or recorded cash)
  **and** delivered (a physical shipment marked delivered; digital-only orders once paid).
- ``sync_order`` is the only place awards and restorations change. It is target-based and
  idempotent: whatever event calls it, and however often, the ledger converges on
  ``floor(points * (1 - refunded / paid))`` for an eligible order and 0 for a cancelled one.
- Redeemed points are debited at placement under the account lock and restored once if the
  order is cancelled or fully refunded. Partial refunds do not restore redeemed points.
- A reversal larger than the usable balance never makes it negative: the shortfall becomes an
  explicit ``deficit`` that later earnings repay first, recorded in the ledger.
- Optional expiry: the whole balance expires after ``expiry_days`` without earning or
  redeeming, applied when the account is next used or viewed.
- Points have no cash value: they only reduce an order total and cannot be transferred.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.core.domain import ConflictError
from commerce.identity.models import AccountType, User
from commerce.orders.models import Order, OrderPaymentStatus, OrderStatus
from commerce.stores.models import StoreSettings
from commerce.stores.modules import enabled_modules

from .models import (
    AwardStatus,
    LedgerKind,
    LoyaltyAccount,
    LoyaltyAward,
    LoyaltyLedger,
    LoyaltyProgram,
)

PROGRAM_FIELDS = [
    "is_enabled",
    "earn_points",
    "earn_spend",
    "point_value",
    "min_redeem_points",
    "max_redeem_percent",
    "expiry_days",
]
MAX_ADJUSTMENT = 1_000_000


class LoyaltyUnavailable(ConflictError):
    default_code = "loyalty_unavailable"
    default_detail = "Your points balance changed. Review your checkout again."


def _quantum(currency: str) -> Decimal:
    from commerce.pricing.services import currency_precision

    return Decimal(1).scaleb(-currency_precision(currency))


def program() -> LoyaltyProgram:
    current = LoyaltyProgram.objects.first()
    if current is not None:
        return current
    currency = StoreSettings.objects.values_list("default_currency", flat=True).first()
    if currency is None:
        raise ConflictError("Store settings are missing.", "store_missing")
    try:
        with transaction.atomic():
            return LoyaltyProgram.objects.create(currency=currency)
    except IntegrityError:
        found = LoyaltyProgram.objects.first()
        if found is None:
            raise
        return found


def active_program() -> LoyaltyProgram | None:
    """The program when customers can earn and redeem: module effective and switched on."""
    if "loyalty" not in enabled_modules():
        return None
    current = LoyaltyProgram.objects.first()
    return current if current is not None and current.is_enabled else None


def eligible_customer(user: Any) -> User | None:
    if (
        isinstance(user, User)
        and user.account_type == AccountType.CUSTOMER
        and user.is_active
        and (user.email_verified_at is not None or user.phone_verified_at is not None)
    ):
        return user
    return None


def save_program(*, data: Mapping[str, Any], actor: User) -> LoyaltyProgram:
    with transaction.atomic():
        target = LoyaltyProgram.objects.select_for_update().get(pk=program().pk)
        before = {field: getattr(target, field) for field in PROGRAM_FIELDS}
        for field in PROGRAM_FIELDS:
            if field in data:
                setattr(target, field, data[field])
        quantum = _quantum(target.currency)
        for field in ("earn_spend", "point_value"):
            value: Decimal = getattr(target, field)
            if value <= 0 or value != value.quantize(quantum):
                raise ValidationError({field: [f"Enter a positive {target.currency} amount."]})
        if target.expiry_days is not None and target.expiry_days < 30:
            raise ValidationError({"expiry_days": ["Points can expire after 30 days at least."]})
        if target.is_enabled and "loyalty" not in enabled_modules():
            raise ConflictError(
                "Turn on the Loyalty points module in Settings first.", "module_disabled"
            )
        target.save()
        record_audit_event(
            action="marketing.loyalty.program_updated",
            resource=target,
            actor=actor,
            changes=field_changes(
                before,
                {field: getattr(target, field) for field in PROGRAM_FIELDS},
                fields=PROGRAM_FIELDS,
            ),
        )
        return target


# --- Accounts and the ledger ----------------------------------------------------------------------


def _locked_account(customer_id: Any) -> LoyaltyAccount:
    account = LoyaltyAccount.objects.select_for_update().filter(customer_id=customer_id).first()
    if account is not None:
        return account
    try:
        with transaction.atomic():
            LoyaltyAccount.objects.create(customer_id=customer_id)
    except IntegrityError:
        pass
    return LoyaltyAccount.objects.select_for_update().get(customer_id=customer_id)


def _append(
    account: LoyaltyAccount,
    kind: str,
    *,
    points: int,
    reference: str,
    order: Order | None = None,
    deficit_change: int = 0,
    note: str = "",
    actor: User | None = None,
) -> LoyaltyLedger | None:
    """Apply a balance change once per ``reference``; a repeated reference is a no-op."""
    if points == 0 and deficit_change == 0:
        return None
    if LoyaltyLedger.objects.filter(reference=reference).exists():
        return None
    account.balance += points
    account.deficit += deficit_change
    if account.balance < 0 or account.deficit < 0:
        raise LoyaltyUnavailable()
    now = timezone.now()
    if kind == LedgerKind.EARNED:
        account.lifetime_earned += points - deficit_change
    if kind == LedgerKind.REDEEMED:
        account.lifetime_redeemed += -points
    if kind in (LedgerKind.EARNED, LedgerKind.REDEEMED, LedgerKind.ADJUSTED):
        account.last_activity_at = now
    account.save(
        update_fields=[
            "balance",
            "deficit",
            "lifetime_earned",
            "lifetime_redeemed",
            "last_activity_at",
            "updated_at",
        ]
    )
    return LoyaltyLedger.objects.create(
        account=account,
        kind=kind,
        points=points,
        deficit_change=deficit_change,
        balance_after=account.balance,
        deficit_after=account.deficit,
        order=order,
        reference=reference[:120],
        note=note[:200],
        actor=actor,
        occurred_at=now,
    )


def _credit(account: LoyaltyAccount, points: int, *, reference: str, order: Order) -> None:
    repay = min(account.deficit, points)
    _append(
        account,
        LedgerKind.EARNED,
        points=points - repay,
        deficit_change=-repay,
        reference=reference,
        order=order,
        note="Deficit repaid first" if repay else "",
    )


def _debit_reversal(account: LoyaltyAccount, points: int, *, reference: str, order: Order) -> None:
    take = min(account.balance, points)
    _append(
        account,
        LedgerKind.REVERSED,
        points=-take,
        deficit_change=points - take,
        reference=reference,
        order=order,
        note="Points already spent are recorded as a deficit" if take < points else "",
    )


def apply_expiry(account: LoyaltyAccount) -> None:
    """Expire the balance after the program's inactivity period (caller holds the lock)."""
    current = LoyaltyProgram.objects.first()
    if (
        current is None
        or current.expiry_days is None
        or account.balance == 0
        or account.last_activity_at is None
        or timezone.now() - account.last_activity_at < timedelta(days=current.expiry_days)
    ):
        return
    stamp = account.last_activity_at.isoformat()
    _append(
        account,
        LedgerKind.EXPIRED,
        points=-account.balance,
        reference=f"expire:{account.pk}:{stamp}",
        note=f"No activity for {current.expiry_days} days",
    )


def account_summary(customer: User) -> dict[str, Any]:
    """A customer's own view: balance, pending points and recent ledger (expiry applied)."""
    with transaction.atomic():
        account = LoyaltyAccount.objects.select_for_update().filter(customer=customer).first()
        if account is not None:
            apply_expiry(account)
    current = active_program()
    pending = sum(
        award.points
        for award in LoyaltyAward.objects.filter(
            account__customer=customer, status=AwardStatus.PENDING
        )
    )
    entries = (
        list(
            LoyaltyLedger.objects.filter(account=account)
            .select_related("order")
            .order_by("-occurred_at", "-pk")[:50]
        )
        if account is not None
        else []
    )
    return {
        "enabled": current is not None,
        "program": current,
        "balance": account.balance if account else 0,
        "deficit": account.deficit if account else 0,
        "pending": pending,
        "lifetime_earned": account.lifetime_earned if account else 0,
        "lifetime_redeemed": account.lifetime_redeemed if account else 0,
        "entries": entries,
    }


# --- Checkout -------------------------------------------------------------------------------------


def redemption_cap(base: Decimal, current: LoyaltyProgram) -> int:
    """The most points an order with ``base`` (items after coupon) can use."""
    if base <= 0:
        return 0
    limit = base * Decimal(current.max_redeem_percent) / Decimal(100)
    return int((limit / current.point_value).to_integral_value(rounding=ROUND_DOWN))


def redemption_amount(points: int, current: LoyaltyProgram) -> Decimal:
    amount: Decimal = (Decimal(points) * current.point_value).quantize(
        _quantum(current.currency), rounding=ROUND_HALF_UP
    )
    return amount


def clamp_redemption(
    *, customer_id: Any, points: int, base: Decimal, currency: str
) -> tuple[int, Decimal]:
    """The points a checkout can actually use now (never more than balance or cap), and their
    value. Never raises: invalid requests become zero and are reported by ``validate``."""
    current = active_program()
    if points <= 0 or customer_id is None or current is None or current.currency != currency:
        return 0, Decimal(0)
    balance = (
        LoyaltyAccount.objects.filter(customer_id=customer_id)
        .values_list("balance", flat=True)
        .first()
        or 0
    )
    usable = min(points, balance, redemption_cap(base, current))
    if usable < current.min_redeem_points:
        return 0, Decimal(0)
    return usable, redemption_amount(usable, current)


def validate_redemption(*, customer: Any, points: int, base: Decimal, currency: str) -> None:
    if points == 0:
        return
    if points < 0:
        raise ValidationError({"loyalty_points": ["Enter zero or more points."]})
    current = active_program()
    if current is None or eligible_customer(customer) is None:
        raise ValidationError({"loyalty_points": ["Points can't be used for this order."]})
    if current.currency != currency:
        raise ValidationError({"loyalty_points": ["Points can't be used in this currency."]})
    if points < current.min_redeem_points:
        raise ValidationError(
            {"loyalty_points": [f"Use at least {current.min_redeem_points} points."]}
        )
    with transaction.atomic():
        account = LoyaltyAccount.objects.select_for_update().filter(customer=customer).first()
        if account is not None:
            apply_expiry(account)
        balance = account.balance if account else 0
    if points > balance:
        raise ValidationError({"loyalty_points": [f"You have {balance} points."]})
    cap = redemption_cap(base, current)
    if points > cap:
        raise ValidationError({"loyalty_points": [f"You can use at most {cap} points here."]})


# --- Orders ---------------------------------------------------------------------------------------


def commit_for_order(order: Order, *, points: int) -> None:
    """At placement: debit redeemed points and create the order's pending award."""
    if order.customer_id is None:
        if points:
            raise LoyaltyUnavailable()
        return
    current = active_program()
    customer = User.objects.filter(pk=order.customer_id).first()
    if points:
        if current is None or eligible_customer(customer) is None:
            raise LoyaltyUnavailable()
        account = _locked_account(order.customer_id)
        apply_expiry(account)
        if account.balance < points:
            raise LoyaltyUnavailable()
        _append(
            account,
            LedgerKind.REDEEMED,
            points=-points,
            reference=f"redeem:{order.pk}",
            order=order,
        )
    if current is None or eligible_customer(customer) is None:
        return
    spend = order.item_subtotal - order.discount_total
    if order.currency != current.currency or spend <= 0:
        return
    earned = int(
        (spend / current.earn_spend * current.earn_points).to_integral_value(rounding=ROUND_DOWN)
    )
    if earned <= 0:
        return
    account = _locked_account(order.customer_id)
    LoyaltyAward.objects.create(account=account, order=order, points=earned)


def _delivered(order: Order) -> bool:
    if not order.requires_shipping:
        return True
    from commerce.fulfillment.models import Shipment, ShipmentStatus

    return Shipment.objects.filter(order=order, status=ShipmentStatus.DELIVERED).exists()


def sync_order(order: Order) -> None:
    """Bring the order's award and redemption in line with its current state. Idempotent; the
    caller should hold the order lock (it is taken here when it is not)."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        award = LoyaltyAward.objects.select_for_update().filter(order=order).first()
        redeemed = order.loyalty_points_redeemed
        if award is None and not redeemed:
            return
        account = _locked_account(order.customer_id)
        cancelled = order.status == OrderStatus.CANCELLED
        fully_refunded = order.payment_status == OrderPaymentStatus.REFUNDED
        if (
            redeemed
            and (cancelled or fully_refunded)
            and LoyaltyLedger.objects.filter(reference=f"redeem:{order.pk}").exists()
        ):
            _append(
                account,
                LedgerKind.RESTORED,
                points=redeemed,
                reference=f"restore:{order.pk}",
                order=order,
            )
        if award is None:
            return
        paid = order.amount_paid
        ratio = (order.amount_refunded / paid) if paid > 0 else Decimal(0)
        target = (
            0
            if cancelled
            else int((Decimal(award.points) * (1 - ratio)).to_integral_value(rounding=ROUND_DOWN))
        )
        eligible = (
            not cancelled
            and order.payment_status
            in (OrderPaymentStatus.PAID, OrderPaymentStatus.PARTIALLY_REFUNDED)
            and _delivered(order)
        )
        if award.status == AwardStatus.PENDING:
            if cancelled or (fully_refunded and target == 0):
                award.status = AwardStatus.CANCELLED
            elif eligible and target > 0:
                _credit(account, target, reference=f"earn:{order.pk}", order=order)
                award.earned_points = target
                award.status = AwardStatus.EARNED
        elif award.status == AwardStatus.EARNED and award.earned_points > target:
            _debit_reversal(
                account,
                award.earned_points - target,
                reference=f"reverse:{order.pk}:{target}",
                order=order,
            )
            award.earned_points = target
            if target == 0:
                award.status = AwardStatus.CANCELLED
        award.save(update_fields=["status", "earned_points", "updated_at"])


def schedule_sync(order: Order) -> None:
    """Run ``sync_order`` after the current transaction commits (never fails the caller)."""
    order_id = order.pk

    def run() -> None:
        target = Order.objects.filter(pk=order_id).first()
        if target is not None:
            sync_order(target)

    transaction.on_commit(run)


def adjust(
    *, customer_id: Any, points: int, reason: str, actor: User, key: str = ""
) -> LoyaltyLedger:
    """An authorised manual adjustment. A deduction can never exceed the usable balance."""
    if points == 0 or abs(points) > MAX_ADJUSTMENT:
        raise ValidationError({"points": ["Enter a non-zero number of points."]})
    note = reason.strip()
    if not 5 <= len(note) <= 200:
        raise ValidationError({"reason": ["Explain the adjustment (5-200 characters)."]})
    customer = User.objects.filter(pk=customer_id, account_type=AccountType.CUSTOMER).first()
    if customer is None:
        raise ConflictError("This customer does not exist.", "not_found")
    reference = f"adjust:{key or uuid.uuid4()}"
    with transaction.atomic():
        account = _locked_account(customer.pk)
        previous = LoyaltyLedger.objects.filter(reference=reference).first()
        if previous is not None:
            return previous
        if points < 0 and account.balance < -points:
            raise ConflictError(
                f"The customer has only {account.balance} usable points.", "insufficient_points"
            )
        entry = _append(
            account, LedgerKind.ADJUSTED, points=points, reference=reference, note=note, actor=actor
        )
        if entry is None:  # pragma: no cover - a fresh reference always appends
            raise ConflictError("The adjustment was not recorded.", "adjustment_failed")
        record_audit_event(
            action="marketing.loyalty.adjusted",
            resource=entry,
            actor=actor,
            changes={"points": points, "reason": note, "balance_after": account.balance},
        )
        return entry
