"""Merchant dashboard and sales reports from the store's own records (D-078).

Every figure is read from authoritative rows, never from the browser: order values from placed
orders, collected money from verified payment transactions, refunds from succeeded refund
transactions and courier cash from COD records. Placing an order is not revenue: order value,
verified collections, refunds, cash still to collect on delivery and courier cash still to be
remitted are reported separately. Dates are calendar days in the store's time zone, ranges are
bounded (at most 366 days) and every query is an aggregate over indexed columns.
"""

import zoneinfo
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db.models import Count, F, QuerySet, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.aftersales.models import AfterSalesCase, CaseStatus, ProductReview, ReviewStatus
from commerce.fulfillment.models import (
    PRE_DISPATCH_STATUSES,
    CodReconciliationStatus,
    CourierCodRecord,
    FraudAssessment,
    FraudReviewStatus,
    Shipment,
    ShipmentStatus,
)
from commerce.inventory.models import BatchLevel, StockLevel
from commerce.pricing.services import currency_precision
from commerce.staff.permissions import Permission
from commerce.stores.models import StoreSettings

from .models import (
    IntentStatus,
    Order,
    OrderItem,
    OrderStatus,
    PaymentIntent,
    PaymentTransaction,
    TransactionKind,
    TransactionStatus,
)

MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30
RECENT_LIMIT = 8
TOP_PRODUCTS = 10
EXPIRY_WINDOW_DAYS = 30
CLOSED_CASE_STATUSES = (CaseStatus.REJECTED, CaseStatus.RESOLVED, CaseStatus.CLOSED)
COLLECTED_KINDS = (TransactionKind.PAYMENT, TransactionKind.COD_COLLECTION)


def store_zone() -> zoneinfo.ZoneInfo:
    name = StoreSettings.objects.values_list("timezone", flat=True).first() or "UTC"
    return zoneinfo.ZoneInfo(name)


def store_today() -> date:
    return timezone.now().astimezone(store_zone()).date()


def day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """``[start 00:00, end + 1 day 00:00)`` in the store's time zone."""
    zone = store_zone()
    return (
        datetime.combine(start, time.min, tzinfo=zone),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone),
    )


def date_range(start: date | None, end: date | None, days: int | None = None) -> tuple[date, date]:
    """The requested store-local range, at most 366 days. ``days`` alone means the last that
    many store days including today; with nothing given, the last 30 days."""
    last = end or store_today()
    first = start or last - timedelta(days=(days or DEFAULT_RANGE_DAYS) - 1)
    if first > last:
        raise ValidationError({"start": ["The start date must not be after the end date."]})
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise ValidationError({"start": [f"Choose a range of at most {MAX_RANGE_DAYS} days."]})
    return first, last


def _round(value: Decimal, currency: str) -> Decimal:
    quantum = Decimal(1).scaleb(-currency_precision(currency))
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _by_currency(rows: Iterable[dict[str, Any]], field: str = "total") -> dict[str, Decimal]:
    return {row["currency"]: row[field] or Decimal(0) for row in rows}


def _sum_by_currency(queryset: QuerySet[Any], amount: str) -> dict[str, Decimal]:
    return _by_currency(queryset.values("currency").annotate(total=Sum(amount)).order_by())


def _money_rows(*named: tuple[str, dict[str, Decimal]]) -> list[dict[str, Any]]:
    """One row per currency that appears in any figure, every figure present (zero if none)."""
    currencies = sorted({currency for _, values in named for currency in values})
    return [
        {
            "currency": currency,
            **{name: _round(values.get(currency, Decimal(0)), currency) for name, values in named},
        }
        for currency in currencies
    ]


def _placed(start: datetime, end: datetime) -> QuerySet[Order]:
    return Order.objects.filter(placed_at__gte=start, placed_at__lt=end)


def _collected(start: datetime, end: datetime) -> dict[str, Decimal]:
    rows = PaymentTransaction.objects.filter(
        kind__in=COLLECTED_KINDS,
        status=TransactionStatus.SUCCEEDED,
        occurred_at__gte=start,
        occurred_at__lt=end,
    )
    return _sum_by_currency(rows, "amount")


def _refunded(start: datetime, end: datetime) -> dict[str, Decimal]:
    rows = PaymentTransaction.objects.filter(
        kind=TransactionKind.REFUND,
        status=TransactionStatus.SUCCEEDED,
        occurred_at__gte=start,
        occurred_at__lt=end,
    )
    return _sum_by_currency(rows, "amount")


