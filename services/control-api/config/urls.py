from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView

from control.accounts.views import CsrfView

urlpatterns: list[URLPattern | URLResolver] = [
    path("api/health/", include("control.core.urls")),
    path("api/csrf/", CsrfView.as_view(), name="csrf"),
    path("api/console/", include("control.accounts.urls")),
]

if settings.API_SCHEMA_ENABLED:
    urlpatterns.append(path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"))

handler404 = "control.core.views.not_found"
handler500 = "control.core.views.server_error"
