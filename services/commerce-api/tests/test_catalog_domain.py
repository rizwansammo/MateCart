"""Catalogue business rules: categories, lifecycle, variants, identifiers, attributes, presets."""

from decimal import Decimal
from typing import Any

import pytest
from rest_framework.exceptions import ValidationError

from commerce.audit.models import AuditEvent
from commerce.catalog import services as catalog
from commerce.catalog.models import (
    AttributeDefinition,
    AttributeOption,
    AttributeScope,
    AttributeValueType,
    BookDetails,
    Category,
    Product,
    ProductStatus,
    ProductVariant,
    TrackingMode,
)
from commerce.core.domain import ConflictError
from commerce.inventory import services as inventory
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, set_preset, simple_product, size_attribute
from .factories import make_store_settings

pytestmark = pytest.mark.usefixtures("store")


def category(tenant: Tenant, name: str, parent: Category | None = None) -> Category:
    with tenant_context(tenant):
        return catalog.save_category(
            category=None, data={"name": name, "parent": parent}, actor=None
        )


def errors(exc: pytest.ExceptionInfo[ValidationError]) -> Any:
    return exc.value.detail


# --- Categories -----------------------------------------------------------------------------------


def test_categories_nest_with_generated_unique_slugs(deployment_tenant: Tenant) -> None:
    men = category(deployment_tenant, "Men")
    shirts = category(deployment_tenant, "Shirts", men)
    again = category(deployment_tenant, "Shirts", men)

    assert (shirts.parent_id, shirts.slug, again.slug) == (men.pk, "shirts", "shirts-2")


def test_category_cycles_and_self_parents_are_refused(deployment_tenant: Tenant) -> None:
    root = category(deployment_tenant, "Root")
    child = category(deployment_tenant, "Child", root)
    grandchild = category(deployment_tenant, "Grandchild", child)

    with tenant_context(deployment_tenant):
        for new_parent in (grandchild, child, root):
            with pytest.raises(ValidationError) as exc:
                catalog.save_category(category=root, data={"parent": new_parent}, actor=None)
            assert "parent" in errors(exc)
        root.refresh_from_db()
        assert root.parent_id is None


def test_category_depth_is_limited(deployment_tenant: Tenant) -> None:
    parent = None
    for level in range(catalog.MAX_CATEGORY_DEPTH):
        parent = category(deployment_tenant, f"Level {level}", parent)
    with pytest.raises(ValidationError):
        category(deployment_tenant, "Too deep", parent)

    # Moving a whole subtree under a deep node counts the subtree's height too.
    other = category(deployment_tenant, "Other")
    category(deployment_tenant, "Other child", other)
    with tenant_context(deployment_tenant):
        fourth = Category.objects.get(name="Level 3")
        with pytest.raises(ValidationError):
            catalog.save_category(category=other, data={"parent": fourth}, actor=None)


def test_categories_in_use_or_with_children_are_not_deleted(deployment_tenant: Tenant) -> None:
    root = category(deployment_tenant, "Root")
    leaf = category(deployment_tenant, "Leaf", root)
    simple_product(deployment_tenant, categories=[leaf])

    with tenant_context(deployment_tenant):
        with pytest.raises(ConflictError, match="subcategories"):
            catalog.delete_category(category=root, actor=None)
        with pytest.raises(ConflictError, match="products"):
            catalog.delete_category(category=leaf, actor=None)
        empty = catalog.save_category(category=None, data={"name": "Empty"}, actor=None)
        catalog.delete_category(category=empty, actor=None)
        assert not Category.objects.filter(pk=empty.pk).exists()


def test_slugs_are_unique_per_store_only(deployment_tenant: Tenant, tenant_b: Tenant) -> None:
    make_store_settings(tenant_b)
    category(deployment_tenant, "Sale")
    other = category(tenant_b, "Sale")
    assert other.slug == "sale"
    with tenant_context(deployment_tenant), pytest.raises(ValidationError):
        catalog.save_category(category=None, data={"name": "X", "slug": "sale"}, actor=None)


# --- Product lifecycle ----------------------------------------------------------------------------


