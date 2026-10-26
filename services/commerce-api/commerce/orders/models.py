"""Tenant-owned orders, payments, refunds and invoices (Phase 08, D-071 to D-073).

An order is created once from a reviewed checkout and keeps its own snapshots of items, prices,
contact and delivery details. Order status, the order's payment status, each payment attempt and
each refund are separate state machines. Money is Decimal with an ISO 4217 currency.
"""

from __future__ import annotations

from typing import Any, NoReturn

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from commerce.catalog.models import ProductVariant
from commerce.checkout.models import (
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    SHA256_HEX_PATTERN,
    CheckoutReservation,
    CheckoutSession,
    Coupon,
    DeliverySlot,
    ShippingMethod,
)
from commerce.core.models import TimestampedModel
from commerce.core.standards import validate_currency_code
from commerce.identity.models import User
from commerce.inventory.models import StockLocation
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

ORDER_NUMBER_PATTERN = r"^ORD-[0-9]{6,}$"
INVOICE_NUMBER_PATTERN = r"^INV-[0-9]{6,}$"


def _money(**kwargs: Any) -> models.DecimalField[Any, Any]:
    return models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, **kwargs
    )


def _quantity() -> models.DecimalField[Any, Any]:
    return models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )


def _currency_check(name: str, field: str = "currency") -> models.CheckConstraint:
    return models.CheckConstraint(condition=Q(**{f"{field}__regex": r"^[A-Z]{3}$"}), name=name)


class ImmutableRecordError(RuntimeError):
    pass


class DocumentSequence(TenantOwnedModel):
    """Per-store counters for human-readable order and invoice numbers (locked FOR UPDATE)."""

    kind = models.CharField(max_length=16)
    last_value = models.PositiveBigIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant", "kind"], name="orders_sequence_kind_unique"),
        ]


class OrderStatus(models.TextChoices):
    PENDING_PAYMENT = "pending_payment", "Awaiting online payment"
    PLACED = "placed", "Placed"
    CONFIRMED = "confirmed", "Confirmed"
    PROCESSING = "processing", "Processing"
    CANCELLED = "cancelled", "Cancelled"


class PaymentMethod(models.TextChoices):
    CASH_ON_DELIVERY = "cod", "Cash on delivery"
    ONLINE = "online", "Online payment"


class OrderPaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"
    FAILED = "failed", "Payment not completed"


class Order(TenantOwnedModel, TimestampedModel):
    number = models.CharField(max_length=20, validators=[RegexValidator(ORDER_NUMBER_PATTERN)])
    checkout = models.ForeignKey(CheckoutSession, on_delete=models.PROTECT, related_name="+")
    placement_key_hash = models.CharField(
        max_length=64, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    customer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="orders"
    )
    guest_access_hash = models.CharField(
        max_length=64, blank=True, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    status = models.CharField(max_length=20, choices=OrderStatus.choices)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    payment_status = models.CharField(
        max_length=20, choices=OrderPaymentStatus.choices, default=OrderPaymentStatus.UNPAID
    )
    payment_due_at = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=3, validators=[validate_currency_code])
    item_subtotal = _money()
    discount_total = _money()
    shipping_total = _money()
    tax_total = _money()
    grand_total = _money()
    amount_paid = _money(default=0)
    amount_refunded = _money(default=0)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=16, blank=True)
    requires_shipping = models.BooleanField()
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
    shipping_method_name = models.CharField(max_length=100, blank=True)
    delivery_slot = models.ForeignKey(
        DeliverySlot, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    delivery_slot_label = models.CharField(max_length=100, blank=True)
    delivery_slot_starts_at = models.DateTimeField(null=True, blank=True)
    delivery_slot_ends_at = models.DateTimeField(null=True, blank=True)
    delivery_slot_booked = models.BooleanField(default=False)
    coupon_code = models.CharField(max_length=32, blank=True)
    placed_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=40, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="checkout", name="orders_order_checkout_tenant"),
            TenantForeignKeyConstraint(field="customer", name="orders_order_customer_tenant"),
            TenantForeignKeyConstraint(
                field="shipping_method", name="orders_order_shipping_tenant"
            ),
            TenantForeignKeyConstraint(field="delivery_slot", name="orders_order_slot_tenant"),
            models.UniqueConstraint(fields=["tenant", "number"], name="orders_order_number_unique"),
            # One checkout produces at most one order, whatever path tries to convert it.
            models.UniqueConstraint(
                fields=["tenant", "checkout"], name="orders_order_checkout_unique"
            ),
            models.CheckConstraint(
                condition=Q(number__regex=ORDER_NUMBER_PATTERN), name="orders_order_number_format"
            ),
            models.CheckConstraint(
                condition=(Q(customer__isnull=False, guest_access_hash=""))
                | (Q(customer__isnull=True) & ~Q(guest_access_hash="")),
                name="orders_order_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(status__in=OrderStatus.values), name="orders_order_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(payment_method__in=PaymentMethod.values),
                name="orders_order_payment_method_valid",
            ),
            models.CheckConstraint(
                condition=Q(payment_status__in=OrderPaymentStatus.values),
                name="orders_order_payment_status_valid",
            ),
            _currency_check("orders_order_currency_format"),
            models.CheckConstraint(
                condition=Q(item_subtotal__gte=0)
                & Q(discount_total__gte=0)
                & Q(shipping_total__gte=0)
                & Q(tax_total__gte=0)
                & Q(grand_total__gte=0)
                & Q(amount_paid__gte=0)
                & Q(amount_refunded__gte=0),
                name="orders_order_amounts_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(amount_refunded__lte=F("amount_paid")),
                name="orders_order_refund_within_paid",
            ),
            models.CheckConstraint(
                condition=~Q(status=OrderStatus.CANCELLED) | Q(cancelled_at__isnull=False),
                name="orders_order_cancelled_has_time",
            ),
            models.CheckConstraint(
                condition=Q(delivery_slot_booked=False) | Q(delivery_slot__isnull=False),
                name="orders_order_booking_has_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "placed_at"], name="orders_order_status"),
            models.Index(fields=["tenant", "customer", "placed_at"], name="orders_order_customer"),
        ]


