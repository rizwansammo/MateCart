"""Customer/guest order endpoints, the provider callback and merchant order actions."""

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

from commerce.checkout.views import CheckoutOwnerMixin, StorefrontAPIView
from commerce.core.pagination import paginate, paginated
from commerce.identity import sessions
from commerce.identity.auth import HasStaffPermission
from commerce.identity.models import User
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView

from . import payments, services
from .models import Invoice, Order
from .serializers import (
    CallbackResultSerializer,
    InvoiceSerializer,
    MerchantOrderSerializer,
    OrderSerializer,
    OrderSummarySerializer,
    PaymentStartSerializer,
    PlaceOrderSerializer,
    RefundInputSerializer,
    RefundSerializer,
    TransitionSerializer,
)

ORDER_TAG = ["orders"]
MANAGE_TAG = ["manage-orders"]
CanManageOrders = HasStaffPermission.of(Permission.ORDERS_MANAGE)


def _data(serializer: Any) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _actor(request: Request) -> User | None:
    return request.user if isinstance(request.user, User) else None


@method_decorator(never_cache, name="dispatch")
class PlaceOrderView(CheckoutOwnerMixin, StorefrontAPIView):
    @extend_schema(
        operation_id="checkout_place_order",
        tags=ORDER_TAG,
        request=PlaceOrderSerializer,
        responses={201: OrderSerializer, 200: OrderSerializer},
    )
    def post(self, request: Request, session_id: Any) -> Response:
        values = _data(PlaceOrderSerializer(data=request.data))
        placement = services.place_order(
            session=self.session(request, session_id),
            payment_method=values["payment_method"],
            idempotency_key=values["idempotency_key"],
            actor=_actor(request),
        )
        response = Response(
            OrderSerializer(placement.order).data,
            status=status.HTTP_200_OK if placement.replayed else status.HTTP_201_CREATED,
        )
        services.set_order_cookie(response, placement)
        return response


@method_decorator(never_cache, name="dispatch")
class CustomerOrderListView(StorefrontAPIView):
    """Only orders bound to the signed-in customer; guest tokens cannot list orders."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="customer_order_list",
        tags=ORDER_TAG,
        responses={200: paginated(OrderSummarySerializer)},
    )
    def get(self, request: Request) -> Response:
        user = request.user
        if not isinstance(user, User):
            raise NotFound("Customer account not found.")
        items, meta = paginate(
            Order.objects.filter(customer=user).order_by("-placed_at", "-pk"),
            request,
        )
        return Response({**meta, "results": OrderSummarySerializer(items, many=True).data})


class OrderOwnerMixin:
    def order(self, request: Request, order_id: Any) -> Order:
        return services.owned_order(
            order_id=order_id,
            user=request.user,
            order_token=request.COOKIES.get(services.ORDER_COOKIE),
        )


@method_decorator(never_cache, name="dispatch")
class OrderView(OrderOwnerMixin, StorefrontAPIView):
    @extend_schema(operation_id="order_get", tags=ORDER_TAG, responses={200: OrderSerializer})
    def get(self, request: Request, order_id: Any) -> Response:
        return Response(OrderSerializer(self.order(request, order_id)).data)


@method_decorator(never_cache, name="dispatch")
class OrderCancelView(OrderOwnerMixin, StorefrontAPIView):
    @extend_schema(
        operation_id="order_cancel", tags=ORDER_TAG, request=None, responses={200: OrderSerializer}
    )
    def post(self, request: Request, order_id: Any) -> Response:
        order = services.customer_cancel(order=self.order(request, order_id), actor=_actor(request))
        return Response(OrderSerializer(order).data)


@method_decorator(never_cache, name="dispatch")
class OrderPaymentView(OrderOwnerMixin, StorefrontAPIView):
    @extend_schema(
        operation_id="order_payment_start",
        tags=ORDER_TAG,
        request=None,
        responses={200: PaymentStartSerializer},
    )
    def post(self, request: Request, order_id: Any) -> Response:
        intent = payments.start_online_payment(order=self.order(request, order_id))
        return Response({"redirect_url": intent.redirect_url})


@method_decorator(never_cache, name="dispatch")
class OrderInvoiceView(OrderOwnerMixin, StorefrontAPIView):
    @extend_schema(operation_id="order_invoice", tags=ORDER_TAG, responses={200: InvoiceSerializer})
    def get(self, request: Request, order_id: Any) -> Response:
        order = self.order(request, order_id)
        invoice = Invoice.objects.select_related("order").get(order=order)
        return Response(InvoiceSerializer(invoice).data)


@method_decorator(never_cache, name="dispatch")
class PaymentCallbackView(APIView):
    """Server-to-server provider callbacks. Anonymous by design (reviewed opt-in): the provider
    adapter authenticates every request by signature before anything is read or changed."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="payment_callback",
        tags=ORDER_TAG,
        request=None,
        responses={200: CallbackResultSerializer},
    )
    def post(self, request: Request, provider: str) -> Response:
        outcome = payments.process_callback(
            provider_code=provider, body=request.body, headers=request.headers
        )
        return Response({"outcome": outcome})


