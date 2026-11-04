"""Promotion engine (D-081): campaigns and flash sales on top of the default price list.

Precedence, applied per variant at the server's current time, never stacked:

1. A live flash sale item for the variant (``flash_sales`` module effective, sale active and in
   its window, price in the same currency and below the regular price, enough promotional units
   left for the requested quantity and within its per-order limit).
2. Otherwise the live discount campaign giving the lowest price (ties: earliest start, then id).
   A campaign that would bring a price to zero or below does not apply to that variant.
3. Otherwise a live featured campaign adds only a badge; the price is unchanged.

Lines sold at a promotional price are not eligible for coupons (D-081). The checkout snapshots the
promotion on each line; a change between quote and review follows the existing price-changed
review. Placement commits limited flash units under the item's row lock; cancellation releases
them once.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.catalog.models import (
    Category,
    Collection,
    CollectionProduct,
    Product,
    ProductCategory,
    ProductVariant,
)
from commerce.core.domain import ConflictError
from commerce.identity.models import User
from commerce.orders.models import Order, OrderItem, OrderStatus
from commerce.pricing.models import Price
from commerce.stores.models import StoreSettings
from commerce.stores.modules import enabled_modules

from .models import (
    Campaign,
    CampaignKind,
    CampaignTarget,
    DiscountType,
    FlashSale,
    FlashSaleItem,
    FlashSaleUsage,
    PromotionStatus,
    UsageStatus,
)

KIND_CAMPAIGN = "campaign"
KIND_FLASH = "flash_sale"
KIND_FEATURED = "featured"
PUBLIC_KINDS = (KIND_CAMPAIGN, KIND_FLASH, KIND_FEATURED)
MAX_TARGETS = 500
MAX_FLASH_ITEMS = 200
MAX_CAMPAIGN_DAYS = 366
MAX_FLASH_DAYS = 14


class PromotionUnavailable(ConflictError):
    default_code = "promotion_unavailable"
    default_detail = "A sale price is no longer available. Review your checkout again."


@dataclass(frozen=True)
class Offer:
    """What a variant sells for now. ``kind`` is '' when no promotion applies."""

    amount: Decimal
    regular_amount: Decimal
    currency: str
    kind: str = ""
    promotion_id: Any = None
    name: str = ""
    badge: str = ""
    ends_at: datetime | None = None
    remaining: Decimal | None = None

    @property
    def discounted(self) -> bool:
        return self.kind in (KIND_CAMPAIGN, KIND_FLASH)


def money_quantum(currency: str) -> Decimal:
    from commerce.pricing.services import currency_precision

    return Decimal(1).scaleb(-currency_precision(currency))


def state(status: str, starts_at: datetime, ends_at: datetime, now: datetime | None = None) -> str:
    """The display state: draft, scheduled, live, paused, ended or cancelled."""
    now = now or timezone.now()
    if status == PromotionStatus.CANCELLED:
        return "cancelled"
    if ends_at <= now:
        return "ended"
    if status == PromotionStatus.DRAFT:
        return "draft"
    if status == PromotionStatus.PAUSED:
        return "paused"
    return "live" if starts_at <= now else "scheduled"


# --- Resolution -----------------------------------------------------------------------------------


def _campaign_price(campaign: Campaign, price: Price) -> Decimal | None:
    if campaign.value is None:
        return None
    if campaign.discount_type == DiscountType.PERCENT:
        reduced = price.amount * (Decimal(100) - campaign.value) / Decimal(100)
    elif campaign.currency == price.currency:
        reduced = price.amount - campaign.value
    else:
        return None
    reduced = reduced.quantize(money_quantum(price.currency), rounding=ROUND_HALF_UP)
    return reduced if Decimal(0) < reduced < price.amount else None


def _campaign_products(campaigns: Sequence[Campaign], product_ids: set[Any]) -> dict[Any, set[Any]]:
    """Campaign id -> the requested product ids it targets (categories include subcategories)."""
    targets = list(CampaignTarget.objects.filter(campaign__in=campaigns))
    if not targets:
        return {}
    by_category: dict[Any, set[Any]] = {}
    by_collection: dict[Any, set[Any]] = {}
    if any(target.category_id for target in targets):
        parents = dict(Category.objects.values_list("pk", "parent_id"))
        for product_id, category_id in ProductCategory.objects.filter(
            product_id__in=product_ids
        ).values_list("product_id", "category_id"):
            seen = 0
            node: Any = category_id
            while node is not None and seen < 10:  # the tree is at most five levels deep
                by_category.setdefault(node, set()).add(product_id)
                node = parents.get(node)
                seen += 1
    if any(target.collection_id for target in targets):
        for product_id, collection_id in CollectionProduct.objects.filter(
            product_id__in=product_ids, collection__is_active=True
        ).values_list("product_id", "collection_id"):
            by_collection.setdefault(collection_id, set()).add(product_id)
    result: dict[Any, set[Any]] = {}
    for target in targets:
        hits: set[Any] = set()
        if target.product_id is not None and target.product_id in product_ids:
            hits = {target.product_id}
        elif target.category_id is not None:
            hits = by_category.get(target.category_id, set())
        elif target.collection_id is not None:
            hits = by_collection.get(target.collection_id, set())
        if hits:
            result.setdefault(target.campaign_id, set()).update(hits)
    return result


def resolve(
    variants: Iterable[ProductVariant],
    prices: Mapping[Any, Price],
    *,
    quantities: Mapping[Any, Decimal] | None = None,
    now: datetime | None = None,
) -> dict[Any, Offer]:
    """The offer for each variant that has a regular price, in a bounded number of queries."""
    now = now or timezone.now()
    rows = [variant for variant in variants if variant.pk in prices]
    if not rows:
        return {}
    ids = [variant.pk for variant in rows]
    flash: dict[Any, FlashSaleItem] = {}
    if "flash_sales" in enabled_modules():
        for item in (
            FlashSaleItem.objects.filter(
                variant_id__in=ids,
                sale__status=PromotionStatus.ACTIVE,
                sale__starts_at__lte=now,
                sale__ends_at__gt=now,
            )
            .select_related("sale")
            .order_by("sale__starts_at", "sale_id")
        ):
            flash.setdefault(item.variant_id, item)
    campaigns = list(
        Campaign.objects.filter(
            status=PromotionStatus.ACTIVE, starts_at__lte=now, ends_at__gt=now
        ).order_by("starts_at", "pk")
    )
    matches = (
        _campaign_products(campaigns, {variant.product_id for variant in rows}) if campaigns else {}
    )
    offers: dict[Any, Offer] = {}
    for variant in rows:
        price = prices[variant.pk]
        regular = Offer(amount=price.amount, regular_amount=price.amount, currency=price.currency)
        quantity = (quantities or {}).get(variant.pk, Decimal(1))
        flash_item = flash.get(variant.pk)
        if (
            flash_item is not None
            and flash_item.currency == price.currency
            and flash_item.sale_price < price.amount
        ):
            remaining = (
                Decimal(flash_item.quantity_limit) - flash_item.units_sold
                if flash_item.quantity_limit is not None
                else None
            )
            fits = (remaining is None or remaining >= quantity) and (
                flash_item.per_order_limit is None or quantity <= flash_item.per_order_limit
            )
            if fits:
                offers[variant.pk] = Offer(
                    amount=flash_item.sale_price,
                    regular_amount=price.amount,
                    currency=price.currency,
                    kind=KIND_FLASH,
                    promotion_id=flash_item.sale_id,
                    name=flash_item.sale.name,
                    badge="Flash sale",
                    ends_at=flash_item.sale.ends_at,
                    remaining=remaining,
                )
                continue
        best: Offer | None = None
        featured: Offer | None = None
        for campaign in campaigns:
            if variant.product_id not in matches.get(campaign.pk, ()):
                continue
            if campaign.kind == CampaignKind.FEATURED:
                if featured is None:
                    featured = Offer(
                        amount=price.amount,
                        regular_amount=price.amount,
                        currency=price.currency,
                        kind=KIND_FEATURED,
                        promotion_id=campaign.pk,
                        name=campaign.name,
                        badge=campaign.badge_text or "Featured",
                        ends_at=campaign.ends_at,
                    )
                continue
            reduced = _campaign_price(campaign, price)
            if reduced is not None and (best is None or reduced < best.amount):
                best = Offer(
                    amount=reduced,
                    regular_amount=price.amount,
                    currency=price.currency,
                    kind=KIND_CAMPAIGN,
                    promotion_id=campaign.pk,
                    name=campaign.name,
                    badge=campaign.badge_text or "Sale",
                    ends_at=campaign.ends_at,
                )
        offers[variant.pk] = best or featured or regular
    return offers


def upcoming_flash_sale(variant_ids: Sequence[Any], *, within: timedelta) -> FlashSale | None:
    """The next scheduled flash sale for any of the variants, for an honest countdown."""
    if not variant_ids or "flash_sales" not in enabled_modules():
        return None
    now = timezone.now()
    item = (
        FlashSaleItem.objects.filter(
            variant_id__in=variant_ids,
            sale__status=PromotionStatus.ACTIVE,
            sale__starts_at__gt=now,
            sale__starts_at__lte=now + within,
        )
        .select_related("sale")
        .order_by("sale__starts_at", "sale_id")
        .first()
    )
    return item.sale if item is not None else None


# --- Placement ------------------------------------------------------------------------------------


def commit_for_order(order: Order, lines: Sequence[Any]) -> None:
    """Take the promotional units of each flash-sale line, under the items' row locks.

    The reviewed price is honoured for the checkout's reservation window (like regular prices),
    but a limited sale can never sell more promotional units than its limit."""
    wanted = [(line.promotion_id, line.variant_id, line) for line in lines if line.promotion_kind]
    flash = [entry for entry in wanted if entry[2].promotion_kind == KIND_FLASH]
    if not flash:
        return
    items = {
        (item.sale_id, item.variant_id): item
        for item in FlashSaleItem.objects.select_for_update()
        .filter(sale_id__in={entry[0] for entry in flash})
        .order_by("pk")
    }
    for sale_id, variant_id, line in flash:
        item = items.get((sale_id, variant_id))
        if item is None:
            raise PromotionUnavailable()
        if item.per_order_limit is not None and line.quantity > item.per_order_limit:
            raise PromotionUnavailable()
        if item.quantity_limit is not None and item.units_sold + line.quantity > Decimal(
            item.quantity_limit
        ):
            raise PromotionUnavailable()
        item.units_sold += line.quantity
        item.save(update_fields=["units_sold", "updated_at"])
        FlashSaleUsage.objects.create(item=item, order=order, quantity=line.quantity)


def release_for_order(order: Order) -> None:
    """Give back an order's flash units, once (cancellation; the caller holds the order lock)."""
    usages = list(
        FlashSaleUsage.objects.select_for_update()
        .filter(order=order, status=UsageStatus.COMMITTED)
        .order_by("item_id")
    )
    if not usages:
        return
    items = {
        item.pk: item
        for item in FlashSaleItem.objects.select_for_update()
        .filter(pk__in=[usage.item_id for usage in usages])
        .order_by("pk")
    }
    now = timezone.now()
    for usage in usages:
        item = items[usage.item_id]
        item.units_sold = max(Decimal(0), item.units_sold - usage.quantity)
        item.save(update_fields=["units_sold", "updated_at"])
        usage.status = UsageStatus.RELEASED
        usage.released_at = now
        usage.save(update_fields=["status", "released_at", "updated_at"])


