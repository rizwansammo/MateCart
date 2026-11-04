"""HTTP shapes for Phase 07 cart, address and checkout APIs."""

from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from commerce.identity.models import User
from commerce.orders import services as orders

from . import services
from .models import Address, CheckoutSession, CheckoutStatus, DeliverySlot, ShippingMethod


class AddCartItemSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)


class UpdateCartItemSerializer(serializers.Serializer[Any]):
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)


class CartLineSerializer(serializers.Serializer[services.LineQuote]):
    id = serializers.UUIDField(source="item.id")
    variant = serializers.UUIDField(source="variant.id")
    product_slug = serializers.CharField(source="variant.product.slug")
    product_name = serializers.CharField(source="variant.product.name")
    variant_title = serializers.CharField(source="variant.title")
    sku = serializers.CharField(source="variant.sku")
    kind = serializers.CharField(source="variant.kind")
    quantity = serializers.DecimalField(source="item.quantity", max_digits=14, decimal_places=3)
    unit_price = serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True)
    currency = serializers.CharField(allow_null=True)
    subtotal = serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True)
    available_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, allow_null=True)
    issue = serializers.CharField()
    # Phase 12 (D-081): the list price and the promotion behind ``unit_price``, if any.
    regular_unit_price = serializers.DecimalField(
        source="quote.regular_amount", max_digits=19, decimal_places=4, allow_null=True
    )
    promotion_kind = serializers.SerializerMethodField()
    promotion_name = serializers.SerializerMethodField()

    def get_promotion_kind(self, line: services.LineQuote) -> str:
        return line.quote.promotion_kind if line.quote is not None else ""

    def get_promotion_name(self, line: services.LineQuote) -> str:
        return line.quote.promotion_name if line.quote is not None else ""


class CartSerializer(serializers.Serializer[services.CartQuote]):
    id = serializers.UUIDField(source="cart.id", required=False)
    lines = CartLineSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=19, decimal_places=4)
    currency = serializers.CharField()
    item_count = serializers.IntegerField()
    valid = serializers.BooleanField()

    def to_representation(self, instance: services.CartQuote) -> dict[str, Any]:
        data = super().to_representation(instance)
        data["id"] = str(self.context["cart"].pk)
        return data


class AddressInputSerializer(serializers.Serializer[Any]):
    address_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    recipient_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=40, required=False)
    address_line_1 = serializers.CharField(max_length=200, required=False)
    address_line_2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False)
    region = serializers.CharField(max_length=100, required=False, allow_blank=True)
    postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    country = serializers.CharField(max_length=2, required=False)
    is_default = serializers.BooleanField(required=False)


class AddressSerializer(serializers.ModelSerializer[Address]):
    class Meta:
        model = Address
        fields = (
            "id",
            "label",
            "recipient_name",
            "phone",
            "address_line_1",
            "address_line_2",
            "city",
            "region",
            "postal_code",
            "country",
            "is_default",
        )


class CheckoutCreateSerializer(serializers.Serializer[Any]):
    pass


class CheckoutUpdateSerializer(serializers.Serializer[Any]):
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    recipient_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    address_line_1 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    address_line_2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    region = serializers.CharField(max_length=100, required=False, allow_blank=True)
    postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    delivery_notes = serializers.CharField(max_length=500, required=False, allow_blank=True)
    coupon_code = serializers.CharField(max_length=32, required=False, allow_blank=True)
    shipping_method = serializers.UUIDField(required=False, allow_null=True)
    delivery_slot = serializers.UUIDField(required=False, allow_null=True)
    loyalty_points = serializers.IntegerField(required=False, min_value=0, max_value=10_000_000)


class CheckoutReviewInputSerializer(serializers.Serializer[Any]):
    idempotency_key = serializers.CharField(min_length=8, max_length=100)


class CheckoutLineSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField(source="variant_id")
    product_name = serializers.CharField()
    variant_title = serializers.CharField()
    sku = serializers.CharField()
    kind = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    unit_price = serializers.DecimalField(max_digits=19, decimal_places=4)
    line_subtotal = serializers.DecimalField(max_digits=19, decimal_places=4)
    regular_unit_price = serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True)
    promotion_kind = serializers.CharField()
    promotion_name = serializers.CharField()
    coupon_eligible = serializers.BooleanField()


class LoyaltyCheckoutSerializer(serializers.Serializer[Any]):
    balance = serializers.IntegerField()
    point_value = serializers.DecimalField(max_digits=19, decimal_places=4)
    min_redeem_points = serializers.IntegerField()
    max_points = serializers.IntegerField(help_text="Most points this checkout can use now.")


