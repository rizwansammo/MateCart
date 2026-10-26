"""Tenant-owned cart, delivery configuration and checkout-session state (Phase 07)."""

from __future__ import annotations

from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from commerce.catalog.models import ProductVariant
from commerce.core.contact import E164_PATTERN
from commerce.core.models import TimestampedModel
from commerce.core.standards import validate_country_code, validate_currency_code
from commerce.identity.models import User
from commerce.inventory.models import StockLocation
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
COUPON_CODE_PATTERN = r"^[A-Z0-9][A-Z0-9_-]{1,31}$"
MONEY_MAX_DIGITS = 19
MONEY_DECIMAL_PLACES = 4
QUANTITY_MAX_DIGITS = 14
QUANTITY_DECIMAL_PLACES = 3


def _money(**kwargs: Any) -> models.DecimalField[Any, Any]:
    return models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, **kwargs
    )


class CartStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CHECKOUT = "checkout", "In checkout"
    CONVERTED = "converted", "Converted to order"
    ABANDONED = "abandoned", "Abandoned"


class Cart(TenantOwnedModel, TimestampedModel):
    """One server-authoritative cart, owned by one customer or one opaque guest credential."""

    customer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="carts"
    )
    guest_token_hash = models.CharField(
        max_length=64, blank=True, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    status = models.CharField(max_length=16, choices=CartStatus.choices, default=CartStatus.ACTIVE)
    expires_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="customer", name="checkout_cart_customer_tenant"),
            models.CheckConstraint(
                condition=(Q(customer__isnull=False, guest_token_hash=""))
                | (Q(customer__isnull=True) & ~Q(guest_token_hash="")),
                name="checkout_cart_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(status__in=CartStatus.values), name="checkout_cart_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(guest_token_hash="") | Q(guest_token_hash__regex=SHA256_HEX_PATTERN),
                name="checkout_cart_guest_hash_format",
            ),
            models.UniqueConstraint(
                fields=["tenant", "customer"],
                condition=Q(status=CartStatus.ACTIVE, customer__isnull=False),
                name="checkout_cart_one_active_customer",
            ),
            models.UniqueConstraint(
                fields=["tenant", "guest_token_hash"],
                condition=~Q(guest_token_hash=""),
                name="checkout_cart_guest_hash_unique",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "expires_at"], name="checkout_cart_expiry")]


class CartItem(TenantOwnedModel, TimestampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="cart", name="checkout_item_cart_tenant"),
            TenantForeignKeyConstraint(field="variant", name="checkout_item_variant_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "cart", "variant"], name="checkout_item_variant_unique"
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="checkout_item_quantity_gt_0"),
        ]


class Address(TenantOwnedModel, TimestampedModel):
    """A customer's optional reusable delivery address; checkout also keeps its own snapshot."""

    customer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=50, blank=True)
    recipient_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=16, validators=[RegexValidator(E164_PATTERN)])
    address_line_1 = models.CharField(max_length=200)
    address_line_2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=2, validators=[validate_country_code])
    is_default = models.BooleanField(default=False)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="customer", name="checkout_address_customer_tenant"),
            models.CheckConstraint(
                condition=Q(country__regex=r"^[A-Z]{2}$"), name="checkout_address_country_format"
            ),
            models.CheckConstraint(
                condition=Q(phone__regex=E164_PATTERN), name="checkout_address_phone_e164"
            ),
            models.UniqueConstraint(
                fields=["tenant", "customer"],
                condition=Q(is_default=True),
                name="checkout_address_one_default",
            ),
        ]


class ShippingMethod(TenantOwnedModel, TimestampedModel):
    code = models.CharField(max_length=50, validators=[RegexValidator(r"^[a-z][a-z0-9_-]+$")])
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    amount = _money(default=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, validators=[validate_currency_code])
    supported_countries = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    minimum_subtotal = _money(default=0, validators=[MinValueValidator(0)])
    free_above = _money(null=True, blank=True, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "code"], name="checkout_shipping_code_unique"
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0), name="checkout_shipping_amount_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(minimum_subtotal__gte=0), name="checkout_shipping_minimum_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(free_above__isnull=True) | Q(free_above__gte=0),
                name="checkout_shipping_free_above_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"),
                name="checkout_shipping_currency_format",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "is_active", "position"], name="checkout_shipping_active"
            )
        ]


