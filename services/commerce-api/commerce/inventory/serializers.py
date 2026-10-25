from decimal import Decimal
from typing import Any

from rest_framework import serializers

from commerce.catalog.models import ProductVariant
from commerce.core.fields import TenantRelatedField

from .models import (
    MovementType,
    SerialStatus,
    StockLevel,
    StockLocation,
)
from .services import ADJUSTMENT_TYPES


def quantity_field(**kwargs: Any) -> serializers.DecimalField:
    return serializers.DecimalField(max_digits=14, decimal_places=3, **kwargs)


def idempotency_key_field() -> serializers.RegexField:
    return serializers.RegexField(
        regex=r"^[A-Za-z0-9._:-]{8,100}$",
        required=False,
        allow_null=True,
        help_text="Optional client-generated key; a retry with the same key is applied once.",
    )


# --- Requests -------------------------------------------------------------------------------------


class LocationWriteSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.RegexField(regex=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$", max_length=32)
    name = serializers.CharField(max_length=120)
    is_active = serializers.BooleanField(required=False)
    is_default = serializers.BooleanField(required=False)
    address_line = serializers.CharField(max_length=300, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class BatchInputSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.CharField(max_length=64)
    expires_on = serializers.DateField(required=False, allow_null=True)
    manufactured_on = serializers.DateField(required=False, allow_null=True)
    supplier_reference = serializers.CharField(max_length=100, required=False, allow_blank=True)


class _StockChangeSerializer(serializers.Serializer[dict[str, Any]]):
    variant = TenantRelatedField(ProductVariant)
    quantity = quantity_field()
    serials = serializers.ListField(
        child=serializers.CharField(max_length=100), max_length=500, required=False
    )
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    reference_type = serializers.CharField(max_length=50, required=False, allow_blank=True)
    reference_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    idempotency_key = idempotency_key_field()


class ReceiptSerializer(_StockChangeSerializer):
    location = TenantRelatedField(
        StockLocation, required=False, allow_null=True, help_text="Default location if omitted."
    )
    batch = BatchInputSerializer(required=False, allow_null=True)


class AdjustmentSerializer(_StockChangeSerializer):
    location = TenantRelatedField(StockLocation, required=False, allow_null=True)
    movement_type = serializers.ChoiceField(choices=list(ADJUSTMENT_TYPES))
    batch = BatchInputSerializer(required=False, allow_null=True)
    reason = serializers.CharField(max_length=500)


class TransferSerializer(_StockChangeSerializer):
    from_location = TenantRelatedField(StockLocation)
    to_location = TenantRelatedField(StockLocation)
    batch = serializers.CharField(max_length=64, required=False, allow_null=True)


class ReversalSerializer(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(max_length=500)
    idempotency_key = idempotency_key_field()


class LevelUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    low_stock_threshold = quantity_field(min_value=Decimal(0), allow_null=True)


class LevelQuerySerializer(serializers.Serializer[dict[str, Any]]):
    variant = serializers.UUIDField(required=False)
    product = serializers.UUIDField(required=False)
    location = serializers.UUIDField(required=False)
    low_stock = serializers.BooleanField(required=False, allow_null=True, default=None)
    out_of_stock = serializers.BooleanField(required=False, allow_null=True, default=None)


class MovementQuerySerializer(serializers.Serializer[dict[str, Any]]):
    variant = serializers.UUIDField(required=False)
    location = serializers.UUIDField(required=False)
    operation = serializers.UUIDField(required=False)
    movement_type = serializers.ChoiceField(choices=MovementType.choices, required=False)


class BatchQuerySerializer(serializers.Serializer[dict[str, Any]]):
    variant = serializers.UUIDField(required=False)
    expires_before = serializers.DateField(required=False)


class SerialQuerySerializer(serializers.Serializer[dict[str, Any]]):
    variant = serializers.UUIDField(required=False)
    location = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=SerialStatus.choices, required=False)
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)


# --- Responses ------------------------------------------------------------------------------------


class LocationSerializer(serializers.ModelSerializer[StockLocation]):
    class Meta:
        model = StockLocation
        fields = (
            "id",
            "code",
            "name",
            "is_active",
            "is_default",
            "address_line",
            "city",
            "country",
            "notes",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class VariantRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    sku = serializers.CharField()
    title = serializers.CharField()
    product = serializers.UUIDField(source="product_id")
    product_name = serializers.CharField(source="product.name")


class LocationRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()


class StockLevelSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    variant = VariantRefSerializer()
    location = LocationRefSerializer()
    on_hand = quantity_field()
    reserved = quantity_field()
    available = quantity_field()
    low_stock_threshold = quantity_field(allow_null=True)
    is_low = serializers.SerializerMethodField()
    updated_at = serializers.DateTimeField()

    def get_is_low(self, level: StockLevel) -> bool:
        threshold = level.low_stock_threshold
        return threshold is not None and level.available <= threshold


class MovementSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    operation_id = serializers.UUIDField()
    occurred_at = serializers.DateTimeField()
    movement_type = serializers.ChoiceField(choices=MovementType.choices)
    variant = serializers.UUIDField(source="variant_id")
    sku = serializers.CharField(source="variant.sku")
    location = serializers.UUIDField(source="location_id")
    location_code = serializers.CharField(source="location.code")
    on_hand_delta = quantity_field()
    reserved_delta = quantity_field()
    on_hand_after = quantity_field()
    reserved_after = quantity_field()
    batch = serializers.CharField(source="batch.code", allow_null=True, default=None)
    serial = serializers.CharField(source="serial.serial", allow_null=True, default=None)
    reversal_of = serializers.UUIDField(source="reversal_of_id", allow_null=True)
    reference_type = serializers.CharField()
    reference_id = serializers.CharField()
    reason = serializers.CharField()
    actor = serializers.UUIDField(source="actor_user_id", allow_null=True)
    actor_label = serializers.CharField()


class OperationSerializer(serializers.Serializer[Any]):
    operation_id = serializers.UUIDField()
    replayed = serializers.BooleanField()
    movements = MovementSerializer(many=True)


class BatchLevelSerializer(serializers.Serializer[Any]):
    location = serializers.UUIDField(source="location_id")
    location_code = serializers.CharField(source="location.code")
    on_hand = quantity_field()


class BatchSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    variant = serializers.UUIDField(source="variant_id")
    sku = serializers.CharField(source="variant.sku")
    code = serializers.CharField()
    manufactured_on = serializers.DateField(allow_null=True)
    expires_on = serializers.DateField(allow_null=True)
    received_on = serializers.DateField()
    supplier_reference = serializers.CharField()
    levels = BatchLevelSerializer(source="levels.all", many=True)


class SerialSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    serial = serializers.CharField()
    variant = serializers.UUIDField(source="variant_id")
    sku = serializers.CharField(source="variant.sku")
    status = serializers.ChoiceField(choices=SerialStatus.choices)
    location = serializers.UUIDField(source="location_id", allow_null=True)
    updated_at = serializers.DateTimeField()


class DiscrepancySerializer(serializers.Serializer[Any]):
    kind = serializers.CharField()
    variant_id = serializers.UUIDField()
    location_id = serializers.UUIDField()
    batch_id = serializers.UUIDField(allow_null=True)
    expected = quantity_field()
    actual = quantity_field()


class ReconciliationSerializer(serializers.Serializer[dict[str, Any]]):
    consistent = serializers.BooleanField()
    discrepancies = DiscrepancySerializer(many=True)
