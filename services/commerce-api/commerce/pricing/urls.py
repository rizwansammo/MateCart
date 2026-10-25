from django.urls import path

from . import views

urlpatterns = [
    path("price-lists/", views.PriceListListView.as_view(), name="pricing-price-lists"),
    path(
        "price-lists/<uuid:price_list_id>/",
        views.PriceListDetailView.as_view(),
        name="pricing-price-list",
    ),
    path(
        "variants/<uuid:variant_id>/prices/",
        views.VariantPricesView.as_view(),
        name="pricing-variant-prices",
    ),
    path(
        "variants/<uuid:variant_id>/prices/<uuid:price_list_id>/",
        views.VariantPriceDetailView.as_view(),
        name="pricing-variant-price",
    ),
]
