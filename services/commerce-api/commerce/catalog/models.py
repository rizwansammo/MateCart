"""The product catalogue: one engine for every preset (D-051).

Shared models (categories, collections, brands, typed attributes, products, variants) carry every
store. Preset-specific data lives in typed one-to-one detail tables (apparel, grocery,
electronics, books) and in typed attribute values, never in free-form JSON. Which product types a
store may use follows its preset (``services.ALLOWED_PRODUCT_TYPES``).

Products and variants that may be referenced by history (stock movements, later orders) are
archived or deactivated, not deleted. JSON appears only for translated display text, which is
validated against the store's languages.
"""

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q

from commerce.core.models import TimestampedModel
from commerce.core.standards import validate_country_code
from commerce.media.models import MediaAsset
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
# Uppercase letters, digits and . _ - ; starts and ends with a letter or digit; at most 64.
SKU_PATTERN = r"^[A-Z0-9](?:[A-Z0-9._-]{0,62}[A-Z0-9])?$"
GTIN_PATTERN = r"^(?:[0-9]{8}|[0-9]{12,14})$"
ISBN13_PATTERN = r"^97[89][0-9]{10}$"
SWATCH_PATTERN = r"^#[0-9A-F]{6}$"
LANGUAGE_TAG_PATTERN = r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"

slug_validator = RegexValidator(SLUG_PATTERN, "Use lowercase letters, digits and hyphens.")


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class ProductType(models.TextChoices):
    """What kind of item a product is; selects its typed details and publication rules."""

    STANDARD = "standard", "Standard"
    APPAREL = "apparel", "Apparel"
    GROCERY = "grocery", "Grocery"
    ELECTRONICS = "electronics", "Electronics"
    BOOK = "book", "Book"


class TrackingMode(models.TextChoices):
    """How individual units are identified in stock, beyond plain quantities."""

    NONE = "none", "Quantity only"
    BATCH = "batch", "Batches and expiry"
    SERIAL = "serial", "Serial numbers"


class VariantKind(models.TextChoices):
    PHYSICAL = "physical", "Physical"
    DIGITAL = "digital", "Digital"


class InventoryUnit(models.TextChoices):
    """The unit stock is counted in. Only ``each`` is whole-number; the others allow 3 decimals
    for goods sold by weight, volume or length."""

    EACH = "each", "Each"
    KILOGRAM = "kg", "Kilogram"
    GRAM = "g", "Gram"
    LITRE = "l", "Litre"
    MILLILITRE = "ml", "Millilitre"
    METRE = "m", "Metre"


class NetUnit(models.TextChoices):
    """The unit of a variant's net contents (pack size), e.g. 500 g or 12 pcs."""

    PIECES = "pcs", "Pieces"
    GRAM = "g", "Gram"
    KILOGRAM = "kg", "Kilogram"
    MILLILITRE = "ml", "Millilitre"
    LITRE = "l", "Litre"


class DigitalFormat(models.TextChoices):
    EPUB = "epub", "EPUB"
    PDF = "pdf", "PDF"
    AUDIOBOOK = "audiobook", "Audiobook"
    OTHER = "other", "Other"


class AttributeValueType(models.TextChoices):
    TEXT = "text", "Text"
    NUMBER = "number", "Number"
    BOOLEAN = "boolean", "Yes / no"
    OPTION = "option", "Option"
    MEASUREMENT = "measurement", "Measurement"


class AttributeScope(models.TextChoices):
    """``variant`` attributes define variants (size, colour, format); ``specification``
    attributes describe a product (material, RAM, battery)."""

    VARIANT = "variant", "Defines variants"
    SPECIFICATION = "specification", "Specification"


class WarrantyProvider(models.TextChoices):
    MANUFACTURER = "manufacturer", "Manufacturer"
    SELLER = "seller", "Seller"


class ApparelAudience(models.TextChoices):
    WOMEN = "women", "Women"
    MEN = "men", "Men"
    UNISEX = "unisex", "Unisex"
    KIDS = "kids", "Kids"


def _choice(field: str, choices: type[models.TextChoices], name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=Q(**{f"{field}__in": choices.values}), name=name)


def _slug_format(name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=Q(slug__regex=SLUG_PATTERN), name=name)


# --- Organisation ---------------------------------------------------------------------------------


class Brand(TenantOwnedModel, TimestampedModel):
    """Brand or manufacturer shown with products (electronics, fashion labels, publishers' imprints
    can use it too)."""

    name = models.CharField(max_length=120)
    slug = models.CharField(max_length=140, validators=[slug_validator])
    is_active = models.BooleanField(default=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant", "slug"], name="catalog_brand_slug_unique"),
            _slug_format("catalog_brand_slug_format"),
        ]

    def __str__(self) -> str:
        return self.name


