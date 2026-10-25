from django.urls import path

from . import views

urlpatterns = [
    path("brands/", views.BrandListView.as_view(), name="catalog-brands"),
    path("brands/<uuid:brand_id>/", views.BrandDetailView.as_view(), name="catalog-brand"),
    path("warranties/", views.WarrantyListView.as_view(), name="catalog-warranties"),
    path(
        "warranties/<uuid:warranty_id>/",
        views.WarrantyDetailView.as_view(),
        name="catalog-warranty",
    ),
    path("categories/", views.CategoryListView.as_view(), name="catalog-categories"),
    path("categories/tree/", views.CategoryTreeView.as_view(), name="catalog-category-tree"),
    path(
        "categories/<uuid:category_id>/",
        views.CategoryDetailView.as_view(),
        name="catalog-category",
    ),
    path("collections/", views.CollectionListView.as_view(), name="catalog-collections"),
    path(
        "collections/<uuid:collection_id>/",
        views.CollectionDetailView.as_view(),
        name="catalog-collection",
    ),
    path(
        "collections/<uuid:collection_id>/products/",
        views.CollectionProductsView.as_view(),
        name="catalog-collection-products",
    ),
    path("attributes/", views.AttributeListView.as_view(), name="catalog-attributes"),
    path(
        "attributes/<uuid:attribute_id>/",
        views.AttributeDetailView.as_view(),
        name="catalog-attribute",
    ),
    path(
        "attributes/<uuid:attribute_id>/options/",
        views.OptionListView.as_view(),
        name="catalog-options",
    ),
    path(
        "attributes/<uuid:attribute_id>/options/<uuid:option_id>/",
        views.OptionDetailView.as_view(),
        name="catalog-option",
    ),
    path("products/", views.ProductListView.as_view(), name="catalog-products"),
    path("products/<uuid:product_id>/", views.ProductDetailView.as_view(), name="catalog-product"),
    path(
        "products/<uuid:product_id>/status/",
        views.ProductStatusView.as_view(),
        name="catalog-product-status",
    ),
    path(
        "products/<uuid:product_id>/options/",
        views.ProductOptionsView.as_view(),
        name="catalog-product-options",
    ),
    path(
        "products/<uuid:product_id>/variants/",
        views.ProductVariantsView.as_view(),
        name="catalog-product-variants",
    ),
    path(
        "products/<uuid:product_id>/media/",
        views.ProductMediaView.as_view(),
        name="catalog-product-media",
    ),
    path("variants/<uuid:variant_id>/", views.VariantDetailView.as_view(), name="catalog-variant"),
]
