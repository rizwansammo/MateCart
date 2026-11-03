from django.urls import path

from . import manage_customers

urlpatterns = [
    path("", manage_customers.ManageCustomerListView.as_view(), name="manage-customers"),
    path(
        "<uuid:customer_id>/",
        manage_customers.ManageCustomerView.as_view(),
        name="manage-customer",
    ),
]
