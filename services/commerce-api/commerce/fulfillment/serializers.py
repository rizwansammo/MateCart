"""HTTP contracts for physical fulfillment and courier operations."""

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from .models import (
    CourierBooking,
    CourierCodRecord,
    FraudAssessment,
    FraudDecision,
    FraudFlag,
    FraudReview,
    Shipment,
    ShipmentItem,
    ShipmentStatus,
    TrackingEvent,
)


class PrepareShipmentSerializer(serializers.Serializer[Any]):
    operational_notes = serializers.CharField(max_length=500, required=False, allow_blank=True)


class ManualCourierSerializer(serializers.Serializer[Any]):
    courier_name = serializers.CharField(max_length=100)
    tracking_number = serializers.CharField(max_length=120, required=False, allow_blank=True)
    tracking_url = serializers.URLField(max_length=1000, required=False, allow_blank=True)

    def validate_tracking_url(self, value: str) -> str:
        if value and not value.startswith("https://"):
            raise serializers.ValidationError("Tracking links must use HTTPS.")
        return value


TRANSITION_TARGETS = [
    (value, label)
    for value, label in ShipmentStatus.choices
    if value not in (ShipmentStatus.READY,)
]


class ShipmentTransitionSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=TRANSITION_TARGETS)
    occurred_at = serializers.DateTimeField(required=False)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True)
    serials = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField(max_length=120)),
        required=False,
    )


class CourierBookingInputSerializer(serializers.Serializer[Any]):
    idempotency_key = serializers.CharField(min_length=8, max_length=100)


class CodCollectionInputSerializer(serializers.Serializer[Any]):
    collected_amount = serializers.DecimalField(max_digits=19, decimal_places=4, min_value=0)
    courier_fees = serializers.DecimalField(
        max_digits=19, decimal_places=4, min_value=0, default=Decimal(0)
    )
    deductions = serializers.DecimalField(
        max_digits=19, decimal_places=4, min_value=0, default=Decimal(0)
    )
    deduction_note = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default=""
    )
    collection_reference = serializers.CharField(min_length=1, max_length=120)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["deductions"] and not attrs.get("deduction_note"):
            raise serializers.ValidationError(
                {"deduction_note": ["Explain every non-zero deduction."]}
            )
        if attrs["courier_fees"] + attrs["deductions"] > attrs["collected_amount"]:
            raise serializers.ValidationError(
                {"deductions": ["Fees and deductions cannot exceed collection."]}
            )
        return attrs


class CodRemittanceInputSerializer(serializers.Serializer[Any]):
    actual_remittance = serializers.DecimalField(max_digits=19, decimal_places=4, min_value=0)
    remittance_reference = serializers.CharField(min_length=1, max_length=120)
    remitted_at = serializers.DateTimeField()


class FraudReviewInputSerializer(serializers.Serializer[Any]):
    decision = serializers.ChoiceField(choices=FraudDecision.choices)
    reason = serializers.CharField(min_length=1, max_length=500)


class CallbackOutcomeSerializer(serializers.Serializer[Any]):
    outcome = serializers.CharField()


class PublicTrackingEventSerializer(serializers.ModelSerializer[TrackingEvent]):
    class Meta:
        model = TrackingEvent
        fields = ("occurred_at", "status", "attempt_number")


class PublicShipmentSerializer(serializers.ModelSerializer[Shipment]):
    events = serializers.SerializerMethodField()

    class Meta:
        model = Shipment
        fields: tuple[str, ...] = (
            "reference",
            "status",
            "courier_name",
            "tracking_number",
            "tracking_url",
            "dispatched_at",
            "delivered_at",
            "failed_at",
            "events",
        )

    def get_events(self, shipment: Shipment) -> list[dict[str, Any]]:
        rows = TrackingEvent.objects.filter(shipment=shipment).order_by("occurred_at", "pk")
        return list(PublicTrackingEventSerializer(rows, many=True).data)


class ShipmentItemSerializer(serializers.ModelSerializer[ShipmentItem]):
    sku = serializers.CharField(source="order_item.sku")
    product_name = serializers.CharField(source="order_item.product_name")

    class Meta:
        model = ShipmentItem
        fields = ("id", "sku", "product_name", "quantity")


class TrackingEventSerializer(serializers.ModelSerializer[TrackingEvent]):
    class Meta:
        model = TrackingEvent
        fields = (
            "id",
            "occurred_at",
            "recorded_at",
            "from_status",
            "status",
            "source",
            "note",
            "attempt_number",
        )