class Category(TenantOwnedModel, TimestampedModel):
    """A node in the store's category tree (adjacency list, at most ``MAX_CATEGORY_DEPTH`` levels).

    Moves run through ``services.move_category``, which serialises tree changes per store and
    refuses cycles; the database also refuses a category being its own parent.
    """

    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    name = models.CharField(max_length=120)
    slug = models.CharField(max_length=140, validators=[slug_validator])
    description = models.TextField(blank=True, max_length=5000)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    image = models.ForeignKey(
        MediaAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    translations = models.JSONField(default=dict, blank=True)

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "categories"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="parent", name="catalog_category_parent_same_tenant"),
            TenantForeignKeyConstraint(field="image", name="catalog_category_image_same_tenant"),
            models.UniqueConstraint(fields=["tenant", "slug"], name="catalog_category_slug_unique"),
            _slug_format("catalog_category_slug_format"),
            models.CheckConstraint(
                condition=~Q(parent=F("id")), name="catalog_category_not_own_parent"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "parent", "position"], name="catalog_category_siblings")
        ]

    def __str__(self) -> str:
        return self.name


class Collection(TenantOwnedModel, TimestampedModel):
    """A merchandising group with an explicit product order (rule-based collections: Phase 12)."""

    name = models.CharField(max_length=120)
    slug = models.CharField(max_length=140, validators=[slug_validator])
    description = models.TextField(blank=True, max_length=5000)
    is_active = models.BooleanField(default=True)
    image = models.ForeignKey(
        MediaAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    translations = models.JSONField(default=dict, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="image", name="catalog_collection_image_same_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "slug"], name="catalog_collection_slug_unique"
            ),
            _slug_format("catalog_collection_slug_format"),
        ]

    def __str__(self) -> str:
        return self.name


# --- Attributes -----------------------------------------------------------------------------------


class AttributeDefinition(TenantOwnedModel, TimestampedModel):
    """A typed, reusable attribute: Size, Colour, Format (variant-defining options) or Material,
    RAM, Battery (specifications). The type decides which value column a product may use."""

    code = models.CharField(max_length=64, validators=[slug_validator])
    name = models.CharField(max_length=100)
    value_type = models.CharField(max_length=16, choices=AttributeValueType.choices)
    scope = models.CharField(max_length=16, choices=AttributeScope.choices)
    unit = models.CharField(max_length=20, blank=True, help_text="For numbers and measurements.")
    is_filterable = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "code"], name="catalog_attribute_code_unique"
            ),
            models.CheckConstraint(
                condition=Q(code__regex=SLUG_PATTERN), name="catalog_attribute_code_format"
            ),
            _choice("value_type", AttributeValueType, "catalog_attribute_value_type_valid"),
            _choice("scope", AttributeScope, "catalog_attribute_scope_valid"),
            # Variants are chosen from a fixed list, so variant attributes are always options.
            models.CheckConstraint(
                condition=~Q(scope=AttributeScope.VARIANT)
                | Q(value_type=AttributeValueType.OPTION),
                name="catalog_attribute_variant_scope_is_option",
            ),
            models.CheckConstraint(
                condition=Q(unit="")
                | Q(value_type__in=[AttributeValueType.NUMBER, AttributeValueType.MEASUREMENT]),
                name="catalog_attribute_unit_only_for_numbers",
            ),
            models.CheckConstraint(
                condition=~Q(value_type=AttributeValueType.MEASUREMENT) | ~Q(unit=""),
                name="catalog_attribute_measurement_has_unit",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class AttributeOption(TenantOwnedModel, TimestampedModel):
    """One allowed value of an option attribute (``M``, ``Black``, ``Hardcover``)."""

    attribute = models.ForeignKey(
        AttributeDefinition, on_delete=models.CASCADE, related_name="options"
    )
    code = models.CharField(max_length=64, validators=[slug_validator])
    value = models.CharField(max_length=100)
    swatch = models.CharField(
        max_length=7, blank=True, validators=[RegexValidator(SWATCH_PATTERN)], help_text="#RRGGBB"
    )
    position = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="attribute", name="catalog_attributeoption_attribute_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "attribute", "code"], name="catalog_attributeoption_unique"
            ),
            models.CheckConstraint(
                condition=Q(code__regex=SLUG_PATTERN), name="catalog_attributeoption_code_format"
            ),
            models.CheckConstraint(
                condition=Q(swatch="") | Q(swatch__regex=SWATCH_PATTERN),
                name="catalog_attributeoption_swatch_format",
            ),
        ]

    def __str__(self) -> str:
        return self.value


