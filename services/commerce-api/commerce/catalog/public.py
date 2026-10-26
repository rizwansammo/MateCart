"""Public, read-only catalogue under /api/catalog/ (foundation for the Phase 06 storefront).

Anonymous access is a deliberate opt-in for these GET endpoints only. They expose active products
and active categories and collections, default-list selling prices and an in-stock flag. They
never expose drafts, archived products, cost prices, stock quantities, staff data or audit
history. Files are served only when an active product, category or collection uses them.
"""

import uuid
from typing import Any

from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet, Subquery
from django.http import Http404
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commerce.core.domain import found, found_in
from commerce.core.pagination import PageQuerySerializer, paginate, paginated
from commerce.identity.auth import CsrfProtectedAPIView
from commerce.inventory.services import sellable_quantities, store_today
from commerce.media.models import MediaAsset
from commerce.media.views import file_response
from commerce.pricing.models import Price
from commerce.pricing.services import find_default_price_list

from . import services
from .models import (
    ApparelDetails,
    AttributeOption,
    Category,
    Collection,
    CollectionProduct,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductMedia,
    ProductOption,
    ProductStatus,
    ProductType,
    ProductVariant,
    VariantKind,
    VariantOptionValue,
)
from .serializers import (
    BookDetailsSerializer,
    GroceryDetailsSerializer,
    SpecificationSerializer,
    public_media_url,
)

TAGS = ["catalog"]


class PublicView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    http_method_names = ("get", "head", "options")


# --- Serializers ----------------------------------------------------------------------------------


class PublicImageSerializer(serializers.Serializer[Any]):
    url = serializers.SerializerMethodField()
    alt_text = serializers.CharField(source="asset.alt_text")
    width = serializers.IntegerField(source="asset.width", allow_null=True)
    height = serializers.IntegerField(source="asset.height", allow_null=True)
    variant = serializers.UUIDField(source="variant_id", allow_null=True)

    def get_url(self, row: Any) -> str:
        return public_media_url(row.asset_id)


class PublicPriceSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    amount = serializers.DecimalField(max_digits=19, decimal_places=4)
    compare_at_amount = serializers.DecimalField(max_digits=19, decimal_places=4, allow_null=True)


class PublicPriceRangeSerializer(serializers.Serializer[dict[str, Any]]):
    currency = serializers.CharField()
    min_amount = serializers.DecimalField(max_digits=19, decimal_places=4)
    max_amount = serializers.DecimalField(max_digits=19, decimal_places=4)


class PublicBrandSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    slug = serializers.CharField()


class PublicProductSummarySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()
    summary = serializers.CharField()
    product_type = serializers.ChoiceField(choices=ProductType.choices)
    brand = PublicBrandSerializer(allow_null=True)
    price = PublicPriceRangeSerializer(source="price_range", allow_null=True)
    image = serializers.SerializerMethodField()
    in_stock = serializers.BooleanField()
    translations = serializers.JSONField()

    @extend_schema_field(PublicImageSerializer(allow_null=True))
    def get_image(self, product: Product) -> Any:
        row = getattr(product, "primary_media", None)
        return PublicImageSerializer(row).data if row is not None else None


class PublicVariantOptionSerializer(serializers.Serializer[Any]):
    attribute = serializers.CharField(source="attribute.code")
    option = serializers.CharField(source="option.code")
    value = serializers.CharField(source="option.value")


class PublicVariantSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    sku = serializers.CharField()
    title = serializers.CharField()
    options = PublicVariantOptionSerializer(source="option_values.all", many=True)
    kind = serializers.ChoiceField(choices=VariantKind.choices)
    digital_format = serializers.CharField()
    net_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, allow_null=True)
    net_unit = serializers.CharField()
    release_date = serializers.DateField(allow_null=True)
    price = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()

    @extend_schema_field(PublicPriceSerializer(allow_null=True))
    def get_price(self, variant: ProductVariant) -> Any:
        price = self.context["prices"].get(variant.pk)
        return PublicPriceSerializer(price).data if price is not None else None

    def get_in_stock(self, variant: ProductVariant) -> bool:
        return bool(self.context["in_stock"].get(variant.pk, False))


class PublicOptionValueSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    value = serializers.CharField()
    swatch = serializers.CharField()


class PublicProductOptionSerializer(serializers.Serializer[Any]):
    code = serializers.CharField(source="attribute.code")
    name = serializers.CharField(source="attribute.name")
    values = serializers.SerializerMethodField()

    @extend_schema_field(PublicOptionValueSerializer(many=True))
    def get_values(self, product_option: Any) -> Any:
        used = self.context["used_options"].get(product_option.attribute_id, [])
        return PublicOptionValueSerializer(used, many=True).data


class PublicCategoryRefSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(source="category.name")
    slug = serializers.CharField(source="category.slug")
    is_primary = serializers.BooleanField()


class PublicWarrantySerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    duration_months = serializers.IntegerField()
    provider = serializers.CharField()
    terms = serializers.CharField()


class PublicApparelSerializer(serializers.Serializer[Any]):
    audience = serializers.CharField()
    material = serializers.CharField()
    fit = serializers.CharField()
    care_instructions = serializers.CharField()
    size_guide_url = serializers.SerializerMethodField()
    size_guide_notes = serializers.CharField()

    def get_size_guide_url(self, details: ApparelDetails) -> str | None:
        return public_media_url(details.size_guide_id) if details.size_guide_id else None


class PublicElectronicsSerializer(serializers.Serializer[Any]):
    model_number = serializers.CharField()
    warranty = PublicWarrantySerializer(allow_null=True)


class PublicProductDetailSerializer(PublicProductSummarySerializer):
    description = serializers.CharField()
    seo_title = serializers.CharField()
    seo_description = serializers.CharField()
    categories = PublicCategoryRefSerializer(source="placements.all", many=True)
    options = PublicProductOptionSerializer(source="options.all", many=True)
    variants = PublicVariantSerializer(source="active_variants", many=True)
    specifications = SpecificationSerializer(source="specifications.all", many=True)
    images = PublicImageSerializer(source="media.all", many=True)
    apparel = serializers.SerializerMethodField()
    grocery = serializers.SerializerMethodField()
    electronics = serializers.SerializerMethodField()
    book = serializers.SerializerMethodField()

    def _detail(self, product: Product, attr: str, serializer: type[Any]) -> Any:
        details = getattr(product, attr, None)
        return serializer(details).data if details is not None else None

    @extend_schema_field(PublicApparelSerializer(allow_null=True))
    def get_apparel(self, product: Product) -> Any:
        return self._detail(product, "apparel", PublicApparelSerializer)

    @extend_schema_field(GroceryDetailsSerializer(allow_null=True))
    def get_grocery(self, product: Product) -> Any:
        return self._detail(product, "grocery", GroceryDetailsSerializer)

    @extend_schema_field(PublicElectronicsSerializer(allow_null=True))
    def get_electronics(self, product: Product) -> Any:
        return self._detail(product, "electronics", PublicElectronicsSerializer)

    @extend_schema_field(BookDetailsSerializer(allow_null=True))
    def get_book(self, product: Product) -> Any:
        return self._detail(product, "book", BookDetailsSerializer)


class PublicCategoryNodeSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(source="category.name")
    slug = serializers.CharField(source="category.slug")
    description = serializers.CharField(source="category.description")
    image_url = serializers.SerializerMethodField()
    translations = serializers.JSONField(source="category.translations")
    children = serializers.SerializerMethodField()

    def get_image_url(self, node: dict[str, Any]) -> str | None:
        image_id = node["category"].image_id
        return public_media_url(image_id) if image_id else None

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, node: dict[str, Any]) -> list[Any]:
        return [PublicCategoryNodeSerializer(child).data for child in node["children"]]


class PublicCollectionSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    slug = serializers.CharField()
    description = serializers.CharField()
    image_url = serializers.SerializerMethodField()
    translations = serializers.JSONField()

    def get_image_url(self, collection: Collection) -> str | None:
        return public_media_url(collection.image_id) if collection.image_id else None


class PublicProductQuerySerializer(serializers.Serializer[dict[str, Any]]):
    q = serializers.CharField(max_length=100, required=False, allow_blank=True)
    category = serializers.SlugField(max_length=140, required=False)
    collection = serializers.SlugField(max_length=140, required=False)
    brand = serializers.SlugField(max_length=140, required=False)
    product_type = serializers.ChoiceField(choices=ProductType.choices, required=False)
    ordering = serializers.ChoiceField(
        choices=["newest", "name", "price_low", "price_high", "featured"], required=False
    )


# --- Data -----------------------------------------------------------------------------------------


def active_products() -> QuerySet[Product, Product]:
    return Product.objects.filter(status=ProductStatus.ACTIVE)


def _descendant_ids(root: Category) -> list[Any]:
    ids, frontier = [root.pk], [root.pk]
    for _ in range(services.MAX_CATEGORY_DEPTH):
        frontier = list(
            Category.objects.filter(parent_id__in=frontier, is_active=True).values_list(
                "pk", flat=True
            )
        )
        if not frontier:
            break
        ids.extend(frontier)
    return ids


