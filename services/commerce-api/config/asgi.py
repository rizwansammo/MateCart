"""ASGI entry point: HTTP (Django) and WebSocket (Channels, Phase 14 D-092).

Deployed processes must set DJANGO_SETTINGS_MODULE explicitly. Origin and authentication checks
for WebSockets happen in the consumers (commerce.communications.consumers), which also enforce
tenant context, frame limits and session revocation.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

# Django must be set up before the consumers import models.
django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from commerce.communications.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_application,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
