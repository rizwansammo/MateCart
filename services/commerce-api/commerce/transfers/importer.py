"""Applying staged records through the domain services (D-106, D-108).

Every write goes through the service that owns the record: ``catalog.services`` for brands,
categories, collections, attributes, products and variants (with their validation and audit
events), ``pricing`` for prices, ``inventory.services.receive`` for opening stock (idempotent,
one movement per variant, never on an existing product), ``media.services`` for images
(validated from their bytes) and ``marketing.coupons`` for coupons (always imported switched
off). Historical customers and orders are written only to their read-only tables.

Network work (image downloads) happens before the item's transaction; nothing here calls a
remote service while holding a database lock.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from commerce.catalog import services as catalog
from commerce.catalog.models import (
    AttributeDefinition,
    AttributeOption,
    AttributeScope,
    AttributeValueType,
    Brand,
    Category,
    Collection,
    CollectionProduct,
    Product,
    ProductStatus,
    ProductVariant,
    TrackingMode,
)
from commerce.checkout.models import Coupon
from commerce.core import outbound
from commerce.core.ids import uuid7
from commerce.identity.models import User
from commerce.inventory import services as inventory
from commerce.media import services as media
from commerce.media.models import MediaAsset, MediaKind
from commerce.pricing import services as pricing

from .models import (
    DataTransferJob,
    ExternalSourceMapping,
    HistoricalCustomer,
    HistoricalOrder,
    ImportItem,
)

logger = logging.getLogger(__name__)
MAX_IMAGES = 8


@dataclass
class Context:
    job: DataTransferJob
    actor: User
    platform: str
    identity: str
    currency: str

    @property
    def options(self) -> Mapping[str, Any]:
        options: Mapping[str, Any] = self.job.options
        return options


class ItemProblem(Exception):
    """A record that cannot be applied, with a message for the error report."""


# --- Mappings -------------------------------------------------------------------------------------


def mapped(ctx: Context, object_type: str, external_id: str) -> Any:
    if not external_id:
        return None
    row = ExternalSourceMapping.objects.filter(
        platform=ctx.platform,
        source_identity=ctx.identity,
        object_type=object_type,
        external_id=external_id,
    ).first()
    return row.local_id if row else None


def remember(
    ctx: Context, object_type: str, external_id: str, local_id: Any, *, created: bool
) -> None:
    if not external_id:
        return
    existing = ExternalSourceMapping.objects.filter(
        platform=ctx.platform,
        source_identity=ctx.identity,
        object_type=object_type,
        external_id=external_id,
    ).first()
    if existing is not None:
        if existing.local_id != local_id:
            raise ItemProblem("This source record is already linked to another MateCart record.")
        existing.last_job = ctx.job
        existing.save(update_fields=["last_job", "updated_at"])
        return
    owner = ExternalSourceMapping.objects.filter(
        platform=ctx.platform,
        source_identity=ctx.identity,
        object_type=object_type,
        local_id=local_id,
    ).first()
    if owner is not None:
        raise ItemProblem(
            "This MateCart record is already linked to another record of the same source."
        )
    ExternalSourceMapping.objects.create(
        platform=ctx.platform,
        source_identity=ctx.identity,
        object_type=object_type,
        external_id=external_id,
        local_id=local_id,
        created_by_job=created,
        first_job=ctx.job,
        last_job=ctx.job,
    )


# --- Shared catalogue structures ------------------------------------------------------------------


def _code(name: str) -> str:
    return slugify(name)[:60].strip("-") or uuid7().hex[-8:]


def brand(name: str, actor: User) -> Brand | None:
    if not name:
        return None
    found = Brand.objects.filter(name__iexact=name).first()
    return found or catalog.save_brand(brand=None, data={"name": name}, actor=actor)


def category_path(path: Sequence[str], actor: User) -> Category | None:
    parent: Category | None = None
    for name in path:
        found = Category.objects.filter(parent=parent, name__iexact=name).first()
        parent = found or catalog.save_category(
            category=None, data={"name": name, "parent": parent}, actor=actor
        )
    return parent


def variant_attribute(name: str, actor: User) -> AttributeDefinition:
    """An existing variant option attribute with this name or code, or a new one."""
    code = _code(name)
    found = (
        AttributeDefinition.objects.filter(scope=AttributeScope.VARIANT)
        .filter(Q(name__iexact=name) | Q(code=code))
        .first()
    )
    if found is not None:
        return found
    if AttributeDefinition.objects.filter(code=code).exists():
        code = f"{code}-option"[:64]
        found = AttributeDefinition.objects.filter(code=code, scope=AttributeScope.VARIANT).first()
        if found is not None:
            return found
    return catalog.save_attribute(
        attribute=None,
        data={
            "code": code,
            "name": name[:100],
            "value_type": AttributeValueType.OPTION,
            "scope": AttributeScope.VARIANT,
        },
        actor=actor,
    )


def option_code(attribute: AttributeDefinition, value: str, actor: User) -> str:
    found = (
        AttributeOption.objects.filter(attribute=attribute)
        .filter(Q(value__iexact=value) | Q(code=_code(value)))
        .first()
    )
    if found is None:
        found = catalog.save_option(
            attribute=attribute, option=None, data={"value": value[:100]}, actor=actor
        )
    return found.code


def spec_attribute(name: str, actor: User) -> AttributeDefinition | None:
    """A text specification attribute; None when the name belongs to another kind."""
    code = _code(name)
    found = AttributeDefinition.objects.filter(Q(name__iexact=name) | Q(code=code)).first()
    if found is not None:
        if (
            found.scope == AttributeScope.SPECIFICATION
            and found.value_type == AttributeValueType.TEXT
        ):
            return found
        return None
    return catalog.save_attribute(
        attribute=None,
        data={
            "code": code,
            "name": name[:100],
            "value_type": AttributeValueType.TEXT,
            "scope": AttributeScope.SPECIFICATION,
        },
        actor=actor,
    )


def add_to_collection(name: str, product: Product, actor: User) -> None:
    collection = Collection.objects.filter(name__iexact=name).first() or catalog.save_collection(
        collection=None, data={"name": name}, actor=actor
    )
    current = list(
        Product.objects.filter(
            pk__in=CollectionProduct.objects.filter(collection=collection)
            .order_by("position")
            .values("product_id")
        )
    )
    if product.pk not in {row.pk for row in current}:
        catalog.set_collection_products(
            collection=collection, products=[*current, product], actor=actor
        )


# --- Images ---------------------------------------------------------------------------------------


def download_images(record: Mapping[str, Any], actor: User) -> tuple[list[MediaAsset], list[str]]:
    """Fetch, validate and store the record's images (before any database lock)."""
    assets: list[MediaAsset] = []
    warnings: list[str] = []
    limit = int(settings.MATECART_MEDIA_MAX_BYTES)
    for image in list(record.get("images") or [])[:MAX_IMAGES]:
        url = str(image.get("url") or "")
        try:
            response = outbound.request(
                "GET",
                url,
                headers={"Accept": "image/*"},
                timeout=30.0,
                max_bytes=limit,
                policy=outbound.Policy(max_redirects=3),
            )
        except outbound.OutboundError as exc:
            warnings.append(f"Image skipped ({exc.code}).")
            continue
        if response.status != 200 or not response.body:
            warnings.append(f"Image skipped (HTTP {response.status}).")
            continue
        checksum = hashlib.sha256(response.body).hexdigest()
        existing = MediaAsset.objects.filter(checksum_sha256=checksum, kind=MediaKind.IMAGE).first()
        if existing is not None:
            assets.append(existing)
            continue
        try:
            asset = media.upload(
                stream=io.BytesIO(response.body),
                alt_text=str(image.get("alt") or ""),
                actor=actor,
                rate_limited=False,
            )
        except Exception:
            warnings.append("Image skipped (not a valid JPEG, PNG or WebP image).")
            continue
        if asset.kind != MediaKind.IMAGE:
            warnings.append("Image skipped (not an image).")
            continue
        assets.append(asset)
    return assets, warnings


