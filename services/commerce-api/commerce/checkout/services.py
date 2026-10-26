"""Cart and checkout rules: ownership, authoritative totals and temporary stock holds."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError

from commerce.audit.services import record_audit_event
from commerce.catalog.models import ProductStatus, ProductVariant, VariantKind
from commerce.core.contact import normalize_phone
from commerce.core.domain import ConflictError, DomainError, found, validated
from commerce.identity.models import AccountType, User
from commerce.inventory import services as inventory
from commerce.inventory.models import StockLevel, StockLocation
from commerce.pricing import services as pricing
from commerce.stores.models import StoreSettings
from commerce.stores.modules import enabled_modules
from commerce.tenancy.context import get_active_tenant_id

from .models import (
    Address,
    Cart,
    CartItem,
    CartStatus,
    CheckoutLine,
    CheckoutReservation,
    CheckoutSession,
    CheckoutStatus,
    Coupon,
    CouponDiscountType,
    DeliverySlot,
    ShippingMethod,
    TaxRate,
)

CART_LIFETIME = timedelta(days=30)
CHECKOUT_LIFETIME = timedelta(minutes=30)
RESERVATION_LIFETIME = timedelta(minutes=15)
MAX_CART_LINES = 100
MAX_ITEM_QUANTITY = Decimal("999.999")
GUEST_COOKIE = "mc_cart"
GUEST_TOKEN_BYTES = 32


class CartUnavailable(DomainError):
    default_code = "cart_unavailable"
    default_detail = "Cart and checkout are not available for this store."


class PriceChanged(ConflictError):
    default_code = "price_changed"
    default_detail = "A price changed. Review the updated total before continuing."


class CartChanged(ConflictError):
    default_code = "cart_changed"
    default_detail = "Your cart changed. Review the updated items before continuing."


class CheckoutExpired(ConflictError):
    default_code = "checkout_expired"
    default_detail = "This checkout session has expired. Start checkout again."


@dataclass(frozen=True)
class CartAccess:
    cart: Cart
    new_guest_token: str | None = None
    clear_guest_cookie: bool = False


@dataclass(frozen=True)
class LineQuote:
    item: CartItem
    variant: ProductVariant
    unit_price: Decimal | None
    currency: str | None
    subtotal: Decimal | None
    available_quantity: Decimal | None
    issue: str


@dataclass(frozen=True)
class CartQuote:
    lines: list[LineQuote]
    subtotal: Decimal
    currency: str
    item_count: int
    valid: bool


def credential_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _new_guest_token() -> str:
    return secrets.token_urlsafe(GUEST_TOKEN_BYTES)


def _customer(user: Any) -> User | None:
    return user if isinstance(user, User) and user.account_type == AccountType.CUSTOMER else None


def _require_cart_module() -> None:
    if "cart_checkout" not in enabled_modules():
        raise CartUnavailable()


def _fresh_expiry() -> Any:
    return timezone.now() + CART_LIFETIME


def _active_customer_cart(customer: User) -> Cart | None:
    return (
        Cart.objects.select_for_update()
        .filter(customer=customer, status=CartStatus.ACTIVE, expires_at__gt=timezone.now())
        .first()
    )


def _guest_cart(token: str | None) -> Cart | None:
    if not token:
        return None
    return (
        Cart.objects.select_for_update()
        .filter(
            guest_token_hash=credential_hash(token),
            status=CartStatus.ACTIVE,
            expires_at__gt=timezone.now(),
        )
        .first()
    )


def _merge_guest_cart(guest: Cart, customer_cart: Cart, customer: User) -> Cart:
    if guest.pk == customer_cart.pk:
        return customer_cart
    target = {
        item.variant_id: item
        for item in CartItem.objects.select_for_update().filter(cart=customer_cart)
    }
    for item in CartItem.objects.select_for_update().filter(cart=guest).order_by("created_at"):
        existing = target.get(item.variant_id)
        if existing is None and len(target) < MAX_CART_LINES:
            item.cart = customer_cart
            item.save(update_fields=["cart", "updated_at"])
            target[item.variant_id] = item
        elif existing is not None:
            existing.quantity = min(MAX_ITEM_QUANTITY, existing.quantity + item.quantity)
            existing.save(update_fields=["quantity", "updated_at"])
            item.delete()
        else:
            item.delete()
    guest.status = CartStatus.ABANDONED
    guest.save(update_fields=["status", "updated_at"])
    CheckoutSession.objects.filter(
        cart=guest, status=CheckoutStatus.DRAFT, customer__isnull=True
    ).update(status=CheckoutStatus.EXPIRED)
    customer_cart.expires_at = _fresh_expiry()
    customer_cart.save(update_fields=["expires_at", "updated_at"])
    return customer_cart


def cart_for_request(*, user: Any, guest_token: str | None, create: bool = True) -> CartAccess:
    """Resolve one browser's cart and safely associate a valid guest cart after sign-in."""
    _require_cart_module()
    customer = _customer(user)
    with transaction.atomic():
        guest = _guest_cart(guest_token)
        if customer is not None:
            cart = _active_customer_cart(customer)
            if cart is None and guest is not None:
                guest.customer = customer
                guest.guest_token_hash = ""
                guest.expires_at = _fresh_expiry()
                guest.save(
                    update_fields=["customer", "guest_token_hash", "expires_at", "updated_at"]
                )
                CheckoutSession.objects.filter(cart=guest, status=CheckoutStatus.DRAFT).update(
                    customer=customer, guest_token_hash=""
                )
                return CartAccess(guest, clear_guest_cookie=True)
            if cart is None:
                if not create:
                    raise PermissionDenied("This cart is not available.")
                cart = Cart.objects.create(customer=customer, expires_at=_fresh_expiry())
            if guest is not None:
                cart = _merge_guest_cart(guest, cart, customer)
            return CartAccess(cart, clear_guest_cookie=guest_token is not None)

        if guest is not None:
            guest.expires_at = _fresh_expiry()
            guest.save(update_fields=["expires_at", "updated_at"])
            return CartAccess(guest)
        if not create:
            raise NotAuthenticated("This cart is not available.")
        token = _new_guest_token()
        cart = Cart.objects.create(
            guest_token_hash=credential_hash(token), expires_at=_fresh_expiry()
        )
        return CartAccess(cart, new_guest_token=token)


