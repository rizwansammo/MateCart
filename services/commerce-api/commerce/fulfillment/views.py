"""Restricted merchant fulfillment APIs and authenticated courier callbacks."""

from typing import Any, cast

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commerce.core.pagination import paginate, paginated
from commerce.identity import sessions
from commerce.identity.auth import HasStaffPermission
from commerce.orders.models import Order
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView

from . import services
from .models import CourierCodRecord, FraudAssessment, Shipment
from .serializers import (
    CallbackOutcomeSerializer,
    CodCollectionInputSerializer,
    CodRecordSerializer,
    CodRemittanceInputSerializer,
    CourierBookingInputSerializer,
    CourierBookingSerializer,
    FraudAssessmentSerializer,
    FraudReviewInputSerializer,
    ManualCourierSerializer,
    MerchantShipmentSerializer,
    PrepareShipmentSerializer,
    ShipmentSummarySerializer,
    ShipmentTransitionSerializer,
)

MANAGE_TAG = ["manage-fulfillment"]
CALLBACK_TAG = ["courier-callbacks"]
CanManageCouriers = HasStaffPermission.of(Permission.COURIERS_MANAGE)
CanManageOrders = HasStaffPermission.of(Permission.ORDERS_MANAGE)


def _data(serializer: Any) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


@method_decorator(never_cache, name="dispatch")
class FulfillmentStaffView(StaffView):
    permission_classes = (IsAuthenticated, CanManageCouriers)

    def shipment(self, shipment_id: Any) -> Shipment:
        shipment = Shipment.objects.select_related("order").filter(pk=shipment_id).first()
        if shipment is None:
            raise NotFound("This shipment does not exist.")
        return shipment


class ManageShipmentListView(FulfillmentStaffView):
    @extend_schema(
        operation_id="manage_shipment_list",
        tags=MANAGE_TAG,
        responses={200: paginated(ShipmentSummarySerializer)},
    )
    def get(self, request: Request) -> Response:
        queryset = Shipment.objects.select_related("order").order_by("-created_at", "-pk")
        items, meta = paginate(queryset, request)
        return Response({**meta, "results": ShipmentSummarySerializer(items, many=True).data})


class ManageShipmentView(FulfillmentStaffView):
    @extend_schema(
        operation_id="manage_shipment_get",
        tags=MANAGE_TAG,
        responses={200: MerchantShipmentSerializer},
    )
    def get(self, request: Request, shipment_id: Any) -> Response:
        return Response(MerchantShipmentSerializer(self.shipment(shipment_id)).data)


class ManagePrepareShipmentView(FulfillmentStaffView):
    @extend_schema(
        operation_id="manage_shipment_prepare",
        tags=MANAGE_TAG,
        request=PrepareShipmentSerializer,
        responses={201: MerchantShipmentSerializer, 200: MerchantShipmentSerializer},
    )
    def post(self, request: Request, order_id: Any) -> Response:
        values = _data(PrepareShipmentSerializer(data=request.data))
        existed = Shipment.objects.filter(order_id=order_id).exists()
        shipment = services.prepare_shipment(
            order_id=order_id,
            actor=self.user,
            operational_notes=values.get("operational_notes", ""),
        )
        return Response(
            MerchantShipmentSerializer(shipment).data,
            status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED,
        )


class ManageShipmentTransitionView(FulfillmentStaffView):
    @extend_schema(
        operation_id="manage_shipment_transition",
        tags=MANAGE_TAG,
        request=ShipmentTransitionSerializer,
        responses={200: MerchantShipmentSerializer},
    )
    def post(self, request: Request, shipment_id: Any) -> Response:
        self.shipment(shipment_id)
        values = _data(ShipmentTransitionSerializer(data=request.data))
        shipment = services.transition_shipment(
            shipment_id=shipment_id,
            to_status=values["status"],
            actor=self.user,
            occurred_at=values.get("occurred_at"),
            note=values.get("note", ""),
            serials=values.get("serials"),
        )
        return Response(MerchantShipmentSerializer(shipment).data)


class ManageManualCourierView(FulfillmentStaffView):
    @extend_schema(
        operation_id="manage_shipment_manual_courier",
        tags=MANAGE_TAG,
        request=ManualCourierSerializer,
        responses={200: MerchantShipmentSerializer},
    )
    def post(self, request: Request, shipment_id: Any) -> Response:
        self.shipment(shipment_id)
        values = _data(ManualCourierSerializer(data=request.data))
        shipment = services.update_manual_details(
            shipment_id=shipment_id,
            actor=self.user,
            courier_name=values["courier_name"],
            tracking_number=values.get("tracking_number", ""),
            tracking_url=values.get("tracking_url", ""),
        )
        return Response(MerchantShipmentSerializer(shipment).data)


