"""Merchant catalogue API under /api/manage/catalog/ (staff realm, ``catalog.manage``).

Views parse requests and render responses; every change goes through ``services``. Objects are
looked up through tenant-scoped managers, so another store's ids answer 404 like unknown ids.
"""

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from django.db.models import Count, Max, Min, Prefetch, Q, QuerySet, Sum
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.core.domain import found, found_in
from commerce.core.pagination import PageQuerySerializer, paginate, paginated
from commerce.inventory.models import StockLevel
from commerce.pricing.models import Price
from commerce.pricing.services import find_default_price_list
from commerce.staff.views import CatalogStaffView

from . import services
from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    Collection,
    CollectionProduct,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductMedia,
    ProductOption,
    ProductVariant,
    VariantOptionValue,
    WarrantyPolicy,
)
from .serializers import (
    AttributeSerializer,
    AttributeWriteSerializer,
    BrandSerializer,
    BrandWriteSerializer,
    CategoryNodeSerializer,
    CategorySerializer,
    CategoryWriteSerializer,
    CollectionProductsSerializer,
    CollectionSerializer,
    CollectionWriteSerializer,
    OptionSerializer,
    OptionWriteSerializer,
    ProductCreateSerializer,
    ProductDetailSerializer,
    ProductListItemSerializer,
    ProductListQuerySerializer,
    ProductMediaSetSerializer,
    ProductOptionsSerializer,
    ProductStatusSerializer,
    ProductUpdateSerializer,
    VariantSerializer,
    VariantUpdateSerializer,
    VariantWriteSerializer,
    WarrantySerializer,
    WarrantyWriteSerializer,
)

TAGS = ["manage-catalog"]
ZERO = Decimal(0)
PRODUCT_ORDERING = {
    "name": ("name", "pk"),
    "-name": ("-name", "-pk"),
    "created_at": ("created_at", "pk"),
    "-created_at": ("-created_at", "-pk"),
    "updated_at": ("updated_at", "pk"),
    "-updated_at": ("-updated_at", "-pk"),
}


def body(
    serializer_class: type[serializers.Serializer[Any]],
    request: Request,
    *,
    partial: bool = False,
    instance: Any = None,
) -> dict[str, Any]:
    serializer = serializer_class(instance, data=request.data, partial=partial)
    serializer.is_valid(raise_exception=True)
    data: dict[str, Any] = dict(serializer.validated_data)
    return data


def query(serializer_class: type[serializers.Serializer[Any]], request: Request) -> dict[str, Any]:
    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data: dict[str, Any] = dict(serializer.validated_data)
    return data


def page_response(
    items: Sequence[Any], meta: dict[str, int], serializer: type[Any], **context: Any
) -> Response:
    return Response({**meta, "results": serializer(items, many=True, context=context).data})


class CatalogView(CatalogStaffView):
    pass


@method_decorator(never_cache, name="dispatch")
class _ReadView(CatalogView):
    """Merchant reads are never cached by browsers or proxies (drafts, costs)."""


# --- Brands ---------------------------------------------------------------------------------------


class BrandListView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_brands",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(BrandSerializer)},
    )
    def get(self, request: Request) -> Response:
        items, meta = paginate(Brand.objects.order_by("name", "pk"), request)
        return page_response(items, meta, BrandSerializer)

    @extend_schema(
        operation_id="manage_catalog_brand_create",
        tags=TAGS,
        request=BrandWriteSerializer,
        responses={201: BrandSerializer},
    )
    def post(self, request: Request) -> Response:
        brand = services.save_brand(
            brand=None, data=body(BrandWriteSerializer, request), actor=self.user
        )
        return Response(BrandSerializer(brand).data, status=status.HTTP_201_CREATED)