def set_guest_cookie(response: Any, access: CartAccess) -> None:
    if access.clear_guest_cookie:
        response.delete_cookie(GUEST_COOKIE, path="/", samesite="Lax")
    elif access.new_guest_token:
        response.set_cookie(
            GUEST_COOKIE,
            access.new_guest_token,
            max_age=int(CART_LIFETIME.total_seconds()),
            path="/",
            secure=settings.MATECART_SECURE_COOKIES,
            httponly=True,
            samesite="Lax",
        )


def _preorder_allowed(variant: ProductVariant) -> bool:
    return (
        variant.release_date is not None
        and variant.release_date > inventory.store_today()
        and "preorders" in enabled_modules()
    )


def _variant_issue(variant: ProductVariant, quantity: Decimal) -> tuple[str, Decimal | None]:
    product = variant.product
    if product.status != ProductStatus.ACTIVE or not variant.is_active:
        return "unavailable", None
    if pricing.quote(variant) is None:
        return "price_unavailable", None
    if not inventory.is_tracked(variant) or _preorder_allowed(variant):
        return "", None
    available = inventory.sellable_quantity(variant)
    return ("insufficient_stock" if quantity > available else "", available)


def quote_cart(cart: Cart) -> CartQuote:
    items = list(
        CartItem.objects.filter(cart=cart).select_related("variant__product").order_by("created_at")
    )
    lines: list[LineQuote] = []
    subtotal = Decimal(0)
    currency = ""
    count = 0
    valid = bool(items)
    for item in items:
        variant = item.variant
        price = pricing.quote(variant)
        issue, available = _variant_issue(variant, item.quantity)
        if price is not None and currency and price.currency != currency:
            issue = "currency_mismatch"
        if price is not None and not currency:
            currency = price.currency
        line_subtotal = price.amount * item.quantity if price is not None else None
        if line_subtotal is not None:
            subtotal += line_subtotal
        lines.append(
            LineQuote(
                item=item,
                variant=variant,
                unit_price=price.amount if price else None,
                currency=price.currency if price else None,
                subtotal=line_subtotal,
                available_quantity=available,
                issue=issue,
            )
        )
        count += int(item.quantity) if item.quantity == item.quantity.to_integral() else 1
        valid = valid and not issue
    if not currency:
        currency = StoreSettings.objects.values_list("default_currency", flat=True).first() or "USD"
    return CartQuote(lines, _round_money(subtotal, currency), currency, count, valid)