class CourierBookingSerializer(serializers.ModelSerializer[CourierBooking]):
    class Meta:
        model = CourierBooking
        fields = (
            "id",
            "provider",
            "status",
            "provider_reference",
            "failure_code",
            "created_at",
            "cancelled_at",
        )


class CodRecordSerializer(serializers.ModelSerializer[CourierCodRecord]):
    outstanding_amount = serializers.SerializerMethodField()

    class Meta:
        model = CourierCodRecord
        fields = (
            "id",
            "courier_code",
            "currency",
            "amount_due",
            "collected_amount",
            "courier_fees",
            "deductions",
            "deduction_note",
            "expected_remittance",
            "actual_remittance",
            "outstanding_amount",
            "collection_reference",
            "collection_recorded_at",
            "remittance_reference",
            "remitted_at",
            "status",
        )

    def get_outstanding_amount(self, record: CourierCodRecord) -> Decimal:
        actual = record.actual_remittance or Decimal(0)
        return Decimal(record.expected_remittance) - actual


class FraudFlagSerializer(serializers.ModelSerializer[FraudFlag]):
    class Meta:
        model = FraudFlag
        fields = ("code", "description", "weight", "evidence")


class FraudReviewSerializer(serializers.ModelSerializer[FraudReview]):
    reviewer_id = serializers.UUIDField()

    class Meta:
        model = FraudReview
        fields = ("decision", "reason", "reviewer_id", "occurred_at")


class FraudAssessmentSerializer(serializers.ModelSerializer[FraudAssessment]):
    flags = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()

    class Meta:
        model = FraudAssessment
        fields = (
            "id",
            "score",
            "status",
            "assessed_at",
            "reviewed_at",
            "reviewed_by_id",
            "flags",
            "reviews",
        )

    def get_flags(self, assessment: FraudAssessment) -> list[dict[str, Any]]:
        return list(
            FraudFlagSerializer(FraudFlag.objects.filter(assessment=assessment), many=True).data
        )

    def get_reviews(self, assessment: FraudAssessment) -> list[dict[str, Any]]:
        rows = FraudReview.objects.filter(assessment=assessment).order_by("occurred_at", "pk")
        return list(FraudReviewSerializer(rows, many=True).data)


class ShipmentSummarySerializer(serializers.ModelSerializer[Shipment]):
    order_number = serializers.CharField(source="order.number")
    fraud_status = serializers.SerializerMethodField()

    class Meta:
        model = Shipment
        fields = (
            "id",
            "reference",
            "order_number",
            "status",
            "courier_name",
            "tracking_number",
            "created_at",
            "fraud_status",
        )

    def get_fraud_status(self, shipment: Shipment) -> str | None:
        return (
            FraudAssessment.objects.filter(order=shipment.order)
            .values_list("status", flat=True)
            .first()
        )


class MerchantShipmentSerializer(PublicShipmentSerializer):
    id = serializers.UUIDField()
    order_id = serializers.UUIDField()
    order_number = serializers.CharField(source="order.number")
    items = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    bookings = serializers.SerializerMethodField()
    cod = serializers.SerializerMethodField()
    fraud = serializers.SerializerMethodField()
    operational_notes = serializers.CharField()

    class Meta(PublicShipmentSerializer.Meta):
        fields: tuple[str, ...] = (
            "id",
            "order_id",
            "order_number",
            *PublicShipmentSerializer.Meta.fields,
            "operational_notes",
            "items",
            "bookings",
            "cod",
            "fraud",
        )

    def get_items(self, shipment: Shipment) -> list[dict[str, Any]]:
        rows = ShipmentItem.objects.filter(shipment=shipment).select_related("order_item")
        return list(ShipmentItemSerializer(rows, many=True).data)

    def get_events(self, shipment: Shipment) -> list[dict[str, Any]]:
        rows = TrackingEvent.objects.filter(shipment=shipment).order_by("occurred_at", "pk")
        return list(TrackingEventSerializer(rows, many=True).data)

    def get_bookings(self, shipment: Shipment) -> list[dict[str, Any]]:
        rows = CourierBooking.objects.filter(shipment=shipment).order_by("created_at", "pk")
        return list(CourierBookingSerializer(rows, many=True).data)

    def get_cod(self, shipment: Shipment) -> dict[str, Any] | None:
        record = CourierCodRecord.objects.filter(shipment=shipment).first()
        return None if record is None else dict(CodRecordSerializer(record).data)

    def get_fraud(self, shipment: Shipment) -> dict[str, Any] | None:
        assessment = FraudAssessment.objects.filter(order=shipment.order).first()
        return None if assessment is None else dict(FraudAssessmentSerializer(assessment).data)
