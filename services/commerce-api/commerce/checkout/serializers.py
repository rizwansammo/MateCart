"""HTTP shapes for Phase 07 cart, address and checkout APIs."""

from typing import Any

from rest_framework import serializers

from commerce.identity.models import User

from . import services
from .models import Address, CheckoutSession, DeliverySlot, ShippingMethod


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
    lines = CheckoutLineSerializer(many=True)
    shipping_methods = serializers.SerializerMethodField()
    delivery_slots = serializers.SerializerMethodField()
    selected_shipping_method = serializers.UUIDField(source="shipping_method_id", allow_null=True)
    selected_delivery_slot = serializers.UUIDField(source="delivery_slot_id", allow_null=True)
    order_creation_available = serializers.SerializerMethodField()

    def get_shipping_methods(self, session: CheckoutSession) -> list[dict[str, Any]]:
        return list(
            ShippingMethodSerializer(services.available_shipping_methods(session), many=True).data
        )

    def get_delivery_slots(self, session: CheckoutSession) -> list[dict[str, Any]]:
        return list(
            DeliverySlotSerializer(services.available_delivery_slots(session), many=True).data
        )

    def get_order_creation_available(self, session: CheckoutSession) -> bool:
        # Phase 08 consumes a reviewed session. Phase 07 never pretends an order exists.
        return False


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
