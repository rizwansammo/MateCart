"""Merchant dashboard and sales report endpoints (D-078). Figures come from ``reports``."""

from typing import Any

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.identity.auth import HasStaffPermission
from commerce.inventory.models import StockLevel
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView

from . import reports
from .serializers import OrderSummarySerializer

TAG = ["manage-reports"]
CanViewReports = HasStaffPermission.of(Permission.REPORTS_VIEW)


def _money(**kwargs: Any) -> serializers.DecimalField:
    return serializers.DecimalField(max_digits=19, decimal_places=4, **kwargs)


class RangeQuerySerializer(serializers.Serializer[Any]):
    start = serializers.DateField(required=False, help_text="Store-local date, inclusive.")
    end = serializers.DateField(required=False, help_text="Store-local date, inclusive.")
    days = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=reports.MAX_RANGE_DAYS,
        help_text="The last N store days including today, when start and end are omitted.",
    )


class FinanceRowSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    order_value = _money(help_text="Grand total of orders placed in the range, not cancelled.")
    collected = _money(help_text="Verified payments and COD collections in the range.")
    refunded = _money(help_text="Succeeded refunds in the range.")
    net_collected = _money()


class CodRowSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    awaiting_collection = _money(help_text="COD still to be collected on open orders (now).")
    courier_remittance_pending = _money(help_text="Courier-collected cash not yet remitted.")
    courier_remittance_disputed = _money(help_text="Shortfall on disputed remittances.")


class CashPositionSerializer(serializers.Serializer[Any]):
    cod = CodRowSerializer(many=True)
    awaiting_collection_orders = serializers.IntegerField()
    courier_records_pending = serializers.IntegerField()
    courier_records_disputed = serializers.IntegerField()


class FinanceSerializer(serializers.Serializer[Any]):
    totals = FinanceRowSerializer(many=True)
    cash = CashPositionSerializer()


class OrderCountsSerializer(serializers.Serializer[Any]):
    total = serializers.IntegerField()
    placed = serializers.IntegerField()
    pending_payment = serializers.IntegerField()
    confirmed = serializers.IntegerField()
    processing = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    delivered = serializers.IntegerField(help_text="Shipments delivered in the range.")


class OperationsSerializer(serializers.Serializer[Any]):
    awaiting_confirmation = serializers.IntegerField()
    to_prepare = serializers.IntegerField()
    pending_shipments = serializers.IntegerField()
    in_transit = serializers.IntegerField()
    delivery_failed = serializers.IntegerField()
    fraud_review = serializers.IntegerField()


class AftersalesCountsSerializer(serializers.Serializer[Any]):
    open_returns = serializers.IntegerField()
    open_exchanges = serializers.IntegerField()
    open_warranty = serializers.IntegerField()
    open_support = serializers.IntegerField()
    pending_reviews = serializers.IntegerField()


class LowStockItemSerializer(serializers.Serializer[StockLevel]):
    id = serializers.UUIDField()
    variant = serializers.UUIDField(source="variant_id")
    sku = serializers.CharField(source="variant.sku")
    product_name = serializers.CharField(source="variant.product.name")
    variant_title = serializers.CharField(source="variant.title")
    location_code = serializers.CharField(source="location.code")
    on_hand = serializers.DecimalField(
        max_digits=14, decimal_places=3, help_text="Physical stock, including expired batches."
    )
    available = serializers.DecimalField(
        max_digits=14, decimal_places=3, help_text="On hand minus reserved, before expiry."
    )
    sellable = serializers.DecimalField(
        max_digits=14,
        decimal_places=3,
        help_text="What can be promised: expired batches excluded, reservations deducted.",
    )
    low_stock_threshold = serializers.DecimalField(max_digits=14, decimal_places=3)