def test_a_simple_product_has_one_default_variant_and_publishes(deployment_tenant: Tenant) -> None:
    product = simple_product(deployment_tenant, publish=True)

    with tenant_context(deployment_tenant):
        variant = ProductVariant.objects.get(product=product)
        product.refresh_from_db()
        assert (variant.sku, variant.title, variant.option_signature) == ("TEE-1", "", "")
        assert product.status == ProductStatus.ACTIVE
        assert product.published_at is not None
        assert AuditEvent.objects.filter(action="catalog.product.status_changed").exists()


def test_products_without_a_price_cannot_be_published(deployment_tenant: Tenant) -> None:
    product = simple_product(deployment_tenant, amount=None)

    with tenant_context(deployment_tenant), pytest.raises(ValidationError) as exc:
        catalog.set_status(product=product, status="active", actor=None)
    assert "Set a price for TEE-1." in errors(exc)["publication"]


@pytest.mark.parametrize(("current", "target"), [("archived", "active"), ("archived", "archived")])
def test_lifecycle_transitions(deployment_tenant: Tenant, current: str, target: str) -> None:
    product = simple_product(deployment_tenant)
    with tenant_context(deployment_tenant):
        catalog.set_status(product=product, status="archived", actor=None)
        if current == target:
            assert catalog.set_status(product=product, status=target, actor=None).status == target
            return
        with pytest.raises(ConflictError):
            catalog.set_status(product=product, status=target, actor=None)
        restored = catalog.set_status(product=product, status="draft", actor=None)
        assert restored.archived_at is None


def test_published_or_stocked_products_are_archived_not_deleted(deployment_tenant: Tenant) -> None:
    published = simple_product(deployment_tenant, sku="PUB-1", publish=True)
    stocked = simple_product(deployment_tenant, sku="STK-1", name="Stocked")
    fresh = simple_product(deployment_tenant, sku="NEW-1", name="Fresh")
    with tenant_context(deployment_tenant):
        inventory.receive(
            variant=first_variant(deployment_tenant, stocked),
            location=inventory.default_location(),
            quantity=1,
            request=inventory.StockRequest(),
        )
        catalog.set_status(product=published, status="draft", actor=None)
        for product in (published, stocked):
            with pytest.raises(ConflictError):
                catalog.delete_product(product=product, actor=None)
        catalog.delete_product(product=fresh, actor=None)
        assert set(Product.objects.values_list("slug", flat=True)) == {"plain-tee", "stocked"}


def test_active_products_keep_a_sellable_variant(deployment_tenant: Tenant) -> None:
    product = simple_product(deployment_tenant, publish=True)
    variant = first_variant(deployment_tenant, product)
    with tenant_context(deployment_tenant):
        with pytest.raises(ValidationError):
            catalog.update_variant(variant=variant, data={"is_active": False}, actor=None)
        with pytest.raises(ConflictError):
            catalog.delete_variant(variant=variant, actor=None)
        assert ProductVariant.objects.filter(pk=variant.pk, is_active=True).exists()


# --- SKUs, identifiers and variants ---------------------------------------------------------------


def test_skus_are_normalised_and_unique_per_store(
    deployment_tenant: Tenant, tenant_b: Tenant
) -> None:
    make_store_settings(tenant_b)
    product = simple_product(deployment_tenant, sku="  tee-blue_1 ")
    assert first_variant(deployment_tenant, product).sku == "TEE-BLUE_1"

    with pytest.raises(ValidationError) as exc:
        simple_product(deployment_tenant, sku="TEE-BLUE_1", name="Copy")
    assert "sku" in errors(exc)
    with pytest.raises(ValidationError):
        simple_product(deployment_tenant, sku="has space", name="Bad")
    # Another store may use the same SKU.
    assert first_variant(tenant_b, simple_product(tenant_b, sku="TEE-BLUE_1")).sku == "TEE-BLUE_1"


def options_product(tenant: Tenant, variants: list[dict[str, Any]]) -> Product:
    with tenant_context(tenant):
        attribute = AttributeDefinition.objects.get(code="size")
        return catalog.create_product(
            data={
                "name": "Shirt",
                "product_type": "apparel",
                "options": [attribute],
                "variants": variants,
            },
            actor=None,
            allow_cost=False,
        )


