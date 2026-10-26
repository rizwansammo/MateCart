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
    # Phase 05 store appearance, modules and content (staff realm).
    path("api/manage/store/", include("commerce.stores.urls")),
    path("api/manage/content/", include("commerce.content.urls")),
    # Public, read-only catalogue for the storefront: active products only.
    path("api/catalog/", include("commerce.catalog.public_urls")),
    # Public, read-only store configuration, homepage and published pages.
    path("api/storefront/", include("commerce.content.public_urls")),
    # Phase 07 customer/guest cart, checkout sessions and customer addresses.
    path("api/", include("commerce.checkout.urls")),
    # Phase 08 orders, payments, invoices and provider callbacks; merchant order actions.
    path("api/", include("commerce.orders.urls")),
    path("api/manage/orders/", include("commerce.orders.manage_urls")),
    # Phase 09 manual fulfillment, courier boundary, COD reconciliation and fraud review.
    path("api/", include("commerce.fulfillment.urls")),
    path("api/manage/fulfillment/", include("commerce.fulfillment.manage_urls")),
    # Phase 10 customer and merchant after sales workflows.
    path("api/", include("commerce.aftersales.urls")),
    path("api/manage/aftersales/", include("commerce.aftersales.manage_urls")),
]

if settings.API_SCHEMA_ENABLED:
    urlpatterns.append(path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"))

handler404 = "commerce.core.views.not_found"
handler500 = "commerce.core.views.server_error"