def cash_positions() -> dict[str, Any]:
    """Point-in-time cash not yet in the store's hands, whatever the date range."""
    awaiting = PaymentIntent.objects.filter(status=IntentStatus.AWAITING_COLLECTION).exclude(
        order__status=OrderStatus.CANCELLED
    )
    pending = CourierCodRecord.objects.filter(status=CodReconciliationStatus.PENDING)
    disputed = CourierCodRecord.objects.filter(status=CodReconciliationStatus.DISPUTED)
    disputed_gap = _by_currency(
        disputed.values("currency")
        .annotate(total=Sum(F("expected_remittance") - F("actual_remittance")))
        .order_by()
    )
    return {
        "cod": _money_rows(
            ("awaiting_collection", _sum_by_currency(awaiting, "amount")),
            ("courier_remittance_pending", _sum_by_currency(pending, "expected_remittance")),
            ("courier_remittance_disputed", disputed_gap),
        ),
        "awaiting_collection_orders": awaiting.count(),
        "courier_records_pending": pending.count(),
        "courier_records_disputed": disputed.count(),
    }


def _finance(start: datetime, end: datetime) -> dict[str, Any]:
    placed = _placed(start, end).exclude(status=OrderStatus.CANCELLED)
    order_value = _sum_by_currency(placed, "grand_total")
    collected = _collected(start, end)
    refunded = _refunded(start, end)
    net = {
        currency: collected.get(currency, Decimal(0)) - refunded.get(currency, Decimal(0))
        for currency in {*collected, *refunded}
    }
    cash = cash_positions()
    return {
        "totals": _money_rows(
            ("order_value", order_value),
            ("collected", collected),
            ("refunded", refunded),
            ("net_collected", net),
        ),
        "cash": cash,
    }


def _order_counts(start: datetime, end: datetime) -> dict[str, int]:
    placed = _placed(start, end)
    by_status = {
        row["status"]: row["n"]
        for row in placed.values("status").annotate(n=Count("pk")).order_by()
    }
    delivered = Shipment.objects.filter(
        status=ShipmentStatus.DELIVERED, delivered_at__gte=start, delivered_at__lt=end
    ).count()
    return {
        "total": sum(by_status.values()),
        "placed": by_status.get(OrderStatus.PLACED, 0),
        "pending_payment": by_status.get(OrderStatus.PENDING_PAYMENT, 0),
        "confirmed": by_status.get(OrderStatus.CONFIRMED, 0),
        "processing": by_status.get(OrderStatus.PROCESSING, 0),
        "cancelled": by_status.get(OrderStatus.CANCELLED, 0),
        "delivered": delivered,
    }


def _operations() -> dict[str, int]:
    """Point-in-time work queues."""
    shipments = Shipment.objects.values("status").annotate(n=Count("pk")).order_by()
    counts = {row["status"]: row["n"] for row in shipments}
    return {
        "awaiting_confirmation": Order.objects.filter(status=OrderStatus.PLACED).count(),
        "to_prepare": Order.objects.filter(
            status__in=(OrderStatus.CONFIRMED, OrderStatus.PROCESSING), requires_shipping=True
        )
        .exclude(pk__in=Shipment.objects.values("order_id"))
        .count(),
        "pending_shipments": sum(counts.get(status, 0) for status in PRE_DISPATCH_STATUSES),
        "in_transit": counts.get(ShipmentStatus.DISPATCHED, 0)
        + counts.get(ShipmentStatus.IN_TRANSIT, 0),
        "delivery_failed": counts.get(ShipmentStatus.DELIVERY_FAILED, 0),
        "fraud_review": FraudAssessment.objects.filter(status=FraudReviewStatus.PENDING).count(),
    }


def _aftersales() -> dict[str, int]:
    open_cases = AfterSalesCase.objects.exclude(status__in=CLOSED_CASE_STATUSES)
    by_kind = {
        row["kind"]: row["n"]
        for row in open_cases.values("kind").annotate(n=Count("pk")).order_by()
    }
    return {
        "open_returns": by_kind.get("return", 0),
        "open_exchanges": by_kind.get("exchange", 0),
        "open_warranty": by_kind.get("warranty", 0),
        "open_support": by_kind.get("support", 0),
        "pending_reviews": ProductReview.objects.filter(status=ReviewStatus.PENDING).count(),
    }


def low_stock_levels() -> QuerySet[StockLevel]:
    return StockLevel.objects.filter(
        low_stock_threshold__isnull=False, available__lte=F("low_stock_threshold")
    )


