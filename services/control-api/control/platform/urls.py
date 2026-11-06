from django.urls import path

from . import views

urlpatterns = [
    path("overview/", views.OverviewView.as_view(), name="console-overview"),
    path("capabilities/", views.CapabilitiesView.as_view(), name="console-capabilities"),
    path("clients/", views.ClientCollectionView.as_view(), name="console-clients"),
    path("clients/<uuid:pk>/", views.ClientDetailView.as_view(), name="console-client"),
    path(
        "clients/<uuid:client_id>/contacts/",
        views.ContactCollectionView.as_view(),
        name="console-client-contacts",
    ),
    path("stores/", views.StoreCollectionView.as_view(), name="console-stores"),
    path("stores/<uuid:pk>/", views.StoreDetailView.as_view(), name="console-store"),
    path("servers/", views.ServerCollectionView.as_view(), name="console-servers"),
    path("servers/<uuid:pk>/", views.ServerDetailView.as_view(), name="console-server"),
    path("deployments/", views.DeploymentCollectionView.as_view(), name="console-deployments"),
    path("deployments/<uuid:pk>/", views.DeploymentDetailView.as_view(), name="console-deployment"),
    path(
        "deployments/<uuid:deployment_id>/monitoring-credentials/",
        views.MonitoringCredentialView.as_view(),
        name="console-monitoring-credentials",
    ),
    path(
        "deployments/<uuid:deployment_id>/monitoring-credentials/<uuid:pk>/",
        views.MonitoringCredentialDetailView.as_view(),
        name="console-monitoring-credential",
    ),
    path("operations/", views.OperationCollectionView.as_view(), name="console-operations"),
    path("operations/<uuid:pk>/", views.OperationDetailView.as_view(), name="console-operation"),
    path("domains/", views.DomainCollectionView.as_view(), name="console-domains"),
    path("backups/", views.BackupCollectionView.as_view(), name="console-backups"),
    path("monitoring/", views.MonitoringView.as_view(), name="console-monitoring"),
    path("alerts/", views.AlertCollectionView.as_view(), name="console-alerts"),
    path("alerts/<uuid:pk>/", views.AlertDetailView.as_view(), name="console-alert"),
    path("notifications/", views.NotificationView.as_view(), name="console-notifications"),
    path(
        "administrators/",
        views.AdministratorCollectionView.as_view(),
        name="console-administrators",
    ),
    path(
        "administrators/<uuid:pk>/",
        views.AdministratorDetailView.as_view(),
        name="console-administrator",
    ),
    path("audit/", views.AuditView.as_view(), name="console-audit"),
    path("telemetry/observations/", views.TelemetryIngestView.as_view(), name="console-telemetry"),
]