class TaxRate(TenantOwnedModel, TimestampedModel):
    name = models.CharField(max_length=100)
    country = models.CharField(max_length=2, validators=[validate_country_code])
    region = models.CharField(max_length=100, blank=True)
    rate = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    applies_to_shipping = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "country", "region"], name="checkout_tax_region_unique"
            ),
            models.CheckConstraint(
                condition=Q(country__regex=r"^[A-Z]{2}$"), name="checkout_tax_country_format"
            ),
            models.CheckConstraint(
                condition=Q(rate__gte=0, rate__lte=1), name="checkout_tax_rate_range"
            ),
        ]


class CouponDiscountType(models.TextChoices):
    FIXED = "fixed", "Fixed amount"
    PERCENT = "percent", "Percentage"


class Coupon(TenantOwnedModel, TimestampedModel):
    code = models.CharField(max_length=32, validators=[RegexValidator(COUPON_CODE_PATTERN)])
    discount_type = models.CharField(max_length=16, choices=CouponDiscountType.choices)
    value = _money(validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, blank=True)
    minimum_subtotal = _money(default=0, validators=[MinValueValidator(0)])
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    max_redemptions = models.PositiveIntegerField(null=True, blank=True)
    times_redeemed = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant", "code"], name="checkout_coupon_code_unique"),
            models.CheckConstraint(
                condition=Q(code__regex=COUPON_CODE_PATTERN), name="checkout_coupon_code_format"
            ),
            models.CheckConstraint(
                condition=Q(discount_type__in=CouponDiscountType.values),
                name="checkout_coupon_type_valid",
            ),
            models.CheckConstraint(condition=Q(value__gt=0), name="checkout_coupon_value_gt_0"),
            models.CheckConstraint(
                condition=Q(minimum_subtotal__gte=0), name="checkout_coupon_minimum_gte_0"
            ),
            models.CheckConstraint(
                condition=(
                    Q(discount_type=CouponDiscountType.FIXED) & Q(currency__regex=r"^[A-Z]{3}$")
                )
                | (Q(discount_type=CouponDiscountType.PERCENT, currency="", value__lte=100)),
                name="checkout_coupon_value_matches_type",
            ),
            models.CheckConstraint(
                condition=Q(starts_at__isnull=True)
                | Q(ends_at__isnull=True)
                | Q(starts_at__lt=models.F("ends_at")),
                name="checkout_coupon_window_valid",
            ),
            models.CheckConstraint(
                condition=Q(max_redemptions__isnull=True)
                | Q(times_redeemed__lte=models.F("max_redemptions")),
                name="checkout_coupon_usage_valid",
            ),
        ]


class DeliverySlot(TenantOwnedModel, TimestampedModel):
    label = models.CharField(max_length=100)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    capacity = models.PositiveIntegerField(null=True, blank=True)
    bookings = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.CheckConstraint(
                condition=Q(starts_at__lt=models.F("ends_at")), name="checkout_slot_window_valid"
            ),
            models.CheckConstraint(
                condition=Q(capacity__isnull=True) | Q(bookings__lte=models.F("capacity")),
                name="checkout_slot_capacity_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active", "starts_at"], name="checkout_slot_active")
        ]


class CheckoutStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEWED = "reviewed", "Reviewed and reserved"
    RELEASED = "released", "Reservation released"
    EXPIRED = "expired", "Expired"
    COMPLETED = "completed", "Completed by order creation"


