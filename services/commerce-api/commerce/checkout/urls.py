from django.urls import path

from . import views

urlpatterns = [
    path("cart/", views.CartView.as_view(), name="cart"),
    path("cart/items/", views.CartItemsView.as_view(), name="cart-items"),
    path("cart/items/<uuid:item_id>/", views.CartItemView.as_view(), name="cart-item"),
    path("checkout/", views.CheckoutCreateView.as_view(), name="checkout-create"),
    path("checkout/<uuid:session_id>/", views.CheckoutView.as_view(), name="checkout"),
    path(
        "checkout/<uuid:session_id>/review/",
        views.CheckoutReviewView.as_view(),
        name="checkout-review",
    ),
    path(
        "checkout/<uuid:session_id>/release/",
        views.CheckoutReleaseView.as_view(),
        name="checkout-release",
    ),
    path("account/addresses/", views.AddressListView.as_view(), name="addresses"),
    path("account/addresses/<uuid:address_id>/", views.AddressView.as_view(), name="address"),
]
