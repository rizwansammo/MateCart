"""Private buyer library and merchant digital resource configuration."""

from typing import Any, cast
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework import serializers

from commerce.core.pagination import paginated
from commerce.identity.auth import CustomerSessionAuthentication, CsrfProtectedAPIView, HasStaffPermission
from commerce.identity.models import User
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView
from . import digital
from .models import DigitalProductAsset


class LibraryItemSerializer(serializers.Serializer[Any]):
    order_item = serializers.UUIDField()
    order_number = serializers.CharField()
    product_name = serializers.CharField()
    variant_title = serializers.CharField()
    purchased_at = serializers.DateTimeField()
    state = serializers.ChoiceField(choices=["ready", "payment_required", "awaiting_resource", "unavailable"])
    entitlement_id = serializers.UUIDField(allow_null=True)
    remaining_downloads = serializers.IntegerField()


class DigitalAssetInputSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField()
    asset = serializers.UUIDField()
    download_limit = serializers.IntegerField(min_value=1, max_value=100, default=10)


class DigitalAssetSerializer(serializers.ModelSerializer[DigitalProductAsset]):
    class Meta:
        model = DigitalProductAsset
        fields = ("id", "variant", "asset", "enabled", "download_limit")
        read_only_fields = fields


@method_decorator(never_cache, name="dispatch")
class BuyerLibraryView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(operation_id="buyer_digital_library", tags=["digital-library"], responses={200: LibraryItemSerializer(many=True)})
    def get(self, request: Request) -> Response:
        return Response(digital.library(cast(User, request.user)))


@method_decorator(never_cache, name="dispatch")
class DigitalDownloadView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(operation_id="buyer_digital_download", tags=["digital-library"], responses={(200, "application/octet-stream"): bytes})
    def get(self, request: Request, entitlement_id: Any) -> Any:
        return digital.download_for_customer(
            entitlement_id=entitlement_id, customer=cast(User, request.user),
        )


class ManageDigitalAssetsView(StaffView):
    permission_classes = (IsAuthenticated, HasStaffPermission.of(Permission.CATALOG_MANAGE))

    @extend_schema(operation_id="manage_digital_asset_assign", tags=["manage-digital"], request=DigitalAssetInputSerializer, responses={200: DigitalAssetSerializer})
    def post(self, request: Request) -> Response:
        params = DigitalAssetInputSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        data = params.validated_data
        mapping = digital.assign_asset(
            variant_id=data["variant"], asset_id=data["asset"],
            limit=data["download_limit"], actor=self.user,
        )
        return Response(DigitalAssetSerializer(mapping).data, status=status.HTTP_200_OK)