# --- Products -------------------------------------------------------------------------------------


class Product(TenantOwnedModel, TimestampedModel):
    """A sellable item. Customers buy its variants; a simple product has exactly one.

    Lifecycle (``services.set_status``): draft → active (publication checks) → draft or
    archived; archived → draft. Only active products appear in the public catalogue. Products are
    archived, not deleted, once they have inventory history.
    """

    name = models.CharField(max_length=200)
    slug = models.CharField(max_length=220, validators=[slug_validator])
    product_type = models.CharField(
        max_length=16, choices=ProductType.choices, default=ProductType.STANDARD
    )
    status = models.CharField(
        max_length=16, choices=ProductStatus.choices, default=ProductStatus.DRAFT
    )
    brand = models.ForeignKey(
        Brand, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    summary = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True, max_length=20000)
    tags = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    seo_title = models.CharField(max_length=70, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    track_inventory = models.BooleanField(default=True)
    tracking_mode = models.CharField(
        max_length=16, choices=TrackingMode.choices, default=TrackingMode.NONE
    )
    translations = models.JSONField(default=dict, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="brand", name="catalog_product_brand_same_tenant"),
            models.UniqueConstraint(fields=["tenant", "slug"], name="catalog_product_slug_unique"),
            _slug_format("catalog_product_slug_format"),
            _choice("status", ProductStatus, "catalog_product_status_valid"),
            _choice("product_type", ProductType, "catalog_product_type_valid"),
            _choice("tracking_mode", TrackingMode, "catalog_product_tracking_valid"),
            models.CheckConstraint(
                condition=~Q(status=ProductStatus.ACTIVE) | Q(published_at__isnull=False),
                name="catalog_product_active_is_published",
            ),
            models.CheckConstraint(
                condition=Q(status=ProductStatus.ARCHIVED, archived_at__isnull=False)
                | (~Q(status=ProductStatus.ARCHIVED) & Q(archived_at__isnull=True)),
                name="catalog_product_archived_at_matches_status",
            ),
            models.CheckConstraint(
                condition=Q(tracking_mode=TrackingMode.NONE) | Q(track_inventory=True),
                name="catalog_product_unit_tracking_needs_inventory",
            ),
            models.CheckConstraint(
                condition=Q(tags__len__lte=20), name="catalog_product_tags_limit"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"], name="catalog_product_status"),
        ]

    def __str__(self) -> str:
        return self.name


class ProductCategory(TenantOwnedModel):
    """Places a product in a category; at most one placement per product is primary."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="placements")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="+")
    is_primary = models.BooleanField(default=False)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_productcategory_product_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="category", name="catalog_productcategory_category_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "product", "category"], name="catalog_productcategory_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant", "product"],
                condition=Q(is_primary=True),
                name="catalog_productcategory_one_primary",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "category"], name="catalog_productcategory_cat"),
        ]


class CollectionProduct(TenantOwnedModel):
    """A product's place in a collection's explicit order."""

    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="entries")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")
    position = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="collection", name="catalog_collectionproduct_collection_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="product", name="catalog_collectionproduct_product_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "collection", "product"],
                name="catalog_collectionproduct_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "collection", "position"], name="catalog_collection_order"
            ),
        ]


class ProductOption(TenantOwnedModel):
    """A variant-defining attribute of one product (its variants differ by these, in order)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="options")
    attribute = models.ForeignKey(AttributeDefinition, on_delete=models.PROTECT, related_name="+")
    position = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_productoption_product_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="attribute", name="catalog_productoption_attribute_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "product", "attribute"], name="catalog_productoption_unique"
            ),
        ]


