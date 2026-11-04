"""Merchant marketing routes under /api/manage/marketing/ (staff realm)."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.OverviewView.as_view(), name="marketing-overview"),
    path("lookup/", views.LookupView.as_view(), name="marketing-lookup"),
    path("campaigns/", views.CampaignListView.as_view(), name="marketing-campaigns"),
    path(
        "campaigns/<uuid:campaign_id>/",
        views.CampaignDetailView.as_view(),
        name="marketing-campaign",
    ),
    path(
        "campaigns/<uuid:campaign_id>/status/",
        views.CampaignStatusView.as_view(),
        name="marketing-campaign-status",
    ),
    path("flash-sales/", views.FlashSaleListView.as_view(), name="marketing-flash-sales"),
    path(
        "flash-sales/<uuid:sale_id>/",
        views.FlashSaleDetailView.as_view(),
        name="marketing-flash-sale",
    ),
    path(
        "flash-sales/<uuid:sale_id>/status/",
        views.FlashSaleStatusView.as_view(),
        name="marketing-flash-sale-status",
    ),
    path("coupons/", views.CouponListView.as_view(), name="marketing-coupons"),
    path("coupons/<uuid:coupon_id>/", views.CouponDetailView.as_view(), name="marketing-coupon"),
    path("loyalty/", views.LoyaltyProgramView.as_view(), name="marketing-loyalty"),
    path("loyalty/members/", views.LoyaltyMembersView.as_view(), name="marketing-members"),
    path(
        "loyalty/members/<uuid:customer_id>/",
        views.LoyaltyMemberView.as_view(),
        name="marketing-member",
    ),
    path("loyalty/adjust/", views.LoyaltyAdjustView.as_view(), name="marketing-adjust"),
    path("seo/", views.SeoView.as_view(), name="marketing-seo"),
    path(
        "seo/<str:kind>/<uuid:item_id>/",
        views.SeoOverrideView.as_view(),
        name="marketing-seo-override",
    ),
    path("analytics/", views.AnalyticsView.as_view(), name="marketing-analytics"),
    path("analytics/live/", views.LiveAnalyticsView.as_view(), name="marketing-live"),
    path("tracking/", views.TrackingView.as_view(), name="marketing-tracking"),
]