def _sellable_variants(product_ids: list[Any]) -> dict[Any, list[ProductVariant]]:
    variants: dict[Any, list[ProductVariant]] = {}
    for variant in ProductVariant.objects.filter(product_id__in=product_ids, is_active=True):
        variants.setdefault(variant.product_id, []).append(variant)
    return variants


def _prices(variant_ids: list[Any]) -> dict[Any, Price]:
    price_list = find_default_price_list()
    if price_list is None:
        return {}
    return {
        price.variant_id: price
        for price in Price.objects.filter(price_list=price_list, variant_id__in=variant_ids)
    }


def _in_stock(variants: list[ProductVariant]) -> dict[Any, bool]:
    """True per variant when a customer could order it now: untracked, digital, a future release
    (pre-order) or sellable stock (unexpired, unreserved, at an active location; D-060).
    Quantities are never exposed."""
    tracked_products = set(
        Product.objects.filter(
            pk__in={variant.product_id for variant in variants}, track_inventory=True
        ).values_list("pk", flat=True)
    )
    sellable = sellable_quantities(variants)
    stocked = {pk for pk, quantity in sellable.items() if quantity > 0}
    today = store_today()
    result: dict[Any, bool] = {}
    for variant in variants:
        untracked = (
            variant.product_id not in tracked_products or variant.kind != VariantKind.PHYSICAL
        )
        preorder = variant.release_date is not None and variant.release_date > today
        result[variant.pk] = untracked or preorder or variant.pk in stocked
    return result


def _summaries(products: list[Product]) -> list[Product]:
    ids = [product.pk for product in products]
    variants = _sellable_variants(ids)
    all_variants = [variant for rows in variants.values() for variant in rows]
    prices = _prices([variant.pk for variant in all_variants])
    in_stock = _in_stock(all_variants)
    media = {
        row.product_id: row
        for row in ProductMedia.objects.filter(product_id__in=ids, is_primary=True).select_related(
            "asset"
        )
    }
    for product in products:
        rows = variants.get(product.pk, [])
        amounts = [prices[v.pk] for v in rows if v.pk in prices]
        product.price_range = (  # type: ignore[attr-defined]
            {
                "currency": amounts[0].currency,
                "min_amount": min(price.amount for price in amounts),
                "max_amount": max(price.amount for price in amounts),
            }
            if amounts
            else None
        )
        product.in_stock = any(in_stock.get(v.pk) for v in rows)  # type: ignore[attr-defined]
        product.primary_media = media.get(product.pk)  # type: ignore[attr-defined]
    return products


# --- Views ----------------------------------------------------------------------------------------


class PublicProductListView(PublicView):
    @extend_schema(
        operation_id="catalog_products",
        tags=TAGS,
        parameters=[PublicProductQuerySerializer, PageQuerySerializer],
        responses={200: paginated(PublicProductSummarySerializer)},
    )
    def get(self, request: Request) -> Response:
        params = PublicProductQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        filters: dict[str, Any] = dict(params.validated_data)
        products = active_products().select_related("brand")
        if filters.get("q"):
            text = filters["q"].strip()
            products = products.filter(Q(name__icontains=text) | Q(tags__contains=[text.lower()]))
        if filters.get("product_type"):
            products = products.filter(product_type=filters["product_type"])
        if filters.get("brand"):
            products = products.filter(brand__slug=filters["brand"], brand__is_active=True)
        if filters.get("category"):
            category = found(Category, slug=filters["category"], is_active=True)
            products = products.filter(
                Exists(
                    ProductCategory.objects.filter(
                        product=OuterRef("pk"), category_id__in=_descendant_ids(category)
                    )
                )
            )
        ordering = filters.get("ordering", "newest")
        if filters.get("collection"):
            collection = found(Collection, slug=filters["collection"], is_active=True)
            position = CollectionProduct.objects.filter(
                collection=collection, product=OuterRef("pk")
            ).values("position")[:1]
            products = products.annotate(collection_position=Subquery(position)).filter(
                collection_position__isnull=False
            )
            if ordering == "featured":
                products = products.order_by("collection_position", "pk")
        if ordering in ("price_low", "price_high"):
            price_list = find_default_price_list()
            lowest = (
                Price.objects.filter(
                    price_list=price_list, variant__product=OuterRef("pk"), variant__is_active=True
                )
                .order_by("amount")
                .values("amount")[:1]
            )
            products = products.annotate(lowest_price=Subquery(lowest)).order_by(
                "lowest_price" if ordering == "price_low" else "-lowest_price", "pk"
            )
        elif ordering == "name":
            products = products.order_by("name", "pk")
        elif ordering != "featured" or not filters.get("collection"):
            products = products.order_by("-published_at", "-pk")
        items, meta = paginate(products, request)
        return Response(
            {**meta, "results": PublicProductSummarySerializer(_summaries(items), many=True).data}
        )


