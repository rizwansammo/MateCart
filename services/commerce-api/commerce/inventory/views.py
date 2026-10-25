"""Merchant inventory API under /api/manage/inventory/ (staff realm, ``catalog.manage``).

Stock changes are operations (receipt, adjustment, transfer, reversal) that append ledger
movements; levels are never edited directly. A replayed idempotent request answers 200 with the
original operation; a newly applied one answers 201.
"""

import uuid
from typing import Any

from django.db.models import F, QuerySet
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.core.domain import found
from commerce.core.pagination import PageQuerySerializer, paginate, paginated
from commerce.staff.views import CatalogStaffView

from . import services
from .models import Batch, SerialNumber, StockLevel, StockLocation, StockMovement
from .serializers import (
    AdjustmentSerializer,
    BatchQuerySerializer,
    BatchSerializer,
    LevelQuerySerializer,
    LevelUpdateSerializer,
    LocationSerializer,
    LocationWriteSerializer,
    MovementQuerySerializer,
    MovementSerializer,
    OperationSerializer,
    ReceiptSerializer,
    ReconciliationSerializer,
    ReversalSerializer,
    SerialQuerySerializer,
    SerialSerializer,
    StockLevelSerializer,
    TransferSerializer,
)

TAGS = ["manage-inventory"]


def _valid(serializer_class: Any, data: Any) -> dict[str, Any]:
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    validated: dict[str, Any] = dict(serializer.validated_data)
    return validated


def _request(view: CatalogStaffView, data: dict[str, Any]) -> services.StockRequest:
    return services.StockRequest(
        reason=data.get("reason", ""),
        reference_type=data.get("reference_type", ""),
        reference_id=data.get("reference_id", ""),
        idempotency_key=data.get("idempotency_key"),
        actor=view.user,
    )


def _batch(data: dict[str, Any] | None) -> services.BatchInput | None:
    if not data:
        return None
    return services.BatchInput(
        code=data["code"],
        expires_on=data.get("expires_on"),
        manufactured_on=data.get("manufactured_on"),
        supplier_reference=data.get("supplier_reference", ""),
    )


def _operation_response(operation: services.Operation) -> Response:
    movements = (
        StockMovement.objects.filter(pk__in=[m.pk for m in operation.movements])
        .select_related("variant", "location", "batch", "serial")
        .order_by("occurred_at", "pk")
    )
    payload = {
        "operation_id": operation.operation_id,
        "replayed": operation.replayed,
        "movements": movements,
    }
    code = status.HTTP_200_OK if operation.replayed else status.HTTP_201_CREATED
    return Response(OperationSerializer(payload).data, status=code)


# --- Locations ------------------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class LocationListView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_locations",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(LocationSerializer)},
    )
    def get(self, request: Request) -> Response:
        services.default_location()
        locations = StockLocation.objects.order_by("-is_default", "code", "pk")
        items, meta = paginate(locations, request)
        return Response({**meta, "results": LocationSerializer(items, many=True).data})

    @extend_schema(
        operation_id="manage_inventory_location_create",
        tags=TAGS,
        request=LocationWriteSerializer,
        responses={201: LocationSerializer},
    )
    def post(self, request: Request) -> Response:
        location = services.save_location(
            location=None, data=_valid(LocationWriteSerializer, request.data), actor=self.user
        )
        return Response(LocationSerializer(location).data, status=status.HTTP_201_CREATED)


class LocationDetailView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_location_update",
        tags=TAGS,
        request=LocationWriteSerializer,
        responses={200: LocationSerializer},
    )
    def patch(self, request: Request, location_id: uuid.UUID) -> Response:
        location = found(StockLocation, pk=location_id)
        serializer = LocationWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        saved = services.save_location(
            location=location, data=dict(serializer.validated_data), actor=self.user
        )
        return Response(LocationSerializer(saved).data)


# --- Levels and ledger ----------------------------------------------------------------------------


def _levels() -> QuerySet[StockLevel, StockLevel]:
    return StockLevel.objects.select_related("variant__product", "location")