class InventorySummarySerializer(serializers.Serializer[Any]):
    low_stock = serializers.IntegerField()
    out_of_stock = serializers.IntegerField()
    expiring_batches = serializers.IntegerField(help_text="Batches in stock expiring in 30 days.")
    low_stock_items = LowStockItemSerializer(many=True)


class OrderBriefSerializer(OrderSummarySerializer):
    class Meta(OrderSummarySerializer.Meta):
        fields = (*OrderSummarySerializer.Meta.fields, "source", "recipient_name")
        read_only_fields = fields


class DashboardSerializer(serializers.Serializer[Any]):
    """Sections the staff member's role does not cover are null."""

    start = serializers.DateField()
    end = serializers.DateField()
    orders = OrderCountsSerializer(allow_null=True)
    operations = OperationsSerializer(allow_null=True)
    aftersales = AftersalesCountsSerializer(allow_null=True)
    recent_orders = OrderBriefSerializer(many=True, allow_null=True)
    finance = FinanceSerializer(allow_null=True)
    inventory = InventorySummarySerializer(allow_null=True)


class KeyCountSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    orders = serializers.IntegerField()


class GroupedValueSerializer(KeyCountSerializer):
    currency = serializers.CharField()
    value = _money()


class SummaryRowSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    order_value = _money()
    average_order_value = _money()


class CurrencyCountSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    orders = serializers.IntegerField()


class DailyRowSerializer(serializers.Serializer[Any]):
    date = serializers.DateField()
    currency = serializers.CharField()
    orders = serializers.IntegerField()
    value = _money()


class TopProductSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField()
    sku = serializers.CharField()
    product_name = serializers.CharField()
    currency = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    revenue = _money()
    orders = serializers.IntegerField()


class CaseCountSerializer(serializers.Serializer[Any]):
    kind = serializers.CharField()
    status = serializers.CharField()
    cases = serializers.IntegerField()


class StockCountsSerializer(serializers.Serializer[Any]):
    low_stock = serializers.IntegerField()
    out_of_stock = serializers.IntegerField()


class SalesReportSerializer(serializers.Serializer[Any]):
    start = serializers.DateField()
    end = serializers.DateField()
    summary = SummaryRowSerializer(many=True)
    order_counts = CurrencyCountSerializer(many=True)
    totals = FinanceRowSerializer(many=True)
    cash = CashPositionSerializer()
    by_status = KeyCountSerializer(many=True)
    by_payment_method = GroupedValueSerializer(many=True)
    by_source = GroupedValueSerializer(many=True)
    daily = DailyRowSerializer(many=True)
    top_products = TopProductSerializer(many=True)
    aftersales = CaseCountSerializer(many=True)
    inventory = StockCountsSerializer()


def _range(request: Request) -> tuple[Any, Any]:
    params = RangeQuerySerializer(data=request.query_params)
    params.is_valid(raise_exception=True)
    values = params.validated_data
    return reports.date_range(values.get("start"), values.get("end"), values.get("days"))


@method_decorator(never_cache, name="dispatch")
class DashboardView(StaffView):
    """Any active staff member; each section is gated by the permission that owns its data."""

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="manage_dashboard",
        tags=TAG,
        parameters=[RangeQuerySerializer],
        responses={200: DashboardSerializer},
    )
    def get(self, request: Request) -> Response:
        start, end = _range(request)
        data = reports.dashboard(permissions=self.staff.permissions, start=start, end=end)
        return Response(DashboardSerializer(data).data)


@method_decorator(never_cache, name="dispatch")
class SalesReportView(StaffView):
    """``reports.view`` (Owner, Manager)."""

    permission_classes = (IsAuthenticated, CanViewReports)

    @extend_schema(
        operation_id="manage_sales_report",
        tags=TAG,
        parameters=[RangeQuerySerializer],
        responses={200: SalesReportSerializer},
    )
    def get(self, request: Request) -> Response:
        start, end = _range(request)
        return Response(SalesReportSerializer(reports.sales_report(start=start, end=end)).data)
