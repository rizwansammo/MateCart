from django.urls import path

from . import public

urlpatterns = [
    path("products/", public.PublicProductListView.as_view(), name="public-products"),
    path("products/<slug:slug>/", public.PublicProductDetailView.as_view(), name="public-product"),
    path("categories/", public.PublicCategoryTreeView.as_view(), name="public-categories"),
    path("collections/", public.PublicCollectionListView.as_view(), name="public-collections"),
    path(
        "collections/<slug:slug>/",
        public.PublicCollectionDetailView.as_view(),
        name="public-collection",
    ),
    path("media/<uuid:asset_id>/", public.PublicMediaView.as_view(), name="public-media"),
]
