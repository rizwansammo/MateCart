"""WebSocket routes. Staff sockets live under /api/manage so the staff cookie (path-scoped to
/api/manage) is sent; visitor sockets live under /api/chat where the chat cookie is scoped."""

from django.urls import path

from .consumers import ChatConsumer, InboxConsumer

websocket_urlpatterns = [
    path("api/chat/ws/", ChatConsumer.as_asgi()),
    path("api/manage/ws/inbox/", InboxConsumer.as_asgi()),
]