# --- Merchant services ----------------------------------------------------------------------------


def store_currency() -> str:
    from commerce.pricing.services import find_default_price_list

    price_list = find_default_price_list()
    if price_list is not None:
        return price_list.currency
    currency = StoreSettings.objects.values_list("default_currency", flat=True).first()
    if currency is None:
        raise ConflictError("Store settings are missing.", "store_missing")
    return currency


def _check_window(starts_at: datetime, ends_at: datetime, *, max_days: int) -> None:
    if ends_at <= starts_at:
        raise ValidationError({"ends_at": ["The end must be after the start."]})
    if ends_at - starts_at > timedelta(days=max_days):
        raise ValidationError({"ends_at": [f"A promotion can run for at most {max_days} days."]})


def _editable(status: str, starts_at: datetime, ends_at: datetime) -> None:
    current = state(status, starts_at, ends_at)
    if current in ("live", "ended", "cancelled"):
        message = {
            "live": "Pause this promotion before changing it.",
            "ended": "This promotion has ended and can no longer be changed.",
            "cancelled": "This promotion was cancelled and can no longer be changed.",
        }[current]
        raise ConflictError(message, "promotion_locked")


CAMPAIGN_FIELDS = [
    "name",
    "description",
    "kind",
    "discount_type",
    "value",
    "currency",
    "badge_text",
    "starts_at",
    "ends_at",
]


