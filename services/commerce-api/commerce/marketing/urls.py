"""Public marketing routes: consent, view events, the sitemap feed and the customer's points."""

from django.urls import path

from . import views

urlpatterns = [
    path("consent/", views.ConsentView.as_view(), name="storefront-consent"),
    path("analytics/events/", views.EventView.as_view(), name="storefront-events"),
    path("storefront/sitemap/", views.SitemapView.as_view(), name="storefront-sitemap"),
    path("account/loyalty/", views.CustomerLoyaltyView.as_view(), name="account-loyalty"),
]
