from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView

from commerce.identity.views import CsrfView

# Every route lives under /api/ so a reverse proxy can send /api/* here and everything else to
# the Next.js app without path collisions.
urlpatterns: list[URLPattern | URLResolver] = [
    path("api/health/", include("commerce.core.urls")),
    path("api/csrf/", CsrfView.as_view(), name="csrf"),
    # Customer realm (storefront accounts) and staff realm (merchant dashboard, D-040).
    path("api/account/", include("commerce.identity.urls")),
    path("api/manage/", include("commerce.staff.urls")),
]

if settings.API_SCHEMA_ENABLED:
    urlpatterns.append(path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"))

handler404 = "commerce.core.views.not_found"
handler500 = "commerce.core.views.server_error"