class OrderItem(TenantOwnedModel):
    """An immutable line snapshot: names, SKU, price and quantity as the customer bought them."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    product_name = models.CharField(max_length=200)
    variant_title = models.CharField(max_length=200, blank=True)
    sku = models.CharField(max_length=64)
    kind = models.CharField(max_length=16)
    quantity = _quantity()
    unit_price = _money()
    line_total = _money()
    price_id = models.UUIDField()
    position = models.PositiveIntegerField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="orders_item_order_tenant"),
            TenantForeignKeyConstraint(field="variant", name="orders_item_variant_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "order", "variant"], name="orders_item_variant_unique"
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="orders_item_quantity_gt_0"),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0) & Q(line_total__gte=0),
                name="orders_item_amounts_nonnegative",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Order items are historical snapshots.")
        super().save(*args, **kwargs)


class OrderStatusEvent(TenantOwnedModel):
    """Append-only order history (ORM guard plus a PostgreSQL trigger, migration 0002)."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="events")
    occurred_at = models.DateTimeField(default=timezone.now)
    event = models.CharField(max_length=40)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    payment_status = models.CharField(max_length=20)
    reason = models.CharField(max_length=40, blank=True)
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    actor_label = models.CharField(max_length=100, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="orders_event_order_tenant"),
            TenantForeignKeyConstraint(field="actor", name="orders_event_actor_tenant"),
        ]
        indexes = [models.Index(fields=["tenant", "order", "occurred_at"], name="orders_event")]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Order history is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ImmutableRecordError("Order history is append-only.")


class AllocationStatus(models.TextChoices):
    ALLOCATED = "allocated", "Reserved for the order"
    RELEASED = "released", "Released"
    FULFILLED = "fulfilled", "Fulfilled"


