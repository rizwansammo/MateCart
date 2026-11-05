from django.urls import path

from . import views

urlpatterns = [
    path("", views.OverviewView.as_view(), name="ai-overview"),
    path("settings/", views.AISettingsView.as_view(), name="ai-settings"),
    path("settings/key/", views.AIKeyView.as_view(), name="ai-key"),
    path("settings/verify/", views.AIVerifyView.as_view(), name="ai-verify"),
    path("settings/customer-data/", views.AICustomerDataView.as_view(), name="ai-customer-data"),
    path("usage/", views.AIUsageView.as_view(), name="ai-usage"),
    path("requests/", views.AIRequestListView.as_view(), name="ai-requests"),
    path("requests/<uuid:request_id>/", views.AIRequestDetailView.as_view(), name="ai-request"),
    path(
        "requests/<uuid:request_id>/cancel/",
        views.AIRequestCancelView.as_view(),
        name="ai-request-cancel",
    ),
    path(
        "requests/<uuid:request_id>/retry/",
        views.AIRequestRetryView.as_view(),
        name="ai-request-retry",
    ),
    path(
        "requests/<uuid:request_id>/apply/",
        views.AIRequestApplyView.as_view(),
        name="ai-request-apply",
    ),
    path(
        "requests/<uuid:request_id>/review/",
        views.AIRequestReviewView.as_view(),
        name="ai-request-review",
    ),
]
