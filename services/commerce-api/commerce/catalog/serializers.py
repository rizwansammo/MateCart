"""Request and response shapes for the merchant catalogue API. Shapes only: rules live in services.

Money and quantities are strings (Decimal, never float). Cost prices are rendered only when the
serializer context has ``show_costs`` (staff with ``catalog.costs``).
"""

from decimal import Decimal
from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from commerce.core.fields import TenantRelatedField, TenantSlugRelatedField
from commerce.media.models import MediaAsset

from .models import (
    SKU_PATTERN,
    SLUG_PATTERN,
    ApparelAudience,
    ApparelDetails,
    AttributeDefinition,
    AttributeOption,
    AttributeScope,
    AttributeValueType,
    BookDetails,
    Brand,
    Category,
    Collection,
    DigitalFormat,
    ElectronicsDetails,
    GroceryDetails,
    InventoryUnit,
    NetUnit,
    Product,
    ProductStatus,
    ProductType,
    ProductVariant,
    TrackingMode,
    VariantKind,
    WarrantyPolicy,
    WarrantyProvider,
)

TRANSLATIONS_HELP = 'Additional languages only: {"bn": {"name": "...", "description": "..."}}.'


def slug_field() -> serializers.RegexField:
    return serializers.RegexField(
        regex=SLUG_PATTERN, max_length=140, required=False, allow_blank=True
    )


def media_url(asset_id: Any) -> str:
    """Staff file URL of an asset (public pages use the public catalogue media URL)."""
    return f"/api/manage/media/assets/{asset_id}/file/"


def public_media_url(asset_id: Any) -> str:
    return f"/api/catalog/media/{asset_id}/"


# --- Requests -------------------------------------------------------------------------------------


class BrandWriteSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=120)
    slug = slug_field()
    is_active = serializers.BooleanField(required=False)


class WarrantyWriteSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=120)
    duration_months = serializers.IntegerField(min_value=1, max_value=120)
    provider = serializers.ChoiceField(choices=WarrantyProvider.choices)
    terms = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


class CategoryWriteSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=120)
    slug = slug_field()
    # DRF's metaclass moves declared fields off the class, so this name is safe at runtime.
    parent = TenantRelatedField(Category, required=False, allow_null=True)  # type: ignore[assignment]
    description = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    position = serializers.IntegerField(min_value=0, max_value=1_000_000, required=False)
    is_active = serializers.BooleanField(required=False)
    image = TenantRelatedField(MediaAsset, required=False, allow_null=True)
    translations = serializers.JSONField(required=False, help_text=TRANSLATIONS_HELP)


class CollectionWriteSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=120)
    slug = slug_field()
    description = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    image = TenantRelatedField(MediaAsset, required=False, allow_null=True)
    translations = serializers.JSONField(required=False, help_text=TRANSLATIONS_HELP)


class CollectionProductsSerializer(serializers.Serializer[dict[str, Any]]):
    products = serializers.ListField(
        child=TenantRelatedField(Product), max_length=1000, allow_empty=True
    )


class AttributeWriteSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.RegexField(max_length=64, regex=SLUG_PATTERN)
    name = serializers.CharField(max_length=100)
    value_type = serializers.ChoiceField(choices=AttributeValueType.choices)
    scope = serializers.ChoiceField(choices=AttributeScope.choices)
    unit = serializers.CharField(max_length=20, required=False, allow_blank=True)
    is_filterable = serializers.BooleanField(required=False)
    position = serializers.IntegerField(min_value=0, max_value=1_000_000, required=False)


class OptionWriteSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.RegexField(max_length=64, regex=SLUG_PATTERN, required=False)
    value = serializers.CharField(max_length=100)
    swatch = serializers.RegexField(
        regex=r"^#[0-9A-Fa-f]{6}$", required=False, allow_blank=True, max_length=7
    )
    position = serializers.IntegerField(min_value=0, max_value=1_000_000, required=False)


class PriceInputSerializer(serializers.Serializer[dict[str, Any]]):
    """Default-price-list price given with a new variant. Omit ``cost_amount`` without
    ``catalog.costs``."""

    amount = serializers.DecimalField(max_digits=19, decimal_places=4, min_value=Decimal(0))
    compare_at_amount = serializers.DecimalField(
        max_digits=19, decimal_places=4, required=False, allow_null=True
    )
    cost_amount = serializers.DecimalField(
        max_digits=19, decimal_places=4, min_value=Decimal(0), required=False, allow_null=True
    )