class CheckoutSession(TenantOwnedModel, TimestampedModel):
    """Server-owned checkout state. Phase 08 consumes a REVIEWED session to create an order."""

    cart = models.ForeignKey(Cart, on_delete=models.PROTECT, related_name="checkouts")
    customer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="checkout_sessions"
    )
    guest_token_hash = models.CharField(
        max_length=64, blank=True, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    status = models.CharField(
        max_length=16, choices=CheckoutStatus.choices, default=CheckoutStatus.DRAFT
    )
    expires_at = models.DateTimeField()
    reviewed_at = models.DateTimeField(null=True, blank=True)
    idempotency_key_hash = models.CharField(
        max_length=64, blank=True, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=16, blank=True)
    recipient_name = models.CharField(max_length=150, blank=True)
    address_line_1 = models.CharField(max_length=200, blank=True)
    address_line_2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=2, blank=True)
    delivery_notes = models.CharField(max_length=500, blank=True)
    shipping_method = models.ForeignKey(
        ShippingMethod, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    delivery_slot = models.ForeignKey(
        DeliverySlot, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    coupon = models.ForeignKey(
        Coupon, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    coupon_code = models.CharField(max_length=32, blank=True)
    requires_shipping = models.BooleanField(default=False)
    currency = models.CharField(max_length=3, validators=[validate_currency_code])
    item_subtotal = _money(default=0)
    discount_total = _money(default=0)
    shipping_total = _money(default=0)
    tax_total = _money(default=0)
    grand_total = _money(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="cart", name="checkout_session_cart_tenant"),
            TenantForeignKeyConstraint(field="customer", name="checkout_session_customer_tenant"),
            TenantForeignKeyConstraint(
                field="shipping_method", name="checkout_session_shipping_tenant"
            ),
            TenantForeignKeyConstraint(field="delivery_slot", name="checkout_session_slot_tenant"),
            TenantForeignKeyConstraint(field="coupon", name="checkout_session_coupon_tenant"),
            models.CheckConstraint(
                condition=(Q(customer__isnull=False, guest_token_hash=""))
                | (Q(customer__isnull=True) & ~Q(guest_token_hash="")),
                name="checkout_session_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(status__in=CheckoutStatus.values), name="checkout_session_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(country="") | Q(country__regex=r"^[A-Z]{2}$"),
                name="checkout_session_country_format",
            ),
            models.CheckConstraint(
                condition=Q(contact_phone="") | Q(contact_phone__regex=E164_PATTERN),
                name="checkout_session_phone_e164",
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"),
                name="checkout_session_currency_format",
            ),
            models.CheckConstraint(
                condition=Q(item_subtotal__gte=0)
                & Q(discount_total__gte=0)
                & Q(shipping_total__gte=0)
                & Q(tax_total__gte=0)
                & Q(grand_total__gte=0),
                name="checkout_session_totals_nonnegative",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "expires_at"], name="checkout_session_expiry")]


class CheckoutLine(TenantOwnedModel):
    session = models.ForeignKey(CheckoutSession, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    product_name = models.CharField(max_length=200)
    variant_title = models.CharField(max_length=200, blank=True)
    sku = models.CharField(max_length=64)
    kind = models.CharField(max_length=16)
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )
    unit_price = _money()
    line_subtotal = _money()
    price_id = models.UUIDField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="session", name="checkout_line_session_tenant"),
            TenantForeignKeyConstraint(field="variant", name="checkout_line_variant_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "session", "variant"], name="checkout_line_variant_unique"
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="checkout_line_quantity_gt_0"),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0), name="checkout_line_price_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(line_subtotal__gte=0), name="checkout_line_subtotal_gte_0"
            ),
        ]


class CheckoutReservation(TenantOwnedModel, TimestampedModel):
    session = models.ForeignKey(
        CheckoutSession, on_delete=models.PROTECT, related_name="reservations"
    )
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )
    operation_id = models.UUIDField()
    expires_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    release_operation_id = models.UUIDField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="session", name="checkout_reservation_session_tenant"),
            TenantForeignKeyConstraint(field="variant", name="checkout_reservation_variant_tenant"),
            TenantForeignKeyConstraint(
                field="location", name="checkout_reservation_location_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "session", "variant", "location"],
                name="checkout_reservation_unique",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="checkout_reservation_quantity_gt_0"
            ),
            models.CheckConstraint(
                condition=(Q(released_at__isnull=True) & Q(release_operation_id__isnull=True))
                | (Q(released_at__isnull=False) & Q(release_operation_id__isnull=False)),
                name="checkout_reservation_release_consistent",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "released_at", "expires_at"], name="checkout_reservation_expiry"
            )
        ]
