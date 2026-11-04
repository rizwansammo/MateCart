"""Merchant notification routes under /api/manage/notifications/ (staff realm)."""

from django.urls import path

from . import views

urlpatterns = [
    path("inbox/", views.StaffInboxView.as_view(), name="notifications-inbox"),
    path("inbox/unread/", views.StaffUnreadView.as_view(), name="notifications-unread"),
    path("inbox/read/", views.StaffReadView.as_view(), name="notifications-read"),
    path("preferences/", views.StaffPreferencesView.as_view(), name="notifications-prefs"),
    path("deliveries/", views.DeliveryListView.as_view(), name="notifications-deliveries"),
    path(
        "deliveries/summary/",
        views.DeliverySummaryView.as_view(),
        name="notifications-delivery-summary",
    ),
    path(
        "deliveries/<uuid:notification_id>/attempts/",
        views.DeliveryAttemptsView.as_view(),
        name="notifications-attempts",
    ),
    path(
        "deliveries/<uuid:notification_id>/retry/",
        views.DeliveryRetryView.as_view(),
        name="notifications-retry",
    ),
    path("email/", views.EmailSettingsView.as_view(), name="notifications-email"),
    path("email/smtp/", views.SmtpView.as_view(), name="notifications-smtp"),
    path("email/smtp/test/", views.SmtpTestView.as_view(), name="notifications-smtp-test"),
    path("email/smtp/sender-code/", views.SenderCodeView.as_view(), name="notifications-code"),
    path(
        "email/smtp/sender-confirm/",
        views.SenderConfirmView.as_view(),
        name="notifications-confirm",
    ),
    path("email/imap/", views.ImapView.as_view(), name="notifications-imap"),
    path("email/imap/verify/", views.ImapVerifyView.as_view(), name="notifications-imap-verify"),
    path(
        "email/matemail/start/",
        views.MateMailStartView.as_view(),
        name="notifications-matemail-start",
    ),
    path(
        "email/matemail/check/",
        views.MateMailCheckView.as_view(),
        name="notifications-matemail-check",
    ),
    path(
        "email/matemail/verify/",
        views.MateMailVerifyView.as_view(),
        name="notifications-matemail-verify",
    ),
    path(
        "email/matemail/disconnect/",
        views.MateMailDisconnectView.as_view(),
        name="notifications-matemail-disconnect",
    ),
    path("email/select/", views.SelectProviderView.as_view(), name="notifications-select"),
    path("sms/", views.SmsView.as_view(), name="notifications-sms"),
    path("templates/", views.TemplateListView.as_view(), name="notifications-templates"),
    path(
        "templates/<str:key>/<str:locale>/",
        views.TemplateDetailView.as_view(),
        name="notifications-template",
    ),
    path(
        "templates/<str:key>/<str:locale>/preview/",
        views.TemplatePreviewView.as_view(),
        name="notifications-template-preview",
    ),
]