def test_variant_combinations_are_unique_and_complete(deployment_tenant: Tenant) -> None:
    size_attribute(deployment_tenant)
    product = options_product(
        deployment_tenant,
        [{"sku": "SH-S", "options": {"size": "s"}}, {"sku": "SH-M", "options": {"size": "m"}}],
    )
    with tenant_context(deployment_tenant):
        titles = list(
            ProductVariant.objects.filter(product=product)
            .order_by("position")
            .values_list("title", flat=True)
        )
        assert titles == ["S", "M"]
        for options, message in (
            ({"size": "s"}, "already has a variant"),
            ({}, "Choose exactly one value"),
            ({"size": "xxl"}, "not an option"),
            ({"size": "m", "colour": "red"}, "Choose exactly one value"),
        ):
            with pytest.raises(ValidationError, match=message):
                catalog.create_variant(
                    product=product,
                    data={"sku": f"SH-{len(options)}-{message[:3]}", "options": options},
                    actor=None,
                    allow_cost=False,
                )


def test_specification_attributes_cannot_define_variants(deployment_tenant: Tenant) -> None:
    with tenant_context(deployment_tenant):
        material = AttributeDefinition.objects.create(
            code="material",
            name="Material",
            value_type=AttributeValueType.TEXT,
            scope=AttributeScope.SPECIFICATION,
        )
        with pytest.raises(ValidationError, match="specification"):
            catalog.create_product(
                data={"name": "X", "options": [material], "variants": [{"sku": "X-1"}]},
                actor=None,
                allow_cost=False,
            )


def test_options_change_only_without_variants(deployment_tenant: Tenant) -> None:
    attribute = size_attribute(deployment_tenant)
    product = simple_product(deployment_tenant)
    variant = first_variant(deployment_tenant, product)
    with tenant_context(deployment_tenant):
        with pytest.raises(ConflictError):
            catalog.set_product_options(product=product, attributes=[attribute], actor=None)
        catalog.delete_variant(variant=variant, actor=None)
        catalog.set_product_options(product=product, attributes=[attribute], actor=None)
        created = catalog.create_variant(
            product=product,
            data={"sku": "TEE-L", "options": {"size": "l"}},
            actor=None,
            allow_cost=False,
        )
        assert created.title == "L"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("4006381333931", "4006381333931"), ("0-12345-67890-5", "012345678905"), ("", None)],
)
def test_barcodes_are_gtins_with_valid_check_digits(raw: str, expected: str | None) -> None:
    assert catalog.normalize_barcode(raw) == expected


@pytest.mark.parametrize("raw", ["4006381333932", "123", "ABCDEFGH"])
def test_invalid_barcodes_are_refused(raw: str) -> None:
    with pytest.raises(ValidationError):
        catalog.normalize_barcode(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0-306-40615-2", "9780306406157"),
        ("978-0-306-40615-7", "9780306406157"),
        ("080442957X", "9780804429573"),
    ],
)
def test_isbns_are_stored_as_isbn_13(raw: str, expected: str) -> None:
    assert catalog.normalize_isbn(raw) == expected


@pytest.mark.parametrize("raw", ["0-306-40615-3", "9780306406158", "1234567890123", "12345"])
def test_invalid_isbns_are_refused(raw: str) -> None:
    with pytest.raises(ValidationError):
        catalog.normalize_isbn(raw)


# --- Presets --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset", "allowed", "refused"),
    [
        ("fashion", "apparel", "book"),
        ("grocery", "grocery", "electronics"),
        ("electronics", "electronics", "apparel"),
        ("books", "book", "grocery"),
    ],
)
def test_presets_offer_their_product_types(
    deployment_tenant: Tenant, preset: str, allowed: str, refused: str
) -> None:
    set_preset(deployment_tenant, preset)
    assert simple_product(deployment_tenant, product_type=allowed).product_type == allowed
    assert simple_product(deployment_tenant, sku="STD-1", name="Plain").product_type == "standard"
    with pytest.raises(ValidationError, match="preset"):
        simple_product(deployment_tenant, sku="NO-1", name="Refused", product_type=refused)