def _clean_item_quantity(variant: ProductVariant, value: Any) -> Decimal:
    quantity = inventory.clean_quantity(variant, value)
    if quantity > MAX_ITEM_QUANTITY:
        raise ValidationError({"quantity": ["Choose 999.999 or less."]})
    return quantity


def add_item(*, cart: Cart, variant_id: Any, quantity: Any) -> CartItem:
    with transaction.atomic():
        target = Cart.objects.select_for_update().get(pk=cart.pk)
        if target.status != CartStatus.ACTIVE or target.expires_at <= timezone.now():
            raise ConflictError("This cart has expired.", "cart_expired")
        variant = found(
            ProductVariant.objects.select_related("product").model,
            pk=variant_id,
        )
        variant = ProductVariant.objects.select_related("product").get(pk=variant.pk)
        amount = _clean_item_quantity(variant, quantity)
        existing = CartItem.objects.select_for_update().filter(cart=target, variant=variant).first()
        total = amount + (existing.quantity if existing else Decimal(0))
        if total > MAX_ITEM_QUANTITY:
            raise ValidationError({"quantity": ["The item quantity is too large."]})
        issue, available = _variant_issue(variant, total)
        if issue:
            if issue == "insufficient_stock":
                raise ConflictError(
                    f"Only {available or 0} is currently available.", "insufficient_stock"
                )
            if issue == "price_unavailable":
                raise ConflictError("This item does not have a current price.", issue)
            raise ConflictError("This item is not available for purchase.", issue)
        if existing is not None:
            existing.quantity = total
            existing.save(update_fields=["quantity", "updated_at"])
            item = existing
        else:
            if CartItem.objects.filter(cart=target).count() >= MAX_CART_LINES:
                raise ConflictError("A cart can contain at most 100 different items.", "cart_full")
            item = CartItem.objects.create(cart=target, variant=variant, quantity=amount)
        target.expires_at = _fresh_expiry()
        target.save(update_fields=["expires_at", "updated_at"])
        _expire_draft_checkouts(target)
        return item


def update_item(*, cart: Cart, item_id: Any, quantity: Any) -> CartItem:
    with transaction.atomic():
        target = CartItem.objects.select_for_update().filter(cart=cart, pk=item_id).first()
        if target is None:
            raise PermissionDenied("This cart item is not available.")
        variant = ProductVariant.objects.select_related("product").get(pk=target.variant_id)
        amount = _clean_item_quantity(variant, quantity)
        issue, available = _variant_issue(variant, amount)
        if issue:
            if issue == "insufficient_stock":
                raise ConflictError(
                    f"Only {available or 0} is currently available.", "insufficient_stock"
                )
            raise ConflictError("This item is not available for purchase.", issue)
        target.quantity = amount
        target.save(update_fields=["quantity", "updated_at"])
        _expire_draft_checkouts(cart)
        return target


def remove_item(*, cart: Cart, item_id: Any) -> None:
    with transaction.atomic():
        item = CartItem.objects.select_for_update().filter(cart=cart, pk=item_id).first()
        if item is None:
            raise PermissionDenied("This cart item is not available.")
        item.delete()
        _expire_draft_checkouts(cart)


def _expire_draft_checkouts(cart: Cart) -> None:
    CheckoutSession.objects.filter(cart=cart, status=CheckoutStatus.DRAFT).update(
        status=CheckoutStatus.EXPIRED
    )


