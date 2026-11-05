"""One normalised record shape for every source (D-106).

CSV files, JSON Lines files, WooCommerce and Shopify all become the same plain dictionaries
before anything is validated or written, so one importer serves every source:

product  {external_id, handle, name, product_type, status, brand, categories: [[path]],
          collections, tags, summary, description, seo_title, seo_description,
          translations: {"bn": {...}}, options: [names], variants: [{external_id, sku,
          options: {name: value}, price, compare_at, barcode, isbn, weight_grams, stock,
          track_inventory}], images: [{url, alt}], details: {...}}
category {external_id, name, parent_external_id, description}
customer {external_id, first_name, last_name, email, phone, city, country,
          accepts_marketing, orders_count, created_at}
order    {external_id, number, placed_at, status, financial_status, fulfillment_status,
          currency, subtotal, shipping_total, tax_total, discount_total, grand_total,
          customer_external_id, customer_name, lines: [{name, sku, quantity, unit_price, total}]}
coupon   {external_id, code, discount_type, amount, minimum_subtotal, starts_at, ends_at,
          usage_limit, unsupported: [reasons]}

Validation never trusts a source: names, SKUs, barcodes and ISBNs go through the catalogue's
own normalisers, money is ``Decimal`` at the currency's minor unit, HTML becomes plain text and
unknown fields are reported, not silently dropped.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from django.utils.dateparse import parse_datetime

from commerce.catalog.models import ProductType
from commerce.catalog.services import (
    ALLOWED_PRODUCT_TYPES,
    MAX_PRODUCT_OPTIONS,
    MAX_TAGS,
    MAX_VARIANTS,
    normalize_barcode,
    normalize_isbn,
)

SKU_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COUPON_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,31}$")

# Catalogue template: target field → (label, header aliases). One row per variant; rows with the
# same handle form one product.
CATALOG_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "handle": ("Product handle", ("handle", "product_handle", "slug", "parent_sku", "product_id")),
    "name": ("Product name", ("name", "title", "product_name")),
    "product_type": ("Product type", ("product_type", "type")),
    "status": ("Status (draft or active)", ("status", "published")),
    "brand": ("Brand", ("brand", "vendor", "manufacturer")),
    "categories": ("Categories (A > B | C)", ("categories", "category", "category_path")),
    "collections": ("Collections (a | b)", ("collections", "collection")),
    "tags": ("Tags (a, b)", ("tags", "tag")),
    "summary": ("Summary", ("summary", "short_description", "excerpt")),
    "description": ("Description", ("description", "body", "body_html", "long_description")),
    "seo_title": ("SEO title", ("seo_title", "meta_title")),
    "seo_description": ("SEO description", ("seo_description", "meta_description")),
    "name_bn": ("Name (Bangla)", ("name_bn", "title_bn", "bangla_name")),
    "summary_bn": ("Summary (Bangla)", ("summary_bn",)),
    "description_bn": ("Description (Bangla)", ("description_bn",)),
    "option1_name": ("Option 1 name", ("option1_name", "option_1_name")),
    "option1_value": ("Option 1 value", ("option1_value", "option_1_value")),
    "option2_name": ("Option 2 name", ("option2_name", "option_2_name")),
    "option2_value": ("Option 2 value", ("option2_value", "option_2_value")),
    "option3_name": ("Option 3 name", ("option3_name", "option_3_name")),
    "option3_value": ("Option 3 value", ("option3_value", "option_3_value")),
    "sku": ("SKU", ("sku", "variant_sku")),
    "price": ("Price", ("price", "regular_price", "variant_price")),
    "compare_at_price": ("Compare-at price", ("compare_at_price", "compare_price", "mrp")),
    "barcode": ("Barcode (GTIN)", ("barcode", "gtin", "ean", "upc")),
    "isbn": ("ISBN", ("isbn", "isbn13")),
    "weight_grams": ("Weight (grams)", ("weight_grams", "weight_g", "grams")),
    "opening_stock": ("Opening stock", ("opening_stock", "stock", "quantity", "inventory")),
    "image_urls": ("Image URLs (a | b)", ("image_urls", "images", "image_url", "image_src")),
    "material": ("Material (fashion)", ("material", "fabric")),
    "care_instructions": ("Care instructions (fashion)", ("care_instructions", "care")),
    "ingredients": ("Ingredients (grocery)", ("ingredients",)),
    "allergens": ("Allergens (grocery)", ("allergens",)),
    "net_quantity": ("Net quantity (grocery)", ("net_quantity", "net_weight", "pack_size")),
    "net_unit": ("Net unit: g, kg, ml, l, pcs (grocery)", ("net_unit", "unit")),
    "model_number": ("Model number (electronics)", ("model_number", "model")),
    "authors": ("Authors (books, a | b)", ("authors", "author")),
    "publisher": ("Publisher (books)", ("publisher",)),
    "edition": ("Edition (books)", ("edition",)),
    "page_count": ("Page count (books)", ("page_count", "pages")),
}
TEMPLATE_HEADER = list(CATALOG_FIELDS)
DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    ProductType.APPAREL: ("material", "care_instructions"),
    ProductType.GROCERY: ("ingredients", "allergens"),
    ProductType.ELECTRONICS: ("model_number",),
    ProductType.BOOK: ("authors", "publisher", "edition", "page_count"),
}
NET_UNITS = {
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "ml": "ml",
    "l": "l",
    "litre": "l",
    "liter": "l",
    "pcs": "pcs",
    "pc": "pcs",
    "piece": "pcs",
    "pieces": "pcs",
}


def normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def auto_mapping(headers: Sequence[str]) -> dict[str, str]:
    """Template field → file header, by exact or alias match."""
    by_normal = {normal(header): header for header in headers}
    mapping: dict[str, str] = {}
    for target, (_, aliases) in CATALOG_FIELDS.items():
        for alias in (target, *aliases):
            if alias in by_normal and by_normal[alias] not in mapping.values():
                mapping[target] = by_normal[alias]
                break
    return mapping


class _Text(HTMLParser):
    BLOCK = frozenset(
        {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def plain_text(value: Any, limit: int) -> str:
    """HTML or text → plain text (markup never reaches MateCart, which renders plain text)."""
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        parser = _Text()
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)
    else:
        text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:limit]


def one_line(value: Any, limit: int) -> str:
    return " ".join(plain_text(value, limit * 2).split())[:limit]


def money(value: Any, places: int) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise ValueError("not a number") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("not a positive amount")
    quantum = Decimal(1).scaleb(-places)
    if amount != amount.quantize(quantum):
        raise ValueError(f"has more than {places} decimal places")
    return amount.quantize(quantum)


def when(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(str(value))
    return parsed


def split(value: str, separator: str) -> list[str]:
    return [part.strip() for part in value.split(separator) if part.strip()]


def products_from_rows(
    rows: Sequence[Mapping[str, str]], mapping: Mapping[str, str]
) -> list[tuple[dict[str, Any], list[int]]]:
    """Group CSV rows (one per variant) into normalised product records."""

    def cell(row: Mapping[str, str], target: str) -> str:
        header = mapping.get(target)
        return (row.get(header) or "").strip() if header else ""

    grouped: dict[str, tuple[dict[str, Any], list[int]]] = {}
    for index, row in enumerate(rows):
        line = index + 2  # header is line 1
        handle = cell(row, "handle") or cell(row, "sku") or f"row-{line}"
        entry = grouped.get(handle)
        if entry is None:
            details: dict[str, Any] = {}
            for key in (
                "material",
                "care_instructions",
                "ingredients",
                "allergens",
                "model_number",
                "publisher",
                "edition",
                "page_count",
            ):
                if cell(row, key):
                    details[key] = cell(row, key)
            if cell(row, "authors"):
                details["authors"] = split(cell(row, "authors"), "|")
            translations = {
                key: cell(row, f"{key}_bn")
                for key in ("name", "summary", "description")
                if cell(row, f"{key}_bn")
            }
            record: dict[str, Any] = {
                "external_id": handle,
                "handle": handle,
                "name": cell(row, "name"),
                "product_type": (cell(row, "product_type") or "standard").lower(),
                "status": "active"
                if cell(row, "status").lower() in ("active", "published", "publish", "yes", "true")
                else "draft",
                "brand": cell(row, "brand"),
                "categories": [split(path, ">") for path in split(cell(row, "categories"), "|")],
                "collections": split(cell(row, "collections"), "|"),
                "tags": split(cell(row, "tags"), ","),
                "summary": cell(row, "summary"),
                "description": cell(row, "description"),
                "seo_title": cell(row, "seo_title"),
                "seo_description": cell(row, "seo_description"),
                "translations": {"bn": translations} if translations else {},
                "options": [
                    cell(row, f"option{n}_name") for n in (1, 2, 3) if cell(row, f"option{n}_name")
                ],
                "variants": [],
                "images": [{"url": url, "alt": ""} for url in split(cell(row, "image_urls"), "|")],
                "details": details,
            }
            if cell(row, "net_quantity"):
                record["net"] = {
                    "quantity": cell(row, "net_quantity"),
                    "unit": cell(row, "net_unit"),
                }
            entry = (record, [])
            grouped[handle] = entry
        record, lines = entry
        lines.append(line)
        options = {}
        for n, name in enumerate(record["options"], start=1):
            if cell(row, f"option{n}_value"):
                options[name] = cell(row, f"option{n}_value")
        record["variants"].append(
            {
                "external_id": cell(row, "sku") or f"{handle}-{len(record['variants']) + 1}",
                "sku": cell(row, "sku"),
                "options": options,
                "price": cell(row, "price"),
                "compare_at": cell(row, "compare_at_price"),
                "barcode": cell(row, "barcode"),
                "isbn": cell(row, "isbn"),
                "weight_grams": cell(row, "weight_grams"),
                "stock": cell(row, "opening_stock"),
                "track_inventory": True,
            }
        )
    return list(grouped.values())


@dataclass
class Checked:
    record: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_product(
    raw: Mapping[str, Any], *, preset: str, currency: str, places: int, source_currency: str = ""
) -> Checked:
    """Validate and clean one normalised product (no database writes)."""
    errors: list[str] = []
    warnings: list[str] = []
    name = one_line(raw.get("name"), 200)
    if not name:
        errors.append("Product name is missing.")
    product_type = str(raw.get("product_type") or "standard").lower()
    if product_type not in ProductType.values:
        errors.append(f"Unknown product type “{product_type}”.")
        product_type = ProductType.STANDARD
    elif product_type not in ALLOWED_PRODUCT_TYPES.get(preset, frozenset()):
        errors.append(
            f"This store's business type does not offer {product_type} products; use "
            "“standard” or a matching type."
        )
    if source_currency and source_currency != currency:
        errors.append(
            f"Prices are in {source_currency} but the store sells in {currency}. MateCart does "
            "not convert currencies; choose an approved conversion policy first."
        )
    options = [
        one_line(option, 100) for option in raw.get("options") or [] if one_line(option, 100)
    ]
    if len(options) > MAX_PRODUCT_OPTIONS:
        errors.append(f"At most {MAX_PRODUCT_OPTIONS} options (such as size and colour).")
        options = options[:MAX_PRODUCT_OPTIONS]
    variants_in = list(raw.get("variants") or [])
    if not variants_in:
        errors.append("The product has no variants.")
    if len(variants_in) > MAX_VARIANTS:
        errors.append(f"At most {MAX_VARIANTS} variants per product.")
    variants: list[dict[str, Any]] = []
    combos: set[tuple[str, ...]] = set()
    for index, variant in enumerate(variants_in[:MAX_VARIANTS]):
        label = f"Variant {index + 1}"
        sku = str(variant.get("sku") or "").strip().upper()
        if not sku:
            errors.append(f"{label}: SKU is missing.")
        elif not SKU_PATTERN.match(sku):
            errors.append(f"{label}: SKU “{sku[:40]}” uses characters MateCart does not allow.")
        chosen = {
            one_line(k, 100): one_line(v, 100)
            for k, v in (variant.get("options") or {}).items()
            if one_line(v, 100)
        }
        if options and set(chosen) != set(options):
            errors.append(f"{label}: needs a value for each option ({', '.join(options)}).")
        combo = tuple(chosen.get(option, "").lower() for option in options)
        if options and combo in combos:
            errors.append(f"{label}: duplicates another variant's options.")
        combos.add(combo)
        try:
            price = money(variant.get("price"), places)
        except ValueError as exc:
            errors.append(f"{label}: price {exc}.")
            price = None
        if price is None and not errors:
            warnings.append(f"{label}: no price; the product stays a draft until it has one.")
        try:
            compare_at = money(variant.get("compare_at"), places)
        except ValueError:
            warnings.append(f"{label}: compare-at price ignored (not a valid amount).")
            compare_at = None
        if compare_at is not None and price is not None and compare_at <= price:
            compare_at = None
        barcode = None
        if variant.get("barcode"):
            try:
                barcode = normalize_barcode(str(variant["barcode"]))
            except Exception:
                warnings.append(f"{label}: barcode ignored (not a valid GTIN).")
        isbn = None
        if variant.get("isbn"):
            try:
                isbn = normalize_isbn(str(variant["isbn"]))
            except Exception:
                warnings.append(f"{label}: ISBN ignored (not a valid ISBN).")
        weight = None
        if variant.get("weight_grams") not in (None, ""):
            try:
                weight = int(Decimal(str(variant["weight_grams"])))
                if weight < 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                warnings.append(f"{label}: weight ignored.")
                weight = None
        stock = None
        if variant.get("stock") not in (None, ""):
            try:
                stock = Decimal(str(variant["stock"]))
                if stock < 0:
                    warnings.append(f"{label}: negative source stock treated as none.")
                    stock = None
                elif stock != stock.to_integral_value():
                    warnings.append(f"{label}: fractional stock ignored.")
                    stock = None
            except InvalidOperation:
                warnings.append(f"{label}: stock ignored (not a number).")
        variants.append(
            {
                "external_id": str(variant.get("external_id") or sku)[:128],
                "sku": sku,
                "options": chosen,
                "price": str(price) if price is not None else None,
                "compare_at": str(compare_at) if compare_at is not None else None,
                "barcode": barcode,
                "isbn": isbn,
                "weight_grams": weight,
                "stock": str(stock) if stock is not None else None,
                "track_inventory": bool(variant.get("track_inventory", True)),
            }
        )
    skus = [v["sku"] for v in variants if v["sku"]]
    if len(set(skus)) != len(skus):
        errors.append("Two variants of this product share a SKU.")
    tags = []
    for tag in raw.get("tags") or []:
        text = one_line(tag, 40).lower()
        if text and text not in tags:
            tags.append(text)
    if len(tags) > MAX_TAGS:
        warnings.append(f"Only the first {MAX_TAGS} tags are kept.")
        tags = tags[:MAX_TAGS]
    seo_title = one_line(raw.get("seo_title"), 200)
    if len(seo_title) > 70:
        warnings.append("SEO title shortened to 70 characters.")
        seo_title = seo_title[:70]
    seo_description = one_line(raw.get("seo_description"), 1000)
    if len(seo_description) > 320:
        warnings.append("SEO description shortened to 320 characters.")
        seo_description = seo_description[:320]
    translations: dict[str, dict[str, str]] = {}
    bangla = (raw.get("translations") or {}).get("bn") or {}
    if bangla:
        translations["bn"] = {
            key: (one_line if key == "name" else plain_text)(value, limit)
            for key, limit in (("name", 200), ("summary", 500), ("description", 20000))
            if (value := bangla.get(key))
        }
    images = []
    for image in (raw.get("images") or [])[:10]:
        url = str(image.get("url") or "").strip()
        if url:
            images.append({"url": url[:1000], "alt": one_line(image.get("alt"), 300)})
    categories = []
    for path in raw.get("categories") or []:
        cleaned = [one_line(part, 120) for part in path if one_line(part, 120)][:5]
        if cleaned and cleaned not in categories:
            categories.append(cleaned)
    record = {
        "external_id": str(raw.get("external_id") or raw.get("handle") or name)[:128],
        "handle": str(raw.get("handle") or "")[:220],
        "name": name,
        "product_type": product_type,
        "status": "active" if raw.get("status") == "active" else "draft",
        "brand": one_line(raw.get("brand"), 120),
        "categories": categories[:20],
        "collections": [
            one_line(c, 120) for c in (raw.get("collections") or [])[:20] if one_line(c, 120)
        ],
        "tags": tags,
        "summary": plain_text(raw.get("summary"), 500),
        "description": plain_text(raw.get("description"), 20000),
        "seo_title": seo_title,
        "seo_description": seo_description,
        "translations": translations,
        "options": options,
        "variants": variants,
        "images": images,
        "details": _details(product_type, raw.get("details") or {}, warnings),
        # Source category ids (resolved through mappings of categories imported earlier).
        "category_refs": [str(ref)[:128] for ref in (raw.get("category_refs") or [])[:20]],
        # Descriptive (non-variant) attributes: imported as text specifications.
        "specifications": {
            one_line(key, 100): one_line(value, 500)
            for key, value in list((raw.get("specifications") or {}).items())[:30]
            if one_line(key, 100) and one_line(value, 500)
        },
    }
    net = raw.get("net")
    if net and product_type == ProductType.GROCERY:
        unit = NET_UNITS.get(str(net.get("unit") or "").strip().lower())
        try:
            quantity = Decimal(str(net.get("quantity")))
            if unit and quantity > 0:
                record["net"] = {"quantity": str(quantity), "unit": unit}
            else:
                raise InvalidOperation
        except InvalidOperation:
            warnings.append("Net quantity ignored (use a number and g, kg, ml, l or pcs).")
    return Checked(record, errors, warnings)


def _details(product_type: str, raw: Mapping[str, Any], warnings: list[str]) -> dict[str, Any]:
    allowed = DETAIL_KEYS.get(product_type, ())
    out: dict[str, Any] = {}
    ignored = [key for key, value in raw.items() if value and key not in allowed]
    if ignored:
        warnings.append(
            f"Ignored details that do not apply to {product_type} products: "
            + ", ".join(sorted(ignored))
            + "."
        )
    for key in allowed:
        value = raw.get(key)
        if not value:
            continue
        if key == "authors":
            out[key] = [one_line(a, 200) for a in (value if isinstance(value, list) else [value])][
                :10
            ]
        elif key == "page_count":
            try:
                out[key] = max(1, int(str(value)))
            except ValueError:
                warnings.append("Page count ignored.")
        elif key in ("care_instructions", "ingredients"):
            out[key] = plain_text(value, 1000 if key == "care_instructions" else 2000)
        else:
            out[key] = one_line(value, 200 if key != "allergens" else 500)
    return out


def check_customer(raw: Mapping[str, Any]) -> Checked:
    from commerce.core.contact import normalize_phone

    errors: list[str] = []
    warnings: list[str] = []
    email = one_line(raw.get("email"), 254).lower()
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        warnings.append("E-mail address ignored (not valid).")
        email = ""
    phone = ""
    if raw.get("phone"):
        try:
            phone = (
                normalize_phone(str(raw["phone"]), default_region=str(raw.get("country") or "BD"))
                or ""
            )
        except Exception:
            warnings.append("Phone number ignored (not valid).")
    country = str(raw.get("country") or "").upper()
    if country and not re.match(r"^[A-Z]{2}$", country):
        country = ""
    if not raw.get("external_id"):
        errors.append("Source customer id is missing.")
    if not email and not phone and not raw.get("first_name") and not raw.get("last_name"):
        errors.append("The customer has no name or contact details.")
    count = raw.get("orders_count")
    return Checked(
        {
            "external_id": str(raw.get("external_id") or "")[:128],
            "first_name": one_line(raw.get("first_name"), 100),
            "last_name": one_line(raw.get("last_name"), 100),
            "email": email,
            "phone": phone,
            "city": one_line(raw.get("city"), 100),
            "country": country,
            "accepts_marketing": raw.get("accepts_marketing")
            if isinstance(raw.get("accepts_marketing"), bool)
            else None,
            "orders_count": count if isinstance(count, int) and count >= 0 else None,
            "created_at": raw.get("created_at") or None,
        },
        errors,
        warnings,
    )


def check_order(raw: Mapping[str, Any], *, places: int = 2) -> Checked:
    errors: list[str] = []
    warnings: list[str] = []
    currency = str(raw.get("currency") or "").upper()
    if not re.match(r"^[A-Z]{3}$", currency):
        errors.append("Order currency is missing.")
    totals: dict[str, str | None] = {}
    for key in ("subtotal", "shipping_total", "tax_total", "discount_total", "grand_total"):
        try:
            value = money(raw.get(key), 4)
        except ValueError:
            value = None
            warnings.append(f"{key.replace('_', ' ')} ignored (not a valid amount).")
        totals[key] = str(value) if value is not None else None
    if totals["grand_total"] is None:
        errors.append("Order total is missing.")
    lines = []
    for line in (raw.get("lines") or [])[:200]:
        lines.append(
            {
                "name": one_line(line.get("name"), 200),
                "sku": one_line(line.get("sku"), 64),
                "quantity": str(line.get("quantity") or ""),
                "unit_price": str(line.get("unit_price") or ""),
                "total": str(line.get("total") or ""),
            }
        )
    if not raw.get("external_id"):
        errors.append("Source order id is missing.")
    return Checked(
        {
            "external_id": str(raw.get("external_id") or "")[:128],
            "number": one_line(raw.get("number") or raw.get("external_id"), 64),
            "placed_at": raw.get("placed_at") or None,
            "status": one_line(raw.get("status"), 64),
            "financial_status": one_line(raw.get("financial_status"), 64),
            "fulfillment_status": one_line(raw.get("fulfillment_status"), 64),
            "currency": currency,
            **totals,
            "customer_external_id": str(raw.get("customer_external_id") or "")[:128],
            "customer_name": one_line(raw.get("customer_name"), 200),
            "lines": lines,
        },
        errors,
        warnings,
    )


def check_coupon(raw: Mapping[str, Any], *, currency: str, places: int) -> Checked:
    errors: list[str] = []
    warnings = [f"Not imported as a rule: {reason}" for reason in raw.get("unsupported") or []]
    code = re.sub(r"\s+", "", str(raw.get("code") or "")).upper()[:32]
    if not COUPON_PATTERN.match(code):
        errors.append("Coupon code uses characters MateCart does not allow.")
    kind = raw.get("discount_type")
    if kind not in ("percent", "fixed"):
        errors.append("Only percentage and fixed-amount coupons can be imported.")
    try:
        amount = money(raw.get("amount"), 2 if kind == "percent" else places)
    except ValueError:
        amount = None
    if amount is None or amount <= 0 or (kind == "percent" and amount > 100):
        errors.append("Coupon value is not valid.")
    try:
        minimum = money(raw.get("minimum_subtotal"), places) or Decimal(0)
    except ValueError:
        minimum = Decimal(0)
        warnings.append("Minimum spend ignored.")
    source_currency = str(raw.get("currency") or currency).upper()
    if kind == "fixed" and source_currency != currency:
        errors.append(f"Fixed amount is in {source_currency}; the store sells in {currency}.")
    return Checked(
        {
            "external_id": str(raw.get("external_id") or code)[:128],
            "code": code,
            "discount_type": kind,
            "amount": str(amount) if amount is not None else None,
            "minimum_subtotal": str(minimum),
            "starts_at": raw.get("starts_at") or None,
            "ends_at": raw.get("ends_at") or None,
            "usage_limit": raw.get("usage_limit")
            if isinstance(raw.get("usage_limit"), int)
            else None,
        },
        errors,
        warnings,
    )


def check_category(raw: Mapping[str, Any]) -> Checked:
    name = one_line(raw.get("name"), 120)
    return Checked(
        {
            "external_id": str(raw.get("external_id") or name)[:128],
            "name": name,
            "parent_external_id": str(raw.get("parent_external_id") or "")[:128],
            "description": plain_text(raw.get("description"), 5000),
        },
        [] if name else ["Category name is missing."],
        [],
    )