def test_general_stores_offer_every_type(deployment_tenant: Tenant) -> None:
    for index, product_type in enumerate(("apparel", "grocery", "electronics", "book")):
        product = simple_product(
            deployment_tenant, sku=f"G-{index}", name=product_type, product_type=product_type
        )
        assert product.product_type == product_type


def test_books_own_isbn_per_format_and_need_an_author(deployment_tenant: Tenant) -> None:
    set_preset(deployment_tenant, "books")
    with tenant_context(deployment_tenant):
        fmt = AttributeDefinition.objects.create(
            code="format", name="Format", value_type="option", scope="variant"
        )
        for code in ("paperback", "hardcover", "ebook"):
            AttributeOption.objects.create(attribute=fmt, code=code, value=code.title())
        product = catalog.create_product(
            data={
                "name": "Gitanjali",
                "product_type": "book",
                "options": [fmt],
                "variants": [
                    {
                        "sku": "GIT-PB",
                        "options": {"format": "paperback"},
                        "isbn": "0-306-40615-2",
                        "price": {"amount": Decimal("650")},
                    },
                    {
                        "sku": "GIT-EB",
                        "options": {"format": "ebook"},
                        "kind": "digital",
                        "digital_format": "epub",
                        "price": {"amount": Decimal("240")},
                    },
                ],
                "details": {"authors": [], "language": "en", "page_count": 160},
            },
            actor=None,
            allow_cost=False,
        )
        with pytest.raises(ValidationError) as exc:
            catalog.set_status(product=product, status="active", actor=None)
        assert "Add the book's author." in errors(exc)["publication"]
        catalog.update_product(
            product=product,
            data={"details": {"authors": ["  Rabindranath   Tagore "]}},
            actor=None,
        )
        catalog.set_status(product=product, status="active", actor=None)
        assert BookDetails.objects.get(product=product).authors == ["Rabindranath Tagore"]
        ebook = ProductVariant.objects.get(sku="GIT-EB")
        assert ebook.isbn is None
        assert ebook.weight_grams is None
        with pytest.raises(ValidationError, match="ISBN"):
            catalog.create_variant(
                product=product,
                data={"sku": "GIT-HC", "options": {"format": "hardcover"}, "isbn": "9780306406157"},
                actor=None,
                allow_cost=False,
            )


def test_grocery_variants_need_a_pack_size_to_publish(deployment_tenant: Tenant) -> None:
    set_preset(deployment_tenant, "grocery")
    product = simple_product(deployment_tenant, product_type="grocery", sku="RICE-5KG")
    with tenant_context(deployment_tenant):
        with pytest.raises(ValidationError, match="pack size"):
            catalog.set_status(product=product, status="active", actor=None)
        variant = ProductVariant.objects.get(product=product)
        catalog.update_variant(
            variant=variant, data={"net_quantity": Decimal("5"), "net_unit": "kg"}, actor=None
        )
        assert catalog.set_status(product=product, status="active", actor=None).status == "active"
        with pytest.raises(ValidationError):
            catalog.update_variant(variant=variant, data={"net_unit": ""}, actor=None)


def test_details_belong_to_their_product_type(deployment_tenant: Tenant) -> None:
    with pytest.raises(ValidationError, match="Standard products"):
        simple_product(deployment_tenant, details={"material": "Cotton"})


def test_serial_tracking_needs_whole_physical_units(deployment_tenant: Tenant) -> None:
    with pytest.raises(ValidationError, match="Serial numbers"):
        simple_product(
            deployment_tenant,
            tracking_mode=TrackingMode.SERIAL,
            variant={"inventory_unit": "kg"},
        )
    product = simple_product(deployment_tenant, sku="PHONE-1", tracking_mode="serial")
    assert product.tracking_mode == "serial"


def test_tracking_is_fixed_once_stock_is_recorded(deployment_tenant: Tenant) -> None:
    product = simple_product(deployment_tenant)
    with tenant_context(deployment_tenant):
        catalog.update_product(product=product, data={"tracking_mode": "batch"}, actor=None)
        catalog.update_product(product=product, data={"tracking_mode": "none"}, actor=None)
        inventory.receive(
            variant=first_variant(deployment_tenant, product),
            location=inventory.default_location(),
            quantity=2,
            request=inventory.StockRequest(),
        )
        with pytest.raises(ConflictError, match="tracking is fixed"):
            catalog.update_product(product=product, data={"track_inventory": False}, actor=None)
        with pytest.raises(ConflictError):
            catalog.update_variant(
                variant=first_variant(deployment_tenant, product),
                data={"inventory_unit": "kg"},
                actor=None,
            )


