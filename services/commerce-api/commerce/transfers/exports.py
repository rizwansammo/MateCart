"""Exports of merchant-owned data (D-109).

Each export has its own permissions; customer and order exports are sensitive (recent sign-in,
audited downloads). Files are written by the worker into tenant-scoped private storage, expire
after ``MATECART_TRANSFER_FILE_DAYS`` and are served only through the authorised download view.
Every CSV cell is formula-safe. Exports never contain passwords, tokens, cost prices, private
digital download files or payment credentials; addresses are not exported.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from django.db.models import Count, Prefetch

from commerce.catalog.models import (
    Category,
    Collection,
    CollectionProduct,
    Product,
    ProductCategory,
    ProductMedia,
    ProductVariant,
    VariantOptionValue,
)
from commerce.identity.models import AccountType, User
from commerce.inventory import services as inventory
from commerce.inventory.models import StockLevel
from commerce.orders.models import Order
from commerce.pricing.services import Price, find_default_price_list
from commerce.staff.permissions import Permission

from . import csvio
from .normalize import TEMPLATE_HEADER

EXPORTS: dict[str, dict[str, Any]] = {
    "products": {
        "label": "Products and variants",
        "permissions": (Permission.CATALOG_MANAGE,),
        "sensitive": False,
        "formats": ("csv", "jsonl"),
    },
    "categories": {
        "label": "Categories",
        "permissions": (Permission.CATALOG_MANAGE,),
        "sensitive": False,
        "formats": ("csv",),
    },
    "inventory": {
        "label": "Inventory summary",
        "permissions": (Permission.CATALOG_MANAGE,),
        "sensitive": False,
        "formats": ("csv",),
    },
    "customers": {
        "label": "Customer accounts",
        "permissions": (Permission.CUSTOMERS_VIEW, Permission.STORE_SETTINGS),
        "sensitive": True,
        "formats": ("csv",),
    },
    "orders": {
        "label": "Order history",
        "permissions": (Permission.ORDERS_MANAGE, Permission.REPORTS_VIEW),
        "sensitive": True,
        "formats": ("csv",),
    },
}


def _path(category: Category | None, names: dict[Any, tuple[str, Any]]) -> str:
    parts: list[str] = []
    current = category.pk if category else None
    while current is not None and len(parts) < 6:
        name, parent = names[current]
        parts.append(name)
        current = parent
    return " > ".join(reversed(parts))


def product_rows() -> Iterator[dict[str, Any]]:
    """One row per variant in the import template's columns (round trip)."""
    names = {
        row["pk"]: (row["name"], row["parent_id"])
        for row in Category.objects.values("pk", "name", "parent_id")
    }
    price_list = find_default_price_list()
    products = (
        Product.objects.select_related("brand")
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.order_by("position").prefetch_related(
                    Prefetch(
                        "option_values",
                        queryset=VariantOptionValue.objects.select_related("attribute", "option"),
                    )
                ),
            )
        )
        .order_by("created_at", "pk")
    )
    for product in products.iterator(chunk_size=200):
        categories = [
            _path(row.category, names)
            for row in ProductCategory.objects.filter(product=product).select_related("category")
        ]
        collections = list(
            Collection.objects.filter(
                pk__in=CollectionProduct.objects.filter(product=product).values("collection_id")
            ).values_list("name", flat=True)
        )
        images = list(
            ProductMedia.objects.filter(product=product)
            .order_by("position")
            .values_list("asset_id", flat=True)
        )
        bangla = (product.translations or {}).get("bn") or {}
        details = _details(product)
        variants = list(product.variants.all())
        prices = (
            {
                row.variant_id: row
                for row in Price.objects.filter(price_list=price_list, variant__in=variants)
            }
            if price_list
            else {}
        )
        for variant in variants:
            options = list(variant.option_values.all())
            price = prices.get(variant.pk)
            row: dict[str, Any] = {
                "handle": product.slug,
                "name": product.name,
                "product_type": product.product_type,
                "status": product.status,
                "brand": product.brand.name if product.brand else "",
                "categories": " | ".join(categories),
                "collections": " | ".join(collections),
                "tags": ", ".join(product.tags),
                "summary": product.summary,
                "description": product.description,
                "seo_title": product.seo_title,
                "seo_description": product.seo_description,
                "name_bn": bangla.get("name", ""),
                "summary_bn": bangla.get("summary", ""),
                "description_bn": bangla.get("description", ""),
                "sku": variant.sku,
                "price": format(price.amount, "f") if price else "",
                "compare_at_price": format(price.compare_at_amount, "f")
                if price and price.compare_at_amount
                else "",
                "barcode": variant.barcode or "",
                "isbn": variant.isbn or "",
                "weight_grams": variant.weight_grams or "",
                "opening_stock": "",
                # Image files are private media; the export lists their MateCart asset ids.
                "image_urls": "",
                "image_assets": " | ".join(str(i) for i in images),
                "net_quantity": format(variant.net_quantity.normalize(), "f")
                if variant.net_quantity
                else "",
                "net_unit": variant.net_unit,
                **details,
            }
            for index, value in enumerate(options[:3], start=1):
                row[f"option{index}_name"] = value.attribute.name
                row[f"option{index}_value"] = value.option.value
            yield row


