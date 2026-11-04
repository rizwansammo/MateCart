"""Request validation and response shapes for the marketing APIs (Phase 12)."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from commerce.checkout.models import COUPON_CODE_PATTERN, Coupon, CouponDiscountType

from .models import (
    Campaign,
    CampaignKind,
    DiscountType,
    FlashSale,
    LoyaltyLedger,
    LoyaltyProgram,
    PurchaseTiming,
)
from .promotions import state

TARGET_KINDS = ("product", "category", "collection")
LOOKUP_KINDS = ("product", "category", "collection", "variant")
SITEMAP_TYPES = ("product", "category", "collection", "page")
PROMOTION_ACTIONS = ("activate", "pause", "cancel")
PROMOTION_STATES = ("draft", "scheduled", "live", "paused", "ended", "cancelled")
CONSENT_ORIGINS = ("banner", "preferences")
BROWSER_EVENTS = ("page_view", "product_view", "category_view", "collection_view")
PIXEL_STATES = ("active", "off")
SERVER_EVENT_STATES = ("active", "missing_credentials", "off")
ANALYTICS_RANGES = ("24h", "7d", "30d", "custom")


def _money(**kwargs: Any) -> serializers.DecimalField:
    return serializers.DecimalField(max_digits=19, decimal_places=4, **kwargs)


# --- Campaigns ------------------------------------------------------------------------------------


class TargetsSerializer(serializers.Serializer[Any]):
    products = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=500)
    categories = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=500
    )
    collections = serializers.ListField(
        child=serializers.UUIDField(), required=False, max_length=500
    )


class CampaignInputSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    kind = serializers.ChoiceField(choices=CampaignKind.choices)
    discount_type = serializers.ChoiceField(
        choices=DiscountType.choices, required=False, allow_blank=True
    )
    value = _money(required=False, allow_null=True, min_value=0)
    badge_text = serializers.CharField(max_length=24, required=False, allow_blank=True)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    targets = TargetsSerializer(required=False)

    def validate_badge_text(self, value: str) -> str:
        if "<" in value or ">" in value:
            raise serializers.ValidationError("Use plain text.")
        return value.strip()


class CampaignUpdateSerializer(CampaignInputSerializer):
    name = serializers.CharField(max_length=120, required=False)
    kind = serializers.ChoiceField(choices=CampaignKind.choices, required=False)
    starts_at = serializers.DateTimeField(required=False)
    ends_at = serializers.DateTimeField(required=False)


class StatusActionSerializer(serializers.Serializer[Any]):
    action = serializers.ChoiceField(choices=PROMOTION_ACTIONS)


class PerformanceCurrencySerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    sales = _money()
    discount = _money()


class PerformanceSerializer(serializers.Serializer[Any]):
    orders = serializers.IntegerField()
    units = serializers.DecimalField(max_digits=14, decimal_places=3)
    by_currency = PerformanceCurrencySerializer(many=True)


class CampaignSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField()
    kind = serializers.CharField()
    discount_type = serializers.CharField()
    value = _money(allow_null=True)
    currency = serializers.CharField()
    badge_text = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    status = serializers.CharField()
    state = serializers.SerializerMethodField()
    target_count = serializers.SerializerMethodField()
    performance = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_state(self, row: Campaign) -> str:
        return state(row.status, row.starts_at, row.ends_at)

    def get_target_count(self, row: Campaign) -> int:
        counts: dict[Any, int] = self.context.get("target_counts", {})
        return counts.get(row.pk, 0)

    def get_performance(self, row: Campaign) -> Any:
        data = self.context.get("performance", {}).get(row.pk)
        return PerformanceSerializer(data).data if data else None


class TargetRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=TARGET_KINDS)


class PreviewRowSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField()
    sku = serializers.CharField()
    product_name = serializers.CharField()
    product_status = serializers.CharField()
    currency = serializers.CharField()
    regular_amount = _money(allow_null=True)
    promotional_amount = _money(allow_null=True)
    applies = serializers.BooleanField()


class PreviewSerializer(serializers.Serializer[Any]):
    products = serializers.IntegerField()
    variants = serializers.IntegerField()
    rows = PreviewRowSerializer(many=True)


class CampaignDetailSerializer(CampaignSerializer):
    targets = TargetRefSerializer(many=True, source="target_refs")
    preview = PreviewSerializer()


# --- Flash sales ----------------------------------------------------------------------------------


class FlashItemInputSerializer(serializers.Serializer[Any]):
    variant = serializers.UUIDField()
    sale_price = _money(min_value=0)
    quantity_limit = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=1_000_000
    )
    per_order_limit = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=10_000
    )


class FlashSaleInputSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    items = serializers.ListField(child=FlashItemInputSerializer(), required=False, max_length=200)


class FlashSaleUpdateSerializer(FlashSaleInputSerializer):
    name = serializers.CharField(max_length=120, required=False)
    starts_at = serializers.DateTimeField(required=False)
    ends_at = serializers.DateTimeField(required=False)


class FlashItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    variant = serializers.UUIDField(source="variant_id")
    sku = serializers.CharField(source="variant.sku")
    product_name = serializers.CharField(source="variant.product.name")
    variant_title = serializers.CharField(source="variant.title")
    sale_price = _money()
    currency = serializers.CharField()
    regular_amount = _money(allow_null=True)
    quantity_limit = serializers.IntegerField(allow_null=True)
    per_order_limit = serializers.IntegerField(allow_null=True)
    units_sold = serializers.DecimalField(max_digits=14, decimal_places=3)


class FlashSaleSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    status = serializers.CharField()
    state = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()
    performance = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_state(self, row: FlashSale) -> str:
        return state(row.status, row.starts_at, row.ends_at)

    def get_item_count(self, row: FlashSale) -> int:
        counts: dict[Any, int] = self.context.get("item_counts", {})
        return counts.get(row.pk, 0)

    def get_performance(self, row: FlashSale) -> Any:
        data = self.context.get("performance", {}).get(row.pk)
        return PerformanceSerializer(data).data if data else None


class FlashSaleDetailSerializer(FlashSaleSerializer):
    items = FlashItemSerializer(many=True, source="item_rows")
    module_enabled = serializers.BooleanField()


# --- Coupons --------------------------------------------------------------------------------------


class CouponInputSerializer(serializers.Serializer[Any]):
    code = serializers.RegexField(COUPON_CODE_PATTERN, max_length=32)
    discount_type = serializers.ChoiceField(choices=CouponDiscountType.choices)
    value = _money(min_value=0)
    minimum_subtotal = _money(required=False, min_value=0)
    starts_at = serializers.DateTimeField(required=False, allow_null=True)
    ends_at = serializers.DateTimeField(required=False, allow_null=True)
    max_redemptions = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=1_000_000
    )
    is_active = serializers.BooleanField(required=False)

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("code"), str):
            data = {**data, "code": data["code"].strip().upper()}
        return super().to_internal_value(data)


class CouponUpdateSerializer(serializers.Serializer[Any]):
    value = _money(required=False, min_value=0)
    minimum_subtotal = _money(required=False, min_value=0)
    starts_at = serializers.DateTimeField(required=False, allow_null=True)
    ends_at = serializers.DateTimeField(required=False, allow_null=True)
    max_redemptions = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=1_000_000
    )
    is_active = serializers.BooleanField(required=False)


class CouponSerializer(serializers.ModelSerializer[Coupon]):
    class Meta:
        model = Coupon
        fields = (
            "id",
            "code",
            "discount_type",
            "value",
            "currency",
            "minimum_subtotal",
            "starts_at",
            "ends_at",
            "max_redemptions",
            "times_redeemed",
            "is_active",
            "created_at",
        )


# --- Loyalty --------------------------------------------------------------------------------------


class LoyaltyProgramInputSerializer(serializers.Serializer[Any]):
    is_enabled = serializers.BooleanField(required=False)
    earn_points = serializers.IntegerField(required=False, min_value=1, max_value=100_000)
    earn_spend = _money(required=False, min_value=0)
    point_value = _money(required=False, min_value=0)
    min_redeem_points = serializers.IntegerField(required=False, min_value=1, max_value=1_000_000)
    max_redeem_percent = serializers.IntegerField(required=False, min_value=1, max_value=100)
    expiry_days = serializers.IntegerField(
        required=False, allow_null=True, min_value=30, max_value=3650
    )


class LoyaltyProgramSerializer(serializers.ModelSerializer[LoyaltyProgram]):
    class Meta:
        model = LoyaltyProgram
        fields = (
            "is_enabled",
            "currency",
            "earn_points",
            "earn_spend",
            "point_value",
            "min_redeem_points",
            "max_redeem_percent",
            "expiry_days",
            "updated_at",
        )


class LedgerEntrySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    points = serializers.IntegerField()
    deficit_change = serializers.IntegerField()
    balance_after = serializers.IntegerField()
    deficit_after = serializers.IntegerField()
    order_number = serializers.SerializerMethodField()
    note = serializers.CharField()
    occurred_at = serializers.DateTimeField()

    def get_order_number(self, row: LoyaltyLedger) -> str | None:
        return row.order.number if row.order is not None else None


class LoyaltyAccountRowSerializer(serializers.Serializer[Any]):
    customer = serializers.UUIDField(source="customer_id")
    name = serializers.CharField(source="customer.full_name")
    email = serializers.CharField(source="customer.email", allow_null=True)
    balance = serializers.IntegerField()
    deficit = serializers.IntegerField()
    lifetime_earned = serializers.IntegerField()
    lifetime_redeemed = serializers.IntegerField()
    last_activity_at = serializers.DateTimeField(allow_null=True)


class LoyaltyAdjustmentSerializer(serializers.Serializer[Any]):
    customer = serializers.UUIDField()
    points = serializers.IntegerField(min_value=-1_000_000, max_value=1_000_000)
    reason = serializers.CharField(min_length=5, max_length=200)
    idempotency_key = serializers.CharField(min_length=8, max_length=80)


class CustomerLoyaltySerializer(serializers.Serializer[Any]):
    enabled = serializers.BooleanField()
    balance = serializers.IntegerField()
    deficit = serializers.IntegerField()
    pending = serializers.IntegerField(help_text="Points waiting for payment and delivery.")
    lifetime_earned = serializers.IntegerField()
    lifetime_redeemed = serializers.IntegerField()
    point_value = _money(allow_null=True)
    currency = serializers.CharField(allow_null=True)
    earn_points = serializers.IntegerField(allow_null=True)
    earn_spend = _money(allow_null=True)
    min_redeem_points = serializers.IntegerField(allow_null=True)
    expiry_days = serializers.IntegerField(allow_null=True)
    entries = LedgerEntrySerializer(many=True)


# --- SEO ------------------------------------------------------------------------------------------


class SeoSettingsInputSerializer(serializers.Serializer[Any]):
    home_title = serializers.CharField(max_length=70, required=False, allow_blank=True)
    home_description = serializers.CharField(max_length=320, required=False, allow_blank=True)
    social_image = serializers.UUIDField(required=False, allow_null=True)
    allow_indexing = serializers.BooleanField(required=False)


class SeoOverrideInputSerializer(serializers.Serializer[Any]):
    seo_title = serializers.CharField(max_length=70, required=False, allow_blank=True)
    seo_description = serializers.CharField(max_length=320, required=False, allow_blank=True)


class SeoItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    public = serializers.BooleanField()
    seo_title = serializers.CharField()
    seo_description = serializers.CharField()


class SeoSettingsSerializer(serializers.Serializer[Any]):
    home_title = serializers.CharField()
    home_description = serializers.CharField()
    social_image = serializers.UUIDField(allow_null=True)
    social_image_url = serializers.CharField(allow_null=True)
    allow_indexing = serializers.BooleanField()
    can_change_indexing = serializers.BooleanField()
    items = SeoItemSerializer(many=True)
    products_missing_seo = serializers.IntegerField()
    active_products = serializers.IntegerField()


class SitemapEntrySerializer(serializers.Serializer[Any]):
    type = serializers.ChoiceField(choices=SITEMAP_TYPES)
    slug = serializers.CharField()
    updated_at = serializers.DateTimeField()


class SitemapSerializer(serializers.Serializer[Any]):
    count = serializers.IntegerField(help_text="Published products.")
    page_size = serializers.IntegerField()
    entries = SitemapEntrySerializer(many=True)


class SitemapQuerySerializer(serializers.Serializer[Any]):
    section = serializers.ChoiceField(choices=["pages", "products"])
    page = serializers.IntegerField(min_value=1, max_value=100, default=1)


# --- Tracking, consent and analytics --------------------------------------------------------------


class TrackingInputSerializer(serializers.Serializer[Any]):
    analytics_enabled = serializers.BooleanField(required=False)
    meta_enabled = serializers.BooleanField(required=False)
    meta_pixel_id = serializers.CharField(max_length=20, required=False, allow_blank=True)
    meta_capi_enabled = serializers.BooleanField(required=False)
    meta_send_hashed_contact = serializers.BooleanField(required=False)
    meta_purchase_timing = serializers.ChoiceField(choices=PurchaseTiming.choices, required=False)
    meta_test_event_code = serializers.CharField(max_length=40, required=False, allow_blank=True)


class TrackingStatusSerializer(serializers.Serializer[Any]):
    browser_pixel = serializers.ChoiceField(choices=PIXEL_STATES)
    server_events = serializers.ChoiceField(choices=SERVER_EVENT_STATES)
    token_configured = serializers.BooleanField()
    public_url_configured = serializers.BooleanField()
    pending_events = serializers.IntegerField()
    failed_events = serializers.IntegerField()
    sent_events = serializers.IntegerField()


class TrackingSerializer(serializers.Serializer[Any]):
    analytics_enabled = serializers.BooleanField()
    meta_enabled = serializers.BooleanField()
    meta_pixel_id = serializers.CharField()
    meta_capi_enabled = serializers.BooleanField()
    meta_send_hashed_contact = serializers.BooleanField()
    meta_purchase_timing = serializers.CharField()
    meta_test_event_code = serializers.CharField()
    status = TrackingStatusSerializer()
    consent_records_30d = serializers.IntegerField()
    consent_marketing_30d = serializers.IntegerField()
    consent_analytics_30d = serializers.IntegerField()


class ConsentInputSerializer(serializers.Serializer[Any]):
    analytics = serializers.BooleanField()
    marketing = serializers.BooleanField()
    origin = serializers.ChoiceField(choices=CONSENT_ORIGINS)


class ConsentSerializer(serializers.Serializer[Any]):
    analytics = serializers.BooleanField()
    marketing = serializers.BooleanField()
    policy_version = serializers.CharField()


class EventInputSerializer(serializers.Serializer[Any]):
    name = serializers.ChoiceField(choices=BROWSER_EVENTS)
    object_id = serializers.UUIDField(required=False, allow_null=True)
    landing = serializers.BooleanField(required=False, default=False)


class AmountSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    amount = _money()


class OrderValueSerializer(AmountSerializer):
    orders = serializers.IntegerField()


class LiveSummarySerializer(serializers.Serializer[Any]):
    window_start = serializers.DateTimeField(help_text="Counters: current hour and 23 before it.")
    exact_start = serializers.DateTimeField(help_text="Visitors and orders: exactly 24 h ago.")
    now = serializers.DateTimeField()
    unique_visitors = serializers.IntegerField(help_text="Consented visitors only.")
    page_views = serializers.IntegerField()
    consented_page_views = serializers.IntegerField()
    product_views = serializers.IntegerField()
    add_to_cart = serializers.IntegerField()
    checkout_started = serializers.IntegerField()
    storefront_orders = serializers.IntegerField()
    converting_visitors = serializers.IntegerField()
    conversion_rate = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    consent_coverage = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)


class SeriesPointSerializer(serializers.Serializer[Any]):
    bucket = serializers.CharField(help_text="Store-local hour (24 h) or date.")
    page_views = serializers.IntegerField()
    add_to_cart = serializers.IntegerField()
    checkout_started = serializers.IntegerField()
    unique_visitors = serializers.IntegerField(allow_null=True)


class EngagedProductSerializer(serializers.Serializer[Any]):
    product = serializers.CharField()
    name = serializers.CharField()
    views = serializers.IntegerField()
    add_to_cart = serializers.IntegerField()


class SourceRowSerializer(serializers.Serializer[Any]):
    utm_source = serializers.CharField()
    utm_medium = serializers.CharField()
    utm_campaign = serializers.CharField()
    landings = serializers.IntegerField()
    orders = serializers.IntegerField()
    order_value = AmountSerializer(many=True)


class PromotionResultSerializer(PerformanceSerializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class CouponUsageSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    currency = serializers.CharField()
    uses = serializers.IntegerField()
    discount = _money()


class LoyaltyActivitySerializer(serializers.Serializer[Any]):
    earned = serializers.IntegerField()
    redeemed = serializers.IntegerField()
    restored = serializers.IntegerField()
    reversed = serializers.IntegerField()
    expired = serializers.IntegerField()
    adjusted = serializers.IntegerField()
    pending_awards = serializers.IntegerField()


class TrafficSerializer(serializers.Serializer[Any]):
    page_views = serializers.IntegerField()
    consented_page_views = serializers.IntegerField()
    consent_coverage = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)
    unique_visitors = serializers.IntegerField()
    product_views = serializers.IntegerField()
    category_views = serializers.IntegerField()


class FunnelSerializer(serializers.Serializer[Any]):
    product_views = serializers.IntegerField()
    add_to_cart = serializers.IntegerField()
    checkout_started = serializers.IntegerField()
    storefront_orders = serializers.IntegerField()
    converting_visitors = serializers.IntegerField()
    conversion_rate = serializers.DecimalField(max_digits=6, decimal_places=1, allow_null=True)


class OrdersSummarySerializer(serializers.Serializer[Any]):
    storefront_orders = serializers.IntegerField()
    storefront_cancelled = serializers.IntegerField()
    assisted_orders = serializers.IntegerField()
    order_value = OrderValueSerializer(many=True)
    collected = AmountSerializer(many=True)


class AnalyticsReportSerializer(serializers.Serializer[Any]):
    range = serializers.CharField()
    rolling = serializers.BooleanField()
    start = serializers.DateTimeField()
    counter_start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    first_day = serializers.DateField(allow_null=True)
    last_day = serializers.DateField(allow_null=True)
    traffic = TrafficSerializer()
    funnel = FunnelSerializer()
    orders = OrdersSummarySerializer()
    series = SeriesPointSerializer(many=True)
    top_products = EngagedProductSerializer(many=True)
    sources = SourceRowSerializer(many=True)
    campaigns = PromotionResultSerializer(many=True)
    flash_sales = PromotionResultSerializer(many=True)
    coupons = CouponUsageSerializer(many=True)
    loyalty = LoyaltyActivitySerializer()
    active_coupons = serializers.IntegerField()


class AnalyticsQuerySerializer(serializers.Serializer[Any]):
    range = serializers.ChoiceField(choices=ANALYTICS_RANGES, default="7d")
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)


class LookupRowSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=LOOKUP_KINDS)
    name = serializers.CharField()
    detail = serializers.CharField()
    regular_amount = _money(allow_null=True)
    currency = serializers.CharField()


class OverviewSerializer(serializers.Serializer[Any]):
    live_campaigns = serializers.IntegerField(allow_null=True)
    scheduled_campaigns = serializers.IntegerField(allow_null=True)
    live_flash_sales = serializers.IntegerField(allow_null=True)
    scheduled_flash_sales = serializers.IntegerField(allow_null=True)
    active_coupons = serializers.IntegerField(allow_null=True)
    loyalty_enabled = serializers.BooleanField(allow_null=True)
    loyalty_members = serializers.IntegerField(allow_null=True)
    flash_sales_module = serializers.BooleanField()
    loyalty_module = serializers.BooleanField()
    tracking = TrackingStatusSerializer(allow_null=True)
    analytics_enabled = serializers.BooleanField(allow_null=True)
    live = LiveSummarySerializer(allow_null=True)
    seo_indexing_preference = serializers.BooleanField(allow_null=True)
