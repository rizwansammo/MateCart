from django.urls import path

from . import digital_views

urlpatterns = [
    path("assets/", digital_views.ManageDigitalAssetsView.as_view(), name="manage-digital-assets"),
]