@method_decorator(never_cache, name="dispatch")
class LevelListView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_levels",
        tags=TAGS,
        parameters=[LevelQuerySerializer, PageQuerySerializer],
        responses={200: paginated(StockLevelSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(LevelQuerySerializer, request.query_params)
        levels = _levels()
        if params.get("variant"):
            levels = levels.filter(variant_id=params["variant"])
        if params.get("product"):
            levels = levels.filter(variant__product_id=params["product"])
        if params.get("location"):
            levels = levels.filter(location_id=params["location"])
        if params.get("low_stock"):
            levels = levels.filter(
                low_stock_threshold__isnull=False, available__lte=F("low_stock_threshold")
            )
        if params.get("out_of_stock"):
            levels = levels.filter(available__lte=0)
        items, meta = paginate(levels.order_by("available", "variant__sku", "pk"), request)
        return Response({**meta, "results": StockLevelSerializer(items, many=True).data})


class LevelDetailView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_level_update",
        tags=TAGS,
        request=LevelUpdateSerializer,
        responses={200: StockLevelSerializer},
        description="Only the low-stock threshold is editable; quantities change by operations.",
    )
    def patch(self, request: Request, level_id: uuid.UUID) -> Response:
        level = found(StockLevel, pk=level_id)
        data = _valid(LevelUpdateSerializer, request.data)
        services.update_level_settings(
            level=level, low_stock_threshold=data["low_stock_threshold"], actor=self.user
        )
        return Response(StockLevelSerializer(_levels().get(pk=level.pk)).data)


@method_decorator(never_cache, name="dispatch")
class MovementListView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_movements",
        tags=TAGS,
        parameters=[MovementQuerySerializer, PageQuerySerializer],
        responses={200: paginated(MovementSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(MovementQuerySerializer, request.query_params)
        movements = StockMovement.objects.select_related("variant", "location", "batch", "serial")
        for field, column in (
            ("variant", "variant_id"),
            ("location", "location_id"),
            ("operation", "operation_id"),
            ("movement_type", "movement_type"),
        ):
            if params.get(field):
                movements = movements.filter(**{column: params[field]})
        items, meta = paginate(movements.order_by("-occurred_at", "-pk"), request)
        return Response({**meta, "results": MovementSerializer(items, many=True).data})


# --- Operations -----------------------------------------------------------------------------------


class ReceiptView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_receive",
        tags=TAGS,
        request=ReceiptSerializer,
        responses={201: OperationSerializer, 200: OperationSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _valid(ReceiptSerializer, request.data)
        operation = services.receive(
            variant=data["variant"],
            location=data.get("location") or services.default_location(),
            quantity=data["quantity"],
            batch=_batch(data.get("batch")),
            serials=data.get("serials", []),
            request=_request(self, data),
        )
        return _operation_response(operation)


class AdjustmentView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_adjust",
        tags=TAGS,
        request=AdjustmentSerializer,
        responses={201: OperationSerializer, 200: OperationSerializer},
        description="Count corrections (signed quantity), damaged, lost or returned stock.",
    )
    def post(self, request: Request) -> Response:
        data = _valid(AdjustmentSerializer, request.data)
        operation = services.adjust(
            variant=data["variant"],
            location=data.get("location") or services.default_location(),
            quantity=data["quantity"],
            movement_type=data["movement_type"],
            batch=_batch(data.get("batch")),
            serials=data.get("serials", []),
            request=_request(self, data),
        )
        return _operation_response(operation)


class TransferView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_transfer",
        tags=TAGS,
        request=TransferSerializer,
        responses={201: OperationSerializer, 200: OperationSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _valid(TransferSerializer, request.data)
        operation = services.transfer(
            variant=data["variant"],
            source=data["from_location"],
            destination=data["to_location"],
            quantity=data["quantity"],
            batch_code=data.get("batch"),
            serials=data.get("serials", []),
            request=_request(self, data),
        )
        return _operation_response(operation)


class ReversalView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_reverse",
        tags=TAGS,
        request=ReversalSerializer,
        responses={201: OperationSerializer, 200: OperationSerializer},
        description="Reverse the whole operation this movement belongs to.",
    )
    def post(self, request: Request, movement_id: uuid.UUID) -> Response:
        movement = found(StockMovement, pk=movement_id)
        data = _valid(ReversalSerializer, request.data)
        operation = services.reverse(movement=movement, request=_request(self, data))
        return _operation_response(operation)


# --- Batches, serials, reconciliation -------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class BatchListView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_batches",
        tags=TAGS,
        parameters=[BatchQuerySerializer, PageQuerySerializer],
        responses={200: paginated(BatchSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(BatchQuerySerializer, request.query_params)
        batches = Batch.objects.select_related("variant").prefetch_related(
            # Prefetch reads through the scoped manager: only this store's batch levels.
            "levels__location"
        )
        if params.get("variant"):
            batches = batches.filter(variant_id=params["variant"])
        if params.get("expires_before"):
            batches = batches.filter(expires_on__lt=params["expires_before"])
        ordered = batches.order_by(F("expires_on").asc(nulls_last=True), "code", "pk")
        items, meta = paginate(ordered, request)
        return Response({**meta, "results": BatchSerializer(items, many=True).data})


@method_decorator(never_cache, name="dispatch")
class SerialListView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_serials",
        tags=TAGS,
        parameters=[SerialQuerySerializer, PageQuerySerializer],
        responses={200: paginated(SerialSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _valid(SerialQuerySerializer, request.query_params)
        serials = SerialNumber.objects.select_related("variant")
        for field, column in (
            ("variant", "variant_id"),
            ("location", "location_id"),
            ("status", "status"),
        ):
            if params.get(field):
                serials = serials.filter(**{column: params[field]})
        if params.get("q"):
            serials = serials.filter(serial__icontains=params["q"].strip())
        items, meta = paginate(serials.order_by("serial", "pk"), request)
        return Response({**meta, "results": SerialSerializer(items, many=True).data})


@method_decorator(never_cache, name="dispatch")
class ReconciliationView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_inventory_reconciliation",
        tags=TAGS,
        responses={200: ReconciliationSerializer},
        description="Checks that every stock level equals the sum of its ledger movements.",
    )
    def get(self, request: Request) -> Response:
        problems = services.reconcile()
        return Response(
            ReconciliationSerializer(
                {"consistent": not problems, "discrepancies": problems[:100]}
            ).data
        )