def _round_money(value: Decimal, currency: str) -> Decimal:
    quantum = Decimal(1).scaleb(-pricing.currency_precision(currency))
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _sync_lines(session: CheckoutSession, quote: CartQuote) -> None:
    CheckoutLine.objects.filter(session=session).delete()
    rows: list[CheckoutLine] = []
    for line in quote.lines:
        current = pricing.quote(line.variant)
        if line.unit_price is not None and line.subtotal is not None and current is not None:
            rows.append(
                CheckoutLine(
                    session=session,
                    variant=line.variant,
                    product_name=line.variant.product.name,
                    variant_title=line.variant.title,
                    sku=line.variant.sku,
                    kind=line.variant.kind,
                    quantity=line.item.quantity,
                    unit_price=line.unit_price,
                    line_subtotal=line.subtotal,
                    price_id=current.price_id,
                )
            )
    CheckoutLine.objects.bulk_create(rows)


def _default_shipping(currency: str) -> ShippingMethod:
    method = ShippingMethod.objects.filter(code="standard").first()
    if method is not None:
        return method
    return ShippingMethod.objects.create(
        code="standard",
        name="Standard delivery",
        description="Store-arranged standard delivery.",
        amount=Decimal(0),
        currency=currency,
    )


def available_shipping_methods(session: CheckoutSession) -> list[ShippingMethod]:
    if not session.requires_shipping or not session.country:
        return []
    rows = ShippingMethod.objects.filter(
        is_active=True,
        currency=session.currency,
        minimum_subtotal__lte=session.item_subtotal - session.discount_total,
    ).order_by("position", "code")
    return [
        method
        for method in rows
        if not method.supported_countries or session.country in method.supported_countries
    ]


def available_delivery_slots(session: CheckoutSession) -> list[DeliverySlot]:
    if "delivery_slots" not in enabled_modules() or not session.requires_shipping:
        return []
    now = timezone.now()
    return list(
        DeliverySlot.objects.filter(is_active=True, starts_at__gt=now)
        .filter(Q(capacity__isnull=True) | Q(bookings__lt=F("capacity")))
        .order_by("starts_at")
    )


def _coupon(session: CheckoutSession, code: str) -> Coupon | None:
    normalized = code.strip().upper()
    if not normalized:
        return None
    coupon = Coupon.objects.filter(code=normalized, is_active=True).first()
    now = timezone.now()
    if coupon is None:
        raise ValidationError({"coupon_code": ["This coupon is not valid."]})
    if coupon.starts_at and coupon.starts_at > now:
        raise ValidationError({"coupon_code": ["This coupon is not active yet."]})
    if coupon.ends_at and coupon.ends_at <= now:
        raise ValidationError({"coupon_code": ["This coupon has expired."]})
    if coupon.max_redemptions is not None and coupon.times_redeemed >= coupon.max_redemptions:
        raise ValidationError({"coupon_code": ["This coupon has reached its usage limit."]})
    if session.item_subtotal < coupon.minimum_subtotal:
        raise ValidationError({"coupon_code": ["The cart subtotal is too low for this coupon."]})
    if coupon.discount_type == CouponDiscountType.FIXED and coupon.currency != session.currency:
        raise ValidationError({"coupon_code": ["This coupon uses a different currency."]})
    return coupon


def _calculate(session: CheckoutSession) -> None:
    discount = Decimal(0)
    if session.coupon is not None:
        if session.coupon.discount_type == CouponDiscountType.FIXED:
            discount = session.coupon.value
        else:
            discount = session.item_subtotal * session.coupon.value / Decimal(100)
    discount = _round_money(min(discount, session.item_subtotal), session.currency)
    shipping = Decimal(0)
    if session.requires_shipping and session.shipping_method is not None:
        method = session.shipping_method
        shipping = (
            Decimal(0)
            if method.free_above is not None
            and session.item_subtotal - discount >= method.free_above
            else method.amount
        )
    shipping = _round_money(shipping, session.currency)
    tax = Decimal(0)
    if session.country:
        rates = TaxRate.objects.filter(country=session.country, is_active=True)
        rate = rates.filter(region__iexact=session.region).first() if session.region else None
        rate = rate or rates.filter(region="").first()
        if rate is not None:
            base = session.item_subtotal - discount
            if rate.applies_to_shipping:
                base += shipping
            tax = _round_money(base * rate.rate, session.currency)
    session.discount_total = discount
    session.shipping_total = shipping
    session.tax_total = tax
    session.grand_total = _round_money(
        session.item_subtotal - discount + shipping + tax, session.currency
    )


