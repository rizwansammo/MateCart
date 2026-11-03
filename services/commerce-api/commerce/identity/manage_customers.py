"""Merchant view of the store's customer accounts (``customers.view``, Phase 11).

Read-only and deliberately narrow: profile fields the store needs to serve the customer, the
customer's own orders and after-sales requests. Password hashes, MFA state, recovery data,
sessions and tokens are never exposed, staff cannot act as the customer, and guest or
merchant-assisted orders are never attached to an account by matching an email or phone.
"""

from typing import Any

from django.db.models import Count, Max, Q
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.aftersales.models import AfterSalesCase
from commerce.core.pagination import paginate, paginated
from commerce.orders.models import Order
from commerce.orders.serializers import OrderSummarySerializer, RelatedCaseSerializer
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView

from .auth import HasStaffPermission
from .models import AccountType, User

TAG = ["manage-customers"]
CanViewCustomers = HasStaffPermission.of(Permission.CUSTOMERS_VIEW)
HISTORY_LIMIT = 20


class CustomerQuerySerializer(serializers.Serializer[Any]):
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)


class CustomerSummarySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    email = serializers.EmailField(allow_null=True)
    phone = serializers.CharField(allow_null=True)
    email_verified = serializers.SerializerMethodField()
    phone_verified = serializers.SerializerMethodField()
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    order_count = serializers.IntegerField()
    last_order_at = serializers.DateTimeField(allow_null=True)

    def get_email_verified(self, user: User) -> bool:
        return user.email_verified_at is not None

    def get_phone_verified(self, user: User) -> bool:
        return user.phone_verified_at is not None


class CustomerDetailSerializer(CustomerSummarySerializer):
    orders = serializers.SerializerMethodField()
    cases = serializers.SerializerMethodField()

    @extend_schema_field(OrderSummarySerializer(many=True))
    def get_orders(self, user: User) -> list[dict[str, Any]]:
        rows = Order.objects.filter(customer=user).order_by("-placed_at", "-pk")[:HISTORY_LIMIT]
        return list(OrderSummarySerializer(rows, many=True).data)

    @extend_schema_field(RelatedCaseSerializer(many=True))
    def get_cases(self, user: User) -> list[dict[str, Any]]:
        rows = AfterSalesCase.objects.filter(customer=user).order_by("-created_at", "-pk")[
            :HISTORY_LIMIT
        ]
        return list(RelatedCaseSerializer(rows, many=True).data)


def _customers() -> Any:
    return User.objects.filter(account_type=AccountType.CUSTOMER).annotate(
        order_count=Count("orders"), last_order_at=Max("orders__placed_at")
    )


@method_decorator(never_cache, name="dispatch")
class CustomerStaffView(StaffView):
    permission_classes = (IsAuthenticated, CanViewCustomers)


class ManageCustomerListView(CustomerStaffView):
    @extend_schema(
        operation_id="manage_customer_list",
        tags=TAG,
        parameters=[CustomerQuerySerializer],
        responses={200: paginated(CustomerSummarySerializer)},
    )
    def get(self, request: Request) -> Response:
        params = CustomerQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        rows = _customers()
        text = (params.validated_data.get("q") or "").strip()
        if text:
            rows = rows.filter(
                Q(full_name__icontains=text) | Q(email__icontains=text) | Q(phone__icontains=text)
            )
        items, meta = paginate(rows.order_by("-created_at", "-pk"), request)
        return Response({**meta, "results": CustomerSummarySerializer(items, many=True).data})


class ManageCustomerView(CustomerStaffView):
    @extend_schema(
        operation_id="manage_customer_get", tags=TAG, responses={200: CustomerDetailSerializer}
    )
    def get(self, request: Request, customer_id: Any) -> Response:
        customer = _customers().filter(pk=customer_id).first()
        if customer is None:
            raise NotFound("This customer does not exist.")
        return Response(CustomerDetailSerializer(customer).data)
