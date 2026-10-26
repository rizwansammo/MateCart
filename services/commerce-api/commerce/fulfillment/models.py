"""Physical shipment, courier COD and fraud-review records (Phase 09)."""

from __future__ import annotations

from typing import Any, NoReturn

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from commerce.checkout.models import (
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    SHA256_HEX_PATTERN,
)
from commerce.core.models import TimestampedModel
from commerce.core.standards import validate_currency_code
from commerce.identity.models import User
from commerce.orders.models import ImmutableRecordError, Order, OrderItem
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

SHIPMENT_REFERENCE_PATTERN = r"^SHP-[0-9]{6,}$"
PROVIDER_CODE_PATTERN = r"^[a-z][a-z0-9_-]{1,39}$"


def _money(**kwargs: Any) -> models.DecimalField[Any, Any]:
    return models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, **kwargs
    )


def _quantity() -> models.DecimalField[Any, Any]:
    return models.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )


class ShipmentStatus(models.TextChoices):
    READY = "ready", "Ready for fulfillment"
    PICKING = "picking", "Picking"
    PACKED = "packed", "Packed"
    READY_FOR_DISPATCH = "ready_for_dispatch", "Ready for dispatch"
    DISPATCHED = "dispatched", "Dispatched"
    IN_TRANSIT = "in_transit", "In transit"
    DELIVERED = "delivered", "Delivered"
    DELIVERY_FAILED = "delivery_failed", "Delivery failed"
    RETURN_TO_SENDER = "return_to_sender", "Return to sender"
    CANCELLED = "cancelled", "Cancelled before dispatch"


PRE_DISPATCH_STATUSES = (
    ShipmentStatus.READY,
    ShipmentStatus.PICKING,
    ShipmentStatus.PACKED,
    ShipmentStatus.READY_FOR_DISPATCH,
)