class OrderAllocation(TenantOwnedModel, TimestampedModel):
    """Stock the order owns. Placement takes over a checkout reservation without a new ledger
    movement; cancellation releases it once and fulfilment (Phase 09) deducts it once."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="allocations")
    item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="allocations")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(StockLocation, on_delete=models.PROTECT, related_name="+")
    source_reservation = models.ForeignKey(
        CheckoutReservation, on_delete=models.PROTECT, related_name="+"
    )
    quantity = _quantity()
    status = models.CharField(
        max_length=16, choices=AllocationStatus.choices, default=AllocationStatus.ALLOCATED
    )
    reserve_operation_id = models.UUIDField()
    release_operation_id = models.UUIDField(null=True, blank=True)
    fulfil_operation_id = models.UUIDField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="orders_allocation_order_tenant"),
            TenantForeignKeyConstraint(field="item", name="orders_allocation_item_tenant"),
            TenantForeignKeyConstraint(field="variant", name="orders_allocation_variant_tenant"),
            TenantForeignKeyConstraint(field="location", name="orders_allocation_location_tenant"),
            TenantForeignKeyConstraint(
                field="source_reservation", name="orders_allocation_reservation_tenant"
            ),
            # A checkout hold becomes an order commitment at most once.
            models.UniqueConstraint(
                fields=["tenant", "source_reservation"],
                name="orders_allocation_reservation_unique",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="orders_allocation_quantity_gt_0"
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=AllocationStatus.ALLOCATED)
                    & Q(release_operation_id__isnull=True, fulfil_operation_id__isnull=True)
                )
                | (
                    Q(status=AllocationStatus.RELEASED)
                    & Q(release_operation_id__isnull=False, fulfil_operation_id__isnull=True)
                )
                | (
                    Q(status=AllocationStatus.FULFILLED)
                    & Q(release_operation_id__isnull=True, fulfil_operation_id__isnull=False)
                ),
                name="orders_allocation_status_consistent",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "variant", "location", "status"], name="orders_allocation_open"
            )
        ]


class RedemptionStatus(models.TextChoices):
    REDEEMED = "redeemed", "Redeemed"
    VOIDED = "voided", "Voided by cancellation"


class CouponRedemption(TenantOwnedModel, TimestampedModel):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="redemptions")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=32)
    discount = _money()
    currency = models.CharField(max_length=3)
    status = models.CharField(
        max_length=16, choices=RedemptionStatus.choices, default=RedemptionStatus.REDEEMED
    )
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="coupon", name="orders_redemption_coupon_tenant"),
            TenantForeignKeyConstraint(field="order", name="orders_redemption_order_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "order"], name="orders_redemption_order_unique"
            ),
            models.CheckConstraint(
                condition=(Q(status=RedemptionStatus.REDEEMED) & Q(voided_at__isnull=True))
                | (Q(status=RedemptionStatus.VOIDED) & Q(voided_at__isnull=False)),
                name="orders_redemption_status_consistent",
            ),
            models.CheckConstraint(
                condition=Q(discount__gte=0), name="orders_redemption_discount_gte_0"
            ),
        ]


class IntentStatus(models.TextChoices):
    AWAITING_COLLECTION = "awaiting_collection", "Cash to collect on delivery"
    CREATED = "created", "Created"
    REQUIRES_ACTION = "requires_action", "Waiting for the customer at the provider"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


OPEN_INTENT_STATUSES = (
    IntentStatus.AWAITING_COLLECTION,
    IntentStatus.CREATED,
    IntentStatus.REQUIRES_ACTION,
)


class PaymentIntent(TenantOwnedModel, TimestampedModel):
    """One attempt to collect an order's amount: cash on delivery or one provider checkout."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payment_intents")
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    provider = models.CharField(max_length=40, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True)
    redirect_url = models.URLField(max_length=1000, blank=True)
    amount = _money()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=24, choices=IntentStatus.choices)
    failure_code = models.CharField(max_length=40, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="orders_intent_order_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "order"],
                condition=Q(status__in=OPEN_INTENT_STATUSES),
                name="orders_intent_one_open",
            ),
            models.UniqueConstraint(
                fields=["tenant", "provider", "provider_ref"],
                condition=~Q(provider_ref=""),
                name="orders_intent_provider_ref_unique",
            ),
            models.CheckConstraint(
                condition=Q(status__in=IntentStatus.values), name="orders_intent_status_valid"
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="orders_intent_amount_gt_0"),
            _currency_check("orders_intent_currency_format"),
            models.CheckConstraint(
                condition=(Q(method=PaymentMethod.CASH_ON_DELIVERY) & Q(provider=""))
                | (Q(method=PaymentMethod.ONLINE) & ~Q(provider="")),
                name="orders_intent_provider_matches_method",
            ),
        ]


class TransactionKind(models.TextChoices):
    PAYMENT = "payment", "Payment"
    COD_COLLECTION = "cod_collection", "Cash collected on delivery"
    REFUND = "refund", "Refund"


