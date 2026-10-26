"""Catalogue business rules. Views validate request shapes; every change happens here, inside one
transaction with its audit event.

References arrive as model instances resolved through tenant-scoped managers, so another store's
rows are unreachable. Uniqueness is enforced by the database and reported as field errors
(``save_unique``); category tree changes are serialised per store so cycles cannot race in.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.db.models import Model, ProtectedError
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.core.domain import ConflictError, DomainError, save_unique, validated
from commerce.core.ids import uuid7
from commerce.identity.models import User
from commerce.inventory.models import Batch, SerialNumber, StockMovement
from commerce.media.models import MediaAsset, MediaKind
from commerce.stores.models import StorePreset, StoreSettings
from commerce.tenancy.context import get_active_tenant_id
from commerce.tenancy.models import Tenant

from .models import (
    ApparelDetails,
    AttributeDefinition,
    AttributeOption,
    AttributeScope,
    AttributeValueType,
    BookDetails,
    Brand,
    Category,
    Collection,
    CollectionProduct,
    ElectronicsDetails,
    GroceryDetails,
    InventoryUnit,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductMedia,
    ProductOption,
    ProductStatus,
    ProductType,
    ProductVariant,
    TrackingMode,
    VariantKind,
    VariantOptionValue,
    WarrantyPolicy,
)

MAX_CATEGORY_DEPTH = 5
MAX_VARIANTS = 250
MAX_PRODUCT_OPTIONS = 3
MAX_TAGS = 20
MAX_COLLECTION_PRODUCTS = 1000

# Which product types each preset offers. A general store sells anything; specialised stores
# keep their own type plus plain products (D-051).
ALLOWED_PRODUCT_TYPES: dict[str, frozenset[str]] = {
    StorePreset.FASHION: frozenset({ProductType.STANDARD, ProductType.APPAREL}),
    StorePreset.GROCERY: frozenset({ProductType.STANDARD, ProductType.GROCERY}),
    StorePreset.ELECTRONICS: frozenset({ProductType.STANDARD, ProductType.ELECTRONICS}),
    StorePreset.BOOKS: frozenset({ProductType.STANDARD, ProductType.BOOK}),
    StorePreset.GENERAL: frozenset(ProductType.values),
}
DETAIL_MODELS: dict[str, type[Model]] = {
    ProductType.APPAREL: ApparelDetails,
    ProductType.GROCERY: GroceryDetails,
    ProductType.ELECTRONICS: ElectronicsDetails,
    ProductType.BOOK: BookDetails,
}
DETAIL_FIELDS: dict[str, tuple[str, ...]] = {
    ProductType.APPAREL: (
        "audience",
        "material",
        "fit",
        "care_instructions",
        "size_guide",
        "size_guide_notes",
    ),
    ProductType.GROCERY: (
        "is_perishable",
        "storage_instructions",
        "shelf_life_days",
        "country_of_origin",
        "ingredients",
        "allergens",
    ),
    ProductType.ELECTRONICS: ("model_number", "warranty"),
    ProductType.BOOK: (
        "subtitle",
        "authors",
        "publisher",
        "publication_date",
        "edition",
        "language",
        "genre",
        "page_count",
        "series_name",
        "series_number",
    ),
}
TRANSLATABLE_PRODUCT_FIELDS = {"name": 200, "summary": 500, "description": 20000}
TRANSLATABLE_GROUP_FIELDS = {"name": 120, "description": 5000}
PRODUCT_AUDIT_FIELDS = [
    "name",
    "slug",
    "product_type",
    "brand_id",
    "summary",
    "tags",
    "seo_title",
    "seo_description",
    "track_inventory",
    "tracking_mode",
]
VARIANT_AUDIT_FIELDS = [
    "sku",
    "is_active",
    "kind",
    "digital_format",
    "barcode",
    "isbn",
    "weight_grams",
    "inventory_unit",
    "net_quantity",
    "net_unit",
    "release_date",
]

_SKU_CONFLICT = ("sku", "Another variant already uses this SKU.")
_VARIANT_CONFLICTS = {
    "catalog_variant_sku_unique": _SKU_CONFLICT,
    "catalog_variant_combination_unique": (
        "options",
        "This product already has a variant with these options.",
    ),
    "catalog_variant_barcode_unique": ("barcode", "Another variant already uses this barcode."),
    "catalog_variant_isbn_unique": ("isbn", "Another variant already uses this ISBN."),
}


def _snapshot(instance: Model, fields: Iterable[str]) -> dict[str, Any]:
    return {name: getattr(instance, name) for name in fields}


def _audit(action: str, resource: Model, actor: User | None, **changes: Any) -> None:
    record_audit_event(action=action, resource=resource, actor=actor, changes=changes)


# --- Store context --------------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreCatalogContext:
    preset: str
    default_language: str
    languages: tuple[str, ...]


def store_context() -> StoreCatalogContext:
    row = (
        StoreSettings.objects.values("preset", "default_language", "supported_languages")
        .order_by()
        .first()
    )
    if row is None:
        raise DomainError("Store settings are missing; provision the store first.", "store_missing")
    return StoreCatalogContext(
        preset=row["preset"],
        default_language=row["default_language"],
        languages=tuple(row["supported_languages"]),
    )


def _lock_catalogue_structure() -> None:
    """Serialise category tree changes in this store. FOR NO KEY UPDATE on the tenant row does not
    block the foreign-key checks other writes take, only other tree changes."""
    list(
        Tenant.objects.select_for_update(no_key=True)
        .filter(pk=get_active_tenant_id())
        .values_list("pk", flat=True)
    )


# --- Normalisation and validation helpers ---------------------------------------------------------


def clean_translations(value: Any, *, fields: Mapping[str, int]) -> dict[str, dict[str, str]]:
    """``{"bn": {"name": "...", ...}}`` for the store's supported non-default languages only."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValidationError({"translations": ["Use an object keyed by language."]})
    context = store_context()
    cleaned: dict[str, dict[str, str]] = {}
    for language, entry in value.items():
        if language not in context.languages or language == context.default_language:
            raise ValidationError(
                {"translations": [f"{language} is not an additional language of this store."]}
            )
        if not isinstance(entry, dict):
            raise ValidationError({"translations": ["Each language needs an object of texts."]})
        texts: dict[str, str] = {}
        for key, text in entry.items():
            limit = fields.get(key)
            if limit is None or not isinstance(text, str) or len(text) > limit:
                raise ValidationError(
                    {"translations": [f"{language}.{key} is not a translatable text."]}
                )
            if text.strip():
                texts[key] = text.strip()
        if texts:
            cleaned[language] = texts
    return cleaned