def _inventory() -> dict[str, Any]:
    today = store_today()
    low = low_stock_levels()
    return {
        "low_stock": low.count(),
        "out_of_stock": StockLevel.objects.filter(available__lte=0).count(),
        "expiring_batches": BatchLevel.objects.filter(
            on_hand__gt=0,
            batch__expires_on__gte=today,
            batch__expires_on__lte=today + timedelta(days=EXPIRY_WINDOW_DAYS),
        )
        .values("batch_id")
        .distinct()
        .count(),
        "low_stock_items": list(
            low.select_related("variant__product", "location").order_by("available", "pk")[
                :RECENT_LIMIT
            ]
        ),
    }


def dashboard(*, permissions: frozenset[str], start: date, end: date) -> dict[str, Any]:
    """Only the sections the staff member's role may see; the rest are null."""
    since, until = day_bounds(start, end)
    can_orders = Permission.ORDERS_MANAGE in permissions
    return {
        "start": start,
        "end": end,
        "orders": _order_counts(since, until) if can_orders else None,
        "operations": _operations() if can_orders else None,
        "aftersales": _aftersales() if can_orders else None,
        "recent_orders": (
            list(Order.objects.order_by("-placed_at", "-pk")[:RECENT_LIMIT]) if can_orders else None
        ),
        "finance": _finance(since, until) if Permission.REPORTS_VIEW in permissions else None,
        "inventory": _inventory() if Permission.CATALOG_MANAGE in permissions else None,
    }


def _grouped(queryset: QuerySet[Order], field: str) -> list[dict[str, Any]]:
    rows = (
        queryset.values(field, "currency")
        .annotate(orders=Count("pk"), value=Sum("grand_total"))
        .order_by(field, "currency")
    )
    return [
        {
            "key": row[field],
            "currency": row["currency"],
            "orders": row["orders"],
            "value": _round(row["value"] or Decimal(0), row["currency"]),
        }
        for row in rows
    ]


def sales_report(*, start: date, end: date) -> dict[str, Any]:
    since, until = day_bounds(start, end)
    placed = _placed(since, until)
    active = placed.exclude(status=OrderStatus.CANCELLED)
    counts = {
        row["currency"]: row["n"]
        for row in active.values("currency").annotate(n=Count("pk")).order_by()
    }
    order_value = _sum_by_currency(active, "grand_total")
    average = {
        currency: value / counts[currency]
        for currency, value in order_value.items()
        if counts.get(currency)
    }
    finance = _finance(since, until)
    daily = (
        active.annotate(day=TruncDate("placed_at", tzinfo=store_zone()))
        .values("day", "currency")
        .annotate(orders=Count("pk"), value=Sum("grand_total"))
        .order_by("day", "currency")
    )
    top = (
        OrderItem.objects.filter(order__placed_at__gte=since, order__placed_at__lt=until)
        .exclude(order__status=OrderStatus.CANCELLED)
        .values("variant_id", "sku", "product_name", "order__currency")
        .annotate(
            quantity=Sum("quantity"),
            revenue=Sum("line_total"),
            orders=Count("order", distinct=True),
        )
        .order_by("-quantity", "sku")[:TOP_PRODUCTS]
    )
    cases = (
        AfterSalesCase.objects.filter(created_at__gte=since, created_at__lt=until)
        .values("kind", "status")
        .annotate(n=Count("pk"))
        .order_by("kind", "status")
    )
    return {
        "start": start,
        "end": end,
        "summary": _money_rows(
            ("order_value", order_value),
            ("average_order_value", average),
        ),
        "order_counts": [
            {"currency": currency, "orders": count} for currency, count in sorted(counts.items())
        ],
        "totals": finance["totals"],
        "cash": finance["cash"],
        "by_status": [
            {"key": row["status"], "orders": row["n"]}
            for row in placed.values("status").annotate(n=Count("pk")).order_by("status")
        ],
        "by_payment_method": _grouped(active, "payment_method"),
        "by_source": _grouped(active, "source"),
        "daily": [
            {
                "date": row["day"],
                "currency": row["currency"],
                "orders": row["orders"],
                "value": _round(row["value"] or Decimal(0), row["currency"]),
            }
            for row in daily
        ],
        "top_products": [
            {
                "variant": row["variant_id"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "currency": row["order__currency"],
                "quantity": row["quantity"],
                "revenue": _round(row["revenue"] or Decimal(0), row["order__currency"]),
                "orders": row["orders"],
            }
            for row in top
        ],
        "aftersales": [
            {"kind": row["kind"], "status": row["status"], "cases": row["n"]} for row in cases
        ],
        "inventory": {
            "low_stock": low_stock_levels().count(),
            "out_of_stock": StockLevel.objects.filter(available__lte=0).count(),
        },
    }