# --- Products -------------------------------------------------------------------------------------


def existing_product(ctx: Context, record: Mapping[str, Any]) -> tuple[Product | None, str]:
    """The product this record matches (by source mapping, then by SKU), and a problem if the
    match is ambiguous or owned by another source record."""
    local = mapped(ctx, "product", str(record.get("external_id") or ""))
    if local is not None:
        product = Product.objects.filter(pk=local).first()
        if product is not None:
            return product, ""
    skus = [v["sku"] for v in record.get("variants") or [] if v.get("sku")]
    products = set(ProductVariant.objects.filter(sku__in=skus).values_list("product_id", flat=True))
    if len(products) > 1:
        return None, "Its SKUs belong to different existing products."
    if not products:
        return None, ""
    product_id = next(iter(products))
    owner = ExternalSourceMapping.objects.filter(
        platform=ctx.platform,
        source_identity=ctx.identity,
        object_type="product",
        local_id=product_id,
    ).first()
    if owner is not None and owner.external_id != str(record.get("external_id") or ""):
        return None, "Its SKU belongs to a product imported from another source record."
    return Product.objects.filter(pk=product_id).first(), ""


def _translations(record: Mapping[str, Any]) -> dict[str, Any]:
    context = catalog.store_context()
    bangla = (record.get("translations") or {}).get("bn")
    if bangla and "bn" in context.languages and context.default_language != "bn":
        return {"bn": dict(bangla)}
    return {}