def save_campaign(*, campaign: Campaign | None, data: Mapping[str, Any], actor: User) -> Campaign:
    with transaction.atomic():
        creating = campaign is None
        target = (
            Campaign()
            if campaign is None
            else Campaign.objects.select_for_update().get(pk=campaign.pk)
        )
        if not creating:
            _editable(target.status, target.starts_at, target.ends_at)
        before = {field: getattr(target, field, None) for field in CAMPAIGN_FIELDS}
        for field in ("name", "description", "badge_text", "starts_at", "ends_at", "kind"):
            if field in data:
                setattr(target, field, data[field])
        if target.kind == CampaignKind.FEATURED:
            target.discount_type, target.value, target.currency = "", None, ""
        else:
            target.discount_type = data.get("discount_type", target.discount_type)
            target.value = data.get("value", target.value)
            if target.discount_type == DiscountType.PERCENT:
                target.currency = ""
                if target.value is None or not Decimal(0) < target.value < Decimal(100):
                    raise ValidationError({"value": ["Use a percentage above 0 and below 100."]})
                if target.value != target.value.quantize(Decimal("0.01")):
                    raise ValidationError({"value": ["Use at most two decimal places."]})
            elif target.discount_type == DiscountType.FIXED:
                target.currency = store_currency()
                if target.value is None or target.value <= 0:
                    raise ValidationError({"value": ["Enter an amount above zero."]})
                if target.value != target.value.quantize(money_quantum(target.currency)):
                    raise ValidationError(
                        {"value": [f"{target.currency} amounts have fewer decimal places."]}
                    )
            else:
                raise ValidationError({"discount_type": ["Choose percentage or fixed amount."]})
        _check_window(target.starts_at, target.ends_at, max_days=MAX_CAMPAIGN_DAYS)
        target.save()
        if "targets" in data:
            _set_targets(target, data["targets"])
        after = {field: getattr(target, field) for field in CAMPAIGN_FIELDS}
        record_audit_event(
            action="marketing.campaign.created" if creating else "marketing.campaign.updated",
            resource=target,
            actor=actor,
            changes={
                **field_changes(before, after, fields=CAMPAIGN_FIELDS),
                **({"targets": _target_counts(target)} if "targets" in data else {}),
            },
        )
        return target