class PublicProductDetailView(PublicView):
    @extend_schema(
        operation_id="catalog_product",
        tags=TAGS,
        responses={200: PublicProductDetailSerializer},
    )
    def get(self, request: Request, slug: str) -> Response:
        product = found_in(
            active_products()
            .select_related("brand", "apparel", "grocery", "electronics__warranty", "book")
            .prefetch_related(
                Prefetch(
                    "placements",
                    queryset=ProductCategory.objects.filter(category__is_active=True)
                    .select_related("category")
                    .order_by("-is_primary", "category__name"),
                ),
                Prefetch(
                    "options",
                    queryset=ProductOption.objects.select_related("attribute").order_by(
                        "position", "pk"
                    ),
                ),
                Prefetch(
                    "specifications",
                    queryset=ProductAttributeValue.objects.select_related(
                        "attribute", "option"
                    ).order_by("attribute__position", "attribute__name"),
                ),
                Prefetch(
                    "media",
                    queryset=ProductMedia.objects.select_related("asset").order_by(
                        "-is_primary", "position", "pk"
                    ),
                ),
            ),
            slug=slug,
        )
        variants = list(
            ProductVariant.objects.filter(product=product, is_active=True)
            .prefetch_related(
                Prefetch(
                    "option_values",
                    queryset=VariantOptionValue.objects.select_related("attribute", "option"),
                )
            )
            .order_by("position", "pk")
        )
        product.active_variants = variants  # type: ignore[attr-defined]
        _summaries([product])
        used: dict[Any, list[AttributeOption]] = {}
        for variant in variants:
            for value in variant.option_values.all():
                options = used.setdefault(value.attribute_id, [])
                if value.option not in options:
                    options.append(value.option)
        for options in used.values():
            options.sort(key=lambda option: (option.position, option.value))
        context = {
            "prices": _prices([variant.pk for variant in variants]),
            "in_stock": _in_stock(variants),
            "used_options": used,
        }
        return Response(PublicProductDetailSerializer(product, context=context).data)


class PublicCategoryTreeView(PublicView):
    @extend_schema(
        operation_id="catalog_categories",
        tags=TAGS,
        responses={200: PublicCategoryNodeSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        tree = services.category_tree(active_only=True)
        return Response(PublicCategoryNodeSerializer(tree, many=True).data)


class PublicCollectionListView(PublicView):
    @extend_schema(
        operation_id="catalog_collections",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(PublicCollectionSerializer)},
    )
    def get(self, request: Request) -> Response:
        items, meta = paginate(
            Collection.objects.filter(is_active=True).order_by("name", "pk"), request
        )
        return Response({**meta, "results": PublicCollectionSerializer(items, many=True).data})


class PublicCollectionDetailView(PublicView):
    @extend_schema(
        operation_id="catalog_collection", tags=TAGS, responses={200: PublicCollectionSerializer}
    )
    def get(self, request: Request, slug: str) -> Response:
        collection = found(Collection, slug=slug, is_active=True)
        return Response(PublicCollectionSerializer(collection).data)


def is_public_asset(asset: MediaAsset) -> bool:
    """Whether storefront visitors may load ``asset``: it must be used by something published."""
    active = Q(product__status=ProductStatus.ACTIVE)
    return (
        ProductMedia.objects.filter(active, asset=asset).exists()
        or ApparelDetails.objects.filter(active, size_guide=asset).exists()
        or Category.objects.filter(image=asset, is_active=True).exists()
        or Collection.objects.filter(image=asset, is_active=True).exists()
    )


class PublicMediaView(APIView):
    """Like PublicView, but without ``never_cache``: published images may be cached briefly."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    http_method_names = ("get", "head", "options")

    @extend_schema(
        operation_id="catalog_media",
        tags=TAGS,
        responses={(200, "application/octet-stream"): bytes},
        description="An image (or PDF size guide) used by an active product, category or "
        "collection.",
    )
    def get(self, request: Request, asset_id: uuid.UUID) -> Any:
        asset = MediaAsset.objects.filter(pk=asset_id).first()
        if asset is None or not is_public_asset(asset):
            raise Http404
        return file_response(asset, public=True)