def create_product(
    ctx: Context, item: ImportItem, assets: list[MediaAsset]
) -> tuple[Product, list[str]]:
    record = item.payload
    warnings: list[str] = []
    actor = ctx.actor
    categories: list[Category] = []
    for ref in record.get("category_refs") or []:
        local = mapped(ctx, "category", ref)
        found = Category.objects.filter(pk=local).first() if local else None
        if found is not None and found not in categories:
            categories.append(found)
    if not categories:
        for path in record.get("categories") or []:
            found = category_path(path, actor)
            if found is not None and found not in categories:
                categories.append(found)
    attributes = [variant_attribute(name, actor) for name in record.get("options") or []]
    by_name = dict(zip(record.get("options") or [], attributes, strict=True))
    net = record.get("net")
    variants: list[dict[str, Any]] = []
    for variant in record["variants"]:
        entry: dict[str, Any] = {
            "sku": variant["sku"],
            "options": {
                by_name[name].code: option_code(by_name[name], value, actor)
                for name, value in (variant.get("options") or {}).items()
            },
        }
        if variant.get("price") is not None:
            entry["price"] = {"amount": Decimal(variant["price"])}
            if variant.get("compare_at"):
                entry["price"]["compare_at_amount"] = Decimal(variant["compare_at"])
        for key in ("barcode", "isbn", "weight_grams"):
            if variant.get(key):
                entry[key] = variant[key]
        if net:
            entry["net_quantity"] = Decimal(net["quantity"])
            entry["net_unit"] = net["unit"]
        variants.append(entry)
    specifications = []
    for name, value in (record.get("specifications") or {}).items():
        attribute = spec_attribute(name, actor)
        if attribute is None:
            warnings.append(f"Specification “{name}” skipped: an attribute with that name exists.")
            continue
        specifications.append({"attribute": attribute, "value": value})
    tracked = any(v.get("track_inventory") for v in record["variants"])
    data: dict[str, Any] = {
        "name": record["name"],
        "product_type": record["product_type"],
        "brand": brand(record.get("brand") or "", actor),
        "summary": record.get("summary") or "",
        "description": record.get("description") or "",
        "tags": record.get("tags") or [],
        "seo_title": record.get("seo_title") or "",
        "seo_description": record.get("seo_description") or "",
        "track_inventory": tracked,
        "translations": _translations(record),
        "categories": categories,
        "primary_category": categories[0] if categories else None,
        "options": attributes,
        "variants": variants,
        "specifications": specifications,
    }
    if record.get("details"):
        data["details"] = record["details"]
    product = catalog.create_product(data=data, actor=actor, allow_cost=False)
    for name in record.get("collections") or []:
        add_to_collection(name, product, actor)
    if assets:
        catalog.set_product_media(
            product=product,
            entries=[{"asset": asset} for asset in dict.fromkeys(assets)],
            actor=actor,
        )
    for variant_record in record["variants"]:
        local_variant = ProductVariant.objects.get(sku=variant_record["sku"])
        remember(
            ctx, "variant", variant_record.get("external_id") or "", local_variant.pk, created=True
        )
    if ctx.options.get("include_stock", True):
        warnings += _opening_stock(ctx, item, product)
    if record.get("status") == "active" and ctx.options.get("publish", False):
        problems = catalog.publication_problems(product)
        if problems:
            warnings.append("Kept as a draft: " + " ".join(problems))
        else:
            product = catalog.set_status(product=product, status=ProductStatus.ACTIVE, actor=actor)
    return product, warnings