def normalize_sku(raw: str) -> str:
    sku = raw.strip().upper()
    if not sku:
        raise ValidationError({"sku": ["Enter a SKU."]})
    return sku


def _gtin_check_digit_valid(digits: str) -> bool:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(reversed(digits[:-1]), 1))
    return (10 - total % 10) % 10 == int(digits[-1])


def normalize_barcode(raw: str | None) -> str | None:
    """GTIN-8, UPC-A (12), EAN-13 or GTIN-14 digits with a valid check digit."""
    if raw is None or not raw.strip():
        return None
    digits = raw.strip().replace(" ", "").replace("-", "")
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        raise ValidationError({"barcode": ["Enter an 8, 12, 13 or 14 digit barcode."]})
    if not _gtin_check_digit_valid(digits):
        raise ValidationError({"barcode": ["This barcode's check digit is wrong."]})
    return digits


def normalize_isbn(raw: str | None) -> str | None:
    """ISBN-10 or ISBN-13 (hyphens and spaces allowed), stored as ISBN-13."""
    if raw is None or not raw.strip():
        return None
    value = raw.strip().replace("-", "").replace(" ", "").upper()
    if len(value) == 10 and value[:9].isdigit() and (value[9].isdigit() or value[9] == "X"):
        total = sum((10 - i) * int(d) for i, d in enumerate(value[:9]))
        total += 10 if value[9] == "X" else int(value[9])
        if total % 11:
            raise ValidationError({"isbn": ["This ISBN's check digit is wrong."]})
        body = "978" + value[:9]
        check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body)) % 10) % 10
        return f"{body}{check}"
    if len(value) == 13 and value.isdigit() and value[:3] in ("978", "979"):
        check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(value[:12])) % 10) % 10
        if check != int(value[12]):
            raise ValidationError({"isbn": ["This ISBN's check digit is wrong."]})
        return value
    raise ValidationError({"isbn": ["Enter an ISBN-10 or ISBN-13."]})


def _clean_tags(tags: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        text = " ".join(tag.split()).lower()
        if text and text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > MAX_TAGS:
        raise ValidationError({"tags": [f"Use at most {MAX_TAGS} tags."]})
    return cleaned


def unique_slug(model: type[Model], source: str, *, exclude_pk: Any = None) -> str:
    """A free slug for ``source`` (``name``, ``name-2``, ...). Names without Latin letters
    (for example Bangla) get a short generated slug; merchants can set their own."""
    base = slugify(source)[:120].strip("-") or f"item-{uuid7().hex[-8:]}"
    taken = set(
        model._default_manager.filter(slug__startswith=base)
        .exclude(pk=exclude_pk)
        .values_list("slug", flat=True)
    )
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def _require_image(asset: MediaAsset | None, field: str) -> None:
    if asset is not None and asset.kind != MediaKind.IMAGE:
        raise ValidationError({field: ["Choose an image."]})


# --- Brands and warranty policies -----------------------------------------------------------------


def save_brand(*, brand: Brand | None, data: Mapping[str, Any], actor: User | None) -> Brand:
    with transaction.atomic():
        creating = brand is None
        target = Brand() if brand is None else Brand.objects.select_for_update().get(pk=brand.pk)
        before = _snapshot(target, ["name", "slug", "is_active"])
        for field in ("name", "is_active"):
            if field in data:
                setattr(target, field, data[field])
        if data.get("slug"):
            target.slug = data["slug"]
        elif creating:
            target.slug = unique_slug(Brand, target.name)
        validated(target, exclude=["tenant"])
        save_unique(
            target, {"catalog_brand_slug_unique": ("slug", "Another brand uses this slug.")}
        )
        _audit(
            "catalog.brand.created" if creating else "catalog.brand.updated",
            target,
            actor,
            **field_changes(before, _snapshot(target, before), fields=list(before)),
        )
    return target


def delete_brand(*, brand: Brand, actor: User | None) -> None:
    with transaction.atomic():
        _audit("catalog.brand.deleted", brand, actor, slug=brand.slug)
        try:
            brand.delete()
        except ProtectedError as exc:
            raise ConflictError("Products still use this brand.", "brand_in_use") from exc


def save_warranty(
    *, policy: WarrantyPolicy | None, data: Mapping[str, Any], actor: User | None
) -> WarrantyPolicy:
    fields = ["name", "duration_months", "provider", "terms", "is_active"]
    with transaction.atomic():
        creating = policy is None
        target = (
            WarrantyPolicy()
            if policy is None
            else WarrantyPolicy.objects.select_for_update().get(pk=policy.pk)
        )
        before = _snapshot(target, fields)
        for field in fields:
            if field in data:
                setattr(target, field, data[field])
        validated(target, exclude=["tenant"])
        save_unique(
            target,
            {"catalog_warranty_name_unique": ("name", "Another warranty policy uses this name.")},
        )
        _audit(
            "catalog.warranty.created" if creating else "catalog.warranty.updated",
            target,
            actor,
            **field_changes(before, _snapshot(target, fields), fields=fields),
        )
    return target


def delete_warranty(*, policy: WarrantyPolicy, actor: User | None) -> None:
    with transaction.atomic():
        _audit("catalog.warranty.deleted", policy, actor, name=policy.name)
        try:
            policy.delete()
        except ProtectedError as exc:
            raise ConflictError("Products still use this warranty.", "warranty_in_use") from exc


# --- Categories -----------------------------------------------------------------------------------


def _ancestors(category_id: Any) -> list[Any]:
    """Ids from ``category_id`` up to its root (bounded: a corrupt chain stops at the limit)."""
    chain: list[Any] = []
    current = category_id
    while current is not None and len(chain) <= MAX_CATEGORY_DEPTH + 1:
        chain.append(current)
        current = Category.objects.filter(pk=current).values_list("parent_id", flat=True).first()
    return chain


def _subtree_height(category_id: Any) -> int:
    level, frontier = 1, [category_id]
    while True:
        frontier = list(
            Category.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)
        )
        if not frontier:
            return level
        level += 1
        if level > MAX_CATEGORY_DEPTH:
            return level