class ProductVariant(TenantOwnedModel, TimestampedModel):
    """What a customer actually buys: a SKU with its own options, identifiers, units and price.

    ``option_signature`` is the canonical list of the variant's option values, so the database
    refuses two variants of one product with the same combination. SKUs, barcodes and ISBNs are
    unique within the store. Deactivate variants with history; delete only unused ones.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, validators=[RegexValidator(SKU_PATTERN)])
    title = models.CharField(max_length=200, blank=True)
    option_signature = models.CharField(max_length=500, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    kind = models.CharField(
        max_length=16, choices=VariantKind.choices, default=VariantKind.PHYSICAL
    )
    digital_format = models.CharField(max_length=16, choices=DigitalFormat.choices, blank=True)
    barcode = models.CharField(  # noqa: DJ001 (NULL = absent, so uniqueness ignores it)
        max_length=14, null=True, blank=True, help_text="GTIN-8, UPC-A, EAN-13 or GTIN-14."
    )
    isbn = models.CharField(  # noqa: DJ001 (NULL = absent, so uniqueness ignores it)
        max_length=13, null=True, blank=True, help_text="ISBN-13 of this edition and format."
    )
    weight_grams = models.PositiveIntegerField(null=True, blank=True)
    inventory_unit = models.CharField(
        max_length=8, choices=InventoryUnit.choices, default=InventoryUnit.EACH
    )
    net_quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    net_unit = models.CharField(max_length=8, choices=NetUnit.choices, blank=True)
    release_date = models.DateField(
        null=True, blank=True, help_text="Pre-order until this date (foundation for Phase 07)."
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_productvariant_product_same_tenant"
            ),
            models.UniqueConstraint(fields=["tenant", "sku"], name="catalog_variant_sku_unique"),
            models.UniqueConstraint(
                fields=["tenant", "product", "option_signature"],
                name="catalog_variant_combination_unique",
            ),
            models.UniqueConstraint(
                fields=["tenant", "barcode"],
                condition=Q(barcode__isnull=False),
                name="catalog_variant_barcode_unique",
            ),
            models.UniqueConstraint(
                fields=["tenant", "isbn"],
                condition=Q(isbn__isnull=False),
                name="catalog_variant_isbn_unique",
            ),
            models.CheckConstraint(
                condition=Q(sku__regex=SKU_PATTERN), name="catalog_variant_sku_format"
            ),
            models.CheckConstraint(
                condition=Q(barcode__isnull=True) | Q(barcode__regex=GTIN_PATTERN),
                name="catalog_variant_barcode_format",
            ),
            models.CheckConstraint(
                condition=Q(isbn__isnull=True) | Q(isbn__regex=ISBN13_PATTERN),
                name="catalog_variant_isbn_format",
            ),
            _choice("kind", VariantKind, "catalog_variant_kind_valid"),
            _choice("inventory_unit", InventoryUnit, "catalog_variant_inventory_unit_valid"),
            models.CheckConstraint(
                condition=Q(digital_format="")
                | Q(kind=VariantKind.DIGITAL, digital_format__in=DigitalFormat.values),
                name="catalog_variant_digital_format_only_digital",
            ),
            models.CheckConstraint(
                condition=Q(kind=VariantKind.PHYSICAL)
                | Q(weight_grams__isnull=True, inventory_unit=InventoryUnit.EACH),
                name="catalog_variant_digital_has_no_physical_data",
            ),
            models.CheckConstraint(
                condition=Q(net_quantity__isnull=True, net_unit="")
                | Q(net_quantity__gt=0, net_unit__in=NetUnit.values),
                name="catalog_variant_net_contents_complete",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "product", "position"], name="catalog_variant_order"),
        ]

    def __str__(self) -> str:
        return self.sku


class VariantOptionValue(TenantOwnedModel):
    """The option a variant has for one of its product's option attributes."""

    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="option_values"
    )
    attribute = models.ForeignKey(AttributeDefinition, on_delete=models.PROTECT, related_name="+")
    option = models.ForeignKey(AttributeOption, on_delete=models.PROTECT, related_name="+")

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="variant", name="catalog_variantoptionvalue_variant_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="attribute", name="catalog_variantoptionvalue_attribute_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="option", name="catalog_variantoptionvalue_option_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "variant", "attribute"],
                name="catalog_variantoptionvalue_unique",
            ),
        ]


class ProductAttributeValue(TenantOwnedModel):
    """A product's value for one specification attribute. Exactly one typed column is set, and
    it matches the attribute's type (checked by ``services``; the database checks the count)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="specifications")
    attribute = models.ForeignKey(AttributeDefinition, on_delete=models.PROTECT, related_name="+")
    value_text = models.CharField(max_length=500, blank=True)
    value_number = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    value_boolean = models.BooleanField(null=True, blank=True)
    option = models.ForeignKey(
        AttributeOption, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_productattributevalue_product_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="attribute", name="catalog_productattributevalue_attribute_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="option", name="catalog_productattributevalue_option_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "product", "attribute"],
                name="catalog_productattributevalue_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(value_number__isnull=True, value_boolean__isnull=True, option__isnull=True)
                    & ~Q(value_text="")
                )
                | Q(
                    value_text="",
                    value_number__isnull=False,
                    value_boolean__isnull=True,
                    option__isnull=True,
                )
                | Q(
                    value_text="",
                    value_number__isnull=True,
                    value_boolean__isnull=False,
                    option__isnull=True,
                )
                | Q(
                    value_text="",
                    value_number__isnull=True,
                    value_boolean__isnull=True,
                    option__isnull=False,
                ),
                name="catalog_productattributevalue_one_value",
            ),
        ]


class ProductMedia(TenantOwnedModel):
    """A gallery entry: an image of the product, optionally of one variant. One is primary."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="media")
    asset = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="+")
    variant = models.ForeignKey(
        ProductVariant, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    position = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_productmedia_product_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="asset", name="catalog_productmedia_asset_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="variant", name="catalog_productmedia_variant_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "product", "asset"], name="catalog_productmedia_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant", "product"],
                condition=Q(is_primary=True),
                name="catalog_productmedia_one_primary",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "product", "position"], name="catalog_media_order"),
        ]