def _set_targets(campaign: Campaign, targets: Mapping[str, Sequence[Any]]) -> None:
    products = list(dict.fromkeys(targets.get("products", [])))
    categories = list(dict.fromkeys(targets.get("categories", [])))
    collections = list(dict.fromkeys(targets.get("collections", [])))
    if len(products) + len(categories) + len(collections) > MAX_TARGETS:
        raise ValidationError({"targets": [f"Choose at most {MAX_TARGETS} targets."]})
    found_products = set(Product.objects.filter(pk__in=products).values_list("pk", flat=True))
    found_categories = set(Category.objects.filter(pk__in=categories).values_list("pk", flat=True))
    found_collections = set(
        Collection.objects.filter(pk__in=collections).values_list("pk", flat=True)
    )
    if (
        len(found_products) != len(products)
        or len(found_categories) != len(categories)
        or len(found_collections) != len(collections)
    ):
        raise ValidationError({"targets": ["A chosen product, category or collection is missing."]})
    CampaignTarget.objects.filter(campaign=campaign).delete()
    CampaignTarget.objects.bulk_create(
        [CampaignTarget(campaign=campaign, product_id=pk) for pk in products]
        + [CampaignTarget(campaign=campaign, category_id=pk) for pk in categories]
        + [CampaignTarget(campaign=campaign, collection_id=pk) for pk in collections]
    )