def _check_placement(category: Category, parent: Category | None) -> None:
    if parent is None:
        if not category._state.adding and _subtree_height(category.pk) > MAX_CATEGORY_DEPTH:
            raise ValidationError({"parent": ["The category tree is too deep."]})
        return
    chain = _ancestors(parent.pk)
    if not category._state.adding and category.pk in chain:
        raise ValidationError({"parent": ["A category cannot be placed inside itself."]})
    height = 1 if category._state.adding else _subtree_height(category.pk)
    if len(chain) + height > MAX_CATEGORY_DEPTH:
        raise ValidationError(
            {"parent": [f"Categories can be nested at most {MAX_CATEGORY_DEPTH} levels deep."]}
        )


GROUP_FIELDS = ["name", "slug", "description", "is_active", "image", "translations"]


def save_category(
    *, category: Category | None, data: Mapping[str, Any], actor: User | None
) -> Category:
    with transaction.atomic():
        _lock_catalogue_structure()
        creating = category is None
        target = (
            Category()
            if category is None
            else Category.objects.select_for_update().get(pk=category.pk)
        )
        audited = ["name", "slug", "parent_id", "position", "is_active", "image_id"]
        before = _snapshot(target, audited)
        for field in ("name", "description", "position", "is_active", "image"):
            if field in data:
                setattr(target, field, data[field])
        _require_image(target.image, "image")
        if "translations" in data:
            target.translations = clean_translations(
                data["translations"], fields=TRANSLATABLE_GROUP_FIELDS
            )
        if data.get("slug"):
            target.slug = data["slug"]
        elif creating:
            target.slug = unique_slug(Category, target.name)
        if "parent" in data or creating:
            parent = data.get("parent")
            _check_placement(target, parent)
            target.parent = parent
        validated(target, exclude=["tenant"])
        save_unique(
            target, {"catalog_category_slug_unique": ("slug", "Another category uses this slug.")}
        )
        _audit(
            "catalog.category.created" if creating else "catalog.category.updated",
            target,
            actor,
            **field_changes(before, _snapshot(target, audited), fields=audited),
        )
    return target


def delete_category(*, category: Category, actor: User | None) -> None:
    with transaction.atomic():
        _lock_catalogue_structure()
        if Category.objects.filter(parent=category).exists():
            raise ConflictError(
                "Move or delete this category's subcategories first.", "category_has_children"
            )
        if ProductCategory.objects.filter(category=category).exists():
            raise ConflictError(
                "Move this category's products to another category first.", "category_in_use"
            )
        _audit("catalog.category.deleted", category, actor, slug=category.slug)
        category.delete()


def category_tree(*, active_only: bool) -> list[dict[str, Any]]:
    """The category tree as nested dicts, siblings ordered by position then name."""
    rows = Category.objects.order_by("position", "name", "pk")
    if active_only:
        rows = rows.filter(is_active=True)
    nodes: dict[Any, dict[str, Any]] = {
        row.pk: {"category": row, "children": []}
        for row in rows.only(
            "id",
            "parent_id",
            "name",
            "slug",
            "description",
            "position",
            "is_active",
            "image_id",
            "translations",
        )
    }
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent_id = node["category"].parent_id
        if parent_id is None:
            roots.append(node)
        elif parent_id in nodes:  # children of an inactive parent stay hidden publicly
            nodes[parent_id]["children"].append(node)
    return roots


# --- Collections ----------------------------------------------------------------------------------


