"""Price rules (D-053).

- Amounts are Decimals with no more decimal places than the currency's minor unit (BDT 2, JPY 0).
- A price's currency is its price list's currency; there is no conversion between lists.
- Zero is a valid price (free items). ``compare_at_amount`` must exceed the price.
- Cost prices are internal: callers without ``catalog.costs`` cannot set them, and audit events
  record only that a cost changed.
- Prices change under a lock on the variant, so concurrent edits apply one after the other.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import Any

from babel.numbers import get_currency_precision
from django.db import IntegrityError, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.catalog.models import Product, ProductStatus, ProductVariant
from commerce.core.domain import ConflictError, save_unique, validated
from commerce.identity.models import User
from commerce.stores.models import StoreSettings

from .models import Price, PriceList

__all__ = ["Price", "PriceList"]

DEFAULT_PRICE_LIST_CODE = "default"
_UNSET = object()


@cache
def currency_precision(currency: str) -> int:
    precision: int = get_currency_precision(currency)
    return precision


def check_amount(value: Decimal | None, *, currency: str, field: str) -> None:
    if value is None:
        return
    places = currency_precision(currency)
    if value != value.quantize(Decimal(1).scaleb(-places)):
        raise ValidationError(
            {field: [f"{currency} amounts have at most {places} decimal places."]}
        )


# --- Price lists ----------------------------------------------------------------------------------


def find_default_price_list() -> PriceList | None:
    return PriceList.objects.filter(is_default=True).first()


def default_price_list() -> PriceList:
    """The store's default price list, created in the store's default currency on first use."""
    existing = find_default_price_list()
    if existing is not None:
        return existing
    currency = StoreSettings.objects.values_list("default_currency", flat=True).first()
    if currency is None:
        raise ConflictError(
            "Store settings are missing; provision the store first.", "store_missing"
        )
    try:
        with transaction.atomic():
            return PriceList.objects.create(
                code=DEFAULT_PRICE_LIST_CODE, name="Default", currency=currency, is_default=True
            )
    except IntegrityError:  # created concurrently
        created = find_default_price_list()
        if created is None:
            raise
        return created


def save_price_list(
    *, price_list: PriceList | None, data: Mapping[str, Any], actor: User | None
) -> PriceList:
    fields = ["code", "name", "currency", "is_active"]
    with transaction.atomic():
        creating = price_list is None
        target = (
            PriceList()
            if price_list is None
            else PriceList.objects.select_for_update().get(pk=price_list.pk)
        )
        before = {field: getattr(target, field) for field in fields}
        if not creating and target.is_default:
            if data.get("is_active") is False:
                raise ConflictError("The default price list stays active.", "default_price_list")
            if "currency" in data and data["currency"] != target.currency:
                raise ConflictError(
                    "The default price list uses the store's currency.", "default_price_list"
                )
        if (
            not creating
            and "currency" in data
            and data["currency"] != target.currency
            and Price.objects.filter(price_list=target).exists()
        ):
            raise ConflictError(
                "This price list has prices; its currency is fixed.", "price_list_in_use"
            )
        for field in fields:
            if field in data:
                setattr(target, field, data[field])
        validated(target, exclude=["tenant"])
        save_unique(
            target,
            {"pricing_pricelist_code_unique": ("code", "Another price list uses this code.")},
        )
        record_audit_event(
            action="pricing.price_list.created" if creating else "pricing.price_list.updated",
            resource=target,
            actor=actor,
            changes=field_changes(
                before, {field: getattr(target, field) for field in fields}, fields=fields
            ),
        )
    return target


# --- Prices ---------------------------------------------------------------------------------------


def set_prices(
    *,
    variant: ProductVariant,
    entries: Sequence[Mapping[str, Any]],
    actor: User | None,
    allow_cost: bool,
) -> list[Price]:
    """Create or update the variant's price in each entry's price list (``None``: default list).

    An entry omitting ``compare_at_amount`` or ``cost_amount`` keeps the stored value; ``None``
    clears it. Everything is applied atomically or not at all.
    """
    lists = [entry.get("price_list") or default_price_list() for entry in entries]
    if len({price_list.pk for price_list in lists}) != len(lists):
        raise ValidationError({"prices": ["Give each price list once."]})
    if not allow_cost and any("cost_amount" in entry for entry in entries):
        raise PermissionDenied("You don't have permission to see or change cost prices.")
    saved: list[Price] = []
    with transaction.atomic():
        ProductVariant.objects.select_for_update().filter(pk=variant.pk).first()
        for entry, price_list in zip(entries, lists, strict=True):
            price = Price.objects.filter(price_list=price_list, variant=variant).first()
            creating = price is None
            if price is None:
                price = Price(price_list=price_list, variant=variant)
            before = {
                "amount": price.amount,
                "compare_at_amount": price.compare_at_amount,
                "cost_amount": price.cost_amount,
            }
            price.currency = price_list.currency
            price.amount = entry["amount"]
            for field in ("compare_at_amount", "cost_amount"):
                value = entry.get(field, _UNSET)
                if value is not _UNSET:
                    setattr(price, field, value)
            for field in ("amount", "compare_at_amount", "cost_amount"):
                check_amount(getattr(price, field), currency=price.currency, field=field)
            if price.amount < 0:
                raise ValidationError({"amount": ["A price cannot be negative."]})
            if price.compare_at_amount is not None and price.compare_at_amount <= price.amount:
                raise ValidationError(
                    {"compare_at_amount": ["The compare-at price must be higher than the price."]}
                )
            validated(price, exclude=["tenant"])
            save_unique(
                price,
                {"pricing_price_one_per_list": ("prices", "This variant already has that price.")},
            )
            after = {
                "amount": price.amount,
                "compare_at_amount": price.compare_at_amount,
            }
            changes = field_changes(before, after, fields=["amount", "compare_at_amount"])
            if before["cost_amount"] != price.cost_amount:
                changes["cost_changed"] = True  # the value itself is internal (D-053)
            record_audit_event(
                action="pricing.price.created" if creating else "pricing.price.updated",
                resource=price,
                actor=actor,
                changes={
                    **changes,
                    "sku": variant.sku,
                    "price_list": price_list.code,
                    "currency": price.currency,
                },
            )
            saved.append(price)
    return saved


def remove_price(*, variant: ProductVariant, price_list: PriceList, actor: User | None) -> None:
    with transaction.atomic():
        ProductVariant.objects.select_for_update().filter(pk=variant.pk).first()
        price = Price.objects.filter(price_list=price_list, variant=variant).first()
        if price is None:
            return
        if price_list.is_default and variant.is_active:
            product = Product.objects.get(pk=variant.product_id)
            if product.status == ProductStatus.ACTIVE:
                raise ConflictError(
                    "Active products need a price in the default price list.", "price_required"
                )
        record_audit_event(
            action="pricing.price.removed",
            resource=price,
            actor=actor,
            changes={"sku": variant.sku, "price_list": price_list.code},
        )
        price.delete()


@dataclass(frozen=True)
class PriceQuote:
    """A price as checkout will snapshot it (Phase 07): amount, currency and the source row."""

    amount: Decimal
    compare_at_amount: Decimal | None
    currency: str
    price_id: Any
    price_list_id: Any


def quote(variant: ProductVariant, price_list: PriceList | None = None) -> PriceQuote | None:
    """The variant's current price in ``price_list`` (default list when omitted), or None."""
    target = price_list or find_default_price_list()
    if target is None:
        return None
    price = Price.objects.filter(price_list=target, variant=variant).first()
    if price is None:
        return None
    return PriceQuote(
        amount=price.amount,
        compare_at_amount=price.compare_at_amount,
        currency=price.currency,
        price_id=price.pk,
        price_list_id=target.pk,
    )
