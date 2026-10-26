"""Strict request and response contracts for customer after sales actions."""

from typing import Any
from rest_framework import serializers
from .models import AfterSalesCase, CaseKind, CaseMessage, CaseStatus, ProductReview, ReviewStatus


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
        fields = ("id", "order_item", "rating", "body", "status", "decision_reason", "created_at")
        read_only_fields = fields


class PublicReviewSerializer(serializers.ModelSerializer[ProductReview]):
    class Meta:
        model = ProductReview
        fields = ("id", "rating", "body", "created_at")
        read_only_fields = fields


class CaseInputSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=CaseKind.choices)
    order_item = serializers.UUIDField(required=False, allow_null=True)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, min_value=1, default=1,
    )
    subject = serializers.CharField(min_length=3, max_length=150)
    reason = serializers.CharField(min_length=3, max_length=2000)
    idempotency_key = serializers.CharField(min_length=8, max_length=100)


class CaseTransitionSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=CaseStatus.choices)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


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
        fields = (
            "id", "kind", "status", "order_item", "quantity", "subject", "reason",
            "decision_reason", "created_at", "updated_at", "messages",
        )
        read_only_fields = fields
