from django.urls import path

from . import views

urlpatterns = [
    path("chat/config/", views.ChatConfigView.as_view(), name="chat-config"),
    path("chat/messages/", views.ChatSendView.as_view(), name="chat-send"),
    path("chat/attachments/", views.ChatUploadView.as_view(), name="chat-upload"),
    path(
        "chat/attachments/<uuid:attachment_id>/",
        views.ChatAttachmentView.as_view(),
        name="chat-attachment",
    ),
    path("chat/link-account/", views.ChatLinkAccountView.as_view(), name="chat-link-account"),
    path("webhooks/meta/", views.MetaWebhookView.as_view(), name="webhook-meta"),
]