def _details(product: Product) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for relation, fields in (
        ("apparel", ("material", "care_instructions")),
        ("grocery", ("ingredients", "allergens")),
        ("electronics", ("model_number",)),
        ("book", ("publisher", "edition", "page_count")),
    ):
        detail = getattr(product, relation, None) if hasattr(product, relation) else None
        if detail is None:
            continue
        for field in fields:
            out[field] = getattr(detail, field) or ""
        if relation == "book":
            out["authors"] = " | ".join(detail.authors)
    return out


PRODUCT_HEADER = [*TEMPLATE_HEADER, "image_assets"]


def category_rows() -> Iterator[dict[str, Any]]:
    names = {
        row["pk"]: (row["name"], row["parent_id"])
        for row in Category.objects.values("pk", "name", "parent_id")
    }
    for category in Category.objects.order_by("position", "name"):
        yield {
            "path": _path(category, names),
            "name": category.name,
            "slug": category.slug,
            "active": "yes" if category.is_active else "no",
            "products": ProductCategory.objects.filter(category=category).count(),
        }


def inventory_rows() -> Iterator[dict[str, Any]]:
    levels = (
        inventory.with_sellable(StockLevel.objects.all())
        .select_related("variant__product", "location")
        .order_by("variant__sku", "location__code")
    )
    for level in levels.iterator(chunk_size=500):
        yield {
            "sku": level.variant.sku,
            "product": level.variant.product.name,
            "location": level.location.code,
            "on_hand": format(level.on_hand, "f"),
            "reserved": format(level.reserved, "f"),
            "sellable": format(level.sellable, "f"),
            "low_stock_threshold": format(level.low_stock_threshold, "f")
            if level.low_stock_threshold is not None
            else "",
        }


def customer_rows() -> Iterator[dict[str, Any]]:
    users = (
        User.objects.filter(account_type=AccountType.CUSTOMER)
        .annotate(order_count=Count("orders", distinct=True))
        .order_by("created_at", "pk")
    )
    for user in users.iterator(chunk_size=500):
        yield {
            "name": user.full_name,
            "email": user.email or "",
            "email_verified": "yes" if user.email_verified_at else "no",
            "phone": user.phone or "",
            "phone_verified": "yes" if user.phone_verified_at else "no",
            "active": "yes" if user.is_active else "no",
            "orders": user.order_count,
            "joined": user.created_at.isoformat(),
        }


def order_rows() -> Iterator[dict[str, Any]]:
    """Order history without addresses or payment details: number, dates, states and totals."""
    for order in Order.objects.order_by("placed_at", "pk").iterator(chunk_size=500):
        yield {
            "number": order.number,
            "placed_at": order.placed_at.isoformat(),
            "source": order.source,
            "status": order.status,
            "payment_method": order.payment_method,
            "payment_status": order.payment_status,
            "currency": order.currency,
            "item_subtotal": format(order.item_subtotal, "f"),
            "discount_total": format(order.discount_total, "f"),
            "shipping_total": format(order.shipping_total, "f"),
            "tax_total": format(order.tax_total, "f"),
            "grand_total": format(order.grand_total, "f"),
            "amount_paid": format(order.amount_paid, "f"),
            "amount_refunded": format(order.amount_refunded, "f"),
            "coupon_code": order.coupon_code,
            "city": order.city,
            "country": order.country,
        }


HEADERS = {
    "products": PRODUCT_HEADER,
    "categories": ["path", "name", "slug", "active", "products"],
    "inventory": [
        "sku",
        "product",
        "location",
        "on_hand",
        "reserved",
        "sellable",
        "low_stock_threshold",
    ],
    "customers": [
        "name",
        "email",
        "email_verified",
        "phone",
        "phone_verified",
        "active",
        "orders",
        "joined",
    ],
    "orders": [
        "number",
        "placed_at",
        "source",
        "status",
        "payment_method",
        "payment_status",
        "currency",
        "item_subtotal",
        "discount_total",
        "shipping_total",
        "tax_total",
        "grand_total",
        "amount_paid",
        "amount_refunded",
        "coupon_code",
        "city",
        "country",
    ],
}
ROWS = {
    "products": product_rows,
    "categories": category_rows,
    "inventory": inventory_rows,
    "customers": customer_rows,
    "orders": order_rows,
}


def render(entity: str, file_format: str) -> tuple[bytes, int]:
    rows: Iterable[dict[str, Any]] = ROWS[entity]()
    count = 0

    def counted() -> Iterator[dict[str, Any]]:
        nonlocal count
        for row in rows:
            count += 1
            yield row

    if file_format == "jsonl":
        data = "".join(csvio.write_jsonl(counted())).encode("utf-8")
    else:
        data = "".join(csvio.write_csv(HEADERS[entity], counted())).encode("utf-8-sig")
    return data, count
