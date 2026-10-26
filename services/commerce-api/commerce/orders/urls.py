from django.urls import path

from . import views

urlpatterns = [
    path(
        "checkout/<uuid:session_id>/order/",
        views.PlaceOrderView.as_view(),
        name="checkout-place-order",
    ),
    path("orders/", views.CustomerOrderListView.as_view(), name="customer-order-list"),
    path("orders/<uuid:order_id>/", views.OrderView.as_view(), name="order"),
    path("orders/<uuid:order_id>/cancel/", views.OrderCancelView.as_view(), name="order-cancel"),
    path("orders/<uuid:order_id>/payment/", views.OrderPaymentView.as_view(), name="order-payment"),
    path("orders/<uuid:order_id>/invoice/", views.OrderInvoiceView.as_view(), name="order-invoice"),
    path(
        "payments/callbacks/<slug:provider>/",
        views.PaymentCallbackView.as_view(),
        name="payment-callback",
    ),
]
