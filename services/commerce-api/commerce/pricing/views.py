"""Merchant pricing API under /api/manage/pricing/ (staff realm, ``catalog.manage``; cost prices
also need ``catalog.costs``)."""

import uuid
from decimal import Decimal
from typing import Any

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.catalog.models import ProductVariant
from commerce.catalog.serializers import PriceSerializer
from commerce.core.domain import found
from commerce.core.fields import TenantRelatedField
from commerce.core.pagination import PageQuerySerializer, paginate, paginated
from commerce.core.standards import validate_currency_code
from commerce.staff.views import CatalogStaffView

from . import services
from .models import Price, PriceList

TAGS = ["manage-pricing"]


class PriceListSerializer(serializers.ModelSerializer[PriceList]):
    class Meta:
        model = PriceList
        fields = ("id", "code", "name", "currency", "is_default", "is_active", "created_at")
        read_only_fields = fields


class PriceListWriteSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.RegexField(max_length=40, regex=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name = serializers.CharField(max_length=120)
    currency = serializers.CharField(max_length=3, validators=[validate_currency_code])
    is_active = serializers.BooleanField(required=False)


class PriceEntrySerializer(serializers.Serializer[dict[str, Any]]):
    price_list = TenantRelatedField(
        PriceList, required=False, allow_null=True, help_text="Omit for the default price list."
    )
    amount = serializers.DecimalField(max_digits=19, decimal_places=4, min_value=Decimal(0))
    compare_at_amount = serializers.DecimalField(
        max_digits=19, decimal_places=4, required=False, allow_null=True
    )
    cost_amount = serializers.DecimalField(
        max_digits=19,
        decimal_places=4,
        min_value=Decimal(0),
        required=False,
        allow_null=True,
        help_text="Needs catalog.costs. Omit to keep the stored cost.",
    )


class VariantPricesInputSerializer(serializers.Serializer[dict[str, Any]]):
    prices = PriceEntrySerializer(many=True, allow_empty=False)

    def validate_prices(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > 20:
            raise serializers.ValidationError("Give at most 20 prices at a time.")
        return value


class VariantPricesSerializer(serializers.Serializer[dict[str, Any]]):
    variant = serializers.UUIDField()
    prices = PriceSerializer(many=True)


@method_decorator(never_cache, name="dispatch")
class PriceListListView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_price_lists",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(PriceListSerializer)},
    )
    def get(self, request: Request) -> Response:
        services.default_price_list()
        items, meta = paginate(PriceList.objects.order_by("-is_default", "name", "pk"), request)
        return Response({**meta, "results": PriceListSerializer(items, many=True).data})

    @extend_schema(
        operation_id="manage_price_list_create",
        tags=TAGS,
        request=PriceListWriteSerializer,
        responses={201: PriceListSerializer},
    )
    def post(self, request: Request) -> Response:
        data = PriceListWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        price_list = services.save_price_list(
            price_list=None, data=dict(data.validated_data), actor=self.user
        )
        return Response(PriceListSerializer(price_list).data, status=status.HTTP_201_CREATED)


class PriceListDetailView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_price_list_update",
        tags=TAGS,
        request=PriceListWriteSerializer,
        responses={200: PriceListSerializer},
    )
    def patch(self, request: Request, price_list_id: uuid.UUID) -> Response:
        price_list = found(PriceList, pk=price_list_id)
        data = PriceListWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        saved = services.save_price_list(
            price_list=price_list, data=dict(data.validated_data), actor=self.user
        )
        return Response(PriceListSerializer(saved).data)


@method_decorator(never_cache, name="dispatch")
class VariantPricesView(CatalogStaffView):
    def _response(self, variant: ProductVariant) -> Response:
        prices = Price.objects.filter(variant=variant).order_by("created_at", "pk")
        return Response(
            VariantPricesSerializer(
                {"variant": variant.pk, "prices": prices},
                context={"show_costs": self.can_see_costs},
            ).data
        )

    @extend_schema(
        operation_id="manage_variant_prices", tags=TAGS, responses={200: VariantPricesSerializer}
    )
    def get(self, request: Request, variant_id: uuid.UUID) -> Response:
        return self._response(found(ProductVariant, pk=variant_id))

    @extend_schema(
        operation_id="manage_variant_prices_set",
        tags=TAGS,
        request=VariantPricesInputSerializer,
        responses={200: VariantPricesSerializer},
        description="Create or update prices atomically. Entries omitting cost_amount keep it.",
    )
    def put(self, request: Request, variant_id: uuid.UUID) -> Response:
        variant = found(ProductVariant, pk=variant_id)
        data = VariantPricesInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.set_prices(
            variant=variant,
            entries=data.validated_data["prices"],
            actor=self.user,
            allow_cost=self.can_see_costs,
        )
        return self._response(variant)


class VariantPriceDetailView(CatalogStaffView):
    @extend_schema(operation_id="manage_variant_price_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, variant_id: uuid.UUID, price_list_id: uuid.UUID) -> Response:
        variant = found(ProductVariant, pk=variant_id)
        price_list = found(PriceList, pk=price_list_id)
        services.remove_price(variant=variant, price_list=price_list, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
