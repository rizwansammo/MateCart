from django.urls import path

from . import views

urlpatterns = [
    path("locations/", views.LocationListView.as_view(), name="inventory-locations"),
    path(
        "locations/<uuid:location_id>/",
        views.LocationDetailView.as_view(),
        name="inventory-location",
    ),
    path("levels/", views.LevelListView.as_view(), name="inventory-levels"),
    path("levels/<uuid:level_id>/", views.LevelDetailView.as_view(), name="inventory-level"),
    path("movements/", views.MovementListView.as_view(), name="inventory-movements"),
    path(
        "movements/<uuid:movement_id>/reverse/",
        views.ReversalView.as_view(),
        name="inventory-reverse",
    ),
    path("receipts/", views.ReceiptView.as_view(), name="inventory-receipts"),
    path("adjustments/", views.AdjustmentView.as_view(), name="inventory-adjustments"),
    path("transfers/", views.TransferView.as_view(), name="inventory-transfers"),
    path("batches/", views.BatchListView.as_view(), name="inventory-batches"),
    path("serials/", views.SerialListView.as_view(), name="inventory-serials"),
    path(
        "reconciliation/",
        views.ReconciliationView.as_view(),
        name="inventory-reconciliation",
    ),
]
