"""First-party analytics (D-083).

Definitions (shown to merchants as well):

- **Page views, product/category/collection views**: storefront page loads reported by the
  browser, counted in hourly aggregates for every visitor. Without analytics consent nothing
  identifies the visitor: the count is all that is stored.
- **Add to cart / checkout started**: counted by the server when the cart or checkout API
  succeeds, for every visitor, never from a browser claim.
- **Unique visitors**: consented visitors only. A visitor with analytics consent has a random
  first-party id (``mc_vid``); the server keeps only a keyed hash of it with the last time it was
  seen, for 48 hours. "Last 24 hours" is exact (seen in the 24 hours before now). Longer ranges
  add up each store day's unique visitors (a visitor returning on another day counts again).
- **Orders, sales and collections** always come from the order and payment records, never from
  browser events. Storefront-order conversion uses consented visitors who placed an order.
- **Rolling windows for counters** are the current hour plus the 23 hours before it.
- **Traffic sources**: standard UTM parameters of the landing URL, captured only with analytics
  consent, kept 30 days in the visitor's ``mc_src`` cookie. An order is attributed to the last
  tagged landing (``OrderAttribution``); this is a tracked channel, not proof of causation, and
  merchant-assisted orders are never attributed.

Retention: presence rows 48 hours; hourly counters and daily stats 400 days; consent records two
years; Conversions API delivery rows 30 days after they finish. Clean-up runs at most hourly,
triggered by traffic (there is no scheduler), and by ``manage.py prune_marketing_data``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from commerce.catalog.models import Category, Collection, Product, ProductStatus
from commerce.checkout.models import Coupon
from commerce.orders.models import (
    CouponRedemption,
    Order,
    OrderSource,
    OrderStatus,
    PaymentTransaction,
    RedemptionStatus,
    TransactionKind,
    TransactionStatus,
)
from commerce.orders.reports import store_zone
from commerce.tenancy.context import get_active_tenant_id
from commerce.tenancy.keys import tenant_cache_key

from .models import (
    AwardStatus,
    ConsentRecord,
    DeliveryStatus,
    EventCounter,
    EventName,
    LedgerKind,
    LoyaltyAward,
    LoyaltyLedger,
    MetaEventDelivery,
    OrderAttribution,
    VisitorDailyStat,
    VisitorPresence,
)

PRESENCE_RETENTION = timedelta(hours=48)
COUNTER_RETENTION = timedelta(days=400)
CONSENT_RETENTION = timedelta(days=730)
DELIVERY_RETENTION = timedelta(days=30)
MAX_SOURCES_PER_HOUR = 200
MAX_RANGE_DAYS = 90
SOURCE_PART = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
BOT = re.compile(r"bot|crawl|spider|slurp|headless|preview|monitor", re.IGNORECASE)
BROWSER_EVENTS = (
    EventName.PAGE_VIEW,
    EventName.PRODUCT_VIEW,
    EventName.CATEGORY_VIEW,
    EventName.COLLECTION_VIEW,
)


def visitor_digest(visitor: str) -> str:
    key = f"{settings.SECRET_KEY}:marketing-visitor:{get_active_tenant_id()}".encode()
    return hmac.new(key, visitor.encode(), hashlib.sha256).hexdigest()


def _hour(at: datetime) -> datetime:
    return at.replace(minute=0, second=0, microsecond=0)


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(BOT.search(user_agent))


# --- Recording ------------------------------------------------------------------------------------


def bump(event: str, *, consented: bool, dimension: str = "", at: datetime | None = None) -> None:
    """Add one to an hourly counter (insert or atomic increment, safe under concurrency)."""
    bucket = _hour(at or timezone.now())
    rows = EventCounter.objects.filter(
        bucket=bucket, event=event, dimension=dimension, consented=consented
    )
    if rows.update(count=F("count") + 1):
        return
    try:
        with transaction.atomic():
            EventCounter.objects.create(
                bucket=bucket, event=event, dimension=dimension, consented=consented, count=1
            )
    except IntegrityError:
        rows.update(count=F("count") + 1)


def _daily(today: date, field: str) -> None:
    rows = VisitorDailyStat.objects.filter(date=today)
    if rows.update(**{field: F(field) + 1}):
        return
    try:
        with transaction.atomic():
            VisitorDailyStat.objects.create(date=today, **{field: 1})
    except IntegrityError:
        rows.update(**{field: F(field) + 1})


def touch_presence(visitor: str, *, ordered: bool = False) -> None:
    """Mark a consented visitor as seen (and, at placement, as having ordered) now."""
    now = timezone.now()
    zone = store_zone()
    today = now.astimezone(zone).date()
    digest = visitor_digest(visitor)
    with transaction.atomic():
        row = VisitorPresence.objects.select_for_update().filter(visitor_digest=digest).first()
        if row is None:
            try:
                with transaction.atomic():
                    row = VisitorPresence.objects.create(
                        visitor_digest=digest, first_seen_at=now, last_seen_at=now
                    )
                _daily(today, "unique_visitors")
            except IntegrityError:
                row = VisitorPresence.objects.select_for_update().get(visitor_digest=digest)
        elif row.last_seen_at.astimezone(zone).date() != today:
            _daily(today, "unique_visitors")
        fields = ["last_seen_at"]
        row.last_seen_at = now
        if ordered:
            if row.last_order_at is None or row.last_order_at.astimezone(zone).date() != today:
                _daily(today, "converting_visitors")
            row.last_order_at = now
            fields.append("last_order_at")
        row.save(update_fields=fields)


def parse_source(raw: str | None) -> dict[str, Any] | None:
    """``mc_src`` = ``1~source~medium~campaign~epoch``; anything malformed is ignored."""
    parts = (raw or "").split("~")
    if len(parts) != 5 or parts[0] != "1" or not SOURCE_PART.match(parts[1]):
        return None
    medium, campaign = parts[2], parts[3]
    if (medium and not SOURCE_PART.match(medium)) or (campaign and not SOURCE_PART.match(campaign)):
        return None
    try:
        landed = datetime.fromtimestamp(int(parts[4]), tz=timezone.get_current_timezone())
    except (ValueError, OverflowError, OSError):
        return None
    now = timezone.now()
    if landed > now + timedelta(minutes=5) or now - landed > timedelta(days=30):
        return None
    return {"source": parts[1], "medium": medium, "campaign": campaign, "landed_at": landed}


def source_key(utm: dict[str, Any]) -> str:
    return "/".join([utm["source"], utm.get("medium", ""), utm.get("campaign", "")])[:100]


def _landing_dimension(key: str, at: datetime) -> str:
    bucket = _hour(at)
    existing = EventCounter.objects.filter(bucket=bucket, event=EventName.LANDING)
    if existing.filter(dimension=key).exists():
        return key
    # Poisoning guard: a flood of invented campaign tags folds into one bucket.
    if existing.values("dimension").distinct().count() >= MAX_SOURCES_PER_HOUR:
        return "other"
    return key


def object_exists(event: str, object_id: Any) -> bool:
    if event == EventName.PRODUCT_VIEW:
        return Product.objects.filter(pk=object_id, status=ProductStatus.ACTIVE).exists()
    if event == EventName.CATEGORY_VIEW:
        return Category.objects.filter(pk=object_id, is_active=True).exists()
    if event == EventName.COLLECTION_VIEW:
        return Collection.objects.filter(pk=object_id, is_active=True).exists()
    return False


def record_browser_event(
    *, event: str, object_id: Any, visitor: str | None, utm: dict[str, Any] | None
) -> bool:
    """Count one browser-reported view. Returns False when the event was not counted."""
    if event not in BROWSER_EVENTS:
        return False
    if event != EventName.PAGE_VIEW and (object_id is None or not object_exists(event, object_id)):
        return False
    now = timezone.now()
    consented = visitor is not None
    with transaction.atomic():
        bump(event, consented=consented, at=now)
        if event != EventName.PAGE_VIEW:
            bump(event, consented=consented, dimension=str(object_id), at=now)
        if event == EventName.PAGE_VIEW and utm is not None and consented:
            bump(
                EventName.LANDING,
                consented=True,
                dimension=_landing_dimension(source_key(utm), now),
                at=now,
            )
    if visitor is not None:
        touch_presence(visitor)
    maybe_prune()
    return True


def record_server_event(event: str, *, consented: bool, dimension: str = "") -> None:
    """Add-to-cart and checkout-started, counted where the server performed them. Never raises
    into the commerce request: analytics must not block a cart."""
    try:
        with transaction.atomic():
            bump(event, consented=consented)
            if dimension:
                bump(event, consented=consented, dimension=dimension)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("Analytics counter not recorded", exc_info=True)


def record_order(*, order: Order, visitor: str | None, utm: dict[str, Any] | None) -> None:
    """A storefront order from a consented visitor: conversion presence and its channel."""
    if order.source != OrderSource.STOREFRONT or visitor is None:
        return
    touch_presence(visitor, ordered=True)
    if utm is not None:
        OrderAttribution.objects.get_or_create(
            order=order,
            defaults={
                "source": utm["source"],
                "medium": utm.get("medium", ""),
                "campaign": utm.get("campaign", ""),
                "landed_at": utm.get("landed_at"),
                "recorded_at": timezone.now(),
            },
        )


# --- Retention ------------------------------------------------------------------------------------


def maybe_prune() -> None:
    """Queue the clean-up at most once an hour per store."""
    if not cache.add(tenant_cache_key("marketing", "prune"), 1, timeout=3600):
        return
    tenant_id = str(get_active_tenant_id())

    def enqueue() -> None:
        from .tasks import prune_marketing_data

        prune_marketing_data.delay(tenant_id=tenant_id)

    transaction.on_commit(enqueue)


def prune(now: datetime | None = None) -> dict[str, int]:
    now = now or timezone.now()
    removed: dict[str, int] = {}
    removed["presence"] = VisitorPresence.objects.filter(
        last_seen_at__lt=now - PRESENCE_RETENTION
    ).delete()[0]
    removed["counters"] = EventCounter.objects.filter(bucket__lt=now - COUNTER_RETENTION).delete()[
        0
    ]
    cutoff = (now - COUNTER_RETENTION).astimezone(store_zone()).date()
    removed["daily"] = VisitorDailyStat.objects.filter(date__lt=cutoff).delete()[0]
    # QuerySet deletion: consent rows are never updated, only removed after retention.
    removed["consent"] = ConsentRecord.objects.filter(
        recorded_at__lt=now - CONSENT_RETENTION
    ).delete()[0]
    MetaEventDelivery.objects.filter(
        status=DeliveryStatus.WAITING, order__status=OrderStatus.CANCELLED
    ).update(
        status=DeliveryStatus.DISCARDED,
        fbp="",
        fbc="",
        user_agent="",
        last_error="order_cancelled",
        updated_at=now,
    )
    MetaEventDelivery.objects.filter(
        status=DeliveryStatus.WAITING, created_at__lt=now - DELIVERY_RETENTION
    ).update(
        status=DeliveryStatus.DISCARDED,
        fbp="",
        fbc="",
        user_agent="",
        last_error="payment_not_verified",
        updated_at=now,
    )
    removed["deliveries"] = MetaEventDelivery.objects.filter(
        status__in=(DeliveryStatus.SENT, DeliveryStatus.FAILED, DeliveryStatus.DISCARDED),
        updated_at__lt=now - DELIVERY_RETENTION,
    ).delete()[0]
    return removed


# --- Reporting ------------------------------------------------------------------------------------


def _counter_totals(start: datetime, end: datetime) -> dict[tuple[str, bool], int]:
    rows = (
        EventCounter.objects.filter(bucket__gte=_hour(start), bucket__lt=end, dimension="")
        .values("event", "consented")
        .annotate(total=Sum("count"))
    )
    return {(row["event"], row["consented"]): int(row["total"] or 0) for row in rows}


def _total(totals: dict[tuple[str, bool], int], event: str) -> int:
    return totals.get((event, True), 0) + totals.get((event, False), 0)


def window(*, range_key: str, start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """The reporting window. ``24h`` is rolling; day ranges are whole store-local days."""
    now = timezone.now()
    zone = store_zone()
    if range_key == "24h":
        return {
            "key": "24h",
            "start": _hour(now) - timedelta(hours=23),
            "end": now,
            "exact_start": now - timedelta(hours=24),
            "rolling": True,
            "first_day": None,
            "last_day": None,
        }
    today = now.astimezone(zone).date()
    if range_key == "custom":
        if start is None or end is None or end < start:
            raise ValueError("Choose a start and end date.")
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"Choose at most {MAX_RANGE_DAYS} days.")
        if end > today:
            end = today
        first, last = start, end
    else:
        days = {"7d": 7, "30d": 30}.get(range_key)
        if days is None:
            raise ValueError("Unknown range.")
        first, last = today - timedelta(days=days - 1), today
    begin = datetime.combine(first, datetime.min.time(), tzinfo=zone)
    finish = min(datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=zone), now)
    return {
        "key": range_key,
        "start": begin,
        "end": finish,
        "exact_start": begin,
        "rolling": False,
        "first_day": first,
        "last_day": last,
    }


def _visitors(win: dict[str, Any]) -> tuple[int, int]:
    if win["rolling"]:
        since = win["exact_start"]
        return (
            VisitorPresence.objects.filter(last_seen_at__gt=since).count(),
            VisitorPresence.objects.filter(last_order_at__gt=since).count(),
        )
    totals = VisitorDailyStat.objects.filter(
        date__gte=win["first_day"], date__lte=win["last_day"]
    ).aggregate(visitors=Sum("unique_visitors"), converting=Sum("converting_visitors"))
    return int(totals["visitors"] or 0), int(totals["converting"] or 0)


def _orders(win: dict[str, Any]) -> dict[str, Any]:
    placed = Order.objects.filter(placed_at__gte=win["exact_start"], placed_at__lt=win["end"])
    storefront = placed.filter(source=OrderSource.STOREFRONT)
    value = list(
        storefront.exclude(status=OrderStatus.CANCELLED)
        .values("currency")
        .annotate(total=Sum("grand_total"), orders=Count("pk"))
        .order_by("currency")
    )
    collected = list(
        PaymentTransaction.objects.filter(
            order__source=OrderSource.STOREFRONT,
            status=TransactionStatus.SUCCEEDED,
            occurred_at__gte=win["exact_start"],
            occurred_at__lt=win["end"],
        )
        .exclude(kind=TransactionKind.REFUND)
        .values("currency")
        .annotate(total=Sum("amount"))
        .order_by("currency")
    )
    return {
        "storefront_orders": storefront.count(),
        "storefront_cancelled": storefront.filter(status=OrderStatus.CANCELLED).count(),
        "assisted_orders": placed.exclude(source=OrderSource.STOREFRONT).count(),
        "order_value": [
            {"currency": row["currency"], "amount": row["total"], "orders": row["orders"]}
            for row in value
        ],
        "collected": [{"currency": row["currency"], "amount": row["total"]} for row in collected],
    }


def live_summary() -> dict[str, Any]:
    """The rolling 24-hour figures for the merchant dashboard."""
    win = window(range_key="24h")
    totals = _counter_totals(win["start"], win["end"])
    visitors, converting = _visitors(win)
    orders = Order.objects.filter(
        placed_at__gt=win["exact_start"], source=OrderSource.STOREFRONT
    ).count()
    page_views = _total(totals, EventName.PAGE_VIEW)
    consented_views = totals.get((EventName.PAGE_VIEW, True), 0)
    return {
        "window_start": win["start"],
        "exact_start": win["exact_start"],
        "now": win["end"],
        "unique_visitors": visitors,
        "page_views": page_views,
        "consented_page_views": consented_views,
        "product_views": _total(totals, EventName.PRODUCT_VIEW),
        "add_to_cart": _total(totals, EventName.ADD_TO_CART),
        "checkout_started": _total(totals, EventName.CHECKOUT_STARTED),
        "storefront_orders": orders,
        "converting_visitors": converting,
        "conversion_rate": _rate(converting, visitors),
        "consent_coverage": _rate(consented_views, page_views),
    }


def _rate(part: int, whole: int) -> Decimal | None:
    if whole <= 0:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"))


def _series(win: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (
        EventCounter.objects.filter(
            bucket__gte=win["start"],
            bucket__lt=win["end"],
            dimension="",
            event__in=(EventName.PAGE_VIEW, EventName.ADD_TO_CART, EventName.CHECKOUT_STARTED),
        )
        .values("bucket", "event", "consented")
        .annotate(total=Sum("count"))
    )
    zone = store_zone()
    points: dict[str, dict[str, Any]] = {}
    if win["rolling"]:
        cursor = win["start"]
        while cursor <= win["end"]:
            bucket = cursor.astimezone(zone).isoformat()
            points[bucket] = _empty_point(bucket)
            cursor += timedelta(hours=1)
    else:
        day = win["first_day"]
        while day <= win["last_day"]:
            points[day.isoformat()] = _empty_point(day.isoformat())
            day += timedelta(days=1)
        for stat in VisitorDailyStat.objects.filter(
            date__gte=win["first_day"], date__lte=win["last_day"]
        ):
            if stat.date.isoformat() in points:
                points[stat.date.isoformat()]["unique_visitors"] = stat.unique_visitors
    for row in rows:
        local = row["bucket"].astimezone(zone)
        bucket = local.isoformat() if win["rolling"] else local.date().isoformat()
        point = points.get(bucket)
        if point is None:
            continue
        key = {
            EventName.PAGE_VIEW: "page_views",
            EventName.ADD_TO_CART: "add_to_cart",
            EventName.CHECKOUT_STARTED: "checkout_started",
        }[row["event"]]
        point[key] += int(row["total"] or 0)
    return list(points.values())


def _empty_point(bucket: str) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "page_views": 0,
        "add_to_cart": 0,
        "checkout_started": 0,
        "unique_visitors": None,
    }


def _top_products(win: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    rows = (
        EventCounter.objects.filter(
            bucket__gte=win["start"],
            bucket__lt=win["end"],
            event__in=(EventName.PRODUCT_VIEW, EventName.ADD_TO_CART),
        )
        .exclude(dimension="")
        .values("dimension", "event")
        .annotate(total=Sum("count"))
    )
    views: dict[str, int] = {}
    carts: dict[str, int] = {}
    for row in rows:
        target = views if row["event"] == EventName.PRODUCT_VIEW else carts
        target[row["dimension"]] = target.get(row["dimension"], 0) + int(row["total"] or 0)
    # add_to_cart is counted per variant; map variants to their product.
    from commerce.catalog.models import ProductVariant

    variant_products = dict(
        ProductVariant.objects.filter(pk__in=_uuids(carts)).values_list("pk", "product_id")
    )
    carts_by_product: dict[str, int] = {}
    for variant_id, total in carts.items():
        product = variant_products.get(_uuid(variant_id))
        if product is not None:
            key = str(product)
            carts_by_product[key] = carts_by_product.get(key, 0) + total
    ranked = sorted(
        set(views) | set(carts_by_product),
        key=lambda pk: (-views.get(pk, 0), -carts_by_product.get(pk, 0), pk),
    )[:limit]
    names = dict(Product.objects.filter(pk__in=_uuids(ranked)).values_list("pk", "name"))
    result = []
    for pk in ranked:
        name = names.get(_uuid(pk))
        if name is None:
            continue
        result.append(
            {
                "product": pk,
                "name": name,
                "views": views.get(pk, 0),
                "add_to_cart": carts_by_product.get(pk, 0),
            }
        )
    return result


def _uuid(value: str) -> Any:
    import uuid

    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _uuids(values: Iterable[str]) -> list[Any]:
    return [pk for pk in (_uuid(value) for value in values) if pk is not None]


def _sources(win: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    landings = {
        row["dimension"]: int(row["total"] or 0)
        for row in EventCounter.objects.filter(
            bucket__gte=win["start"], bucket__lt=win["end"], event=EventName.LANDING
        )
        .values("dimension")
        .annotate(total=Sum("count"))
    }
    orders: dict[str, dict[str, Any]] = {}
    for row in (
        OrderAttribution.objects.filter(
            order__placed_at__gte=win["exact_start"], order__placed_at__lt=win["end"]
        )
        .exclude(order__status=OrderStatus.CANCELLED)
        .values("source", "medium", "campaign", "order__currency")
        .annotate(count=Count("pk"), value=Sum("order__grand_total"))
    ):
        key = source_key(row)
        entry = orders.setdefault(key, {"orders": 0, "value": []})
        entry["orders"] += row["count"]
        entry["value"].append({"currency": row["order__currency"], "amount": row["value"]})
    keys = sorted(
        set(landings) | set(orders),
        key=lambda key: (-orders.get(key, {}).get("orders", 0), -landings.get(key, 0), key),
    )[:limit]
    result = []
    for key in keys:
        parts = [*key.split("/"), "", ""][:3]
        result.append(
            {
                "utm_source": parts[0],
                "utm_medium": parts[1],
                "utm_campaign": parts[2],
                "landings": landings.get(key, 0),
                "orders": orders.get(key, {}).get("orders", 0),
                "order_value": orders.get(key, {}).get("value", []),
            }
        )
    return result


def _coupons(win: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (
        CouponRedemption.objects.filter(
            created_at__gte=win["exact_start"],
            created_at__lt=win["end"],
            status=RedemptionStatus.REDEEMED,
        )
        .values("code", "currency")
        .annotate(uses=Count("pk"), discount=Sum("discount"))
        .order_by("-uses", "code")[:20]
    )
    return [
        {
            "code": row["code"],
            "currency": row["currency"],
            "uses": row["uses"],
            "discount": row["discount"],
        }
        for row in rows
    ]


def _loyalty(win: dict[str, Any]) -> dict[str, Any]:
    rows = (
        LoyaltyLedger.objects.filter(
            occurred_at__gte=win["exact_start"], occurred_at__lt=win["end"]
        )
        .values("kind")
        .annotate(points=Sum("points"), entries=Count("pk"))
    )
    by_kind = {row["kind"]: int(row["points"] or 0) for row in rows}
    return {
        "earned": by_kind.get(LedgerKind.EARNED, 0),
        "redeemed": -by_kind.get(LedgerKind.REDEEMED, 0),
        "restored": by_kind.get(LedgerKind.RESTORED, 0),
        "reversed": -by_kind.get(LedgerKind.REVERSED, 0),
        "expired": -by_kind.get(LedgerKind.EXPIRED, 0),
        "adjusted": by_kind.get(LedgerKind.ADJUSTED, 0),
        "pending_awards": LoyaltyAward.objects.filter(status=AwardStatus.PENDING).count(),
    }


def report(*, range_key: str, start: date | None = None, end: date | None = None) -> dict[str, Any]:
    from .promotions import KIND_CAMPAIGN, KIND_FLASH, range_performance

    win = window(range_key=range_key, start=start, end=end)
    totals = _counter_totals(win["start"], win["end"])
    visitors, converting = _visitors(win)
    orders = _orders(win)
    page_views = _total(totals, EventName.PAGE_VIEW)
    consented = totals.get((EventName.PAGE_VIEW, True), 0)
    return {
        "range": win["key"],
        "rolling": win["rolling"],
        "start": win["exact_start"],
        "counter_start": win["start"],
        "end": win["end"],
        "first_day": win["first_day"],
        "last_day": win["last_day"],
        "traffic": {
            "page_views": page_views,
            "consented_page_views": consented,
            "consent_coverage": _rate(consented, page_views),
            "unique_visitors": visitors,
            "product_views": _total(totals, EventName.PRODUCT_VIEW),
            "category_views": _total(totals, EventName.CATEGORY_VIEW)
            + _total(totals, EventName.COLLECTION_VIEW),
        },
        "funnel": {
            "product_views": _total(totals, EventName.PRODUCT_VIEW),
            "add_to_cart": _total(totals, EventName.ADD_TO_CART),
            "checkout_started": _total(totals, EventName.CHECKOUT_STARTED),
            "storefront_orders": orders["storefront_orders"],
            "converting_visitors": converting,
            "conversion_rate": _rate(converting, visitors),
        },
        "orders": orders,
        "series": _series(win),
        "top_products": _top_products(win),
        "sources": _sources(win),
        "campaigns": range_performance(KIND_CAMPAIGN, win["exact_start"], win["end"]),
        "flash_sales": range_performance(KIND_FLASH, win["exact_start"], win["end"]),
        "coupons": _coupons(win),
        "loyalty": _loyalty(win),
        "active_coupons": Coupon.objects.filter(is_active=True).count(),
    }


def has_tracking_data() -> bool:
    return EventCounter.objects.exists() or VisitorPresence.objects.exists()


__all__ = [
    "bump",
    "live_summary",
    "prune",
    "record_browser_event",
    "record_order",
    "record_server_event",
    "report",
]
