from django.urls import path

from . import views

urlpatterns = [
    path("settings/", views.SocialSettingsView.as_view(), name="social-settings"),
    path("live-chat/", views.LiveChatSettingsView.as_view(), name="social-live-chat"),
    path("connections/start/", views.StartConnectionView.as_view(), name="social-start"),
    path("oauth/callback/", views.OAuthCallbackView.as_view(), name="social-oauth-callback"),
    path("connections/selection/", views.SelectionView.as_view(), name="social-selection"),
    path("connections/select/", views.SelectView.as_view(), name="social-select"),
    path(
        "connections/<uuid:connection_id>/verify/",
        views.VerifyConnectionView.as_view(),
        name="social-verify",
    ),
    path(
        "connections/<uuid:connection_id>/enabled/",
        views.EnableConnectionView.as_view(),
        name="social-enabled",
    ),
    path(
        "connections/<uuid:connection_id>/disconnect/",
        views.DisconnectView.as_view(),
        name="social-disconnect",
    ),
    path(
        "connections/<uuid:connection_id>/templates/sync/",
        views.SyncTemplatesView.as_view(),
        name="social-sync-templates",
    ),
    path("simulator/", views.SimulatorView.as_view(), name="social-simulator"),
]