class TransactionStatus(models.TextChoices):
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class PaymentTransaction(TenantOwnedModel):
    """A verified money movement (or a verified failure). Never written from browser claims."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="transactions")
    intent = models.ForeignKey(
        PaymentIntent, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions"
    )
    kind = models.CharField(max_length=16, choices=TransactionKind.choices)
    status = models.CharField(max_length=16, choices=TransactionStatus.choices)
    provider = models.CharField(max_length=40)
    provider_ref = models.CharField(max_length=120)
    amount = _money()
    currency = models.CharField(max_length=3)
    occurred_at = models.DateTimeField(default=timezone.now)
    evidence = models.JSONField(default=dict, blank=True)
    recorded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="orders_transaction_order_tenant"),
            TenantForeignKeyConstraint(field="intent", name="orders_transaction_intent_tenant"),
            TenantForeignKeyConstraint(
                field="recorded_by", name="orders_transaction_recorder_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "provider", "provider_ref"],
                name="orders_transaction_provider_ref_unique",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=TransactionKind.values), name="orders_transaction_kind_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=TransactionStatus.values),
                name="orders_transaction_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="orders_transaction_amount_gt_0"
            ),
            _currency_check("orders_transaction_currency_format"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Payment transactions are historical records.")
        super().save(*args, **kwargs)


class ProviderCallback(TenantOwnedModel):
    """One authenticated provider event, deduplicated by its event id. Raw bodies are not kept:
    only their digest and a sanitised, allowlisted summary."""

    provider = models.CharField(max_length=40)
    event_id = models.CharField(max_length=120)
    received_at = models.DateTimeField(default=timezone.now)
    body_sha256 = models.CharField(max_length=64, validators=[RegexValidator(SHA256_HEX_PATTERN)])
    outcome = models.CharField(max_length=24, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "provider", "event_id"], name="orders_callback_event_unique"
            ),
        ]


class RefundStatus(models.TextChoices):
    PENDING = "pending", "Pending at the provider"
    SUCCEEDED = "succeeded", "Refunded"
    FAILED = "failed", "Failed"


class RefundMethod(models.TextChoices):
    PROVIDER = "provider", "Through the payment provider"
    MANUAL = "manual", "Recorded manual refund"


class Refund(TenantOwnedModel, TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="refunds")
    payment = models.ForeignKey(
        PaymentTransaction, on_delete=models.PROTECT, related_name="refunds"
    )
    method = models.CharField(max_length=10, choices=RefundMethod.choices)
    amount = _money()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=16, choices=RefundStatus.choices)
    reason = models.CharField(max_length=300)
    idempotency_key_hash = models.CharField(
        max_length=64, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    provider_ref = models.CharField(max_length=120, blank=True)
    failure_code = models.CharField(max_length=40, blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="orders_refund_order_tenant"),
            TenantForeignKeyConstraint(field="payment", name="orders_refund_payment_tenant"),
            TenantForeignKeyConstraint(field="requested_by", name="orders_refund_actor_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key_hash"], name="orders_refund_key_unique"
            ),
            models.CheckConstraint(
                condition=Q(status__in=RefundStatus.values), name="orders_refund_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(method__in=RefundMethod.values), name="orders_refund_method_valid"
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="orders_refund_amount_gt_0"),
            _currency_check("orders_refund_currency_format"),
            models.CheckConstraint(
                condition=Q(status=RefundStatus.PENDING) | Q(completed_at__isnull=False),
                name="orders_refund_completed_has_time",
            ),
        ]


class Invoice(TenantOwnedModel):
    """Issued once per order at placement. Seller and buyer details and totals are frozen here;
    the lines are the order's immutable items. Payment status is shown from verified records."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=20, validators=[RegexValidator(INVOICE_NUMBER_PATTERN)])
    issued_at = models.DateTimeField(default=timezone.now)
    currency = models.CharField(max_length=3)
    item_subtotal = _money()
    discount_total = _money()
    shipping_total = _money()
    tax_total = _money()
    grand_total = _money()
    seller = models.JSONField()
    buyer = models.JSONField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="orders_invoice_order_tenant"),
            models.UniqueConstraint(fields=["tenant", "order"], name="orders_invoice_order_unique"),
            models.UniqueConstraint(
                fields=["tenant", "number"], name="orders_invoice_number_unique"
            ),
            models.CheckConstraint(
                condition=Q(number__regex=INVOICE_NUMBER_PATTERN),
                name="orders_invoice_number_format",
            ),
            _currency_check("orders_invoice_currency_format"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Issued invoices cannot be changed.")
        super().save(*args, **kwargs)