def _opening_stock(ctx: Context, item: ImportItem, product: Product) -> list[str]:
    """Opening balances for a product the job just created (never for existing products)."""
    warnings: list[str] = []
    if not product.track_inventory:
        return warnings
    if product.tracking_mode != TrackingMode.NONE:
        return ["Opening stock skipped: batch or serial tracked products need batches or serials."]
    location = inventory.default_location()
    for variant_record in item.payload["variants"]:
        quantity = variant_record.get("stock")
        if quantity in (None, "", "0") or Decimal(quantity) <= 0:
            continue
        variant = ProductVariant.objects.get(sku=variant_record["sku"])
        inventory.receive(
            variant=variant,
            location=location,
            quantity=Decimal(quantity),
            request=inventory.StockRequest(
                reason="Opening balance from import",
                reference_type="data_transfer_job",
                reference_id=str(ctx.job.pk),
                idempotency_key=f"import:{item.pk}:{variant.pk}",
                actor=ctx.actor,
            ),
        )
    return warnings


def update_product(ctx: Context, item: ImportItem, product: Product) -> list[str]:
    """Update the eligible fields of an existing product. Never its type, status, SKUs, stock or
    variants; prices only when the merchant chose to."""
    record = item.payload
    warnings: list[str] = []
    data: dict[str, Any] = {}
    for field in ("summary", "description", "seo_title", "seo_description"):
        if record.get(field):
            data[field] = record[field]
    if record.get("name"):
        data["name"] = record["name"]
    if record.get("tags"):
        data["tags"] = record["tags"]
    translations = _translations(record)
    if translations:
        merged = dict(product.translations or {})
        merged["bn"] = {**(merged.get("bn") or {}), **translations["bn"]}
        data["translations"] = merged
    if data:
        catalog.update_product(product=product, data=data, actor=ctx.actor)
    known = {v.sku: v for v in ProductVariant.objects.filter(product=product)}
    for variant_record in record["variants"]:
        variant = known.get(variant_record["sku"])
        if variant is None:
            warnings.append(
                f"New variant {variant_record['sku']} not added to an existing product."
            )
            continue
        remember(ctx, "variant", variant_record.get("external_id") or "", variant.pk, created=False)
        if ctx.options.get("update_prices") and variant_record.get("price") is not None:
            entry: dict[str, Any] = {"amount": Decimal(variant_record["price"]), "price_list": None}
            if variant_record.get("compare_at"):
                entry["compare_at_amount"] = Decimal(variant_record["compare_at"])
            pricing.set_prices(variant=variant, entries=[entry], actor=ctx.actor, allow_cost=False)
    return warnings


# --- Categories, customers, orders, coupons -------------------------------------------------------


