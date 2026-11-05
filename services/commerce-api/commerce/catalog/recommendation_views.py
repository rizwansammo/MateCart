"""Related products: the public storefront list and merchant overrides (D-110)."""

from __future__ import annotations

import uuid
from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.core.domain import found, found_in
from commerce.core.fields import TenantRelatedField
from commerce.staff.views import CatalogStaffView

from . import recommendations
from .models import Product
from .public import TAGS, PublicProductSummarySerializer, PublicView, active_products


class PublicRelatedProductsView(PublicView):
    @extend_schema(
        operation_id="catalog_product_related",
        tags=TAGS,
        responses={200: PublicProductSummarySerializer(many=True)},
    )
    def get(self, request: Request, slug: str) -> Response:
        product = found_in(active_products(), slug=slug)
        rows = recommendations.related(product)
        return Response(PublicProductSummarySerializer(rows, many=True).data)


class RecommendationEntrySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    status = serializers.CharField()
    reasons = serializers.ListField(child=serializers.CharField())


class RecommendationsSerializer(serializers.Serializer[Any]):
    pinned = RecommendationEntrySerializer(many=True)
    hidden = RecommendationEntrySerializer(many=True)
    automatic = RecommendationEntrySerializer(many=True)
    storefront = RecommendationEntrySerializer(many=True)


class RecommendationsInputSerializer(serializers.Serializer[dict[str, Any]]):
    pinned = serializers.ListField(child=TenantRelatedField(Product), max_length=12)
    hidden = serializers.ListField(child=TenantRelatedField(Product), max_length=100)


class ProductRecommendationsView(CatalogStaffView):
    @extend_schema(
        operation_id="catalog_product_recommendations",
        tags=["manage-catalog"],
        responses={200: RecommendationsSerializer},
    )
    def get(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        return Response(RecommendationsSerializer(recommendations.merchant_view(product)).data)

    @extend_schema(
        operation_id="catalog_product_recommendations_set",
        tags=["manage-catalog"],
        request=RecommendationsInputSerializer,
        responses={200: RecommendationsSerializer},
    )
    def put(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        serializer = RecommendationsInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recommendations.set_overrides(
            product=product,
            pinned=serializer.validated_data["pinned"],
            hidden=serializer.validated_data["hidden"],
            actor=self.user,
        )
        return Response(RecommendationsSerializer(recommendations.merchant_view(product)).data)
