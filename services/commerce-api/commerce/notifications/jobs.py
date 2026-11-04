"""Scheduled notification work, run by the MateCart Celery beat (D-087). Every job is idempotent.

- ``stock_alerts``: compares each stock-limited variant's level (ok, low, out) with the last one
  alerted and emits an alert only on a transition (ok→low, low→out, ok→out). Recovery to ok is
  recorded silently, so the next drop alerts again. Levels use the inventory engine's sellable
  stock (expired batches excluded, reservations deducted), exactly like the dashboard.
- ``daily_summary``: yesterday's figures from orders and payments for staff who opted in, once
  per store day, in the store's time zone after 08:00.
- ``points_reminders``: at most every 30 days, only to customers with an explicit, unwithdrawn
  email opt-in and enough usable points.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from . import services
from .models import Channel, Notification, StockAlertState

SUMMARY_HOUR = 8
REMINDER_INTERVAL = timedelta(days=30)


def _levels() -> dict[Any, str]:
    from commerce.inventory import services as inventory
    from commerce.orders.reports import _tracked_levels, low_stock_levels

    tracked = set(_tracked_levels().values_list("variant_id", flat=True))
    in_stock = set(
        inventory.with_sellable(_tracked_levels())
        .filter(sellable__gt=Decimal(0))
        .values_list("variant_id", flat=True)
    )
    low = set(low_stock_levels().values_list("variant_id", flat=True))
    return {
        variant: "out" if variant not in in_stock else ("low" if variant in low else "ok")
        for variant in tracked
    }


def stock_alerts() -> int:
    from commerce.catalog.models import ProductVariant
    from commerce.inventory import services as inventory

    levels = _levels()
    states = {row.variant_id: row for row in StockAlertState.objects.all()}
    variants = {
        v.pk: v
        for v in ProductVariant.objects.filter(pk__in=list(levels)).select_related("product")
    }
    emitted = 0
    now = timezone.now()
    for variant_id, level in levels.items():
        state = states.get(variant_id)
        previous = state.level if state is not None else "ok"
        if level == previous:
            continue
        with transaction.atomic():
            if state is None:
                state = StockAlertState(variant_id=variant_id, level=level, changed_at=now)
            state.level = level
            state.changed_at = now
            state.sequence += 1
            state.save()
            variant = variants.get(variant_id)
            worse = (previous, level) in {("ok", "low"), ("ok", "out"), ("low", "out")}
            if variant is not None and worse:
                values = {"product_name": variant.product.name, "sku": variant.sku}
                if level == "low":
                    values["available"] = str(inventory.sellable_quantity(variant).normalize())
                services.emit(
                    "staff.stock.out" if level == "out" else "staff.stock.low",
                    event_id=f"{variant_id}:{state.sequence}",
                    values=values,
                    related=("variant", str(variant_id)),
                    staff_link_path="/manage/inventory",
                )
                emitted += 1
    return emitted


def daily_summary(now: datetime | None = None) -> int:
    from commerce.orders.models import Order, OrderStatus
    from commerce.orders.reports import _finance, day_bounds, store_zone

    now = now or timezone.now()
    local = now.astimezone(store_zone())
    if local.hour < SUMMARY_HOUR:
        return 0
    day = local.date() - timedelta(days=1)
    start, end = day_bounds(day, day)
    finance = _finance(start, end)
    placed = Order.objects.filter(placed_at__gte=start, placed_at__lt=end)
    values = {
        "date": day.isoformat(),
        "orders": str(placed.exclude(status=OrderStatus.CANCELLED).count()),
        "order_value": ", ".join(
            services.money(row["order_value"], row["currency"]) for row in finance["totals"]
        )
        or "0",
        "collected": ", ".join(
            services.money(row["collected"], row["currency"]) for row in finance["totals"]
        )
        or "0",
        "to_prepare": str(Order.objects.filter(status=OrderStatus.CONFIRMED).count()),
    }
    created = services.emit(
        "staff.summary.daily",
        event_id=day.isoformat(),
        values=values,
        related=("summary", day.isoformat()),
        staff_link_path="/manage/reports",
        staff_in_app=False,  # an email digest; in app it would duplicate the dashboard
    )
    return len(created)


def points_reminders(now: datetime | None = None) -> int:
    from commerce.marketing.loyalty import active_program
    from commerce.marketing.models import LoyaltyAccount

    now = now or timezone.now()
    program = active_program()
    if program is None:
        return 0
    sent = 0
    period = now.strftime("%Y-%m")
    accounts = LoyaltyAccount.objects.filter(balance__gte=program.min_redeem_points).select_related(
        "customer"
    )[:500]
    for account in accounts:
        customer = account.customer
        if not customer.email or not services.marketing_allowed(customer, Channel.EMAIL):
            continue
        recent = Notification.objects.filter(
            kind="marketing.points_reminder",
            recipient_user=customer,
            created_at__gte=now - REMINDER_INTERVAL,
        ).exists()
        if recent:
            continue
        created = services.emit(
            "marketing.points_reminder",
            event_id=f"{customer.pk}:{period}",
            values={"points": str(account.balance)},
            customer=services.Recipient(
                user=customer, email=customer.email, name=customer.full_name
            ),
            related=("user", str(customer.pk)),
            link_path="/account",
        )
        sent += len(created)
    return sent