def _target_counts(campaign: Campaign) -> dict[str, int]:
    rows = CampaignTarget.objects.filter(campaign=campaign)
    return {
        "products": rows.filter(product__isnull=False).count(),
        "categories": rows.filter(category__isnull=False).count(),
        "collections": rows.filter(collection__isnull=False).count(),
    }


def set_campaign_status(*, campaign_id: Any, action: str, actor: User) -> Campaign:
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_id).first()
        if campaign is None:
            raise ConflictError("This campaign does not exist.", "not_found")
        before = campaign.status
        _transition(
            campaign, action, has_targets=CampaignTarget.objects.filter(campaign=campaign).exists()
        )
        campaign.save()
        record_audit_event(
            action=f"marketing.campaign.{action}",
            resource=campaign,
            actor=actor,
            changes={"name": campaign.name, "status": {"from": before, "to": campaign.status}},
        )
        return campaign


def _transition(promotion: Campaign | FlashSale, action: str, *, has_targets: bool) -> None:
    current = state(promotion.status, promotion.starts_at, promotion.ends_at)
    now = timezone.now()
    if action == "activate":
        if promotion.status not in (PromotionStatus.DRAFT, PromotionStatus.PAUSED):
            raise ConflictError("Only draft or paused promotions can be activated.", "invalid")
        if promotion.ends_at <= now:
            raise ConflictError("Its end time has passed; change the dates first.", "ended")
        if not has_targets:
            raise ConflictError("Add at least one product before activating.", "no_targets")
        promotion.status = PromotionStatus.ACTIVE
        promotion.activated_at = now
    elif action == "pause":
        if promotion.status != PromotionStatus.ACTIVE or current == "ended":
            raise ConflictError("Only an active, unfinished promotion can be paused.", "invalid")
        promotion.status = PromotionStatus.PAUSED
    elif action == "cancel":
        if promotion.status == PromotionStatus.CANCELLED:
            raise ConflictError("This promotion is already cancelled.", "invalid")
        promotion.status = PromotionStatus.CANCELLED
    else:
        raise ValidationError({"action": ["Unknown action."]})


def save_flash_sale(*, sale: FlashSale | None, data: Mapping[str, Any], actor: User) -> FlashSale:
    fields = ["name", "description", "starts_at", "ends_at"]
    with transaction.atomic():
        creating = sale is None
        target = (
            FlashSale() if sale is None else FlashSale.objects.select_for_update().get(pk=sale.pk)
        )
        if not creating:
            _editable(target.status, target.starts_at, target.ends_at)
        before = {field: getattr(target, field, None) for field in fields}
        for field in fields:
            if field in data:
                setattr(target, field, data[field])
        _check_window(target.starts_at, target.ends_at, max_days=MAX_FLASH_DAYS)
        target.save()
        if "items" in data:
            _set_flash_items(target, data["items"])
        if target.status == PromotionStatus.ACTIVE:
            _check_overlap(target)
        record_audit_event(
            action="marketing.flash_sale.created" if creating else "marketing.flash_sale.updated",
            resource=target,
            actor=actor,
            changes={
                **field_changes(
                    before, {field: getattr(target, field) for field in fields}, fields=fields
                ),
                **(
                    {"items": FlashSaleItem.objects.filter(sale=target).count()}
                    if "items" in data
                    else {}
                ),
            },
        )
        return target