# --- Preset details (one-to-one with Product) -----------------------------------------------------


class WarrantyPolicy(TenantOwnedModel, TimestampedModel):
    """A reusable warranty statement for electronics (claims are Phase 10)."""

    name = models.CharField(max_length=120)
    duration_months = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(120)]
    )
    provider = models.CharField(max_length=16, choices=WarrantyProvider.choices)
    terms = models.TextField(blank=True, max_length=4000)
    is_active = models.BooleanField(default=True)

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "warranty policies"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant", "name"], name="catalog_warranty_name_unique"),
            _choice("provider", WarrantyProvider, "catalog_warranty_provider_valid"),
            models.CheckConstraint(
                condition=Q(duration_months__gte=1, duration_months__lte=120),
                name="catalog_warranty_duration_range",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ApparelDetails(TenantOwnedModel):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="apparel")
    audience = models.CharField(max_length=16, choices=ApparelAudience.choices, blank=True)
    material = models.CharField(max_length=200, blank=True)
    fit = models.CharField(max_length=50, blank=True)
    care_instructions = models.CharField(max_length=1000, blank=True)
    size_guide = models.ForeignKey(
        MediaAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    size_guide_notes = models.TextField(blank=True, max_length=2000)

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "apparel details"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_appareldetails_product_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="size_guide", name="catalog_appareldetails_size_guide_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(audience="") | Q(audience__in=ApparelAudience.values),
                name="catalog_appareldetails_audience_valid",
            ),
        ]


class GroceryDetails(TenantOwnedModel):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="grocery")
    is_perishable = models.BooleanField(default=False)
    storage_instructions = models.CharField(max_length=500, blank=True)
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    country_of_origin = models.CharField(
        max_length=2, blank=True, validators=[validate_country_code]
    )
    ingredients = models.TextField(blank=True, max_length=2000)
    allergens = models.CharField(max_length=500, blank=True)

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "grocery details"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_grocerydetails_product_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(country_of_origin="") | Q(country_of_origin__regex=r"^[A-Z]{2}$"),
                name="catalog_grocerydetails_country_format",
            ),
        ]


class ElectronicsDetails(TenantOwnedModel):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="electronics")
    model_number = models.CharField(max_length=100, blank=True)
    warranty = models.ForeignKey(
        WarrantyPolicy, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "electronics details"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_electronicsdetails_product_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="warranty", name="catalog_electronicsdetails_warranty_same_tenant"
            ),
        ]


class BookDetails(TenantOwnedModel):
    """Work-level bibliographic data. Each format (paperback, hardcover, eBook) is a variant and
    owns its own ISBN-13, because an ISBN identifies one edition in one format (D-052)."""

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="book")
    subtitle = models.CharField(max_length=200, blank=True)
    # Optional while drafting; publication requires at least one author.
    authors = ArrayField(models.CharField(max_length=200), default=list, blank=True)
    publisher = models.CharField(max_length=200, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    edition = models.CharField(max_length=100, blank=True)
    language = models.CharField(
        max_length=35,
        blank=True,
        validators=[RegexValidator(LANGUAGE_TAG_PATTERN)],
        help_text="BCP 47 tag of the text, e.g. en or bn.",
    )
    genre = models.CharField(max_length=100, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    series_name = models.CharField(max_length=200, blank=True)
    series_number = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "book details"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="product", name="catalog_bookdetails_product_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(authors__len__lte=10), name="catalog_bookdetails_authors_limit"
            ),
            models.CheckConstraint(
                condition=Q(series_number__isnull=True) | ~Q(series_name=""),
                name="catalog_bookdetails_series_number_needs_name",
            ),
            models.CheckConstraint(
                condition=Q(language="") | Q(language__regex=LANGUAGE_TAG_PATTERN),
                name="catalog_bookdetails_language_format",
            ),
        ]
