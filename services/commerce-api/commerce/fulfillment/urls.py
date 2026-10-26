from django.urls import path

from .views import CourierTrackingCallbackView

urlpatterns = [
    path(
        "couriers/callbacks/<slug:provider>/tracking/",
        CourierTrackingCallbackView.as_view(),
        name="courier-tracking-callback",
    ),
]
