from django.urls import path

from . import views

urlpatterns = [
    path("", views.ManageOrderListView.as_view(), name="manage-orders"),
    path("<uuid:order_id>/", views.ManageOrderView.as_view(), name="manage-order"),
    path(
        "<uuid:order_id>/transition/",
        views.ManageOrderTransitionView.as_view(),
        name="manage-order-transition",
    ),
    path(
        "<uuid:order_id>/cod-collection/",
        views.ManageCodCollectionView.as_view(),
        name="manage-order-cod-collection",
    ),
    path("<uuid:order_id>/refunds/", views.ManageRefundView.as_view(), name="manage-order-refunds"),
]