def apply_category(ctx: Context, item: ImportItem) -> tuple[Any, bool]:
    record = item.payload
    parent: Category | None = None
    if record.get("parent_external_id"):
        local = mapped(ctx, "category", record["parent_external_id"])
        parent = Category.objects.filter(pk=local).first() if local else None
    local = mapped(ctx, "category", record["external_id"])
    if local and Category.objects.filter(pk=local).exists():
        return local, False
    found = Category.objects.filter(parent=parent, name__iexact=record["name"]).first()
    created = found is None
    category = found or catalog.save_category(
        category=None,
        data={
            "name": record["name"],
            "parent": parent,
            "description": record.get("description", ""),
        },
        actor=ctx.actor,
    )
    remember(ctx, "category", record["external_id"], category.pk, created=created)
    return category.pk, created


def apply_customer(ctx: Context, item: ImportItem) -> tuple[Any, bool]:
    record = item.payload
    defaults = {
        "first_name": record["first_name"],
        "last_name": record["last_name"],
        "email": record["email"],
        "phone": record["phone"],
        "city": record["city"],
        "country": record["country"],
        "source_accepts_marketing": record.get("accepts_marketing"),
        "source_orders_count": record.get("orders_count"),
        "source_created_at": parse_datetime(str(record["created_at"]))
        if record.get("created_at")
        else None,
    }
    row = HistoricalCustomer.objects.filter(
        platform=ctx.platform, source_identity=ctx.identity, external_id=record["external_id"]
    ).first()
    if row is None:
        row = HistoricalCustomer.objects.create(
            platform=ctx.platform,
            source_identity=ctx.identity,
            external_id=record["external_id"],
            job=ctx.job,
            **defaults,
        )
        return row.pk, True
    for key, value in defaults.items():
        setattr(row, key, value)
    row.save()
    return row.pk, False


def apply_order(ctx: Context, item: ImportItem) -> tuple[Any, bool]:
    record = item.payload
    if HistoricalOrder.objects.filter(
        platform=ctx.platform, source_identity=ctx.identity, external_id=record["external_id"]
    ).exists():
        raise ItemProblem("This historical order was imported before.")
    customer = None
    if record.get("customer_external_id"):
        customer = HistoricalCustomer.objects.filter(
            platform=ctx.platform,
            source_identity=ctx.identity,
            external_id=record["customer_external_id"],
        ).first()

    def amount(key: str) -> Decimal | None:
        return Decimal(record[key]) if record.get(key) is not None else None

    row = HistoricalOrder.objects.create(
        platform=ctx.platform,
        source_identity=ctx.identity,
        external_id=record["external_id"],
        number=record["number"],
        placed_at=parse_datetime(str(record["placed_at"])) if record.get("placed_at") else None,
        source_status=record.get("status") or "",
        source_financial_status=record.get("financial_status") or "",
        source_fulfillment_status=record.get("fulfillment_status") or "",
        currency=record["currency"],
        subtotal=amount("subtotal"),
        shipping_total=amount("shipping_total"),
        tax_total=amount("tax_total"),
        discount_total=amount("discount_total"),
        grand_total=Decimal(record["grand_total"]),
        lines=record.get("lines") or [],
        customer=customer,
        customer_name=record.get("customer_name") or "",
        job=ctx.job,
    )
    return row.pk, True


def apply_coupon(ctx: Context, item: ImportItem) -> tuple[Any, bool]:
    """Imported coupons are always switched off: the merchant reviews and activates them."""
    from commerce.marketing import coupons

    record = item.payload
    if Coupon.objects.filter(code=record["code"]).exists():
        raise ItemProblem("A coupon with this code already exists.")
    data: dict[str, Any] = {
        "code": record["code"],
        "discount_type": record["discount_type"],
        "value": Decimal(record["amount"]),
        "minimum_subtotal": Decimal(record.get("minimum_subtotal") or 0),
        "starts_at": parse_datetime(str(record["starts_at"])) if record.get("starts_at") else None,
        "ends_at": parse_datetime(str(record["ends_at"])) if record.get("ends_at") else None,
        "max_redemptions": record.get("usage_limit"),
        "is_active": False,
    }
    if record["discount_type"] == "fixed":
        data["currency"] = ctx.currency
    coupon = coupons.save_coupon(coupon=None, data=data, actor=ctx.actor)
    remember(ctx, "coupon", record["external_id"], coupon.pk, created=True)
    return coupon.pk, True
