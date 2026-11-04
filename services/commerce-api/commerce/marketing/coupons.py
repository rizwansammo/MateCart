"""Merchant coupon management (Phase 12) on the unchanged Phase 07 ``Coupon`` model.

Validation, calculation and redemption stay in checkout and order placement. Here merchants only
create and maintain coupons: the code never changes, and once a coupon has been redeemed its
discount and minimum stay fixed (so order history keeps meaning what it said); it can still be
paused, given a new end time or a higher usage limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db import transaction
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.checkout.models import Coupon, CouponDiscountType
from commerce.core.domain import ConflictError, save_unique, validated
from commerce.identity.models import User

from .promotions import money_quantum, store_currency

FIELDS = [
    "code",
    "discount_type",
    "value",
    "currency",
    "minimum_subtotal",
    "starts_at",
    "ends_at",
    "max_redemptions",
    "is_active",
]


def _check(coupon: Coupon) -> None:
    coupon.value = Decimal(coupon.value)
    coupon.minimum_subtotal = Decimal(coupon.minimum_subtotal)
    if coupon.discount_type == CouponDiscountType.PERCENT:
        coupon.currency = ""
        if not Decimal(0) < coupon.value <= Decimal(100):
            raise ValidationError({"value": ["Use a percentage above 0 and up to 100."]})
        if coupon.value != coupon.value.quantize(Decimal("0.01")):
            raise ValidationError({"value": ["Use at most two decimal places."]})
    else:
        coupon.currency = store_currency()
        if coupon.value <= 0 or coupon.value != coupon.value.quantize(
            money_quantum(coupon.currency)
        ):
            raise ValidationError({"value": [f"Enter a valid {coupon.currency} amount."]})
    currency = store_currency()
    if coupon.minimum_subtotal != coupon.minimum_subtotal.quantize(money_quantum(currency)):
        raise ValidationError({"minimum_subtotal": [f"Enter a valid {currency} amount."]})
    if coupon.starts_at and coupon.ends_at and coupon.starts_at >= coupon.ends_at:
        raise ValidationError({"ends_at": ["The end must be after the start."]})
    if coupon.max_redemptions is not None and coupon.max_redemptions < coupon.times_redeemed:
        raise ValidationError(
            {"max_redemptions": [f"It has already been used {coupon.times_redeemed} times."]}
        )


def save_coupon(*, coupon: Coupon | None, data: Mapping[str, Any], actor: User) -> Coupon:
    with transaction.atomic():
        creating = coupon is None
        target = (
            Coupon() if coupon is None else Coupon.objects.select_for_update().get(pk=coupon.pk)
        )
        before = {field: getattr(target, field, None) for field in FIELDS}
        if not creating and target.times_redeemed > 0:
            locked = [
                field
                for field in ("value", "minimum_subtotal")
                if field in data and data[field] != getattr(target, field)
            ]
            if locked:
                raise ConflictError(
                    "This coupon has been used, so its discount and minimum stay fixed. "
                    "Create a new coupon instead.",
                    "coupon_in_use",
                )
        editable = FIELDS if creating else FIELDS[2:]
        for field in editable:
            if field in data and field != "currency":
                setattr(target, field, data[field])
        _check(target)
        validated(target, exclude=["tenant"])
        save_unique(target, {"checkout_coupon_code_unique": ("code", "This code already exists.")})
        record_audit_event(
            action="marketing.coupon.created" if creating else "marketing.coupon.updated",
            resource=target,
            actor=actor,
            changes=field_changes(
                before, {field: getattr(target, field) for field in FIELDS}, fields=FIELDS
            ),
        )
        return target