# --- Attributes and translations ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value_type", "value", "ok"),
    [
        ("text", "Cotton", True),
        ("text", "", False),
        ("number", "16", True),
        ("number", "1.5", True),
        ("number", "abc", False),
        ("number", True, False),
        ("boolean", True, True),
        ("boolean", "yes", False),
        ("measurement", "6000", True),
        ("option", "oled", True),
        ("option", "missing", False),
    ],
)
def test_specifications_are_typed(
    deployment_tenant: Tenant, value_type: str, value: Any, ok: bool
) -> None:
    with tenant_context(deployment_tenant):
        attribute = AttributeDefinition.objects.create(
            code="spec",
            name="Spec",
            value_type=value_type,
            scope="specification",
            unit="mAh" if value_type == "measurement" else "",
        )
        AttributeOption.objects.create(attribute=attribute, code="oled", value="OLED")
    data: dict[str, Any] = {"specifications": [{"attribute": attribute, "value": value}]}
    if ok:
        product = simple_product(deployment_tenant, **data)
        with tenant_context(deployment_tenant):
            assert product.specifications.count() == 1
    else:
        with pytest.raises(ValidationError):
            simple_product(deployment_tenant, **data)


def test_attribute_structure_is_fixed_once_used(deployment_tenant: Tenant) -> None:
    attribute = size_attribute(deployment_tenant)
    with tenant_context(deployment_tenant):
        catalog.save_attribute(
            attribute=attribute, data={"value_type": "text", "scope": "specification"}, actor=None
        )
        catalog.save_attribute(
            attribute=attribute, data={"value_type": "option", "scope": "variant"}, actor=None
        )
    options_product(deployment_tenant, [{"sku": "SH-S", "options": {"size": "s"}}])
    with tenant_context(deployment_tenant):
        with pytest.raises(ConflictError):
            catalog.save_attribute(attribute=attribute, data={"code": "fit"}, actor=None)
        renamed = catalog.save_attribute(
            attribute=attribute, data={"name": "Size (EU)"}, actor=None
        )
        assert renamed.name == "Size (EU)"
        with pytest.raises(ConflictError):
            catalog.delete_option(option=AttributeOption.objects.get(code="s"), actor=None)


@pytest.mark.parametrize(
    ("translations", "ok"),
    [
        ({"bn": {"name": "সাদা টি-শার্ট"}}, True),
        ({"en": {"name": "Default language"}}, False),
        ({"fr": {"name": "Pas offert"}}, False),
        ({"bn": {"price": "1"}}, False),
        ({"bn": "not an object"}, False),
        ("text", False),
    ],
)
def test_translations_follow_store_languages(
    deployment_tenant: Tenant, translations: Any, ok: bool
) -> None:
    if ok:
        product = simple_product(deployment_tenant, translations=translations)
        assert product.translations == translations
    else:
        with pytest.raises(ValidationError):
            simple_product(deployment_tenant, translations=translations)


def test_store_settings_are_required(deployment_tenant: Tenant) -> None:
    with tenant_context(deployment_tenant):
        StoreSettings.objects.all().delete()
    with pytest.raises(Exception, match="Store settings"):
        simple_product(deployment_tenant)


def test_primary_category_can_change_on_its_own(deployment_tenant: Tenant) -> None:
    shirts = category(deployment_tenant, "Shirts")
    sale = category(deployment_tenant, "Sale")
    product = simple_product(deployment_tenant, categories=[shirts, sale])
    with tenant_context(deployment_tenant):
        catalog.update_product(product=product, data={"primary_category": sale}, actor=None)
        placements = dict(
            product.placements.values_list("category__slug", "is_primary").order_by(
                "category__slug"
            )
        )
    assert placements == {"sale": True, "shirts": False}
