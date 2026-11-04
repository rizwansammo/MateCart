"""Customer notification routes and the public unsubscribe link."""

from django.urls import path

from . import views

urlpatterns = [
    path("account/notifications/", views.CustomerInboxView.as_view(), name="account-inbox"),
    path(
        "account/notifications/read/", views.CustomerReadView.as_view(), name="account-inbox-read"
    ),
    path(
        "account/notifications/preferences/",
        views.CustomerPreferencesView.as_view(),
        name="account-notification-prefs",
    ),
    path("notifications/unsubscribe/", views.UnsubscribeView.as_view(), name="unsubscribe"),
]