def create_checkout(*, cart: Cart) -> CheckoutSession:
    quote = quote_cart(cart)
    if not quote.valid:
        raise ConflictError("Resolve unavailable cart items before checkout.", "cart_invalid")
    with transaction.atomic():
        current = (
            CheckoutSession.objects.select_for_update()
            .filter(cart=cart, status=CheckoutStatus.DRAFT, expires_at__gt=timezone.now())
            .first()
        )
        if current is not None:
            return current
        owner = {"customer": cart.customer, "guest_token_hash": cart.guest_token_hash}
        session = CheckoutSession.objects.create(
            cart=cart,
            expires_at=timezone.now() + CHECKOUT_LIFETIME,
            currency=quote.currency,
            item_subtotal=quote.subtotal,
            requires_shipping=any(
                line.variant.kind == VariantKind.PHYSICAL for line in quote.lines
            ),
            **owner,
        )
        _sync_lines(session, quote)
        if session.requires_shipping:
            _default_shipping(session.currency)
        _calculate(session)
        session.save(
            update_fields=[
                "discount_total",
                "shipping_total",
                "tax_total",
                "grand_total",
                "updated_at",
            ]
        )
        return session


def owned_checkout(*, session_id: Any, user: Any, guest_token: str | None) -> CheckoutSession:
    customer = _customer(user)
    query = CheckoutSession.objects.select_related(
        "cart", "shipping_method", "delivery_slot", "coupon"
    )
    if customer is not None:
        session = query.filter(pk=session_id, customer=customer).first()
    elif guest_token:
        session = query.filter(
            pk=session_id, customer__isnull=True, guest_token_hash=credential_hash(guest_token)
        ).first()
    else:
        session = None
    if session is None:
        raise PermissionDenied("This checkout session is not available.")
    if session.expires_at <= timezone.now() and session.status not in (
        CheckoutStatus.EXPIRED,
        CheckoutStatus.RELEASED,
        CheckoutStatus.COMPLETED,
    ):
        release_checkout(session=session, expired=True)
        raise CheckoutExpired()
    return session


def update_checkout(session: CheckoutSession, data: dict[str, Any]) -> CheckoutSession:
    if session.status != CheckoutStatus.DRAFT:
        raise ConflictError("This checkout can no longer be edited.", "checkout_locked")
    settings_row = StoreSettings.objects.first()
    if settings_row is None:
        raise ConflictError("Store settings are missing.", "store_missing")
    with transaction.atomic():
        target = CheckoutSession.objects.select_for_update().get(pk=session.pk)
        fields = (
            "contact_email",
            "recipient_name",
            "address_line_1",
            "address_line_2",
            "city",
            "region",
            "postal_code",
            "country",
            "delivery_notes",
        )
        for field in fields:
            if field in data:
                setattr(target, field, str(data[field]).strip())
        if "contact_phone" in data:
            try:
                target.contact_phone = (
                    normalize_phone(str(data["contact_phone"]), settings_row.country) or ""
                )
            except Exception as exc:
                raise ValidationError({"contact_phone": ["Enter a valid phone number."]}) from exc
        if target.country:
            target.country = target.country.upper()
        if "coupon_code" in data:
            coupon = _coupon(target, str(data["coupon_code"]))
            target.coupon = coupon
            target.coupon_code = coupon.code if coupon else ""
        if "shipping_method" in data:
            value = data["shipping_method"]
            target.shipping_method = (
                ShippingMethod.objects.filter(pk=value).first() if value else None
            )
        if "delivery_slot" in data:
            value = data["delivery_slot"]
            target.delivery_slot = DeliverySlot.objects.filter(pk=value).first() if value else None
        _calculate(target)
        validated(target, exclude=["tenant"])
        target.save()
        return target


def _validate_review_details(session: CheckoutSession) -> None:
    errors: dict[str, list[str]] = {}
    if not session.contact_email and not session.contact_phone:
        errors["contact"] = ["Enter an email address or phone number."]
    if session.requires_shipping:
        required = {
            "recipient_name": session.recipient_name,
            "address_line_1": session.address_line_1,
            "city": session.city,
            "country": session.country,
        }
        for field, value in required.items():
            if not value:
                errors[field] = ["This field is required for delivery."]
        methods = available_shipping_methods(session)
        if session.shipping_method is None or session.shipping_method not in methods:
            errors["shipping_method"] = ["Choose an available delivery method."]
        slots = available_delivery_slots(session)
        if slots and session.delivery_slot not in slots:
            errors["delivery_slot"] = ["Choose an available delivery time."]
    if errors:
        raise ValidationError(errors)