def save_collection(
    *, collection: Collection | None, data: Mapping[str, Any], actor: User | None
) -> Collection:
    with transaction.atomic():
        creating = collection is None
        target = (
            Collection()
            if collection is None
            else Collection.objects.select_for_update().get(pk=collection.pk)
        )
        audited = ["name", "slug", "is_active", "image_id"]
        before = _snapshot(target, audited)
        for field in ("name", "description", "is_active", "image"):
            if field in data:
                setattr(target, field, data[field])
        _require_image(target.image, "image")
        if "translations" in data:
            target.translations = clean_translations(
                data["translations"], fields=TRANSLATABLE_GROUP_FIELDS
            )
        if data.get("slug"):
            target.slug = data["slug"]
        elif creating:
            target.slug = unique_slug(Collection, target.name)
        validated(target, exclude=["tenant"])
        save_unique(
            target,
            {"catalog_collection_slug_unique": ("slug", "Another collection uses this slug.")},
        )
        _audit(
            "catalog.collection.created" if creating else "catalog.collection.updated",
            target,
            actor,
            **field_changes(before, _snapshot(target, audited), fields=audited),
        )
    return target


def set_collection_products(
    *, collection: Collection, products: Sequence[Product], actor: User | None
) -> None:
    """Replace the collection's products with ``products``, in that order."""
    ids = [product.pk for product in products]
    if len(set(ids)) != len(ids):
        raise ValidationError({"products": ["Each product can appear only once."]})
    if len(ids) > MAX_COLLECTION_PRODUCTS:
        raise ValidationError(
            {"products": [f"A collection holds at most {MAX_COLLECTION_PRODUCTS} products."]}
        )
    with transaction.atomic():
        locked = Collection.objects.select_for_update().get(pk=collection.pk)
        CollectionProduct.objects.filter(collection=locked).delete()
        CollectionProduct.objects.bulk_create(
            CollectionProduct(collection=locked, product=product, position=index)
            for index, product in enumerate(products)
        )
        _audit("catalog.collection.products_set", locked, actor, product_count=len(ids))


def delete_collection(*, collection: Collection, actor: User | None) -> None:
    with transaction.atomic():
        _audit("catalog.collection.deleted", collection, actor, slug=collection.slug)
        collection.delete()


# --- Attributes -----------------------------------------------------------------------------------


def _attribute_in_use(attribute: AttributeDefinition) -> bool:
    return (
        ProductOption.objects.filter(attribute=attribute).exists()
        or ProductAttributeValue.objects.filter(attribute=attribute).exists()
    )


def save_attribute(
    *, attribute: AttributeDefinition | None, data: Mapping[str, Any], actor: User | None
) -> AttributeDefinition:
    fields = ["code", "name", "value_type", "scope", "unit", "is_filterable", "position"]
    with transaction.atomic():
        creating = attribute is None
        target = (
            AttributeDefinition()
            if attribute is None
            else AttributeDefinition.objects.select_for_update().get(pk=attribute.pk)
        )
        before = _snapshot(target, fields)
        structural = {"code", "value_type", "scope", "unit"}
        restructured = any(field in data and data[field] != before[field] for field in structural)
        if not creating and restructured and _attribute_in_use(target):
            raise ConflictError(
                "Products use this attribute; its code, type, scope and unit are fixed.",
                "attribute_in_use",
            )
        for field in fields:
            if field in data:
                setattr(target, field, data[field])
        validated(target, exclude=["tenant"])
        save_unique(
            target,
            {"catalog_attribute_code_unique": ("code", "Another attribute uses this code.")},
        )
        _audit(
            "catalog.attribute.created" if creating else "catalog.attribute.updated",
            target,
            actor,
            **field_changes(before, _snapshot(target, fields), fields=fields),
        )
    return target


def delete_attribute(*, attribute: AttributeDefinition, actor: User | None) -> None:
    with transaction.atomic():
        if _attribute_in_use(attribute):
            raise ConflictError("Products use this attribute.", "attribute_in_use")
        _audit("catalog.attribute.deleted", attribute, actor, code=attribute.code)
        try:
            attribute.delete()
        except ProtectedError as exc:
            raise ConflictError("Products use this attribute.", "attribute_in_use") from exc


def save_option(
    *,
    attribute: AttributeDefinition,
    option: AttributeOption | None,
    data: Mapping[str, Any],
    actor: User | None,
) -> AttributeOption:
    if attribute.value_type != AttributeValueType.OPTION:
        raise DomainError("Only option attributes have options.", "attribute_not_option")
    fields = ["code", "value", "swatch", "position"]
    with transaction.atomic():
        creating = option is None
        target = (
            AttributeOption(attribute=attribute)
            if option is None
            else AttributeOption.objects.select_for_update().get(pk=option.pk, attribute=attribute)
        )
        before = _snapshot(target, fields)
        for field in fields:
            if field in data:
                value = data[field]
                setattr(target, field, value.upper() if field == "swatch" else value)
        if not target.code:
            target.code = slugify(target.value)[:64].strip("-") or uuid7().hex[-8:]
        validated(target, exclude=["tenant"])
        save_unique(
            target,
            {"catalog_attributeoption_unique": ("code", "This attribute already has this option.")},
        )
        _audit(
            "catalog.attribute.option_added" if creating else "catalog.attribute.option_updated",
            target,
            actor,
            **field_changes(before, _snapshot(target, fields), fields=fields),
        )
    return target


def delete_option(*, option: AttributeOption, actor: User | None) -> None:
    with transaction.atomic():
        _audit("catalog.attribute.option_deleted", option, actor, code=option.code)
        try:
            option.delete()
        except ProtectedError as exc:
            raise ConflictError("Variants or products use this option.", "option_in_use") from exc


# --- Products -------------------------------------------------------------------------------------


