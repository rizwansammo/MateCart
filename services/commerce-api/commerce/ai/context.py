"""Authorised, minimal context for AI requests (D-104).

Everything here runs in the worker, inside the store's tenant context, *after* the requester's
current permissions have been re-checked. Each builder reads only the records its feature needs
and returns plain data:

- product facts come from the catalogue record and its typed details (never cost prices,
  stock levels or private notes);
- conversation excerpts are the last few customer-visible messages with e-mail addresses,
  phone numbers and long digit runs masked; internal notes are never read;
- verified facts (order and shipment status) are added only when the requester may manage
  orders, and they are produced by the server, not by the model;
- business metrics are aggregates from the existing report services, filtered by the
  requester's permissions.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from commerce.catalog.models import (
    ApparelDetails,
    BookDetails,
    ElectronicsDetails,
    GroceryDetails,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductVariant,
    VariantOptionValue,
)
from commerce.content.models import ContentPage, PageStatus, PageType
from commerce.stores.models import StoreSettings

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_NUMBER = re.compile(r"(?<![A-Za-z]-)(?<![\w-])\+?\d[\d \-().]{5,}\d")
_MARKER = re.compile(r"<<<|>>>")


def mask_personal(text: str) -> str:
    """Hide contact details and number runs (phones, codes, card or account numbers)."""
    masked = _EMAIL.sub("[email]", text)
    return _LONG_NUMBER.sub("[number]", masked)


def plain(value: Any) -> Any:
    """JSON-ready data: decimals and dates as strings, delimiter sequences neutralised."""
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [plain(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        return _MARKER.sub("··", value)
    return value


def store_facts() -> dict[str, Any]:
    store = StoreSettings.objects.first()
    if store is None:
        return {}
    return {
        "store_name": store.name,
        "business_type": store.preset,
        "currency": store.default_currency,
        "country": store.country,
        "default_language": store.default_language,
    }


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def product_facts(product: Product) -> dict[str, Any]:
    categories = [
        row.category.name
        for row in ProductCategory.objects.filter(product=product).select_related("category")
    ]
    variants = []
    for variant in ProductVariant.objects.filter(product=product, is_active=True).order_by(
        "position"
    )[:30]:
        options = [
            f"{row.option.attribute.name}: {row.option.value}"
            for row in VariantOptionValue.objects.filter(variant=variant).select_related(
                "option__attribute"
            )
        ]
        entry: dict[str, Any] = {"sku": variant.sku, "options": options, "kind": variant.kind}
        if variant.net_quantity is not None:
            entry["net_contents"] = f"{variant.net_quantity.normalize()} {variant.net_unit}"
        if variant.weight_grams:
            entry["weight_grams"] = variant.weight_grams
        if variant.isbn:
            entry["isbn"] = variant.isbn
        if variant.digital_format:
            entry["format"] = variant.digital_format
        variants.append(entry)
    specifications = {}
    for row in ProductAttributeValue.objects.filter(product=product).select_related(
        "attribute", "option"
    ):
        if row.option is not None:
            value: Any = row.option.value
        elif row.value_number is not None:
            value = f"{row.value_number.normalize()} {row.attribute.unit}".strip()
        elif row.value_boolean is not None:
            value = "yes" if row.value_boolean else "no"
        else:
            value = row.value_text
        if value not in ("", None):
            specifications[row.attribute.name] = value
    facts: dict[str, Any] = {
        "name": product.name,
        "product_type": product.product_type,
        "status": product.status,
        "brand": product.brand.name if product.brand else None,
        "categories": categories,
        "tags": list(product.tags),
        "summary": product.summary,
        "description": _short(product.description, 3000),
        "seo_title": product.seo_title,
        "seo_description": product.seo_description,
        "variants": variants,
        "specifications": specifications,
        "details": _details(product),
    }
    bangla = (product.translations or {}).get("bn")
    if isinstance(bangla, dict):
        facts["bangla"] = {key: _short(str(value), 1500) for key, value in bangla.items()}
    return facts


def _details(product: Product) -> dict[str, Any]:
    if product.product_type == "apparel":
        apparel = ApparelDetails.objects.filter(product=product).first()
        if apparel:
            return {
                "audience": apparel.audience,
                "material": apparel.material,
                "fit": apparel.fit,
                "care_instructions": apparel.care_instructions,
            }
    if product.product_type == "grocery":
        grocery = GroceryDetails.objects.filter(product=product).first()
        if grocery:
            return {
                "perishable": grocery.is_perishable,
                "storage_instructions": grocery.storage_instructions,
                "shelf_life_days": grocery.shelf_life_days,
                "country_of_origin": grocery.country_of_origin,
                "ingredients": grocery.ingredients,
                "allergens": grocery.allergens,
            }
    if product.product_type == "electronics":
        electronics = (
            ElectronicsDetails.objects.filter(product=product).select_related("warranty").first()
        )
        if electronics:
            warranty = electronics.warranty
            return {
                "model_number": electronics.model_number,
                "warranty": (
                    {
                        "name": warranty.name,
                        "months": warranty.duration_months,
                        "provider": warranty.provider,
                    }
                    if warranty
                    else None
                ),
            }
    if product.product_type == "book":
        book = BookDetails.objects.filter(product=product).first()
        if book:
            return {
                "subtitle": book.subtitle,
                "authors": list(book.authors),
                "publisher": book.publisher,
                "publication_date": book.publication_date,
                "edition": book.edition,
                "language": book.language,
                "genre": book.genre,
                "page_count": book.page_count,
                "series": book.series_name,
            }
    return {}


POLICY_HINTS = (
    "shipping",
    "delivery",
    "return",
    "refund",
    "exchange",
    "warranty",
    "faq",
    "contact",
)


def policy_pages(limit: int = 5, chars: int = 1200) -> list[dict[str, str]]:
    """Published policies and help pages, the store's own approved knowledge."""
    pages = list(
        ContentPage.objects.filter(status=PageStatus.PUBLISHED).order_by("position", "title")[:40]
    )
    chosen = [
        page
        for page in pages
        if page.page_type == PageType.POLICY
        or any(hint in page.slug or hint in page.title.lower() for hint in POLICY_HINTS)
    ][:limit]
    return [
        {"title": page.title, "slug": page.slug, "text": _short(page.body, chars)}
        for page in chosen
    ]


def compact(rows: Iterable[Mapping[str, Any]], keys: Iterable[str]) -> list[dict[str, Any]]:
    wanted = list(keys)
    return [{key: row.get(key) for key in wanted} for row in rows]


def now_local_text() -> str:
    store = StoreSettings.objects.first()
    zone = timezone.get_current_timezone()
    if store is not None:
        import zoneinfo

        zone = zoneinfo.ZoneInfo(store.timezone or "UTC")
    return timezone.now().astimezone(zone).strftime("%Y-%m-%d %H:%M")
