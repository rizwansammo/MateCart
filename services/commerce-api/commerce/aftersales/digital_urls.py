from django.urls import path
from . import digital_views

urlpatterns = [
    path("account/library/", digital_views.BuyerLibraryView.as_view(), name="buyer-library"),
    path("account/library/<uuid:entitlement_id>/download/", digital_views.DigitalDownloadView.as_view(), name="digital-download"),
]