def has_inventory_history(variant_ids: Iterable[Any]) -> bool:
    ids = list(variant_ids)
    if not ids:
        return False
    return (
        StockMovement.objects.filter(variant_id__in=ids).exists()
        or Batch.objects.filter(variant_id__in=ids).exists()
        or SerialNumber.objects.filter(variant_id__in=ids).exists()
    )


def _product_has_history(product: Product) -> bool:
    return has_inventory_history(
        ProductVariant.objects.filter(product=product).values_list("pk", flat=True)
    )


def _check_product_type(product_type: str) -> None:
    allowed = ALLOWED_PRODUCT_TYPES.get(store_context().preset, frozenset())
    if product_type not in allowed:
        raise ValidationError(
            {"product_type": ["This store's preset does not offer this product type."]}
        )


def _set_categories(
    product: Product, categories: Sequence[Category], primary: Category | None
) -> None:
    if len({category.pk for category in categories}) != len(categories):
        raise ValidationError({"categories": ["Each category can appear only once."]})
    if primary is not None and primary.pk not in {category.pk for category in categories}:
        raise ValidationError({"primary_category": ["The primary category must be selected."]})
    ProductCategory.objects.filter(product=product).delete()
    chosen = primary or (categories[0] if categories else None)
    ProductCategory.objects.bulk_create(
        ProductCategory(
            product=product,
            category=category,
            is_primary=chosen is not None and category.pk == chosen.pk,
        )
        for category in categories
    )


def _set_options(product: Product, attributes: Sequence[AttributeDefinition]) -> None:
    if len(attributes) > MAX_PRODUCT_OPTIONS:
        raise ValidationError(
            {"options": [f"A product can have at most {MAX_PRODUCT_OPTIONS} options."]}
        )
    if len({attribute.pk for attribute in attributes}) != len(attributes):
        raise ValidationError({"options": ["Each option can appear only once."]})
    for attribute in attributes:
        if attribute.scope != AttributeScope.VARIANT:
            raise ValidationError(
                {"options": [f"{attribute.name} is a specification, not a variant option."]}
            )
    ProductOption.objects.filter(product=product).delete()
    ProductOption.objects.bulk_create(
        ProductOption(product=product, attribute=attribute, position=index)
        for index, attribute in enumerate(attributes)
    )


def _parse_spec_value(attribute: AttributeDefinition, value: Any) -> dict[str, Any]:
    kind = attribute.value_type
    error = ValidationError({"specifications": [f"{attribute.name}: enter a valid value."]})
    if kind == AttributeValueType.TEXT:
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise error
        return {"value_text": value.strip()}
    if kind in (AttributeValueType.NUMBER, AttributeValueType.MEASUREMENT):
        if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
            raise error
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise error from exc
        if (
            not number.is_finite()
            or abs(number) >= Decimal("1e12")
            or number != number.quantize(Decimal("0.000001"))
        ):
            raise error
        return {"value_number": number}
    if kind == AttributeValueType.BOOLEAN:
        if not isinstance(value, bool):
            raise error
        return {"value_boolean": value}
    option = AttributeOption.objects.filter(attribute=attribute, code=str(value)).first()
    if option is None:
        raise error
    return {"option": option}


def _set_specifications(product: Product, entries: Sequence[Mapping[str, Any]]) -> None:
    rows: list[ProductAttributeValue] = []
    seen: set[Any] = set()
    for entry in entries:
        attribute: AttributeDefinition = entry["attribute"]
        if attribute.scope != AttributeScope.SPECIFICATION:
            raise ValidationError(
                {"specifications": [f"{attribute.name} defines variants; it is not a spec."]}
            )
        if attribute.pk in seen:
            raise ValidationError({"specifications": [f"{attribute.name} appears twice."]})
        seen.add(attribute.pk)
        rows.append(
            ProductAttributeValue(
                product=product, attribute=attribute, **_parse_spec_value(attribute, entry["value"])
            )
        )
    ProductAttributeValue.objects.filter(product=product).delete()
    ProductAttributeValue.objects.bulk_create(rows)


def _set_details(product: Product, values: Mapping[str, Any] | None) -> None:
    model = DETAIL_MODELS.get(product.product_type)
    if model is None:
        if values:
            raise ValidationError({"details": ["Standard products have no preset details."]})
        return
    if values is None:
        return
    details = model._default_manager.filter(product=product).first() or model(product=product)
    for field in DETAIL_FIELDS[product.product_type]:
        if field in values:
            setattr(details, field, values[field])
    if isinstance(details, BookDetails):
        details.authors = [" ".join(a.split()) for a in details.authors if a and a.strip()]
    validated(details, exclude=["tenant", "product"])
    details.save()


def _clear_other_details(product: Product) -> None:
    for product_type, model in DETAIL_MODELS.items():
        if product_type != product.product_type:
            model._default_manager.filter(product=product).delete()


def _check_tracking(product: Product) -> None:
    if product.tracking_mode != TrackingMode.SERIAL:
        return
    unsuitable = ProductVariant.objects.filter(product=product).exclude(
        kind=VariantKind.PHYSICAL, inventory_unit=InventoryUnit.EACH
    )
    if unsuitable.exists():
        raise ValidationError(
            {"tracking_mode": ["Serial numbers need physical variants counted in units (each)."]}
        )


@dataclass(frozen=True)
class VariantOptions:
    rows: list[tuple[AttributeDefinition, AttributeOption]]
    signature: str
    title: str


