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
    # Phase 04 merchant catalogue, pricing, inventory and media (staff realm, catalog.manage).
    path("api/manage/catalog/", include("commerce.catalog.urls")),
    path("api/manage/pricing/", include("commerce.pricing.urls")),
    path("api/manage/inventory/", include("commerce.inventory.urls")),
    path("api/manage/media/", include("commerce.media.urls")),
    # Public, read-only catalogue for the storefront: active products only.
    path("api/catalog/", include("commerce.catalog.public_urls")),
]

if settings.API_SCHEMA_ENABLED:
    urlpatterns.append(path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"))

handler404 = "commerce.core.views.not_found"
handler500 = "commerce.core.views.server_error"