def _snapshot_signature(session: CheckoutSession) -> list[tuple[Any, Decimal, Decimal, Any]]:
    return list(
        CheckoutLine.objects.filter(session=session)
        .order_by("variant_id")
        .values_list("variant_id", "quantity", "unit_price", "price_id")
    )


def _quote_signature(quote: CartQuote) -> list[tuple[Any, Decimal, Decimal, Any]]:
    result = []
    for line in quote.lines:
        current = pricing.quote(line.variant)
        if current is not None:
            result.append((line.variant.pk, line.item.quantity, current.amount, current.price_id))
    return sorted(result, key=lambda row: str(row[0]))


def _location_capacity(variant: ProductVariant, location: StockLocation) -> Decimal:
    level = StockLevel.objects.filter(variant=variant, location=location).first()
    if level is None:
        return Decimal(0)
    if variant.product.tracking_mode == "batch":
        batch_stock = sum(
            (row.on_hand for row in inventory.batches_by_expiry(variant, location)), Decimal(0)
        )
        return max(Decimal(0), batch_stock - level.reserved)
    return Decimal(level.available)


def _reserve_line(session: CheckoutSession, line: CheckoutLine) -> None:
    variant = ProductVariant.objects.select_related("product").get(pk=line.variant_id)
    if not inventory.is_tracked(variant) or _preorder_allowed(variant):
        return
    remaining = line.quantity
    locations = list(StockLocation.objects.filter(is_active=True).order_by("-is_default", "pk"))
    for location in locations:
        amount = min(remaining, _location_capacity(variant, location))
        if amount <= 0:
            continue
        suffix = str(location.pk)
        request = inventory.StockRequest(
            reason="Temporary checkout reservation",
            reference_type="checkout_session",
            reference_id=str(session.pk),
            idempotency_key=f"checkout-reserve:{session.pk}:{suffix}"[:100],
        )
        operation = inventory.reserve(
            variant=variant, location=location, quantity=amount, request=request
        )
        CheckoutReservation.objects.create(
            session=session,
            variant=variant,
            location=location,
            quantity=amount,
            operation_id=operation.operation_id,
            expires_at=session.expires_at,
        )
        remaining -= amount
        if remaining == 0:
            break
    if remaining > 0:
        raise inventory.InsufficientStock("There isn't enough current stock for this checkout.")