def _resolve_options(product: Product, chosen: Mapping[str, str]) -> VariantOptions:
    product_options = list(
        ProductOption.objects.filter(product=product)
        .select_related("attribute")
        .order_by("position", "pk")
    )
    expected = {option.attribute.code for option in product_options}
    if set(chosen) != expected:
        if expected:
            names = ", ".join(sorted(expected))
            raise ValidationError({"options": [f"Choose exactly one value for: {names}."]})
        raise ValidationError({"options": ["This product has no options."]})
    rows: list[tuple[AttributeDefinition, AttributeOption]] = []
    for product_option in product_options:
        attribute = product_option.attribute
        option = AttributeOption.objects.filter(
            attribute=attribute, code=chosen[attribute.code]
        ).first()
        if option is None:
            raise ValidationError(
                {"options": [f"{chosen[attribute.code]} is not an option of {attribute.name}."]}
            )
        rows.append((attribute, option))
    signature = "|".join(f"{a.pk}={o.pk}" for a, o in sorted(rows, key=lambda row: str(row[0].pk)))
    title = " / ".join(option.value for _, option in rows)
    return VariantOptions(rows, signature, title)


def _apply_variant_fields(variant: ProductVariant, data: Mapping[str, Any]) -> None:
    for field in (
        "is_active",
        "kind",
        "digital_format",
        "weight_grams",
        "inventory_unit",
        "net_quantity",
        "net_unit",
        "release_date",
        "position",
    ):
        if field in data:
            setattr(variant, field, data[field])
    if "sku" in data:
        variant.sku = normalize_sku(data["sku"])
    if "barcode" in data:
        variant.barcode = normalize_barcode(data["barcode"])
    if "isbn" in data:
        variant.isbn = normalize_isbn(data["isbn"])
    if variant.kind == VariantKind.DIGITAL:
        variant.weight_grams = None
        variant.inventory_unit = InventoryUnit.EACH
    else:
        variant.digital_format = ""
    if (variant.net_quantity is None) != (not variant.net_unit):
        raise ValidationError({"net_quantity": ["Give both the net quantity and its unit."]})


def _create_variant(
    product: Product,
    data: Mapping[str, Any],
    *,
    position: int,
    actor: User | None,
    allow_cost: bool,
) -> ProductVariant:
    from commerce.pricing import services as pricing

    options = _resolve_options(product, data.get("options") or {})
    variant = ProductVariant(
        product=product,
        option_signature=options.signature,
        title=options.title,
        position=position,
    )
    _apply_variant_fields(variant, data)
    validated(variant, exclude=["tenant"])
    save_unique(variant, _VARIANT_CONFLICTS)
    VariantOptionValue.objects.bulk_create(
        VariantOptionValue(variant=variant, attribute=attribute, option=option)
        for attribute, option in options.rows
    )
    if data.get("price") is not None:
        pricing.set_prices(
            variant=variant,
            entries=[{**data["price"], "price_list": None}],
            actor=actor,
            allow_cost=allow_cost,
        )
    return variant


def create_product(*, data: Mapping[str, Any], actor: User | None, allow_cost: bool) -> Product:
    product_type = data.get("product_type", ProductType.STANDARD)
    _check_product_type(product_type)
    variants = list(data.get("variants") or [])
    if not 1 <= len(variants) <= MAX_VARIANTS:
        raise ValidationError({"variants": [f"Add between 1 and {MAX_VARIANTS} variants."]})
    with transaction.atomic():
        product = Product(
            name=data["name"],
            product_type=product_type,
            brand=data.get("brand"),
            summary=data.get("summary", ""),
            description=data.get("description", ""),
            tags=_clean_tags(data.get("tags", [])),
            seo_title=data.get("seo_title", ""),
            seo_description=data.get("seo_description", ""),
            track_inventory=data.get("track_inventory", True),
            tracking_mode=data.get("tracking_mode", TrackingMode.NONE),
            translations=clean_translations(
                data.get("translations"), fields=TRANSLATABLE_PRODUCT_FIELDS
            ),
        )
        product.slug = data.get("slug") or unique_slug(Product, product.name)
        validated(product, exclude=["tenant"])
        save_unique(
            product, {"catalog_product_slug_unique": ("slug", "Another product uses this slug.")}
        )
        _set_categories(product, data.get("categories", []), data.get("primary_category"))
        _set_options(product, data.get("options", []))
        for index, variant in enumerate(variants):
            _create_variant(product, variant, position=index, actor=actor, allow_cost=allow_cost)
        _check_tracking(product)
        _set_specifications(product, data.get("specifications", []))
        _set_details(product, data.get("details"))
        _audit(
            "catalog.product.created",
            product,
            actor,
            name=product.name,
            slug=product.slug,
            product_type=product.product_type,
            variant_count=len(variants),
        )
    return product


