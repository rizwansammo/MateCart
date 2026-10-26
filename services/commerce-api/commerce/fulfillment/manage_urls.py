from django.urls import path

from . import views

urlpatterns = [
    path("shipments/", views.ManageShipmentListView.as_view(), name="manage-shipments"),
    path(
        "shipments/<uuid:shipment_id>/",
        views.ManageShipmentView.as_view(),
        name="manage-shipment",
    ),
    path(
        "orders/<uuid:order_id>/shipment/",
        views.ManagePrepareShipmentView.as_view(),
        name="manage-shipment-prepare",
    ),
    path(
        "shipments/<uuid:shipment_id>/transition/",
        views.ManageShipmentTransitionView.as_view(),
        name="manage-shipment-transition",
    ),
    path(
        "shipments/<uuid:shipment_id>/courier-details/",
        views.ManageManualCourierView.as_view(),
        name="manage-shipment-courier-details",
    ),
    path(
        "shipments/<uuid:shipment_id>/courier-booking/",
        views.ManageCourierBookingView.as_view(),
        name="manage-shipment-courier-booking",
    ),
    path(
        "shipments/<uuid:shipment_id>/courier-booking/cancel/",
        views.ManageCourierBookingCancelView.as_view(),
        name="manage-shipment-courier-booking-cancel",
    ),
    path(
        "shipments/<uuid:shipment_id>/cod-collection/",
        views.ManageCodCollectionView.as_view(),
        name="manage-shipment-cod-collection",
    ),
    path(
        "cod-records/<uuid:record_id>/remittance/",
        views.ManageCodRemittanceView.as_view(),
        name="manage-cod-remittance",
    ),
    path(
        "orders/<uuid:order_id>/fraud-assessment/",
        views.ManageFraudAssessmentView.as_view(),
        name="manage-fraud-assessment",
    ),
    path(
        "fraud-assessments/<uuid:assessment_id>/review/",
        views.ManageFraudReviewView.as_view(),
        name="manage-fraud-review",
    ),
]
