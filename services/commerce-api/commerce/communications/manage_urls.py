from django.urls import path

from . import views

urlpatterns = [
    path("conversations/", views.ConversationListView.as_view(), name="inbox-conversations"),
    path(
        "conversations/<uuid:conversation_id>/",
        views.ConversationDetailView.as_view(),
        name="inbox-conversation",
    ),
    path(
        "conversations/<uuid:conversation_id>/thread/",
        views.ThreadView.as_view(),
        name="inbox-thread",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages/",
        views.ReplyView.as_view(),
        name="inbox-reply",
    ),
    path(
        "conversations/<uuid:conversation_id>/attachments/",
        views.StaffAttachmentUploadView.as_view(),
        name="inbox-attachment-upload",
    ),
    path(
        "conversations/<uuid:conversation_id>/notes/", views.NoteView.as_view(), name="inbox-note"
    ),
    path(
        "conversations/<uuid:conversation_id>/assign/",
        views.AssignView.as_view(),
        name="inbox-assign",
    ),
    path(
        "conversations/<uuid:conversation_id>/status/",
        views.StatusView.as_view(),
        name="inbox-status",
    ),
    path("conversations/<uuid:conversation_id>/read/", views.ReadView.as_view(), name="inbox-read"),
    path(
        "conversations/<uuid:conversation_id>/orders/",
        views.ConversationOrderView.as_view(),
        name="inbox-order",
    ),
    path(
        "conversations/<uuid:conversation_id>/orders/link/",
        views.LinkOrderView.as_view(),
        name="inbox-order-link",
    ),
    path(
        "conversations/<uuid:conversation_id>/opt-in/",
        views.OptInView.as_view(),
        name="inbox-opt-in",
    ),
    path(
        "conversations/<uuid:conversation_id>/opt-out/",
        views.OptOutView.as_view(),
        name="inbox-opt-out",
    ),
    path("messages/<uuid:message_id>/resend/", views.ResendView.as_view(), name="inbox-resend"),
    path(
        "attachments/<uuid:attachment_id>/",
        views.StaffAttachmentView.as_view(),
        name="inbox-attachment",
    ),
    path("products/", views.ProductSearchView.as_view(), name="inbox-products"),
    path("team/", views.TeamView.as_view(), name="inbox-team"),
    path("templates/", views.TemplatesView.as_view(), name="inbox-templates"),
    path("analytics/", views.AnalyticsView.as_view(), name="inbox-analytics"),
]