def update_product(*, product: Product, data: Mapping[str, Any], actor: User | None) -> Product:
    with transaction.atomic():
        target = Product.objects.select_for_update().get(pk=product.pk)
        before = _snapshot(target, PRODUCT_AUDIT_FIELDS)
        if "product_type" in data and data["product_type"] != target.product_type:
            if target.status != ProductStatus.DRAFT:
                raise ConflictError(
                    "Change a product's type only while it is a draft.", "product_not_draft"
                )
            _check_product_type(data["product_type"])
            target.product_type = data["product_type"]
            _clear_other_details(target)
        tracking_changed = any(
            field in data and data[field] != getattr(target, field)
            for field in ("track_inventory", "tracking_mode")
        )
        if tracking_changed:
            # Stock operations hold a lock on their variant: wait for them, then decide.
            list(ProductVariant.objects.select_for_update().filter(product=target).values("pk"))
        if tracking_changed and _product_has_history(target):
            raise ConflictError(
                "Stock has been recorded for this product; its tracking is fixed.",
                "inventory_history_exists",
            )
        for field in (
            "name",
            "brand",
            "summary",
            "description",
            "seo_title",
            "seo_description",
            "track_inventory",
            "tracking_mode",
        ):
            if field in data:
                setattr(target, field, data[field])
        if data.get("slug"):
            target.slug = data["slug"]
        if "tags" in data:
            target.tags = _clean_tags(data["tags"])
        if "translations" in data:
            target.translations = clean_translations(
                data["translations"], fields=TRANSLATABLE_PRODUCT_FIELDS
            )
        validated(target, exclude=["tenant"])
        save_unique(
            target, {"catalog_product_slug_unique": ("slug", "Another product uses this slug.")}
        )
        if "categories" in data or "primary_category" in data:
            current = [
                placement.category
                for placement in ProductCategory.objects.filter(product=target).select_related(
                    "category"
                )
            ]
            _set_categories(target, data.get("categories", current), data.get("primary_category"))
        if "specifications" in data:
            _set_specifications(target, data["specifications"])
        if "details" in data:
            _set_details(target, data["details"])
        _check_tracking(target)
        if target.status == ProductStatus.ACTIVE:
            problems = publication_problems(target)
            if problems:
                raise ValidationError({"publication": problems})
        _audit(
            "catalog.product.updated",
            target,
            actor,
            **field_changes(
                before, _snapshot(target, PRODUCT_AUDIT_FIELDS), fields=PRODUCT_AUDIT_FIELDS
            ),
            **(
                {"categories_changed": True}
                if "categories" in data or "primary_category" in data
                else {}
            ),
            **({"specifications_changed": True} if "specifications" in data else {}),
            **({"details_changed": True} if "details" in data else {}),
        )
    return target


def publication_problems(product: Product) -> list[str]:
    """Why ``product`` cannot be sold yet (empty when it can be published)."""
    from commerce.pricing import services as pricing

    problems: list[str] = []
    if not product.name.strip():
        problems.append("Give the product a name.")
    if product.product_type not in ALLOWED_PRODUCT_TYPES.get(store_context().preset, frozenset()):
        problems.append("This store's preset does not offer this product type.")
    variants = list(ProductVariant.objects.filter(product=product, is_active=True))
    if not variants:
        problems.append("Add at least one active variant.")
    price_list = pricing.find_default_price_list()
    priced = (
        set(
            pricing.Price.objects.filter(price_list=price_list, variant__in=variants).values_list(
                "variant_id", flat=True
            )
        )
        if price_list is not None
        else set()
    )
    for variant in variants:
        if variant.pk not in priced:
            problems.append(f"Set a price for {variant.sku}.")
        if (
            product.product_type == ProductType.GROCERY
            and variant.kind == VariantKind.PHYSICAL
            and variant.net_quantity is None
        ):
            problems.append(f"Set the pack size (net quantity) of {variant.sku}.")
    if product.product_type == ProductType.BOOK:
        book = BookDetails.objects.filter(product=product).first()
        if book is None or not book.authors:
            problems.append("Add the book's author.")
    return problems


_TRANSITIONS: dict[str, frozenset[str]] = {
    ProductStatus.DRAFT: frozenset({ProductStatus.ACTIVE, ProductStatus.ARCHIVED}),
    ProductStatus.ACTIVE: frozenset({ProductStatus.DRAFT, ProductStatus.ARCHIVED}),
    ProductStatus.ARCHIVED: frozenset({ProductStatus.DRAFT}),
}


def set_status(*, product: Product, status: str, actor: User | None) -> Product:
    with transaction.atomic():
        target = Product.objects.select_for_update().get(pk=product.pk)
        if status == target.status:
            return target
        if status not in _TRANSITIONS[target.status]:
            raise ConflictError(
                f"A {target.status} product cannot become {status}.", "invalid_transition"
            )
        if status == ProductStatus.ACTIVE:
            problems = publication_problems(target)
            if problems:
                raise ValidationError({"publication": problems})
            target.published_at = target.published_at or timezone.now()
        target.archived_at = timezone.now() if status == ProductStatus.ARCHIVED else None
        previous, target.status = target.status, status
        target.save(update_fields=["status", "published_at", "archived_at", "updated_at"])
        _audit(
            "catalog.product.status_changed",
            target,
            actor,
            status={"from": previous, "to": status},
        )
    return target


def delete_product(*, product: Product, actor: User | None) -> None:
    """Delete a product that was never published and has no inventory history; anything else is
    archived instead, so history keeps its references."""
    with transaction.atomic():
        target = Product.objects.select_for_update().get(pk=product.pk)
        if target.published_at is not None or _product_has_history(target):
            raise ConflictError(
                "This product has been published or stocked; archive it instead.",
                "product_has_history",
            )
        _audit("catalog.product.deleted", target, actor, slug=target.slug)
        target.delete()


def set_product_options(
    *, product: Product, attributes: Sequence[AttributeDefinition], actor: User | None
) -> None:
    with transaction.atomic():
        target = Product.objects.select_for_update().get(pk=product.pk)
        if ProductVariant.objects.filter(product=target).exists():
            raise ConflictError(
                "Remove the product's variants before changing its options.", "variants_exist"
            )
        _set_options(target, attributes)
        _audit(
            "catalog.product.options_set",
            target,
            actor,
            options=[attribute.code for attribute in attributes],
        )