def review_checkout(
    *, session: CheckoutSession, idempotency_key: str, actor: User | None
) -> CheckoutSession:
    key = idempotency_key.strip()
    if not 8 <= len(key) <= 100:
        raise ValidationError({"idempotency_key": ["Use an idempotency key of 8-100 characters."]})
    key_hash = credential_hash(key)
    change_error: type[ConflictError] | None = None
    with transaction.atomic():
        target = CheckoutSession.objects.select_for_update().get(pk=session.pk)
        if target.status == CheckoutStatus.REVIEWED:
            if target.idempotency_key_hash == key_hash:
                return target
            raise ConflictError("This checkout was already submitted.", "duplicate_submission")
        if target.status != CheckoutStatus.DRAFT or target.expires_at <= timezone.now():
            raise CheckoutExpired()
        _validate_review_details(target)
        # Pricing writes and inventory operations use the same variant lock. Taking every cart
        # variant in a stable order makes the quote-and-reserve transition one atomic snapshot.
        variant_ids = CheckoutLine.objects.filter(session=target).values_list(
            "variant_id", flat=True
        )
        list(
            ProductVariant.objects.select_for_update()
            .filter(pk__in=variant_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        current = quote_cart(target.cart)
        if not current.valid:
            raise ConflictError("An item is no longer available.", "cart_invalid")
        stored = _snapshot_signature(target)
        latest = _quote_signature(current)
        if stored != latest:
            price_changed = [(row[0], row[2], row[3]) for row in stored] != [
                (row[0], row[2], row[3]) for row in latest
            ]
            target.item_subtotal = current.subtotal
            target.currency = current.currency
            _sync_lines(target, current)
            _calculate(target)
            target.save()
            change_error = PriceChanged if price_changed else CartChanged
        else:
            target.expires_at = timezone.now() + RESERVATION_LIFETIME
            for line in CheckoutLine.objects.filter(session=target).order_by("variant_id"):
                _reserve_line(target, line)
            target.status = CheckoutStatus.REVIEWED
            target.reviewed_at = timezone.now()
            target.idempotency_key_hash = key_hash
            _calculate(target)
            target.save()
            record_audit_event(
                action="checkout.reviewed",
                resource=target,
                actor=actor,
                actor_label="guest" if actor is None else "",
                changes={"currency": target.currency, "grand_total": target.grand_total},
            )
            tenant_id = str(get_active_tenant_id())

            def enqueue_release() -> None:
                if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
                    return
                from .tasks import expire_checkout

                expire_checkout.apply_async(
                    args=(str(target.pk),),
                    kwargs={"tenant_id": tenant_id},
                    eta=target.expires_at,
                )

            transaction.on_commit(enqueue_release)
    if change_error is not None:
        raise change_error()
    return target


def release_checkout(*, session: CheckoutSession, expired: bool = False) -> CheckoutSession:
    with transaction.atomic():
        target = CheckoutSession.objects.select_for_update().get(pk=session.pk)
        if target.status in (CheckoutStatus.RELEASED, CheckoutStatus.EXPIRED):
            return target
        if target.status == CheckoutStatus.COMPLETED:
            return target
        reservations = list(
            CheckoutReservation.objects.select_for_update()
            .filter(session=target, released_at__isnull=True)
            .select_related("variant", "location")
            .order_by("variant_id", "location_id")
        )
        for hold in reservations:
            operation = inventory.release(
                variant=hold.variant,
                location=hold.location,
                quantity=hold.quantity,
                request=inventory.StockRequest(
                    reason="Checkout reservation expired" if expired else "Checkout cancelled",
                    reference_type="checkout_session",
                    reference_id=str(target.pk),
                    idempotency_key=f"checkout-release:{hold.pk}"[:100],
                ),
            )
            hold.released_at = timezone.now()
            hold.release_operation_id = operation.operation_id
            hold.save(update_fields=["released_at", "release_operation_id", "updated_at"])
        target.status = CheckoutStatus.EXPIRED if expired else CheckoutStatus.RELEASED
        target.save(update_fields=["status", "updated_at"])
        return target


def save_address(*, customer: User, address: Address | None, data: dict[str, Any]) -> Address:
    if customer.account_type != AccountType.CUSTOMER:
        raise PermissionDenied("Customer account required.")
    store = StoreSettings.objects.first()
    if store is None:
        raise ConflictError("Store settings are missing.", "store_missing")
    with transaction.atomic():
        target = (
            Address.objects.select_for_update().filter(pk=address.pk, customer=customer).first()
            if address is not None
            else Address(customer=customer)
        )
        if target is None:
            raise PermissionDenied("This address is not available.")
        if "address_name" in data:
            target.label = str(data["address_name"]).strip()
        for field in (
            "recipient_name",
            "address_line_1",
            "address_line_2",
            "city",
            "region",
            "postal_code",
        ):
            if field in data:
                setattr(target, field, str(data[field]).strip())
        if "country" in data:
            target.country = str(data["country"]).strip().upper()
        if "phone" in data:
            try:
                target.phone = normalize_phone(str(data["phone"]), store.country) or ""
            except Exception as exc:
                raise ValidationError({"phone": ["Enter a valid phone number."]}) from exc
        if "is_default" in data:
            target.is_default = bool(data["is_default"])
        validated(target, exclude=["tenant"])
        if target.is_default:
            Address.objects.filter(customer=customer, is_default=True).exclude(pk=target.pk).update(
                is_default=False
            )
        target.save()
        return target


def delete_address(*, customer: User, address_id: Any) -> None:
    address = Address.objects.filter(pk=address_id, customer=customer).first()
    if address is None:
        raise PermissionDenied("This address is not available.")
    address.delete()
