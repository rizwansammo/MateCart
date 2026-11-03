"""HTTP shapes for Phase 08 orders, payments, refunds and invoices. Amounts are Decimal strings."""

from typing import Any

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from commerce.aftersales.models import AfterSalesCase
from commerce.checkout.serializers import DeliverySlotSerializer, ShippingMethodSerializer
from commerce.fulfillment.models import FraudAssessment, Shipment
from commerce.fulfillment.serializers import PublicShipmentSerializer

from .models import (
    ASSISTED_SOURCES,
    AssistedOrder,
    Invoice,
    Order,
    OrderItem,
    OrderPaymentStatus,
    OrderSource,
    OrderStatus,
    OrderStatusEvent,
    PaymentMethod,
    PaymentTransaction,
    Refund,
    TransactionKind,
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
        fields: tuple[str, ...] = (
            "id",
            "number",
            "status",
            "payment_method",
            "payment_status",
            "currency",
            "grand_total",
            "placed_at",
        )
        read_only_fields: tuple[str, ...] = fields


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


# --- Merchant order list and detail (Phase 11) ----------------------------------------------------


class MerchantOrderQuerySerializer(serializers.Serializer[Any]):
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=OrderStatus.choices, required=False)
    payment_status = serializers.ChoiceField(choices=OrderPaymentStatus.choices, required=False)
    source = serializers.ChoiceField(choices=OrderSource.choices, required=False)  # type: ignore[assignment]
    placed_from = serializers.DateField(required=False, help_text="Store-local date, inclusive.")
    placed_to = serializers.DateField(required=False, help_text="Store-local date, inclusive.")


class MerchantOrderSummarySerializer(OrderSummarySerializer):
    item_count = serializers.IntegerField()
    shipment_status = serializers.CharField(allow_null=True)

    class Meta(OrderSummarySerializer.Meta):
        fields = (
            *OrderSummarySerializer.Meta.fields,
            "source",
            "recipient_name",
            "contact_phone",
            "item_count",
            "shipment_status",
        )
        read_only_fields = fields


class PaymentRecordSerializer(serializers.ModelSerializer[PaymentTransaction]):
    class Meta:
        model = PaymentTransaction
        fields = ("id", "kind", "status", "provider", "amount", "currency", "occurred_at")
        read_only_fields = fields


class AssistedProvenanceSerializer(serializers.ModelSerializer[AssistedOrder]):
    created_by_name = serializers.CharField(source="created_by.full_name")

    class Meta:
        model = AssistedOrder
        fields = (
            "created_by_name",
            "created_at",
            "source_reference",
            "customer_request_attested",
        )
        read_only_fields = fields


class RelatedCaseSerializer(serializers.ModelSerializer[AfterSalesCase]):
    class Meta:
        model = AfterSalesCase
        fields = ("id", "kind", "status", "subject", "created_at")
        read_only_fields = fields


class FraudSummarySerializer(serializers.ModelSerializer[FraudAssessment]):
    class Meta:
        model = FraudAssessment
        fields = ("id", "score", "status", "assessed_at")
        read_only_fields = fields


class CustomerRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    full_name = serializers.CharField()


class MerchantOrderDetailSerializer(MerchantOrderSerializer):
    """Everything staff need on one order page. ``allowed_transitions`` lists only the merchant
    status changes the state machine accepts now; the server still enforces every rule."""

    assisted = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    shipment_id = serializers.SerializerMethodField()
    fraud = serializers.SerializerMethodField()
    cases = serializers.SerializerMethodField()
    allowed_transitions = serializers.SerializerMethodField()
    can_record_cod = serializers.SerializerMethodField()
    refundable_amount = serializers.SerializerMethodField()

    class Meta(MerchantOrderSerializer.Meta):
        fields = (
            *MerchantOrderSerializer.Meta.fields,
            "source",
            "assisted",
            "customer",
            "payments",
            "shipment_id",
            "fraud",
            "cases",
            "allowed_transitions",
            "can_record_cod",
            "refundable_amount",
        )
        read_only_fields = fields

    @extend_schema_field(AssistedProvenanceSerializer(allow_null=True))
    def get_assisted(self, order: Order) -> dict[str, Any] | None:
        row = AssistedOrder.objects.select_related("created_by").filter(order=order).first()
        return None if row is None else dict(AssistedProvenanceSerializer(row).data)

    @extend_schema_field(CustomerRefSerializer(allow_null=True))
    def get_customer(self, order: Order) -> dict[str, Any] | None:
        customer = order.customer
        return (
            None if customer is None else {"id": str(customer.pk), "full_name": customer.full_name}
        )

    @extend_schema_field(PaymentRecordSerializer(many=True))
    def get_payments(self, order: Order) -> list[dict[str, Any]]:
        rows = PaymentTransaction.objects.filter(order=order).order_by("occurred_at", "pk")
        return list(PaymentRecordSerializer(rows, many=True).data)

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_shipment_id(self, order: Order) -> str | None:
        shipment = Shipment.objects.filter(order=order).values_list("pk", flat=True).first()
        return None if shipment is None else str(shipment)

    @extend_schema_field(FraudSummarySerializer(allow_null=True))
    def get_fraud(self, order: Order) -> dict[str, Any] | None:
        row = FraudAssessment.objects.filter(order=order).first()
        return None if row is None else dict(FraudSummarySerializer(row).data)

    @extend_schema_field(RelatedCaseSerializer(many=True))
    def get_cases(self, order: Order) -> list[dict[str, Any]]:
        rows = AfterSalesCase.objects.filter(
            Q(order_item__order=order) | Q(replacement_order=order)
        ).order_by("-created_at", "-pk")[:20]
        return list(RelatedCaseSerializer(rows, many=True).data)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_allowed_transitions(self, order: Order) -> list[str]:
        from .services import TRANSITIONS

        allowed = TRANSITIONS.get(order.status, frozenset())
        targets = [value for value, _ in MERCHANT_TARGETS if value in allowed]
        if order.status == OrderStatus.PENDING_PAYMENT:
            # Online orders are confirmed only by a verified payment.
            targets = [value for value in targets if value != OrderStatus.CONFIRMED]
        return targets

    def get_can_record_cod(self, order: Order) -> bool:
        return (
            order.payment_method == PaymentMethod.CASH_ON_DELIVERY
            and order.status in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING)
            and not PaymentTransaction.objects.filter(
                order=order, kind=TransactionKind.COD_COLLECTION
            ).exists()
        )

    @extend_schema_field(serializers.DecimalField(max_digits=19, decimal_places=4))
    def get_refundable_amount(self, order: Order) -> str:
        return str(order.amount_paid - order.amount_refunded)


