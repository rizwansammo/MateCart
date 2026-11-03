"""Strict request and response contracts for customer after sales actions."""

from decimal import Decimal
from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    AfterSalesCase,
    CaseKind,
    CaseMessage,
    CaseStatus,
    ProductReview,
    ReturnRefundEvidence,
    ReviewStatus,
)


class ReviewInputSerializer(serializers.Serializer[Any]):
    order_item = serializers.UUIDField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    body = serializers.CharField(min_length=3, max_length=2000, trim_whitespace=True)


class ReviewDecisionSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=[ReviewStatus.PUBLISHED, ReviewStatus.REJECTED])
    reason = serializers.CharField(max_length=300, allow_blank=True, required=False, default="")


class ProductReviewSerializer(serializers.ModelSerializer[ProductReview]):
    class Meta:
        model = ProductReview
        fields: tuple[str, ...] = (
            "id",
            "order_item",
            "rating",
            "body",
            "status",
            "decision_reason",
            "created_at",
        )
        read_only_fields: tuple[str, ...] = fields


class PublicReviewSerializer(serializers.ModelSerializer[ProductReview]):
    class Meta:
        model = ProductReview
        fields = ("id", "rating", "body", "created_at")
        read_only_fields = fields


class CaseInputSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=CaseKind.choices)
    order_item = serializers.UUIDField(required=False, allow_null=True)
    quantity = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=1,
        default=Decimal("1"),
    )
    subject = serializers.CharField(min_length=3, max_length=150)
    reason = serializers.CharField(min_length=3, max_length=2000)
    idempotency_key = serializers.CharField(min_length=8, max_length=100)


class CaseTransitionSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=CaseStatus.choices)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    refund = serializers.UUIDField(required=False, allow_null=True)
    replacement_order = serializers.UUIDField(required=False, allow_null=True)


class CaseMessageInputSerializer(serializers.Serializer[Any]):
    body = serializers.CharField(min_length=1, max_length=2000)


class CaseMessageSerializer(serializers.ModelSerializer[CaseMessage]):
    class Meta:
        model = CaseMessage
        fields = ("id", "author", "body", "created_at")
        read_only_fields = fields


class AfterSalesCaseSerializer(serializers.ModelSerializer[AfterSalesCase]):
    messages = CaseMessageSerializer(many=True, read_only=True)

    class Meta:
        model = AfterSalesCase
        fields: tuple[str, ...] = (
            "id",
            "kind",
            "status",
            "order_item",
            "quantity",
            "subject",
            "reason",
            "decision_reason",
            "refund",
            "replacement_order",
            "created_at",
            "updated_at",
            "messages",
        )
        read_only_fields: tuple[str, ...] = fields


# --- Merchant views (Phase 11) --------------------------------------------------------------------


def next_statuses(case: AfterSalesCase) -> list[str]:
    """The decisions the case state machine accepts now. Return and exchange resolution also
    needs its evidence step first; the service enforces every rule again on submission."""
    from .services import TRANSITIONS

    allowed = TRANSITIONS.get(case.status, frozenset())
    result: list[str] = []
    for status in CaseStatus.values:
        if status not in allowed:
            continue
        if status == CaseStatus.AWAITING_REFUND and case.kind != CaseKind.RETURN:
            continue
        if status == CaseStatus.REPLACEMENT_PENDING and case.kind != CaseKind.EXCHANGE:
            continue
        if status == CaseStatus.RESOLVED and (
            (case.kind == CaseKind.RETURN and case.status != CaseStatus.AWAITING_REFUND)
            or (case.kind == CaseKind.EXCHANGE and case.status != CaseStatus.REPLACEMENT_PENDING)
        ):
            continue
        result.append(status)
    return result


class AttributedRefundSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(source="refund.id")
    amount = serializers.DecimalField(source="refund.amount", max_digits=19, decimal_places=4)
    currency = serializers.CharField(source="refund.currency")
    status = serializers.CharField(source="refund.status")


class MerchantCaseSerializer(AfterSalesCaseSerializer):
    order_id = serializers.UUIDField(source="order_item.order_id", allow_null=True, default=None)
    order_number = serializers.CharField(
        source="order_item.order.number", allow_null=True, default=None
    )
    product_name = serializers.CharField(
        source="order_item.product_name", allow_null=True, default=None
    )
    sku = serializers.CharField(source="order_item.sku", allow_null=True, default=None)
    currency = serializers.CharField(
        source="order_item.order.currency", allow_null=True, default=None
    )
    customer_name = serializers.CharField(source="customer.full_name")
    next_statuses = serializers.SerializerMethodField()
    required_refund = serializers.SerializerMethodField()
    attributed_refunds = serializers.SerializerMethodField()

    class Meta(AfterSalesCaseSerializer.Meta):
        fields = (
            *AfterSalesCaseSerializer.Meta.fields,
            "order_id",
            "order_number",
            "product_name",
            "sku",
            "currency",
            "customer_name",
            "next_statuses",
            "required_refund",
            "attributed_refunds",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_next_statuses(self, case: AfterSalesCase) -> list[str]:
        return next_statuses(case)

    @extend_schema_field(serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True))
    def get_required_refund(self, case: AfterSalesCase) -> str | None:
        from .services import required_return_refund

        if case.kind != CaseKind.RETURN or case.order_item_id is None:
            return None
        return f"{required_return_refund(case):.4f}"

    @extend_schema_field(AttributedRefundSerializer(many=True))
    def get_attributed_refunds(self, case: AfterSalesCase) -> list[dict[str, Any]]:
        rows = ReturnRefundEvidence.objects.select_related("refund").filter(case=case)
        return list(AttributedRefundSerializer(rows.order_by("pk"), many=True).data)


class MerchantReviewSerializer(ProductReviewSerializer):
    product_name = serializers.CharField(source="order_item.product_name")
    customer_name = serializers.CharField(source="customer.full_name")

    class Meta(ProductReviewSerializer.Meta):
        fields = (*ProductReviewSerializer.Meta.fields, "product_name", "customer_name")
        read_only_fields = fields


class CaseQuerySerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=CaseStatus.choices, required=False)
    kind = serializers.ChoiceField(choices=CaseKind.choices, required=False)


class ReviewQuerySerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=ReviewStatus.choices, required=False)