class BrandDetailView(_ReadView):
    @extend_schema(operation_id="manage_catalog_brand", tags=TAGS, responses={200: BrandSerializer})
    def get(self, request: Request, brand_id: uuid.UUID) -> Response:
        return Response(BrandSerializer(found(Brand, pk=brand_id)).data)

    @extend_schema(
        operation_id="manage_catalog_brand_update",
        tags=TAGS,
        request=BrandWriteSerializer,
        responses={200: BrandSerializer},
    )
    def patch(self, request: Request, brand_id: uuid.UUID) -> Response:
        brand = found(Brand, pk=brand_id)
        data = body(BrandWriteSerializer, request, partial=True)
        return Response(
            BrandSerializer(services.save_brand(brand=brand, data=data, actor=self.user)).data
        )

    @extend_schema(operation_id="manage_catalog_brand_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, brand_id: uuid.UUID) -> Response:
        services.delete_brand(brand=found(Brand, pk=brand_id), actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Warranty policies ----------------------------------------------------------------------------


class WarrantyListView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_warranties",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(WarrantySerializer)},
    )
    def get(self, request: Request) -> Response:
        items, meta = paginate(WarrantyPolicy.objects.order_by("name", "pk"), request)
        return page_response(items, meta, WarrantySerializer)

    @extend_schema(
        operation_id="manage_catalog_warranty_create",
        tags=TAGS,
        request=WarrantyWriteSerializer,
        responses={201: WarrantySerializer},
    )
    def post(self, request: Request) -> Response:
        policy = services.save_warranty(
            policy=None, data=body(WarrantyWriteSerializer, request), actor=self.user
        )
        return Response(WarrantySerializer(policy).data, status=status.HTTP_201_CREATED)


class WarrantyDetailView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_warranty_update",
        tags=TAGS,
        request=WarrantyWriteSerializer,
        responses={200: WarrantySerializer},
    )
    def patch(self, request: Request, warranty_id: uuid.UUID) -> Response:
        policy = found(WarrantyPolicy, pk=warranty_id)
        data = body(WarrantyWriteSerializer, request, partial=True)
        saved = services.save_warranty(policy=policy, data=data, actor=self.user)
        return Response(WarrantySerializer(saved).data)

    @extend_schema(operation_id="manage_catalog_warranty_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, warranty_id: uuid.UUID) -> Response:
        policy = found(WarrantyPolicy, pk=warranty_id)
        services.delete_warranty(policy=policy, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Categories -----------------------------------------------------------------------------------


class CategoryListView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_categories",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(CategorySerializer)},
    )
    def get(self, request: Request) -> Response:
        items, meta = paginate(Category.objects.order_by("position", "name", "pk"), request)
        return page_response(items, meta, CategorySerializer)

    @extend_schema(
        operation_id="manage_catalog_category_create",
        tags=TAGS,
        request=CategoryWriteSerializer,
        responses={201: CategorySerializer},
    )
    def post(self, request: Request) -> Response:
        category = services.save_category(
            category=None, data=body(CategoryWriteSerializer, request), actor=self.user
        )
        return Response(CategorySerializer(category).data, status=status.HTTP_201_CREATED)


class CategoryTreeView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_category_tree",
        tags=TAGS,
        responses={200: CategoryNodeSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        tree = services.category_tree(active_only=False)
        return Response(CategoryNodeSerializer(tree, many=True).data)


class CategoryDetailView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_category", tags=TAGS, responses={200: CategorySerializer}
    )
    def get(self, request: Request, category_id: uuid.UUID) -> Response:
        return Response(CategorySerializer(found(Category, pk=category_id)).data)

    @extend_schema(
        operation_id="manage_catalog_category_update",
        tags=TAGS,
        request=CategoryWriteSerializer,
        responses={200: CategorySerializer},
    )
    def patch(self, request: Request, category_id: uuid.UUID) -> Response:
        category = found(Category, pk=category_id)
        data = body(CategoryWriteSerializer, request, partial=True)
        saved = services.save_category(category=category, data=data, actor=self.user)
        return Response(CategorySerializer(saved).data)

    @extend_schema(operation_id="manage_catalog_category_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, category_id: uuid.UUID) -> Response:
        category = found(Category, pk=category_id)
        services.delete_category(category=category, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Collections ----------------------------------------------------------------------------------


def _collections() -> QuerySet[Collection, Collection]:
    return Collection.objects.annotate(product_count=Count("entries")).order_by("name", "pk")


class CollectionListView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_collections",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(CollectionSerializer)},
    )
    def get(self, request: Request) -> Response:
        items, meta = paginate(_collections(), request)
        return page_response(items, meta, CollectionSerializer)

    @extend_schema(
        operation_id="manage_catalog_collection_create",
        tags=TAGS,
        request=CollectionWriteSerializer,
        responses={201: CollectionSerializer},
    )
    def post(self, request: Request) -> Response:
        collection = services.save_collection(
            collection=None, data=body(CollectionWriteSerializer, request), actor=self.user
        )
        created = _collections().get(pk=collection.pk)
        return Response(CollectionSerializer(created).data, status=status.HTTP_201_CREATED)


class CollectionDetailView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_collection", tags=TAGS, responses={200: CollectionSerializer}
    )
    def get(self, request: Request, collection_id: uuid.UUID) -> Response:
        return Response(CollectionSerializer(found_in(_collections(), pk=collection_id)).data)

    @extend_schema(
        operation_id="manage_catalog_collection_update",
        tags=TAGS,
        request=CollectionWriteSerializer,
        responses={200: CollectionSerializer},
    )
    def patch(self, request: Request, collection_id: uuid.UUID) -> Response:
        collection = found(Collection, pk=collection_id)
        data = body(CollectionWriteSerializer, request, partial=True)
        services.save_collection(collection=collection, data=data, actor=self.user)
        return Response(CollectionSerializer(_collections().get(pk=collection.pk)).data)

    @extend_schema(
        operation_id="manage_catalog_collection_delete", tags=TAGS, responses={204: None}
    )
    def delete(self, request: Request, collection_id: uuid.UUID) -> Response:
        collection = found(Collection, pk=collection_id)
        services.delete_collection(collection=collection, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CollectionProductsView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_collection_products",
        tags=TAGS,
        responses={200: CollectionProductsSerializer},
    )
    def get(self, request: Request, collection_id: uuid.UUID) -> Response:
        collection = found(Collection, pk=collection_id)
        ids = CollectionProduct.objects.filter(collection=collection).order_by("position", "pk")
        return Response({"products": [str(pk) for pk in ids.values_list("product_id", flat=True)]})

    @extend_schema(
        operation_id="manage_catalog_collection_products_set",
        tags=TAGS,
        request=CollectionProductsSerializer,
        responses={200: CollectionProductsSerializer},
    )
    def put(self, request: Request, collection_id: uuid.UUID) -> Response:
        collection = found(Collection, pk=collection_id)
        products = body(CollectionProductsSerializer, request)["products"]
        services.set_collection_products(collection=collection, products=products, actor=self.user)
        return Response({"products": [str(product.pk) for product in products]})


# --- Attributes -----------------------------------------------------------------------------------


def _attributes() -> QuerySet[AttributeDefinition, AttributeDefinition]:
    return AttributeDefinition.objects.prefetch_related(
        Prefetch("options", queryset=AttributeOption.objects.order_by("position", "value", "pk"))
    ).order_by("position", "name", "pk")


class AttributeListView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_attributes",
        tags=TAGS,
        parameters=[PageQuerySerializer],
        responses={200: paginated(AttributeSerializer)},
    )
    def get(self, request: Request) -> Response:
        items, meta = paginate(_attributes(), request)
        return page_response(items, meta, AttributeSerializer)

    @extend_schema(
        operation_id="manage_catalog_attribute_create",
        tags=TAGS,
        request=AttributeWriteSerializer,
        responses={201: AttributeSerializer},
    )
    def post(self, request: Request) -> Response:
        attribute = services.save_attribute(
            attribute=None, data=body(AttributeWriteSerializer, request), actor=self.user
        )
        return Response(
            AttributeSerializer(_attributes().get(pk=attribute.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class AttributeDetailView(_ReadView):
    @extend_schema(
        operation_id="manage_catalog_attribute", tags=TAGS, responses={200: AttributeSerializer}
    )
    def get(self, request: Request, attribute_id: uuid.UUID) -> Response:
        return Response(AttributeSerializer(found_in(_attributes(), pk=attribute_id)).data)

    @extend_schema(
        operation_id="manage_catalog_attribute_update",
        tags=TAGS,
        request=AttributeWriteSerializer,
        responses={200: AttributeSerializer},
    )
    def patch(self, request: Request, attribute_id: uuid.UUID) -> Response:
        attribute = found(AttributeDefinition, pk=attribute_id)
        data = body(AttributeWriteSerializer, request, partial=True)
        services.save_attribute(attribute=attribute, data=data, actor=self.user)
        return Response(AttributeSerializer(_attributes().get(pk=attribute.pk)).data)

    @extend_schema(operation_id="manage_catalog_attribute_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, attribute_id: uuid.UUID) -> Response:
        attribute = found(AttributeDefinition, pk=attribute_id)
        services.delete_attribute(attribute=attribute, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class OptionListView(CatalogView):
    @extend_schema(
        operation_id="manage_catalog_option_create",
        tags=TAGS,
        request=OptionWriteSerializer,
        responses={201: OptionSerializer},
    )
    def post(self, request: Request, attribute_id: uuid.UUID) -> Response:
        attribute = found(AttributeDefinition, pk=attribute_id)
        option = services.save_option(
            attribute=attribute,
            option=None,
            data=body(OptionWriteSerializer, request),
            actor=self.user,
        )
        return Response(OptionSerializer(option).data, status=status.HTTP_201_CREATED)


class OptionDetailView(CatalogView):
    @extend_schema(
        operation_id="manage_catalog_option_update",
        tags=TAGS,
        request=OptionWriteSerializer,
        responses={200: OptionSerializer},
    )
    def patch(self, request: Request, attribute_id: uuid.UUID, option_id: uuid.UUID) -> Response:
        attribute = found(AttributeDefinition, pk=attribute_id)
        option = found(AttributeOption, pk=option_id, attribute=attribute)
        data = body(OptionWriteSerializer, request, partial=True)
        saved = services.save_option(attribute=attribute, option=option, data=data, actor=self.user)
        return Response(OptionSerializer(saved).data)

    @extend_schema(operation_id="manage_catalog_option_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, attribute_id: uuid.UUID, option_id: uuid.UUID) -> Response:
        option = found(AttributeOption, pk=option_id, attribute_id=attribute_id)
        services.delete_option(option=option, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Products -------------------------------------------------------------------------------------


def product_detail_queryset() -> QuerySet[Product, Product]:
    return Product.objects.select_related(
        "brand", "apparel", "grocery", "electronics__warranty", "book"
    ).prefetch_related(
        Prefetch(
            "placements",
            queryset=ProductCategory.objects.select_related("category").order_by(
                "-is_primary", "category__name", "pk"
            ),
        ),
        Prefetch(
            "options",
            queryset=ProductOption.objects.select_related("attribute").order_by("position", "pk"),
        ),
        Prefetch(
            "variants",
            queryset=ProductVariant.objects.order_by("position", "pk").prefetch_related(
                Prefetch(
                    "option_values",
                    queryset=VariantOptionValue.objects.select_related("attribute", "option"),
                ),
                Prefetch("prices", queryset=Price.objects.order_by("created_at", "pk")),
            ),
        ),
        Prefetch(
            "specifications",
            queryset=ProductAttributeValue.objects.select_related("attribute", "option").order_by(
                "attribute__position", "attribute__name"
            ),
        ),
        Prefetch(
            "media",
            queryset=ProductMedia.objects.select_related("asset").order_by("position", "pk"),
        ),
    )


def stock_totals(variant_ids: Sequence[Any]) -> dict[Any, dict[str, Decimal]]:
    rows = (
        StockLevel.objects.filter(variant_id__in=variant_ids, location__is_active=True)
        .values("variant_id")
        .annotate(on_hand=Sum("on_hand"), reserved=Sum("reserved"), available=Sum("available"))
    )
    return {
        row["variant_id"]: {
            "on_hand": row["on_hand"],
            "reserved": row["reserved"],
            "available": row["available"],
        }
        for row in rows
    }


class _ProductView(_ReadView):
    def detail_response(self, product_id: Any, *, code: int = status.HTTP_200_OK) -> Response:
        product = found_in(product_detail_queryset(), pk=product_id)
        product.publication_problems = services.publication_problems(product)  # type: ignore[attr-defined]
        context = {
            "show_costs": self.can_see_costs,
            "stock": stock_totals([variant.pk for variant in product.variants.all()]),
        }
        return Response(ProductDetailSerializer(product, context=context).data, status=code)


def _with_list_extras(products: list[Product]) -> list[Product]:
    """Per-page summaries in a few grouped queries instead of per-row joins."""
    ids = [product.pk for product in products]
    variant_counts = dict(
        ProductVariant.objects.filter(product_id__in=ids)
        .values("product_id")
        .annotate(n=Count("pk"))
        .values_list("product_id", "n")
    )
    available = dict(
        StockLevel.objects.filter(variant__product_id__in=ids, location__is_active=True)
        .values("variant__product_id")
        .annotate(total=Sum("available"))
        .values_list("variant__product_id", "total")
    )
    images = dict(
        ProductMedia.objects.filter(product_id__in=ids, is_primary=True).values_list(
            "product_id", "asset_id"
        )
    )
    price_list = find_default_price_list()
    ranges: dict[Any, dict[str, Any]] = {}
    if price_list is not None:
        for row in (
            Price.objects.filter(
                price_list=price_list, variant__product_id__in=ids, variant__is_active=True
            )
            .values("variant__product_id")
            .annotate(low=Min("amount"), high=Max("amount"))
        ):
            ranges[row["variant__product_id"]] = {
                "currency": price_list.currency,
                "min_amount": row["low"],
                "max_amount": row["high"],
            }
    for product in products:
        product.variant_count = variant_counts.get(product.pk, 0)  # type: ignore[attr-defined]
        product.available = available.get(product.pk) or ZERO  # type: ignore[attr-defined]
        product.primary_image = images.get(product.pk)  # type: ignore[attr-defined]
        product.price_range = ranges.get(product.pk)  # type: ignore[attr-defined]
    return products


class ProductListView(_ProductView):
    @extend_schema(
        operation_id="manage_catalog_products",
        tags=TAGS,
        parameters=[ProductListQuerySerializer, PageQuerySerializer],
        responses={200: paginated(ProductListItemSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = query(ProductListQuerySerializer, request)
        products = Product.objects.all()
        if params.get("q"):
            text = params["q"].strip()
            matching_variants = ProductVariant.objects.filter(sku__icontains=text).values(
                "product_id"
            )
            products = products.filter(
                Q(name__icontains=text)
                | Q(slug__icontains=text.lower())
                | Q(tags__contains=[text.lower()])
                | Q(pk__in=matching_variants)
            )
        for field in ("status", "product_type"):
            if params.get(field):
                products = products.filter(**{field: params[field]})
        if params.get("brand"):
            products = products.filter(brand_id=params["brand"])
        if params.get("category"):
            products = products.filter(
                pk__in=ProductCategory.objects.filter(category_id=params["category"]).values(
                    "product_id"
                )
            )
        if params.get("collection"):
            products = products.filter(
                pk__in=CollectionProduct.objects.filter(collection_id=params["collection"]).values(
                    "product_id"
                )
            )
        ordering = PRODUCT_ORDERING[params.get("ordering", "-created_at")]
        items, meta = paginate(products.order_by(*ordering), request)
        return page_response(_with_list_extras(items), meta, ProductListItemSerializer)

    @extend_schema(
        operation_id="manage_catalog_product_create",
        tags=TAGS,
        request=ProductCreateSerializer,
        responses={201: ProductDetailSerializer},
    )
    def post(self, request: Request) -> Response:
        product = services.create_product(
            data=body(ProductCreateSerializer, request),
            actor=self.user,
            allow_cost=self.can_see_costs,
        )
        return self.detail_response(product.pk, code=status.HTTP_201_CREATED)


class ProductDetailView(_ProductView):
    @extend_schema(
        operation_id="manage_catalog_product", tags=TAGS, responses={200: ProductDetailSerializer}
    )
    def get(self, request: Request, product_id: uuid.UUID) -> Response:
        return self.detail_response(product_id)

    @extend_schema(
        operation_id="manage_catalog_product_update",
        tags=TAGS,
        request=ProductUpdateSerializer,
        responses={200: ProductDetailSerializer},
    )
    def patch(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        data = body(ProductUpdateSerializer, request, partial=True, instance=product)
        services.update_product(product=product, data=data, actor=self.user)
        return self.detail_response(product.pk)

    @extend_schema(operation_id="manage_catalog_product_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        services.delete_product(product=product, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductStatusView(_ProductView):
    @extend_schema(
        operation_id="manage_catalog_product_status",
        tags=TAGS,
        request=ProductStatusSerializer,
        responses={200: ProductDetailSerializer},
        description="Publish (active), unpublish (draft), archive or restore a product.",
    )
    def post(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        target = body(ProductStatusSerializer, request)["status"]
        services.set_status(product=product, status=target, actor=self.user)
        return self.detail_response(product.pk)


class ProductOptionsView(_ProductView):
    @extend_schema(
        operation_id="manage_catalog_product_options",
        tags=TAGS,
        request=ProductOptionsSerializer,
        responses={200: ProductDetailSerializer},
    )
    def put(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        attributes = body(ProductOptionsSerializer, request)["options"]
        services.set_product_options(product=product, attributes=attributes, actor=self.user)
        return self.detail_response(product.pk)


class ProductMediaView(_ProductView):
    @extend_schema(
        operation_id="manage_catalog_product_media",
        tags=TAGS,
        request=ProductMediaSetSerializer,
        responses={200: ProductDetailSerializer},
    )
    def put(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        entries = body(ProductMediaSetSerializer, request)["media"]
        services.set_product_media(product=product, entries=entries, actor=self.user)
        return self.detail_response(product.pk)


def _variant_response(view: CatalogView, variant_id: Any, *, code: int = 200) -> Response:
    variant = found_in(
        ProductVariant.objects.prefetch_related(
            Prefetch(
                "option_values",
                queryset=VariantOptionValue.objects.select_related("attribute", "option"),
            ),
            Prefetch("prices", queryset=Price.objects.order_by("created_at", "pk")),
        ),
        pk=variant_id,
    )
    context = {"show_costs": view.can_see_costs, "stock": stock_totals([variant.pk])}
    return Response(VariantSerializer(variant, context=context).data, status=code)


class ProductVariantsView(CatalogView):
    @extend_schema(
        operation_id="manage_catalog_variant_create",
        tags=TAGS,
        request=VariantWriteSerializer,
        responses={201: VariantSerializer},
    )
    def post(self, request: Request, product_id: uuid.UUID) -> Response:
        product = found(Product, pk=product_id)
        variant = services.create_variant(
            product=product,
            data=body(VariantWriteSerializer, request),
            actor=self.user,
            allow_cost=self.can_see_costs,
        )
        return _variant_response(self, variant.pk, code=status.HTTP_201_CREATED)


@method_decorator(never_cache, name="dispatch")
class VariantDetailView(CatalogView):
    @extend_schema(
        operation_id="manage_catalog_variant",
        tags=TAGS,
        responses={200: VariantSerializer},
    )
    def get(self, request: Request, variant_id: uuid.UUID) -> Response:
        return _variant_response(self, variant_id)

    @extend_schema(
        operation_id="manage_catalog_variant_update",
        tags=TAGS,
        request=VariantUpdateSerializer,
        responses={200: VariantSerializer},
    )
    def patch(self, request: Request, variant_id: uuid.UUID) -> Response:
        variant = found(ProductVariant, pk=variant_id)
        data = body(VariantUpdateSerializer, request, partial=True)
        services.update_variant(variant=variant, data=data, actor=self.user)
        return _variant_response(self, variant.pk)

    @extend_schema(operation_id="manage_catalog_variant_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, variant_id: uuid.UUID) -> Response:
        variant = found(ProductVariant, pk=variant_id)
        services.delete_variant(variant=variant, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