class Shipment(TenantOwnedModel, TimestampedModel):
    """One physical shipment for an order; partial shipments are intentionally deferred."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="shipments")
    reference = models.CharField(
        max_length=20, validators=[RegexValidator(SHIPMENT_REFERENCE_PATTERN)]
    )
    status = models.CharField(
        max_length=24, choices=ShipmentStatus.choices, default=ShipmentStatus.READY
    )
    courier_code = models.CharField(
        max_length=40, blank=True, validators=[RegexValidator(PROVIDER_CODE_PATTERN)]
    )
    courier_name = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=120, blank=True)
    tracking_url = models.URLField(max_length=1000, blank=True)
    recipient_name = models.CharField(max_length=150)
    address_line_1 = models.CharField(max_length=200)
    address_line_2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=2)
    operational_notes = models.CharField(max_length=500, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="fulfil_shipment_order_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "order"], name="fulfil_shipment_order_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant", "reference"], name="fulfil_shipment_reference_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant", "courier_code", "tracking_number"],
                condition=~Q(tracking_number=""),
                name="fulfil_shipment_tracking_unique",
            ),
            models.CheckConstraint(
                condition=Q(reference__regex=SHIPMENT_REFERENCE_PATTERN),
                name="fulfil_shipment_reference_format",
            ),
            models.CheckConstraint(
                condition=Q(status__in=ShipmentStatus.values),
                name="fulfil_shipment_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(courier_code="") | Q(courier_code__regex=PROVIDER_CODE_PATTERN),
                name="fulfil_shipment_courier_format",
            ),
            models.CheckConstraint(
                condition=Q(tracking_url="") | Q(tracking_url__startswith="https://"),
                name="fulfil_shipment_tracking_url_https",
            ),
            models.CheckConstraint(
                condition=~Q(status=ShipmentStatus.DISPATCHED) | Q(dispatched_at__isnull=False),
                name="fulfil_shipment_dispatched_has_time",
            ),
            models.CheckConstraint(
                condition=~Q(status=ShipmentStatus.DELIVERED) | Q(delivered_at__isnull=False),
                name="fulfil_shipment_delivered_has_time",
            ),
            models.CheckConstraint(
                condition=~Q(status=ShipmentStatus.RETURN_TO_SENDER) | Q(returned_at__isnull=False),
                name="fulfil_shipment_returned_has_time",
            ),
            models.CheckConstraint(
                condition=~Q(status=ShipmentStatus.CANCELLED) | Q(cancelled_at__isnull=False),
                name="fulfil_shipment_cancelled_has_time",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"], name="fulfil_shipment_work")
        ]


class ShipmentItem(TenantOwnedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="items")
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="+")
    quantity = _quantity()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="shipment", name="fulfil_item_shipment_tenant"),
            TenantForeignKeyConstraint(field="order_item", name="fulfil_item_order_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "shipment", "order_item"],
                name="fulfil_item_order_item_unique",
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="fulfil_item_quantity_gt_0"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Shipment items are immutable snapshots.")
        super().save(*args, **kwargs)


class TrackingSource(models.TextChoices):
    STAFF = "staff", "Merchant staff"
    PROVIDER = "provider", "Courier provider"
    SYSTEM = "system", "System"


class TrackingEvent(TenantOwnedModel):
    """Append-only shipment history. Provider event ids make callbacks idempotent."""

    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="events")
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(default=timezone.now)
    from_status = models.CharField(max_length=24, blank=True)
    status = models.CharField(max_length=24, choices=ShipmentStatus.choices)
    source = models.CharField(max_length=12, choices=TrackingSource.choices)
    note = models.CharField(max_length=300, blank=True)
    attempt_number = models.PositiveSmallIntegerField(null=True, blank=True)
    provider_event_id = models.CharField(max_length=120, blank=True)
    body_sha256 = models.CharField(
        max_length=64, blank=True, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="shipment", name="fulfil_event_shipment_tenant"),
            TenantForeignKeyConstraint(field="actor", name="fulfil_event_actor_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "shipment", "provider_event_id"],
                condition=~Q(provider_event_id=""),
                name="fulfil_event_provider_id_unique",
            ),
            models.CheckConstraint(
                condition=Q(status__in=ShipmentStatus.values), name="fulfil_event_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(source__in=TrackingSource.values), name="fulfil_event_source_valid"
            ),
            models.CheckConstraint(
                condition=(Q(source=TrackingSource.PROVIDER) & ~Q(provider_event_id=""))
                | (~Q(source=TrackingSource.PROVIDER) & Q(provider_event_id="")),
                name="fulfil_event_provider_id_source",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "shipment", "occurred_at"], name="fulfil_event_time")
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Tracking history is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ImmutableRecordError("Tracking history is append-only.")


class BookingStatus(models.TextChoices):
    PENDING = "pending", "Booking in progress"
    BOOKED = "booked", "Booked"
    CANCELLING = "cancelling", "Cancellation in progress"
    FAILED = "failed", "Booking failed"
    CANCELLED = "cancelled", "Cancelled"


class CourierBooking(TenantOwnedModel, TimestampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="bookings")
    provider = models.CharField(max_length=40, validators=[RegexValidator(PROVIDER_CODE_PATTERN)])
    idempotency_key_hash = models.CharField(
        max_length=64, validators=[RegexValidator(SHA256_HEX_PATTERN)]
    )
    status = models.CharField(max_length=12, choices=BookingStatus.choices)
    provider_reference = models.CharField(max_length=120, blank=True)
    failure_code = models.CharField(max_length=40, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="shipment", name="fulfil_booking_shipment_tenant"),
            TenantForeignKeyConstraint(field="requested_by", name="fulfil_booking_actor_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key_hash"], name="fulfil_booking_key_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant", "shipment"],
                condition=Q(
                    status__in=(
                        BookingStatus.PENDING,
                        BookingStatus.BOOKED,
                        BookingStatus.CANCELLING,
                    )
                ),
                name="fulfil_booking_one_active",
            ),
            models.UniqueConstraint(
                fields=["tenant", "provider", "provider_reference"],
                condition=~Q(provider_reference=""),
                name="fulfil_booking_provider_ref_unique",
            ),
            models.CheckConstraint(
                condition=Q(status__in=BookingStatus.values), name="fulfil_booking_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(provider__regex=PROVIDER_CODE_PATTERN),
                name="fulfil_booking_provider_format",
            ),
            models.CheckConstraint(
                condition=~Q(status=BookingStatus.BOOKED) | ~Q(provider_reference=""),
                name="fulfil_booking_booked_has_ref",
            ),
        ]


class CourierCallback(TenantOwnedModel):
    """Deduplication log for authenticated tracking callbacks; raw provider bodies are not kept."""

    provider = models.CharField(max_length=40, validators=[RegexValidator(PROVIDER_CODE_PATTERN)])
    event_id = models.CharField(max_length=120)
    booking_reference = models.CharField(max_length=120)
    received_at = models.DateTimeField(default=timezone.now)
    body_sha256 = models.CharField(max_length=64, validators=[RegexValidator(SHA256_HEX_PATTERN)])
    outcome = models.CharField(max_length=32, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "provider", "event_id"],
                name="fulfil_callback_event_unique",
            ),
            models.CheckConstraint(
                condition=Q(provider__regex=PROVIDER_CODE_PATTERN),
                name="fulfil_callback_provider_format",
            ),
        ]


class CodReconciliationStatus(models.TextChoices):
    PENDING = "pending", "Awaiting remittance"
    RECONCILED = "reconciled", "Reconciled"
    DISPUTED = "disputed", "Discrepancy"


class CourierCodRecord(TenantOwnedModel, TimestampedModel):
    """Courier collection evidence and the separate merchant remittance assertion."""

    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="+")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="+")
    courier_code = models.CharField(
        max_length=40, validators=[RegexValidator(PROVIDER_CODE_PATTERN)]
    )
    currency = models.CharField(max_length=3, validators=[validate_currency_code])
    amount_due = _money()
    collected_amount = _money()
    courier_fees = _money(default=0)
    deductions = _money(default=0)
    deduction_note = models.CharField(max_length=300, blank=True)
    expected_remittance = _money()
    actual_remittance = _money(null=True, blank=True)
    collection_reference = models.CharField(max_length=120)
    collection_recorded_at = models.DateTimeField(default=timezone.now)
    remittance_reference = models.CharField(max_length=120, blank=True)
    remitted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=12,
        choices=CodReconciliationStatus.choices,
        default=CodReconciliationStatus.PENDING,
    )
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    reconciled_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="shipment", name="fulfil_cod_shipment_tenant"),
            TenantForeignKeyConstraint(field="order", name="fulfil_cod_order_tenant"),
            TenantForeignKeyConstraint(field="recorded_by", name="fulfil_cod_recorder_tenant"),
            TenantForeignKeyConstraint(field="reconciled_by", name="fulfil_cod_reconciler_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "shipment"], name="fulfil_cod_shipment_unique"
            ),
            models.UniqueConstraint(fields=["tenant", "order"], name="fulfil_cod_order_unique"),
            models.UniqueConstraint(
                fields=["tenant", "courier_code", "collection_reference"],
                name="fulfil_cod_collection_ref_unique",
            ),
            models.UniqueConstraint(
                fields=["tenant", "courier_code", "remittance_reference"],
                condition=~Q(remittance_reference=""),
                name="fulfil_cod_remittance_ref_unique",
            ),
            models.CheckConstraint(
                condition=Q(courier_code__regex=PROVIDER_CODE_PATTERN),
                name="fulfil_cod_courier_format",
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"), name="fulfil_cod_currency_format"
            ),
            models.CheckConstraint(
                condition=Q(amount_due__gte=0)
                & Q(collected_amount__gte=0)
                & Q(courier_fees__gte=0)
                & Q(deductions__gte=0)
                & Q(expected_remittance__gte=0)
                & (Q(actual_remittance__isnull=True) | Q(actual_remittance__gte=0)),
                name="fulfil_cod_amounts_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(
                    expected_remittance=F("collected_amount") - F("courier_fees") - F("deductions")
                ),
                name="fulfil_cod_expected_exact",
            ),
            models.CheckConstraint(
                condition=Q(deductions=0) | ~Q(deduction_note=""),
                name="fulfil_cod_deduction_has_note",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=CodReconciliationStatus.PENDING, actual_remittance__isnull=True)
                )
                | (
                    Q(status=CodReconciliationStatus.RECONCILED)
                    & Q(actual_remittance=F("expected_remittance"))
                    & Q(remitted_at__isnull=False)
                    & ~Q(remittance_reference="")
                )
                | (
                    Q(status=CodReconciliationStatus.DISPUTED)
                    & Q(actual_remittance__isnull=False)
                    & ~Q(actual_remittance=F("expected_remittance"))
                    & Q(remitted_at__isnull=False)
                    & ~Q(remittance_reference="")
                ),
                name="fulfil_cod_status_consistent",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "status", "created_at"], name="fulfil_cod_work")]


class FraudReviewStatus(models.TextChoices):
    CLEAR = "clear", "No flags"
    PENDING = "pending", "Review suggested"
    CLEARED = "cleared", "Cleared by staff"
    MONITOR = "monitor", "Monitor"
    CONFIRMED_RISK = "confirmed_risk", "Risk confirmed by staff"


class FraudAssessment(TenantOwnedModel, TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="+")
    score = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20, choices=FraudReviewStatus.choices)
    assessed_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="fulfil_assessment_order_tenant"),
            TenantForeignKeyConstraint(
                field="reviewed_by", name="fulfil_assessment_reviewer_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "order"], name="fulfil_assessment_order_unique"
            ),
            models.CheckConstraint(
                condition=Q(status__in=FraudReviewStatus.values),
                name="fulfil_assessment_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(score__lte=100), name="fulfil_assessment_score_lte_100"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "assessed_at"], name="fulfil_fraud_work")
        ]


class FraudFlag(TenantOwnedModel):
    assessment = models.ForeignKey(FraudAssessment, on_delete=models.PROTECT, related_name="flags")
    code = models.CharField(max_length=40)
    description = models.CharField(max_length=200)
    weight = models.PositiveSmallIntegerField()
    evidence = models.JSONField(default=dict)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="assessment", name="fulfil_flag_assessment_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "assessment", "code"], name="fulfil_flag_code_unique"
            ),
            models.CheckConstraint(
                condition=Q(code__regex=r"^[a-z][a-z0-9_]{1,39}$"),
                name="fulfil_flag_code_format",
            ),
            models.CheckConstraint(condition=Q(weight__gt=0), name="fulfil_flag_weight_gt_0"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Fraud flags are append-only evidence.")
        super().save(*args, **kwargs)


class FraudDecision(models.TextChoices):
    CLEAR = "clear", "Clear"
    MONITOR = "monitor", "Monitor"
    CONFIRM_RISK = "confirm_risk", "Confirm risk"


class FraudReview(TenantOwnedModel):
    assessment = models.ForeignKey(
        FraudAssessment, on_delete=models.PROTECT, related_name="reviews"
    )
    decision = models.CharField(max_length=16, choices=FraudDecision.choices)
    reason = models.CharField(max_length=500)
    reviewer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="assessment", name="fulfil_review_assessment_tenant"),
            TenantForeignKeyConstraint(field="reviewer", name="fulfil_review_reviewer_tenant"),
            models.CheckConstraint(
                condition=Q(decision__in=FraudDecision.values), name="fulfil_review_decision_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "assessment", "occurred_at"], name="fulfil_review_time")
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError("Fraud review history is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ImmutableRecordError("Fraud review history is append-only.")