def _set_flash_items(sale: FlashSale, entries: Sequence[Mapping[str, Any]]) -> None:
    if len(entries) > MAX_FLASH_ITEMS:
        raise ValidationError({"items": [f"A flash sale has at most {MAX_FLASH_ITEMS} items."]})
    currency = store_currency()
    variant_ids = [entry["variant"] for entry in entries]
    if len(set(variant_ids)) != len(variant_ids):
        raise ValidationError({"items": ["List each variant once."]})
    variants = {v.pk: v for v in ProductVariant.objects.filter(pk__in=variant_ids)}
    if len(variants) != len(variant_ids):
        raise ValidationError({"items": ["A chosen variant is missing."]})
    existing = {item.variant_id: item for item in FlashSaleItem.objects.filter(sale=sale)}
    regular = regular_prices(variant_ids)
    errors: list[str] = []
    for index, entry in enumerate(entries, start=1):
        price: Decimal = entry["sale_price"]
        if price < 0 or price != price.quantize(money_quantum(currency)):
            errors.append(f"Item {index}: enter a valid {currency} price.")
            continue
        base = regular.get(entry["variant"])
        if base is None:
            errors.append(f"Item {index}: the variant has no regular price.")
        elif price >= base.amount:
            errors.append(f"Item {index}: the sale price must be below {base.amount}.")
        held = existing.get(entry["variant"])
        limit = entry.get("quantity_limit")
        if held is not None and limit is not None and held.units_sold > limit:
            errors.append(f"Item {index}: {held.units_sold} units are already sold.")
    if errors:
        raise ValidationError({"items": errors})
    keep = set(variant_ids)
    for variant_id, item in existing.items():
        if variant_id not in keep:
            if FlashSaleUsage.objects.filter(item=item).exists():
                raise ValidationError(
                    {"items": ["An item that orders already used cannot be removed."]}
                )
            item.delete()
    for entry in entries:
        item = existing.get(entry["variant"]) or FlashSaleItem(
            sale=sale, variant=variants[entry["variant"]]
        )
        item.sale_price = entry["sale_price"]
        item.currency = currency
        item.quantity_limit = entry.get("quantity_limit")
        item.per_order_limit = entry.get("per_order_limit")
        item.save()


def regular_prices(variant_ids: Sequence[Any]) -> dict[Any, Price]:
    from commerce.pricing.services import find_default_price_list

    price_list = find_default_price_list()
    if price_list is None:
        return {}
    return {
        price.variant_id: price
        for price in Price.objects.filter(price_list=price_list, variant_id__in=variant_ids)
    }


def _check_overlap(sale: FlashSale) -> None:
    # One store-wide lock serialises activations, so two overlapping sales cannot both pass.
    StoreSettings.objects.select_for_update().first()
    variant_ids = list(FlashSaleItem.objects.filter(sale=sale).values_list("variant_id", flat=True))
    clash = (
        FlashSaleItem.objects.filter(
            variant_id__in=variant_ids,
            sale__status=PromotionStatus.ACTIVE,
            sale__starts_at__lt=sale.ends_at,
            sale__ends_at__gt=sale.starts_at,
        )
        .exclude(sale=sale)
        .select_related("variant", "sale")
        .first()
    )
    if clash is not None:
        raise ConflictError(
            f"{clash.variant.sku} is already in the flash sale “{clash.sale.name}” at that time.",
            "flash_sale_overlap",
        )


def set_flash_sale_status(*, sale_id: Any, action: str, actor: User) -> FlashSale:
    with transaction.atomic():
        sale = FlashSale.objects.select_for_update().filter(pk=sale_id).first()
        if sale is None:
            raise ConflictError("This flash sale does not exist.", "not_found")
        before = sale.status
        if action == "activate" and "flash_sales" not in enabled_modules():
            raise ConflictError(
                "Turn on the Flash sales module in Settings first.", "module_disabled"
            )
        _transition(sale, action, has_targets=FlashSaleItem.objects.filter(sale=sale).exists())
        if action == "activate":
            prices = regular_prices(
                list(FlashSaleItem.objects.filter(sale=sale).values_list("variant_id", flat=True))
            )
            for item in FlashSaleItem.objects.filter(sale=sale).select_related("variant"):
                base = prices.get(item.variant_id)
                if base is None or item.sale_price >= base.amount:
                    raise ConflictError(
                        f"{item.variant.sku}: the sale price must be below the regular price.",
                        "flash_price_invalid",
                    )
            _check_overlap(sale)
        sale.save()
        record_audit_event(
            action=f"marketing.flash_sale.{action}",
            resource=sale,
            actor=actor,
            changes={"name": sale.name, "status": {"from": before, "to": sale.status}},
        )
        return sale