class ShippingMethodSerializer(serializers.ModelSerializer[ShippingMethod]):
    class Meta:
        model = ShippingMethod
        fields = ("id", "code", "name", "description", "amount", "currency", "free_above")


class DeliverySlotSerializer(serializers.ModelSerializer[DeliverySlot]):
    class Meta:
        model = DeliverySlot
        fields = ("id", "label", "starts_at", "ends_at")


class CheckoutSerializer(serializers.Serializer[CheckoutSession]):
    id = serializers.UUIDField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()
    contact_email = serializers.EmailField(allow_blank=True)
    contact_phone = serializers.CharField(allow_blank=True)
    recipient_name = serializers.CharField(allow_blank=True)
    address_line_1 = serializers.CharField(allow_blank=True)
    address_line_2 = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    region = serializers.CharField(allow_blank=True)
    postal_code = serializers.CharField(allow_blank=True)
    country = serializers.CharField(allow_blank=True)
    delivery_notes = serializers.CharField(allow_blank=True)
    coupon_code = serializers.CharField(allow_blank=True)
    requires_shipping = serializers.BooleanField()
    currency = serializers.CharField()
    item_subtotal = serializers.DecimalField(max_digits=19, decimal_places=4)
    discount_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    shipping_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    tax_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    grand_total = serializers.DecimalField(max_digits=19, decimal_places=4)
    loyalty_points = serializers.IntegerField()
    loyalty_discount = serializers.DecimalField(max_digits=19, decimal_places=4)
    loyalty = serializers.SerializerMethodField()
    lines = CheckoutLineSerializer(many=True)
    shipping_methods = serializers.SerializerMethodField()
    delivery_slots = serializers.SerializerMethodField()
    selected_shipping_method = serializers.UUIDField(source="shipping_method_id", allow_null=True)
    selected_delivery_slot = serializers.UUIDField(source="delivery_slot_id", allow_null=True)
    payment_methods = serializers.SerializerMethodField()
    order_creation_available = serializers.SerializerMethodField()
    order = serializers.SerializerMethodField()

    def get_shipping_methods(self, session: CheckoutSession) -> list[dict[str, Any]]:
        return list(
            ShippingMethodSerializer(services.available_shipping_methods(session), many=True).data
        )

    def get_delivery_slots(self, session: CheckoutSession) -> list[dict[str, Any]]:
        return list(
            DeliverySlotSerializer(services.available_delivery_slots(session), many=True).data
        )

    @extend_schema_field(LoyaltyCheckoutSerializer(allow_null=True))
    def get_loyalty(self, session: CheckoutSession) -> dict[str, Any] | None:
        """Points the signed-in customer could use here; null when loyalty does not apply."""
        from commerce.marketing import loyalty
        from commerce.marketing.models import LoyaltyAccount

        current = loyalty.active_program()
        if (
            current is None
            or session.customer_id is None
            or current.currency != session.currency
            or loyalty.eligible_customer(session.customer) is None
        ):
            return None
        balance = (
            LoyaltyAccount.objects.filter(customer_id=session.customer_id)
            .values_list("balance", flat=True)
            .first()
            or 0
        )
        coupon_part = session.discount_total - session.loyalty_discount
        cap = loyalty.redemption_cap(session.item_subtotal - coupon_part, current)
        # Rendered through its serializer so money stays a decimal string like every other amount.
        return dict(
            LoyaltyCheckoutSerializer(
                {
                    "balance": balance,
                    "point_value": current.point_value,
                    "min_redeem_points": current.min_redeem_points,
                    "max_points": min(balance, cap),
                }
            ).data
        )

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_payment_methods(self, session: CheckoutSession) -> list[str]:
        return orders.available_payment_methods(session)

    def get_order_creation_available(self, session: CheckoutSession) -> bool:
        # Only a still-held reviewed checkout with a method that can really complete it.
        return (
            session.status == CheckoutStatus.REVIEWED
            and session.expires_at > timezone.now()
            and bool(orders.available_payment_methods(session))
        )

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_order(self, session: CheckoutSession) -> str | None:
        order = orders.order_for_checkout(session)
        return str(order.pk) if order is not None else None


def serialize_cart(cart: Any) -> dict[str, Any]:
    quote = services.quote_cart(cart)
    return dict(CartSerializer(quote, context={"cart": cart}).data)


def serialize_checkout(session: CheckoutSession) -> dict[str, Any]:
    session = (
        CheckoutSession.objects.select_related("shipping_method", "delivery_slot", "coupon")
        .prefetch_related("lines")
        .get(pk=session.pk)
    )
    return dict(CheckoutSerializer(session).data)


def customer_from_request(user: Any) -> User:
    if not isinstance(user, User) or user.account_type != "customer":
        raise PermissionError
    return user