# --- Merchant -------------------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class OrdersStaffView(StaffView):
    """Order actions: ``orders.manage`` (Owner, Manager, Order handler)."""

    permission_classes = (IsAuthenticated, CanManageOrders)

    def target(self, order_id: Any) -> Order:
        order = Order.objects.filter(pk=order_id).first()
        if order is None:
            raise NotFound("This order does not exist.")
        return order


class ManageOrderListView(OrdersStaffView):
    @extend_schema(
        operation_id="manage_order_list",
        tags=MANAGE_TAG,
        responses={200: paginated(OrderSummarySerializer)},
    )
    def get(self, request: Request) -> Response:
        items, meta = paginate(Order.objects.order_by("-placed_at", "-pk"), request)
        return Response({**meta, "results": OrderSummarySerializer(items, many=True).data})


class ManageOrderView(OrdersStaffView):
    @extend_schema(
        operation_id="manage_order_get", tags=MANAGE_TAG, responses={200: MerchantOrderSerializer}
    )
    def get(self, request: Request, order_id: Any) -> Response:
        return Response(MerchantOrderSerializer(self.target(order_id)).data)


class ManageOrderTransitionView(OrdersStaffView):
    @extend_schema(
        operation_id="manage_order_transition",
        tags=MANAGE_TAG,
        request=TransitionSerializer,
        responses={200: MerchantOrderSerializer},
    )
    def post(self, request: Request, order_id: Any) -> Response:
        values = _data(TransitionSerializer(data=request.data))
        order = services.merchant_transition(
            order_id=self.target(order_id).pk,
            to_status=values["status"],
            actor=self.user,
            reason=values.get("reason", ""),
        )
        return Response(MerchantOrderSerializer(order).data)


class ManageCodCollectionView(OrdersStaffView):
    @extend_schema(
        operation_id="manage_order_cod_collected",
        tags=MANAGE_TAG,
        request=None,
        responses={200: MerchantOrderSerializer},
    )
    def post(self, request: Request, order_id: Any) -> Response:
        order = payments.record_cod_collection(order_id=self.target(order_id).pk, actor=self.user)
        return Response(MerchantOrderSerializer(order).data)


class ManageRefundView(OrdersStaffView):
    """Money leaves the store: also needs a recent sign-in or password confirmation."""

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        sessions.require_recent_authentication(self.staff.session)

    @extend_schema(
        operation_id="manage_order_refund",
        tags=MANAGE_TAG,
        request=RefundInputSerializer,
        responses={200: RefundSerializer},
    )
    def post(self, request: Request, order_id: Any) -> Response:
        values = _data(RefundInputSerializer(data=request.data))
        refund = payments.request_refund(
            order_id=self.target(order_id).pk,
            amount=values["amount"],
            reason=values["reason"],
            idempotency_key=values["idempotency_key"],
            actor=self.user,
        )
        return Response(RefundSerializer(refund).data)