# --- Preview and performance ----------------------------------------------------------------------


def preview_campaign(campaign: Campaign, *, limit: int = 30) -> dict[str, Any]:
    """The targeted variants with their regular and campaign prices, as if the campaign were
    live now and no other promotion existed. Bounded to ``limit`` rows."""
    targets = list(CampaignTarget.objects.filter(campaign=campaign))
    product_ids: set[Any] = {t.product_id for t in targets if t.product_id is not None}
    category_ids = {t.category_id for t in targets if t.category_id is not None}
    if category_ids:
        children: dict[Any, list[Any]] = {}
        for pk, parent in Category.objects.values_list("pk", "parent_id"):
            children.setdefault(parent, []).append(pk)
        stack = list(category_ids)
        while stack:
            node = stack.pop()
            for child in children.get(node, []):
                if child not in category_ids:
                    category_ids.add(child)
                    stack.append(child)
        product_ids.update(
            ProductCategory.objects.filter(category_id__in=category_ids).values_list(
                "product_id", flat=True
            )
        )
    collection_ids = [t.collection_id for t in targets if t.collection_id is not None]
    if collection_ids:
        product_ids.update(
            CollectionProduct.objects.filter(collection_id__in=collection_ids).values_list(
                "product_id", flat=True
            )
        )
    variants = list(
        ProductVariant.objects.filter(product_id__in=product_ids, is_active=True)
        .select_related("product")
        .order_by("product__name", "position", "pk")
    )
    prices = regular_prices([v.pk for v in variants[:limit]])
    rows = []
    for variant in variants[:limit]:
        price = prices.get(variant.pk)
        promo = (
            _campaign_price(campaign, price)
            if price is not None and campaign.kind == CampaignKind.DISCOUNT
            else None
        )
        rows.append(
            {
                "variant": variant.pk,
                "sku": variant.sku,
                "product_name": variant.product.name,
                "product_status": variant.product.status,
                "currency": price.currency if price else "",
                "regular_amount": price.amount if price else None,
                "promotional_amount": promo,
                "applies": campaign.kind == CampaignKind.FEATURED or promo is not None,
            }
        )
    return {"products": len(product_ids), "variants": len(variants), "rows": rows}


def performance(
    kind: str,
    promotion_ids: Sequence[Any] | None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[Any, dict[str, Any]]:
    """Orders, units, sales and discount given per promotion, from immutable order lines of
    orders that were not cancelled (one grouped query)."""
    lines = OrderItem.objects.filter(promotion_kind=kind)
    if promotion_ids is not None:
        lines = lines.filter(promotion_id__in=promotion_ids)
    if start is not None and end is not None:
        lines = lines.filter(order__placed_at__gte=start, order__placed_at__lt=end)
    rows = (
        lines.exclude(order__status=OrderStatus.CANCELLED)
        .values("promotion_id", "order__currency")
        .annotate(
            orders=Count("order_id", distinct=True),
            units=Sum("quantity"),
            sales=Sum("line_total"),
            discount=Sum((F("regular_unit_price") - F("unit_price")) * F("quantity")),
        )
    )
    result: dict[Any, dict[str, Any]] = {}
    for row in rows:
        entry = result.setdefault(
            row["promotion_id"], {"orders": 0, "units": Decimal(0), "by_currency": []}
        )
        entry["orders"] += row["orders"]
        entry["units"] += row["units"] or Decimal(0)
        entry["by_currency"].append(
            {
                "currency": row["order__currency"],
                "sales": row["sales"] or Decimal(0),
                "discount": row["discount"] or Decimal(0),
            }
        )
    return result


def range_performance(kind: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Promotion results for orders placed in a window, named, best first (at most 20)."""
    results = performance(kind, None, start=start, end=end)
    model = Campaign if kind == KIND_CAMPAIGN else FlashSale
    names = dict(model.objects.filter(pk__in=list(results)).values_list("pk", "name"))
    rows = [
        {"id": pk, "name": names.get(pk, "Removed promotion"), **values}
        for pk, values in results.items()
    ]
    rows.sort(key=lambda row: (-row["orders"], str(row["name"])))
    return rows[:20]