# --- Merchant-assisted orders (D-077) -------------------------------------------------------------


class AssistedItemSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)


ASSISTED_SOURCE_CHOICES = [(value, OrderSource(value).label) for value in ASSISTED_SOURCES]


class AssistedQuoteInputSerializer(serializers.Serializer[Any]):
    """A draft assisted order. Every detail is optional so staff can price it while typing."""

    items = AssistedItemSerializer(many=True)
    recipient_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    address_line_1 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    address_line_2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    region = serializers.CharField(max_length=100, required=False, allow_blank=True)
    postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    delivery_notes = serializers.CharField(max_length=500, required=False, allow_blank=True)
    shipping_method = serializers.UUIDField(required=False, allow_null=True)
    delivery_slot = serializers.UUIDField(required=False, allow_null=True)
    coupon_code = serializers.CharField(max_length=32, required=False, allow_blank=True)


class AssistedOrderInputSerializer(AssistedQuoteInputSerializer):
    recipient_name = serializers.CharField(max_length=150)
    contact_phone = serializers.CharField(max_length=40)
    address_line_1 = serializers.CharField(max_length=200)
    city = serializers.CharField(max_length=100)
    country = serializers.CharField(max_length=2)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    source = serializers.ChoiceField(choices=ASSISTED_SOURCE_CHOICES)  # type: ignore[assignment]
    source_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    customer_request_attested = serializers.BooleanField(
        help_text="Staff confirm the customer asked for this order. Not a customer signature."
    )
    idempotency_key = serializers.CharField(min_length=8, max_length=100)


class AssistedQuoteLineSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField(source="variant.id")
    sku = serializers.CharField(source="variant.sku")
    product_name = serializers.CharField(source="variant.product.name")
    variant_title = serializers.CharField(source="variant.title")
    kind = serializers.CharField(source="variant.kind")
    quantity = serializers.DecimalField(source="item.quantity", max_digits=14, decimal_places=3)
    unit_price = serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True)
    currency = serializers.CharField(allow_null=True)
    subtotal = serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True)
    available_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, allow_null=True)
    issue = serializers.CharField(allow_blank=True)


class AssistedTotalsSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    requires_shipping = serializers.BooleanField()
    item_subtotal = serializers.DecimalField(max_digits=19, decimal_places=4)
    discount_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    shipping_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    tax_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    grand_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    coupon_code = serializers.CharField(allow_blank=True)
    shipping_method = serializers.UUIDField(source="shipping_method_id", allow_null=True)
    delivery_slot = serializers.UUIDField(source="delivery_slot_id", allow_null=True)


class AssistedQuoteSerializer(serializers.Serializer[Any]):
    valid = serializers.BooleanField()
    lines = AssistedQuoteLineSerializer(many=True)
    totals = AssistedTotalsSerializer(allow_null=True)
    shipping_methods = ShippingMethodSerializer(many=True)
    delivery_slots = DeliverySlotSerializer(many=True)
    payment_methods = serializers.ListField(child=serializers.CharField())


class AssistedSearchQuerySerializer(serializers.Serializer[Any]):
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)


class AssistedVariantSerializer(serializers.Serializer[Any]):
    """One search result: ``(variant, price quote or None, sellable quantity or None)``."""

    id = serializers.SerializerMethodField()
    sku = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    variant_title = serializers.SerializerMethodField()
    kind = serializers.SerializerMethodField()
    inventory_unit = serializers.SerializerMethodField()
    unit_price = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()

    @extend_schema_field(serializers.UUIDField())
    def get_id(self, row: Any) -> str:
        return str(row[0].pk)

    def get_sku(self, row: Any) -> str:
        return str(row[0].sku)

    def get_product_name(self, row: Any) -> str:
        return str(row[0].product.name)

    def get_variant_title(self, row: Any) -> str:
        return str(row[0].title)

    def get_kind(self, row: Any) -> str:
        return str(row[0].kind)

    def get_inventory_unit(self, row: Any) -> str:
        return str(row[0].inventory_unit)

    @extend_schema_field(serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True))
    def get_unit_price(self, row: Any) -> str | None:
        return None if row[1] is None else f"{row[1].amount:.4f}"

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_currency(self, row: Any) -> str | None:
        return None if row[1] is None else str(row[1].currency)

    @extend_schema_field(
        serializers.DecimalField(
            max_digits=14,
            decimal_places=3,
            allow_null=True,
            help_text="Sellable stock now; null when the product does not track stock.",
        )
    )
    def get_available_quantity(self, row: Any) -> str | None:
        return None if row[2] is None else f"{row[2]:.3f}"
