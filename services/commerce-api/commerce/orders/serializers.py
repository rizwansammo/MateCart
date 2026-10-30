"""HTTP shapes for Phase 08 orders, payments, refunds and invoices. Amounts are Decimal strings."""

from typing import Any

from django.utils import timezone
from rest_framework import serializers

from commerce.fulfillment.models import Shipment
from commerce.fulfillment.serializers import PublicShipmentSerializer

from .models import (
    Invoice,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusEvent,
    PaymentMethod,
    Refund,
)
from .providers import payment_provider

# Statuses a merchant may move an order to; payment confirmation is never a manual step.
MERCHANT_TARGETS = [
    (OrderStatus.CONFIRMED.value, OrderStatus.CONFIRMED.label),
    (OrderStatus.PROCESSING.value, OrderStatus.PROCESSING.label),
    (OrderStatus.CANCELLED.value, OrderStatus.CANCELLED.label),
]


class PlaceOrderSerializer(serializers.Serializer[Any]):
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    idempotency_key = serializers.CharField(min_length=8, max_length=100)


class TransitionSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=MERCHANT_TARGETS)
    reason = serializers.RegexField(r"^[a-z_]{0,40}$", required=False, allow_blank=True)


class RefundInputSerializer(serializers.Serializer[Any]):
    amount = serializers.DecimalField(max_digits=19, decimal_places=4)
    reason = serializers.CharField(max_length=300)
    idempotency_key = serializers.CharField(min_length=8, max_length=100)
    return_case = serializers.UUIDField(required=False, allow_null=True)


class PaymentStartSerializer(serializers.Serializer[Any]):
    redirect_url = serializers.URLField()


class CallbackResultSerializer(serializers.Serializer[Any]):
    outcome = serializers.CharField()


class OrderItemSerializer(serializers.ModelSerializer[OrderItem]):
    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product_name",
            "variant_title",
            "sku",
            "kind",
            "quantity",
            "unit_price",
            "line_total",
        )


class OrderSerializer(serializers.ModelSerializer[Order]):
    items = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    can_pay = serializers.SerializerMethodField()
    shipment = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields: tuple[str, ...] = (
            "id",
            "number",
            "status",
            "payment_method",
            "payment_status",
            "payment_due_at",
            "currency",
            "item_subtotal",
            "discount_total",
            "shipping_total",
            "tax_total",
            "grand_total",
            "amount_paid",
            "amount_refunded",
            "contact_email",
            "contact_phone",
            "requires_shipping",
            "recipient_name",
            "address_line_1",
            "address_line_2",
            "city",
            "region",
            "postal_code",
            "country",
            "delivery_notes",
            "shipping_method_name",
            "delivery_slot_label",
            "delivery_slot_starts_at",
            "delivery_slot_ends_at",
            "coupon_code",
            "placed_at",
            "cancelled_at",
            "cancel_reason",
            "items",
            "invoice_number",
            "can_cancel",
            "can_pay",
            "shipment",
        )
        read_only_fields: tuple[str, ...] = fields

    def get_items(self, order: Order) -> list[dict[str, Any]]:
        rows = OrderItem.objects.filter(order=order).order_by("position")
        return list(OrderItemSerializer(rows, many=True).data)

    def get_invoice_number(self, order: Order) -> str | None:
        return Invoice.objects.filter(order=order).values_list("number", flat=True).first()

    def get_can_cancel(self, order: Order) -> bool:
        return (
            order.status in (OrderStatus.PLACED, OrderStatus.PENDING_PAYMENT)
            and order.amount_paid == 0
        )

    def get_can_pay(self, order: Order) -> bool:
        return (
            order.status == OrderStatus.PENDING_PAYMENT
            and order.payment_due_at is not None
            and order.payment_due_at > timezone.now()
            and payment_provider() is not None
        )

    def get_shipment(self, order: Order) -> dict[str, Any] | None:
        shipment = Shipment.objects.filter(order=order).first()
        return None if shipment is None else dict(PublicShipmentSerializer(shipment).data)


class OrderEventSerializer(serializers.ModelSerializer[OrderStatusEvent]):
    class Meta:
        model = OrderStatusEvent
        fields = ("occurred_at", "event", "from_status", "to_status", "payment_status", "reason")


class RefundSerializer(serializers.ModelSerializer[Refund]):
    class Meta:
        model = Refund
        fields = (
            "id",
            "method",
            "amount",
            "currency",
            "status",
            "reason",
            "failure_code",
            "created_at",
            "completed_at",
        )


class MerchantOrderSerializer(OrderSerializer):
    events = serializers.SerializerMethodField()
    refunds = serializers.SerializerMethodField()

    class Meta(OrderSerializer.Meta):
        fields = (*OrderSerializer.Meta.fields, "events", "refunds")
        read_only_fields = fields

    def get_events(self, order: Order) -> list[dict[str, Any]]:
        rows = OrderStatusEvent.objects.filter(order=order).order_by("occurred_at", "pk")
        return list(OrderEventSerializer(rows, many=True).data)

    def get_refunds(self, order: Order) -> list[dict[str, Any]]:
        rows = Refund.objects.filter(order=order).order_by("created_at")
        return list(RefundSerializer(rows, many=True).data)


class OrderSummarySerializer(serializers.ModelSerializer[Order]):
    class Meta:
        model = Order
        fields = (
            "id",
            "number",
            "status",
            "payment_method",
            "payment_status",
            "currency",
            "grand_total",
            "placed_at",
        )
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer[Invoice]):
    order_number = serializers.CharField(source="order.number")
    order_placed_at = serializers.DateTimeField(source="order.placed_at")
    payment_method = serializers.CharField(source="order.payment_method")
    payment_status = serializers.CharField(source="order.payment_status")
    amount_paid = serializers.DecimalField(
        source="order.amount_paid", max_digits=19, decimal_places=4
    )
    amount_refunded = serializers.DecimalField(
        source="order.amount_refunded", max_digits=19, decimal_places=4
    )
    order_cancelled = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    seller = serializers.DictField(child=serializers.CharField(allow_blank=True))
    buyer = serializers.DictField(child=serializers.CharField(allow_blank=True))

    class Meta:
        model = Invoice
        fields = (
            "number",
            "issued_at",
            "order_number",
            "order_placed_at",
            "currency",
            "item_subtotal",
            "discount_total",
            "shipping_total",
            "tax_total",
            "grand_total",
            "payment_method",
            "payment_status",
            "amount_paid",
            "amount_refunded",
            "order_cancelled",
            "seller",
            "buyer",
            "items",
        )
        read_only_fields = fields

    def get_order_cancelled(self, invoice: Invoice) -> bool:
        return invoice.order.status == OrderStatus.CANCELLED

    def get_items(self, invoice: Invoice) -> list[dict[str, Any]]:
        rows = OrderItem.objects.filter(order=invoice.order).order_by("position")
        return list(OrderItemSerializer(rows, many=True).data)