class ManageCourierBookingView(FulfillmentStaffView):
    @extend_schema(
        operation_id="manage_shipment_courier_booking",
        tags=MANAGE_TAG,
        request=CourierBookingInputSerializer,
        responses={200: CourierBookingSerializer},
    )
    def post(self, request: Request, shipment_id: Any) -> Response:
        self.shipment(shipment_id)
        values = _data(CourierBookingInputSerializer(data=request.data))
        booking = services.book_courier(
            shipment_id=shipment_id,
            idempotency_key=values["idempotency_key"],
            actor=self.user,
        )
        return Response(CourierBookingSerializer(booking).data)


class ManageCourierBookingCancelView(FulfillmentStaffView):
    @extend_schema(
        operation_id="manage_shipment_courier_booking_cancel",
        tags=MANAGE_TAG,
        request=None,
        responses={200: CourierBookingSerializer},
    )
    def post(self, request: Request, shipment_id: Any) -> Response:
        self.shipment(shipment_id)
        booking = services.cancel_courier_booking(shipment_id=shipment_id, actor=self.user)
        return Response(CourierBookingSerializer(booking).data)


class ManageCodCollectionView(FulfillmentStaffView):
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        sessions.require_recent_authentication(self.staff.session)

    @extend_schema(
        operation_id="manage_shipment_cod_collection",
        tags=MANAGE_TAG,
        request=CodCollectionInputSerializer,
        responses={200: CodRecordSerializer, 201: CodRecordSerializer},
    )
    def post(self, request: Request, shipment_id: Any) -> Response:
        self.shipment(shipment_id)
        values = _data(CodCollectionInputSerializer(data=request.data))
        existed = CourierCodRecord.objects.filter(shipment_id=shipment_id).exists()
        record = services.record_cod_collection(shipment_id=shipment_id, actor=self.user, **values)
        return Response(
            CodRecordSerializer(record).data,
            status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED,
        )


class ManageCodRemittanceView(FulfillmentStaffView):
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        sessions.require_recent_authentication(self.staff.session)

    @extend_schema(
        operation_id="manage_shipment_cod_remittance",
        tags=MANAGE_TAG,
        request=CodRemittanceInputSerializer,
        responses={200: CodRecordSerializer},
    )
    def post(self, request: Request, record_id: Any) -> Response:
        values = _data(CodRemittanceInputSerializer(data=request.data))
        record = services.reconcile_cod(record_id=record_id, actor=self.user, **values)
        return Response(CodRecordSerializer(record).data)


@method_decorator(never_cache, name="dispatch")
class FraudStaffView(StaffView):
    permission_classes = (IsAuthenticated, CanManageOrders)


class ManageFraudAssessmentView(FraudStaffView):
    @extend_schema(
        operation_id="manage_order_fraud_assessment",
        tags=MANAGE_TAG,
        request=None,
        responses={200: FraudAssessmentSerializer, 201: FraudAssessmentSerializer},
    )
    def post(self, request: Request, order_id: Any) -> Response:
        if not Order.objects.filter(pk=order_id).exists():
            raise NotFound("This order does not exist.")
        existed = FraudAssessment.objects.filter(order_id=order_id).exists()
        assessment = services.assess_order(order_id=order_id)
        return Response(
            FraudAssessmentSerializer(assessment).data,
            status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED,
        )


class ManageFraudReviewView(FraudStaffView):
    @extend_schema(
        operation_id="manage_order_fraud_review",
        tags=MANAGE_TAG,
        request=FraudReviewInputSerializer,
        responses={200: FraudAssessmentSerializer},
    )
    def post(self, request: Request, assessment_id: Any) -> Response:
        values = _data(FraudReviewInputSerializer(data=request.data))
        assessment = services.review_assessment(
            assessment_id=assessment_id, actor=self.user, **values
        )
        return Response(FraudAssessmentSerializer(assessment).data)


@method_decorator(never_cache, name="dispatch")
class CourierTrackingCallbackView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="courier_tracking_callback",
        tags=CALLBACK_TAG,
        request=None,
        responses={200: CallbackOutcomeSerializer},
    )
    def post(self, request: Request, provider: str) -> Response:
        outcome = services.process_tracking_callback(
            provider_code=provider, body=request.body, headers=request.headers
        )
        return Response({"outcome": outcome})
