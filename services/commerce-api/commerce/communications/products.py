"""Products staff can share in a conversation (D-097).

The same rules as the storefront: only active products and active variants, the public selling
price from the promotion engine (D-081), and real sellable stock. A shared product is a snapshot
of those values at send time, so the transcript shows what the customer was actually told.

Links use the store's public origin (``MATECART_PUBLIC_BASE_URL``). External channels receive a
link only when that origin is an ``https://`` address that is not local; otherwise the product
is sent without a link rather than with a localhost URL. Website chat uses the store-relative
path, which always works for the visitor.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Q

from commerce.catalog import public
from commerce.catalog.models import ProductMedia, ProductStatus, ProductVariant
from commerce.catalog.serializers import public_media_url
from commerce.core.domain import DomainError
from commerce.inventory import services as inventory

from .models import ChannelType

SEARCH_LIMIT = 20


@dataclass(frozen=True)
class ProductOption:
    variant: ProductVariant
    amount: Decimal | None
    currency: str | None
    in_stock: bool
    available_quantity: Decimal | None
    image_url: str | None


def _public_variants() -> Any:
    return ProductVariant.objects.select_related("product").filter(
        is_active=True, product__status=ProductStatus.ACTIVE
    )


def search(query: str) -> list[ProductOption]:
    text = query.strip()[:100]
    rows = _public_variants()
    if text:
        rows = rows.filter(
            Q(product__name__icontains=text)
            | Q(sku__icontains=text)
            | Q(title__icontains=text)
            | Q(barcode__iexact=text)
        )
    variants = list(rows.order_by("product__name", "position", "pk")[:SEARCH_LIMIT])
    return _options(variants)


def _options(variants: list[ProductVariant]) -> list[ProductOption]:
    if not variants:
        return []
    offers = public._offers(variants)
    stocked = public._in_stock(variants)
    quantities = inventory.sellable_quantities(variants)
    images = {
        row.product_id: public_media_url(row.asset_id)
        for row in ProductMedia.objects.filter(
            product_id__in={v.product_id for v in variants}, is_primary=True
        )
    }
    result = []
    for variant in variants:
        offer = offers.get(variant.pk)
        result.append(
            ProductOption(
                variant=variant,
                amount=offer["amount"] if offer else None,
                currency=offer["currency"] if offer else None,
                in_stock=stocked.get(variant.pk, False),
                available_quantity=quantities.get(variant.pk)
                if inventory.is_tracked(variant)
                else None,
                image_url=images.get(variant.product_id),
            )
        )
    return result


def _external_origin() -> str:
    base = str(settings.MATECART_PUBLIC_BASE_URL).rstrip("/")
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    local = host in ("localhost", "127.0.0.1", "::1") or host.endswith((".local", ".test"))
    return base if base.startswith("https://") and host and not local else ""


def snapshot(variant_id: Any, channel: str) -> tuple[ProductVariant, dict[str, Any], str]:
    """(variant, snapshot, plain-text rendering) for a shareable product, or a domain error."""
    variant = _public_variants().filter(pk=variant_id).first()
    if variant is None:
        raise DomainError("This product is not available in the store.", "product_unavailable")
    option = _options([variant])[0]
    if option.amount is None or option.currency is None:
        raise DomainError("This product has no public price yet.", "product_unpriced")
    path = f"/products/{variant.product.slug}"
    origin = _external_origin()
    url = path if channel == ChannelType.WEBSITE else (f"{origin}{path}" if origin else "")
    data = {
        "product_id": str(variant.product_id),
        "variant_id": str(variant.pk),
        "name": variant.product.name,
        "variant_title": variant.title,
        "sku": variant.sku,
        "amount": f"{option.amount:.2f}",
        "currency": option.currency,
        "in_stock": option.in_stock,
        "url": url,
        "image_url": option.image_url if channel == ChannelType.WEBSITE else None,
    }
    title = variant.product.name + (f" ({variant.title})" if variant.title else "")
    text = f"{title}\n{option.currency} {option.amount:,.2f}"
    if not option.in_stock:
        text += "\nCurrently out of stock"
    if url and channel != ChannelType.WEBSITE:
        text += f"\n{url}"
    return variant, data, text


def links_available() -> bool:
    return bool(_external_origin())
