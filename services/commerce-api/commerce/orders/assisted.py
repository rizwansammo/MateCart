"""Merchant-assisted orders (D-077): staff enter an order a customer asked for elsewhere.

There is no second order engine. A staff-owned cart and checkout session go through the same
checkout rules (prices, stock, shipping eligibility, tax, coupons, delivery slots), the same
review step (temporary stock reservations under the variant locks) and the same placement step
(order number, allocations, coupon redemption, slot booking, invoice, history and audit) as a
storefront order, all in one transaction. A failure anywhere rolls everything back, so nothing
stays reserved.

The order belongs to no customer account and carries no guest credential: staff record the
contact details the customer gave, never create or link an account, and never mint a browser
token. Payment is cash on delivery only, so the order stays unpaid until the existing collection
operation records real cash. The request key makes placement idempotent per store; a replay
returns the same order, and a reused key with a different request is refused.

Phase 14 (social commerce) may call ``place_assisted_order`` from its own channels later.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from commerce.catalog.models import ProductStatus, ProductVariant
from commerce.checkout import services as checkout
from commerce.checkout.models import (
    Cart,
    CartItem,
    CheckoutSession,
    DeliverySlot,
    ShippingMethod,
)
from commerce.core.domain import ConflictError
from commerce.identity.models import User
from commerce.inventory import services as inventory
from commerce.pricing import services as pricing

from . import services
from .models import ASSISTED_SOURCES, AssistedOrder, Order, PaymentMethod

MAX_LINES = 50
SEARCH_LIMIT = 20
CONTACT_FIELDS = (
    "recipient_name",
    "contact_phone",
    "contact_email",
    "address_line_1",
    "address_line_2",
    "city",
    "region",
    "postal_code",
    "country",
    "delivery_notes",
)
TOTAL_FIELDS = ("item_subtotal", "discount_total", "shipping_total", "tax_total", "grand_total")


@dataclass(frozen=True)
class AssistedQuote:
    """Plain values read before the draft is rolled back (the rows no longer exist after it)."""

    valid: bool
    lines: list[checkout.LineQuote]
    totals: CheckoutSession | None
    shipping_methods: list[ShippingMethod]
    delivery_slots: list[DeliverySlot]
    payment_methods: list[str]


@dataclass(frozen=True)
class AssistedPlacement:
    order: Order
    replayed: bool


def _key_hash(value: str) -> str:
    return hashlib.sha256(f"assisted:{value}".encode()).hexdigest()


def _fingerprint(data: Mapping[str, Any]) -> str:
    """A digest of everything that defines the request except its key, so a replay can be told
    apart from a different order sent with a reused key."""
    canonical = {
        "items": sorted(
            (str(line["variant"]), str(Decimal(str(line["quantity"])).normalize()))
            for line in data["items"]
        ),
        **{field: str(data.get(field) or "").strip() for field in CONTACT_FIELDS},
        "shipping_method": str(data.get("shipping_method") or ""),
        "delivery_slot": str(data.get("delivery_slot") or ""),
        "coupon_code": str(data.get("coupon_code") or "").strip().upper(),
        "payment_method": str(data["payment_method"]),
        "source": str(data["source"]),
        "source_reference": str(data.get("source_reference") or "").strip(),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def _clean_lines(items: Sequence[Mapping[str, Any]]) -> list[tuple[ProductVariant, Decimal]]:
    if not items:
        raise ValidationError({"items": ["Add at least one product."]})
    if len(items) > MAX_LINES:
        raise ValidationError({"items": [f"An order can contain at most {MAX_LINES} products."]})
    seen: set[str] = set()
    lines: list[tuple[ProductVariant, Decimal]] = []
    for line in items:
        variant_id = str(line["variant"])
        if variant_id in seen:
            raise ValidationError({"items": ["List each product variant once."]})
        seen.add(variant_id)
        # The scoped manager only finds this store's variants.
        variant = ProductVariant.objects.select_related("product").filter(pk=variant_id).first()
        if variant is None:
            raise ValidationError({"items": ["A selected product does not exist."]})
        lines.append((variant, checkout._clean_item_quantity(variant, line["quantity"])))
    return lines


def _draft(data: Mapping[str, Any], actor: User) -> tuple[Cart, checkout.CartQuote]:
    checkout._require_cart_module()
    cart = Cart.objects.create(
        assisted_by=actor, expires_at=timezone.now() + checkout.CART_LIFETIME
    )
    CartItem.objects.bulk_create(
        [
            CartItem(cart=cart, variant=variant, quantity=quantity)
            for variant, quantity in _clean_lines(data["items"])
        ]
    )
    return cart, checkout.quote_cart(cart)


def _details(data: Mapping[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {
        field: data.get(field) or "" for field in CONTACT_FIELDS if field in data
    }
    for field in ("shipping_method", "delivery_slot"):
        if field in data:
            details[field] = data[field]
    if "coupon_code" in data:
        details["coupon_code"] = data["coupon_code"] or ""
    return details


def _payment_methods(session: CheckoutSession) -> list[str]:
    # Only cash on delivery: staff cannot hand an online payment page to the customer, and no
    # in-store collection method exists, so nothing else could genuinely complete the order.
    return [
        method
        for method in services.available_payment_methods(session)
        if method == PaymentMethod.CASH_ON_DELIVERY
    ]


def quote_assisted_order(*, data: Mapping[str, Any], actor: User) -> AssistedQuote:
    """Server-calculated lines, totals, delivery and payment options for a draft assisted order.
    Nothing is kept: the draft is rolled back, and no stock is reserved."""
    with transaction.atomic():
        cart, quote = _draft(data, actor)
        if not quote.valid:
            transaction.set_rollback(True)
            return AssistedQuote(False, quote.lines, None, [], [], [])
        session = checkout.create_checkout(cart=cart)
        session = checkout.update_checkout(session, _details(data))
        result = AssistedQuote(
            valid=True,
            lines=quote.lines,
            totals=session,
            shipping_methods=checkout.available_shipping_methods(session),
            delivery_slots=checkout.available_delivery_slots(session),
            payment_methods=_payment_methods(session),
        )
        transaction.set_rollback(True)
    return result


def _require_reviewed_totals(session: CheckoutSession, expected: Mapping[str, Any]) -> None:
    """Staff place the order only at the total they reviewed. If a price, discount, delivery
    charge or tax changed after the quote, nothing is placed and they review the new total."""
    if str(expected["currency"]).upper() != session.currency or any(
        Decimal(str(expected[field])) != getattr(session, field) for field in TOTAL_FIELDS
    ):
        raise ConflictError(
            "The total for this order changed after it was calculated. Review the new total and "
            "place the order again.",
            "quote_changed",
        )


def _replay(digest: str, fingerprint: str) -> Order | None:
    existing = AssistedOrder.objects.select_related("order").filter(request_key_hash=digest).first()
    if existing is None:
        return None
    if existing.request_fingerprint != fingerprint:
        raise ConflictError(
            "This request key was already used for a different order.", "idempotency_conflict"
        )
    return existing.order


def place_assisted_order(*, data: Mapping[str, Any], actor: User) -> AssistedPlacement:
    """Place one merchant-assisted order through the storefront's checkout and placement rules."""
    key = str(data["idempotency_key"]).strip()
    if not 8 <= len(key) <= 100:
        raise ValidationError({"idempotency_key": ["Use an idempotency key of 8-100 characters."]})
    if data["source"] not in ASSISTED_SOURCES:
        raise ValidationError({"source": ["Choose where the customer placed this order."]})
    if data.get("customer_request_attested") is not True:
        raise ValidationError(
            {"customer_request_attested": ["Confirm that the customer asked for this order."]}
        )
    if data["payment_method"] != PaymentMethod.CASH_ON_DELIVERY:
        raise ValidationError({"payment_method": ["This payment method is not available."]})
    if not data.get("expected_totals"):
        raise ValidationError(
            {"expected_totals": ["Calculate and review the total before placing the order."]}
        )
    digest = _key_hash(key)
    fingerprint = _fingerprint(data)
    existing = _replay(digest, fingerprint)
    if existing is not None:
        return AssistedPlacement(existing, True)
    try:
        with transaction.atomic():
            cart, quote = _draft(data, actor)
            if not quote.valid:
                issue = next(line for line in quote.lines if line.issue)
                raise ConflictError(
                    f"{issue.variant.sku}: this item cannot be ordered right now.",
                    issue.issue or "cart_invalid",
                )
            session = checkout.create_checkout(cart=cart)
            checkout.update_checkout(session, _details(data))
            session = checkout.review_checkout(
                session=session, idempotency_key=f"assisted-review:{key}"[:100], actor=actor
            )
            if data["payment_method"] not in _payment_methods(session):
                raise ValidationError(
                    {"payment_method": ["Cash on delivery is not available for this order."]}
                )
            _require_reviewed_totals(session, data["expected_totals"])
            order, _ = services._place(
                session,
                data["payment_method"],
                services.key_hash(f"assisted-place:{key}"),
                actor,
                source=data["source"],
            )
            AssistedOrder.objects.create(
                order=order,
                created_by=actor,
                source_reference=str(data.get("source_reference") or "").strip(),
                customer_request_attested=True,
                request_key_hash=digest,
                request_fingerprint=fingerprint,
            )
    except (IntegrityError, APIException):
        # A concurrent request with the same key may have won; it is the answer to this one.
        replayed = _replay(digest, fingerprint)
        if replayed is None:
            raise
        return AssistedPlacement(replayed, True)
    return AssistedPlacement(order, False)


def search_variants(query: str) -> list[tuple[ProductVariant, pricing.PriceQuote | None, Any]]:
    """Active, sellable-looking variants for the assisted order form, with their current price
    and sellable stock (None when stock is not tracked). Bounded to a short result list."""
    text = query.strip()
    rows = ProductVariant.objects.select_related("product").filter(
        is_active=True, product__status=ProductStatus.ACTIVE
    )
    if text:
        rows = rows.filter(
            Q(product__name__icontains=text)
            | Q(sku__icontains=text)
            | Q(title__icontains=text)
            | Q(barcode__iexact=text)
        )
    variants = list(rows.order_by("product__name", "position", "pk")[:SEARCH_LIMIT])
    stock = inventory.sellable_quantities(variants) if variants else {}
    return [
        (
            variant,
            pricing.quote(variant),
            stock.get(variant.pk) if inventory.is_tracked(variant) else None,
        )
        for variant in variants
    ]
