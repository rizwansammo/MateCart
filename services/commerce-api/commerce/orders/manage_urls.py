from django.urls import path

from . import views

urlpatterns = [
    path("", views.ManageOrderListView.as_view(), name="manage-orders"),
    path("assisted/", views.ManageAssistedOrderView.as_view(), name="manage-assisted-order"),
    path(
        "assisted/quote/",
        views.ManageAssistedQuoteView.as_view(),
        name="manage-assisted-order-quote",
    ),
    path(
        "assisted/variants/",
        views.ManageAssistedVariantsView.as_view(),
        name="manage-assisted-order-variants",
    ),
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
    path(
        "<uuid:order_id>/invoice/",
        views.ManageOrderInvoiceView.as_view(),
        name="manage-order-invoice",
    ),
]