class VariantWriteSerializer(serializers.Serializer[dict[str, Any]]):
    sku = serializers.RegexField(regex=r"^\s*[A-Za-z0-9][A-Za-z0-9._-]{0,63}\s*$", max_length=70)
    options = serializers.DictField(
        child=serializers.CharField(max_length=64),
        required=False,
        help_text='Option value code per product option attribute code, e.g. {"size": "m"}.',
    )
    kind = serializers.ChoiceField(choices=VariantKind.choices, required=False)
    digital_format = serializers.ChoiceField(
        choices=DigitalFormat.choices, required=False, allow_blank=True
    )
    barcode = serializers.CharField(
        max_length=20, required=False, allow_null=True, allow_blank=True
    )
    isbn = serializers.CharField(max_length=20, required=False, allow_null=True, allow_blank=True)
    weight_grams = serializers.IntegerField(
        min_value=0, max_value=10_000_000, required=False, allow_null=True
    )
    inventory_unit = serializers.ChoiceField(choices=InventoryUnit.choices, required=False)
    net_quantity = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        required=False,
        allow_null=True,
    )
    net_unit = serializers.ChoiceField(choices=NetUnit.choices, required=False, allow_blank=True)
    release_date = serializers.DateField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    position = serializers.IntegerField(min_value=0, max_value=100_000, required=False)
    price = PriceInputSerializer(required=False)


class VariantUpdateSerializer(VariantWriteSerializer):
    sku = serializers.RegexField(
        regex=r"^\s*[A-Za-z0-9][A-Za-z0-9._-]{0,63}\s*$", max_length=70, required=False
    )
    price = None  # type: ignore[assignment]  # prices change through the pricing API


class SpecificationInputSerializer(serializers.Serializer[dict[str, Any]]):
    attribute = TenantSlugRelatedField(AttributeDefinition, "code")
    value = serializers.JSONField(help_text="Text, number, boolean or option code by type.")


class ApparelDetailsInputSerializer(serializers.Serializer[dict[str, Any]]):
    audience = serializers.ChoiceField(
        choices=ApparelAudience.choices, required=False, allow_blank=True
    )
    material = serializers.CharField(max_length=200, required=False, allow_blank=True)
    fit = serializers.CharField(max_length=50, required=False, allow_blank=True)
    care_instructions = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    size_guide = TenantRelatedField(MediaAsset, required=False, allow_null=True)
    size_guide_notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class GroceryDetailsInputSerializer(serializers.Serializer[dict[str, Any]]):
    is_perishable = serializers.BooleanField(required=False)
    storage_instructions = serializers.CharField(max_length=500, required=False, allow_blank=True)
    shelf_life_days = serializers.IntegerField(
        min_value=0, max_value=36_500, required=False, allow_null=True
    )
    country_of_origin = serializers.CharField(max_length=2, required=False, allow_blank=True)
    ingredients = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    allergens = serializers.CharField(max_length=500, required=False, allow_blank=True)


class ElectronicsDetailsInputSerializer(serializers.Serializer[dict[str, Any]]):
    model_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    warranty = TenantRelatedField(WarrantyPolicy, required=False, allow_null=True)


class BookDetailsInputSerializer(serializers.Serializer[dict[str, Any]]):
    subtitle = serializers.CharField(max_length=200, required=False, allow_blank=True)
    authors = serializers.ListField(
        child=serializers.CharField(max_length=200), max_length=10, required=False
    )
    publisher = serializers.CharField(max_length=200, required=False, allow_blank=True)
    publication_date = serializers.DateField(required=False, allow_null=True)
    edition = serializers.CharField(max_length=100, required=False, allow_blank=True)
    language = serializers.CharField(max_length=35, required=False, allow_blank=True)
    genre = serializers.CharField(max_length=100, required=False, allow_blank=True)
    page_count = serializers.IntegerField(
        min_value=1, max_value=100_000, required=False, allow_null=True
    )
    series_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    series_number = serializers.IntegerField(
        min_value=1, max_value=10_000, required=False, allow_null=True
    )


DETAIL_INPUTS: dict[str, str] = {
    ProductType.APPAREL: "apparel",
    ProductType.GROCERY: "grocery",
    ProductType.ELECTRONICS: "electronics",
    ProductType.BOOK: "book",
}