# --- Variants -------------------------------------------------------------------------------------


def create_variant(
    *, product: Product, data: Mapping[str, Any], actor: User | None, allow_cost: bool
) -> ProductVariant:
    with transaction.atomic():
        target = Product.objects.select_for_update().get(pk=product.pk)
        if target.status == ProductStatus.ARCHIVED:
            raise ConflictError("Restore the product before adding variants.", "product_archived")
        count = ProductVariant.objects.filter(product=target).count()
        if count >= MAX_VARIANTS:
            raise ValidationError({"variants": [f"A product has at most {MAX_VARIANTS} variants."]})
        variant = _create_variant(
            target, data, position=data.get("position", count), actor=actor, allow_cost=allow_cost
        )
        _check_tracking(target)
        _audit(
            "catalog.variant.created",
            variant,
            actor,
            sku=variant.sku,
            title=variant.title,
            product_id=target.pk,
        )
    return variant


def update_variant(
    *, variant: ProductVariant, data: Mapping[str, Any], actor: User | None
) -> ProductVariant:
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=variant.product_id)
        target = ProductVariant.objects.select_for_update().get(pk=variant.pk)
        before = _snapshot(target, VARIANT_AUDIT_FIELDS)
        fixed = ("kind", "inventory_unit")
        changed = any(field in data and data[field] != before[field] for field in fixed)
        if changed and has_inventory_history([target.pk]):
            raise ConflictError(
                "Stock has been recorded for this variant; its kind and unit are fixed.",
                "inventory_history_exists",
            )
        if "options" in data:
            options = _resolve_options(product, data["options"])
            target.option_signature, target.title = options.signature, options.title
            VariantOptionValue.objects.filter(variant=target).delete()
            VariantOptionValue.objects.bulk_create(
                VariantOptionValue(variant=target, attribute=attribute, option=option)
                for attribute, option in options.rows
            )
        _apply_variant_fields(target, data)
        validated(target, exclude=["tenant"])
        save_unique(target, _VARIANT_CONFLICTS)
        _check_tracking(product)
        if product.status == ProductStatus.ACTIVE:
            problems = publication_problems(product)
            if problems:
                raise ValidationError({"publication": problems})
        _audit(
            "catalog.variant.updated",
            target,
            actor,
            **field_changes(
                before, _snapshot(target, VARIANT_AUDIT_FIELDS), fields=VARIANT_AUDIT_FIELDS
            ),
            **({"options_changed": True} if "options" in data else {}),
        )
    return target


def delete_variant(*, variant: ProductVariant, actor: User | None) -> None:
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=variant.product_id)
        target = ProductVariant.objects.select_for_update().get(pk=variant.pk)
        if has_inventory_history([target.pk]):
            raise ConflictError(
                "Stock has been recorded for this variant; deactivate it instead.",
                "inventory_history_exists",
            )
        _audit("catalog.variant.deleted", target, actor, sku=target.sku)
        target.delete()
        if product.status == ProductStatus.ACTIVE and publication_problems(product):
            raise ConflictError(
                "An active product needs at least one sellable variant; deactivate it instead.",
                "last_variant",
            )


# --- Product media --------------------------------------------------------------------------------


def set_product_media(
    *, product: Product, entries: Sequence[Mapping[str, Any]], actor: User | None
) -> None:
    """Replace the product's gallery. Order follows ``entries``; one image is primary (the first,
    unless another is marked)."""
    assets = [entry["asset"].pk for entry in entries]
    if len(set(assets)) != len(assets):
        raise ValidationError({"media": ["Each image can appear only once."]})
    if sum(1 for entry in entries if entry.get("is_primary")) > 1:
        raise ValidationError({"media": ["Choose one primary image."]})
    with transaction.atomic():
        target = Product.objects.select_for_update().get(pk=product.pk)
        rows: list[ProductMedia] = []
        has_primary = any(entry.get("is_primary") for entry in entries)
        for index, entry in enumerate(entries):
            asset: MediaAsset = entry["asset"]
            _require_image(asset, "media")
            variant: ProductVariant | None = entry.get("variant")
            if variant is not None and variant.product_id != target.pk:
                raise ValidationError({"media": ["That variant belongs to another product."]})
            rows.append(
                ProductMedia(
                    product=target,
                    asset=asset,
                    variant=variant,
                    position=index,
                    is_primary=bool(entry.get("is_primary")) or (not has_primary and index == 0),
                )
            )
        ProductMedia.objects.filter(product=target).delete()
        ProductMedia.objects.bulk_create(rows)
        _audit("catalog.product.media_set", target, actor, image_count=len(rows))


# --- Availability (integration point for the storefront and checkout) -----------------------------


def is_purchasable(variant: ProductVariant) -> bool:
    """Whether a customer could buy one unit now: active product and variant, a default price,
    and sellable stock when tracked (unexpired for batches, D-060) or a future release date for
    pre-orders. Checkout (Phase 07) re-checks this inside its reservation transaction."""
    from commerce.inventory import services as inventory
    from commerce.pricing import services as pricing

    product = variant.product
    if product.status != ProductStatus.ACTIVE or not variant.is_active:
        return False
    if pricing.quote(variant) is None:
        return False
    if not inventory.is_tracked(variant):
        return True
    if variant.release_date is not None and variant.release_date > inventory.store_today():
        return True
    return inventory.sellable_quantity(variant) > 0