class _ProductFieldsSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=200)
    slug = serializers.RegexField(
        required=False, allow_blank=True, max_length=220, regex=SLUG_PATTERN
    )
    product_type = serializers.ChoiceField(choices=ProductType.choices, required=False)
    brand = TenantRelatedField(Brand, required=False, allow_null=True)
    summary = serializers.CharField(max_length=500, required=False, allow_blank=True)
    description = serializers.CharField(max_length=20000, required=False, allow_blank=True)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=40), max_length=40, required=False
    )
    seo_title = serializers.CharField(max_length=70, required=False, allow_blank=True)
    seo_description = serializers.CharField(max_length=320, required=False, allow_blank=True)
    track_inventory = serializers.BooleanField(required=False)
    tracking_mode = serializers.ChoiceField(choices=TrackingMode.choices, required=False)
    translations = serializers.JSONField(required=False, help_text=TRANSLATIONS_HELP)
    categories = serializers.ListField(
        child=TenantRelatedField(Category), max_length=20, required=False
    )
    primary_category = TenantRelatedField(Category, required=False, allow_null=True)
    specifications = SpecificationInputSerializer(many=True, required=False)
    apparel = ApparelDetailsInputSerializer(required=False)
    grocery = GroceryDetailsInputSerializer(required=False)
    electronics = ElectronicsDetailsInputSerializer(required=False)
    book = BookDetailsInputSerializer(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Typed details travel under the key of their product type; services get ``details``."""
        given = [key for key in DETAIL_INPUTS.values() if key in attrs]
        if len(given) > 1:
            raise serializers.ValidationError("Give the details of one product type only.")
        if given:
            product_type = str(
                attrs.get("product_type")
                or getattr(self.instance, "product_type", ProductType.STANDARD)
            )
            if DETAIL_INPUTS.get(product_type) != given[0]:
                raise serializers.ValidationError(
                    {given[0]: [f"{given[0]} details need product_type {given[0]}."]}
                )
            attrs["details"] = attrs.pop(given[0])
        return attrs


class ProductCreateSerializer(_ProductFieldsSerializer):
    options = serializers.ListField(
        child=TenantSlugRelatedField(AttributeDefinition, "code"),
        max_length=3,
        required=False,
        help_text="Codes of the variant-defining attributes, in display order.",
    )
    variants = VariantWriteSerializer(
        many=True, help_text="At least one; a simple product has one."
    )


class ProductUpdateSerializer(_ProductFieldsSerializer):
    name = serializers.CharField(max_length=200, required=False)


class ProductStatusSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=ProductStatus.choices)


class ProductOptionsSerializer(serializers.Serializer[dict[str, Any]]):
    options = serializers.ListField(
        child=TenantSlugRelatedField(AttributeDefinition, "code"), max_length=3
    )


class ProductMediaInputSerializer(serializers.Serializer[dict[str, Any]]):
    asset = TenantRelatedField(MediaAsset)
    variant = TenantRelatedField(ProductVariant, required=False, allow_null=True)
    is_primary = serializers.BooleanField(required=False)


class ProductMediaSetSerializer(serializers.Serializer[dict[str, Any]]):
    media = ProductMediaInputSerializer(many=True)

    def validate_media(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > 50:
            raise serializers.ValidationError("A gallery holds at most 50 images.")
        return value


class ProductListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=ProductStatus.choices, required=False)
    product_type = serializers.ChoiceField(choices=ProductType.choices, required=False)
    category = serializers.UUIDField(required=False)
    collection = serializers.UUIDField(required=False)
    brand = serializers.UUIDField(required=False)
    ordering = serializers.ChoiceField(
        choices=["name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"],
        required=False,
    )


# --- Responses ------------------------------------------------------------------------------------


class BrandSerializer(serializers.ModelSerializer[Brand]):
    class Meta:
        model = Brand
        fields = ("id", "name", "slug", "is_active", "created_at", "updated_at")
        read_only_fields = fields


class WarrantySerializer(serializers.ModelSerializer[WarrantyPolicy]):
    class Meta:
        model = WarrantyPolicy
        fields = ("id", "name", "duration_months", "provider", "terms", "is_active")
        read_only_fields = fields


class CategorySerializer(serializers.ModelSerializer[Category]):
    class Meta:
        model = Category
        fields = (
            "id",
            "parent",
            "name",
            "slug",
            "description",
            "position",
            "is_active",
            "image",
            "translations",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class CategoryNodeSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(source="category.pk")
    name = serializers.CharField(source="category.name")
    slug = serializers.CharField(source="category.slug")
    description = serializers.CharField(source="category.description")
    position = serializers.IntegerField(source="category.position")
    is_active = serializers.BooleanField(source="category.is_active")
    image = serializers.UUIDField(source="category.image_id", allow_null=True)
    translations = serializers.JSONField(source="category.translations")
    children = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, node: dict[str, Any]) -> list[Any]:
        return [CategoryNodeSerializer(child).data for child in node["children"]]


class CollectionSerializer(serializers.ModelSerializer[Collection]):
    product_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Collection
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "is_active",
            "image",
            "translations",
            "product_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OptionSerializer(serializers.ModelSerializer[AttributeOption]):
    class Meta:
        model = AttributeOption
        fields = ("id", "code", "value", "swatch", "position")
        read_only_fields = fields


class AttributeSerializer(serializers.ModelSerializer[AttributeDefinition]):
    options = OptionSerializer(many=True, read_only=True)

    class Meta:
        model = AttributeDefinition
        fields = (
            "id",
            "code",
            "name",
            "value_type",
            "scope",
            "unit",
            "is_filterable",
            "position",
            "options",
        )
        read_only_fields = fields


class PriceSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    price_list = serializers.UUIDField(source="price_list_id")
    currency = serializers.CharField()
    amount = serializers.DecimalField(max_digits=19, decimal_places=4)
    compare_at_amount = serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True)
    cost_amount = serializers.DecimalField(
        max_digits=19,
        decimal_places=4,
        allow_null=True,
        required=False,
        help_text="Present only for staff with catalog.costs.",
    )

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = super().to_representation(instance)
        if not self.context.get("show_costs"):
            data.pop("cost_amount", None)
        return data


class StockSummarySerializer(serializers.Serializer[dict[str, Any]]):
    on_hand = serializers.DecimalField(max_digits=14, decimal_places=3)
    reserved = serializers.DecimalField(max_digits=14, decimal_places=3)
    available = serializers.DecimalField(max_digits=14, decimal_places=3)


class VariantOptionSerializer(serializers.Serializer[Any]):
    attribute = serializers.CharField(source="attribute.code")
    name = serializers.CharField(source="attribute.name")
    option = serializers.CharField(source="option.code")
    value = serializers.CharField(source="option.value")


class VariantSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    product = serializers.UUIDField(source="product_id")
    sku = serializers.CharField()
    title = serializers.CharField()
    options = VariantOptionSerializer(source="option_values.all", many=True)
    position = serializers.IntegerField()
    is_active = serializers.BooleanField()
    kind = serializers.ChoiceField(choices=VariantKind.choices)
    digital_format = serializers.CharField()
    barcode = serializers.CharField(allow_null=True)
    isbn = serializers.CharField(allow_null=True)
    weight_grams = serializers.IntegerField(allow_null=True)
    inventory_unit = serializers.ChoiceField(choices=InventoryUnit.choices)
    net_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, allow_null=True)
    net_unit = serializers.CharField()
    release_date = serializers.DateField(allow_null=True)
    prices = PriceSerializer(source="prices.all", many=True)
    stock = serializers.SerializerMethodField()

    @extend_schema_field(StockSummarySerializer)
    def get_stock(self, variant: ProductVariant) -> dict[str, Any]:
        totals: dict[Any, dict[str, Decimal]] = self.context.get("stock", {})
        empty = {"on_hand": Decimal(0), "reserved": Decimal(0), "available": Decimal(0)}
        return dict(StockSummarySerializer(totals.get(variant.pk, empty)).data)


class ProductCategorySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(source="category.pk")
    name = serializers.CharField(source="category.name")
    slug = serializers.CharField(source="category.slug")
    is_primary = serializers.BooleanField()


class ProductOptionSerializer(serializers.Serializer[Any]):
    attribute = serializers.UUIDField(source="attribute.pk")
    code = serializers.CharField(source="attribute.code")
    name = serializers.CharField(source="attribute.name")
    position = serializers.IntegerField()


class SpecificationSerializer(serializers.Serializer[Any]):
    attribute = serializers.CharField(source="attribute.code")
    name = serializers.CharField(source="attribute.name")
    value_type = serializers.CharField(source="attribute.value_type")
    unit = serializers.CharField(source="attribute.unit")
    value = serializers.SerializerMethodField()
    display = serializers.SerializerMethodField()

    @extend_schema_field(serializers.JSONField())
    def get_value(self, row: Any) -> Any:
        if row.option_id:
            return row.option.code
        if row.value_number is not None:
            return str(row.value_number.normalize())
        if row.value_boolean is not None:
            return row.value_boolean
        return row.value_text

    def get_display(self, row: Any) -> str:
        if row.option_id:
            return str(row.option.value)
        if row.value_boolean is not None:
            return "Yes" if row.value_boolean else "No"
        if row.value_number is not None:
            number = f"{row.value_number.normalize():f}"
            return f"{number} {row.attribute.unit}".strip()
        return str(row.value_text)


class ProductMediaSerializer(serializers.Serializer[Any]):
    asset = serializers.UUIDField(source="asset_id")
    variant = serializers.UUIDField(source="variant_id", allow_null=True)
    position = serializers.IntegerField()
    is_primary = serializers.BooleanField()
    alt_text = serializers.CharField(source="asset.alt_text")
    width = serializers.IntegerField(source="asset.width", allow_null=True)
    height = serializers.IntegerField(source="asset.height", allow_null=True)
    url = serializers.SerializerMethodField()

    def get_url(self, row: Any) -> str:
        return media_url(row.asset_id)


class ApparelDetailsSerializer(serializers.ModelSerializer[ApparelDetails]):
    class Meta:
        model = ApparelDetails
        fields = (
            "audience",
            "material",
            "fit",
            "care_instructions",
            "size_guide",
            "size_guide_notes",
        )
        read_only_fields = fields


class GroceryDetailsSerializer(serializers.ModelSerializer[GroceryDetails]):
    class Meta:
        model = GroceryDetails
        fields = (
            "is_perishable",
            "storage_instructions",
            "shelf_life_days",
            "country_of_origin",
            "ingredients",
            "allergens",
        )
        read_only_fields = fields


class ElectronicsDetailsSerializer(serializers.ModelSerializer[ElectronicsDetails]):
    warranty = WarrantySerializer(read_only=True, allow_null=True)

    class Meta:
        model = ElectronicsDetails
        fields = ("model_number", "warranty")
        read_only_fields = fields


class BookDetailsSerializer(serializers.ModelSerializer[BookDetails]):
    class Meta:
        model = BookDetails
        fields = (
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
        )
        read_only_fields = fields


class PriceRangeSerializer(serializers.Serializer[dict[str, Any]]):
    currency = serializers.CharField()
    min_amount = serializers.DecimalField(max_digits=19, decimal_places=4)
    max_amount = serializers.DecimalField(max_digits=19, decimal_places=4)


class ProductListItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    product_type = serializers.ChoiceField(choices=ProductType.choices)
    status = serializers.ChoiceField(choices=ProductStatus.choices)
    brand = serializers.UUIDField(source="brand_id", allow_null=True)
    variant_count = serializers.IntegerField()
    primary_image = serializers.UUIDField(allow_null=True)
    price_range = PriceRangeSerializer(allow_null=True)
    available = serializers.DecimalField(max_digits=14, decimal_places=3)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ProductDetailSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    product_type = serializers.ChoiceField(choices=ProductType.choices)
    status = serializers.ChoiceField(choices=ProductStatus.choices)
    brand = BrandSerializer(allow_null=True)
    summary = serializers.CharField()
    description = serializers.CharField()
    tags = serializers.ListField(child=serializers.CharField())
    seo_title = serializers.CharField()
    seo_description = serializers.CharField()
    track_inventory = serializers.BooleanField()
    tracking_mode = serializers.ChoiceField(choices=TrackingMode.choices)
    translations = serializers.JSONField()
    published_at = serializers.DateTimeField(allow_null=True)
    archived_at = serializers.DateTimeField(allow_null=True)
    categories = ProductCategorySerializer(source="placements.all", many=True)
    options = ProductOptionSerializer(source="options.all", many=True)
    variants = VariantSerializer(source="variants.all", many=True)
    specifications = SpecificationSerializer(source="specifications.all", many=True)
    media = ProductMediaSerializer(source="media.all", many=True)
    apparel = serializers.SerializerMethodField()
    grocery = serializers.SerializerMethodField()
    electronics = serializers.SerializerMethodField()
    book = serializers.SerializerMethodField()
    publication_problems = serializers.ListField(child=serializers.CharField())
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def _detail(self, product: Product, attr: str, serializer: type[Any]) -> Any:
        details = getattr(product, attr, None)
        return serializer(details).data if details is not None else None

    @extend_schema_field(ApparelDetailsSerializer(allow_null=True))
    def get_apparel(self, product: Product) -> Any:
        return self._detail(product, "apparel", ApparelDetailsSerializer)

    @extend_schema_field(GroceryDetailsSerializer(allow_null=True))
    def get_grocery(self, product: Product) -> Any:
        return self._detail(product, "grocery", GroceryDetailsSerializer)

    @extend_schema_field(ElectronicsDetailsSerializer(allow_null=True))
    def get_electronics(self, product: Product) -> Any:
        return self._detail(product, "electronics", ElectronicsDetailsSerializer)

    @extend_schema_field(BookDetailsSerializer(allow_null=True))
    def get_book(self, product: Product) -> Any:
        return self._detail(product, "book", BookDetailsSerializer)


SKU_REGEX = SKU_PATTERN
