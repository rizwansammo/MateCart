"""Marketing, SEO and analytics records (Phase 12, D-081 to D-085).

Every table is tenant-owned. Promotions (campaigns, flash sales) price variants through
``commerce.marketing.promotions``; the loyalty ledger and consent records are append-only;
analytics keep aggregates and a short-lived, pseudonymous presence row per consented visitor.
"""

from __future__ import annotations

from typing import Any

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q

from commerce.catalog.models import Category, Collection, Product, ProductVariant
from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.media.models import MediaAsset
from commerce.orders.models import Order
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

SHA256_HEX = r"^[0-9a-f]{64}$"
PIXEL_ID_PATTERN = r"^[0-9]{5,20}$"


def _money(**kwargs: Any) -> models.DecimalField[Any, Any]:
    return models.DecimalField(max_digits=19, decimal_places=4, **kwargs)


class AppendOnlyError(RuntimeError):
    pass


class _AppendOnly(models.Model):
    """ORM guard; PostgreSQL triggers (migration 0002) refuse UPDATE and DELETE below it."""

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AppendOnlyError(f"{type(self).__name__} rows are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise AppendOnlyError(f"{type(self).__name__} rows are append-only.")


# --- Promotions -----------------------------------------------------------------------------------


class PromotionStatus(models.TextChoices):
    """Stored lifecycle. Whether an ``active`` promotion is scheduled, live or ended is derived
    from its window and the server clock, never stored or taken from the browser."""

    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    CANCELLED = "cancelled", "Cancelled"


class CampaignKind(models.TextChoices):
    DISCOUNT = "discount", "Price discount"
    FEATURED = "featured", "Featured products (no price change)"


class DiscountType(models.TextChoices):
    PERCENT = "percent", "Percentage"
    FIXED = "fixed", "Fixed amount"


class Campaign(TenantOwnedModel, TimestampedModel):
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True)
    kind = models.CharField(max_length=16, choices=CampaignKind.choices)
    discount_type = models.CharField(max_length=16, choices=DiscountType.choices, blank=True)
    value = _money(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    badge_text = models.CharField(max_length=24, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=PromotionStatus.choices, default=PromotionStatus.DRAFT
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.CheckConstraint(
                condition=Q(kind__in=CampaignKind.values), name="marketing_campaign_kind_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=PromotionStatus.values),
                name="marketing_campaign_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(starts_at__lt=F("ends_at")), name="marketing_campaign_window_valid"
            ),
            models.CheckConstraint(
                condition=(
                    Q(kind=CampaignKind.FEATURED, discount_type="", value__isnull=True, currency="")
                )
                | Q(
                    kind=CampaignKind.DISCOUNT,
                    discount_type=DiscountType.PERCENT,
                    value__gt=0,
                    value__lt=100,
                    currency="",
                )
                | (
                    Q(kind=CampaignKind.DISCOUNT, discount_type=DiscountType.FIXED, value__gt=0)
                    & Q(currency__regex=r"^[A-Z]{3}$")
                ),
                name="marketing_campaign_discount_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "ends_at"], name="marketing_campaign_live"),
        ]


class CampaignTarget(TenantOwnedModel):
    """One product, category (with its subcategories) or collection a campaign applies to."""

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="targets")
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    collection = models.ForeignKey(
        Collection, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="campaign", name="marketing_target_campaign_tenant"),
            TenantForeignKeyConstraint(field="product", name="marketing_target_product_tenant"),
            TenantForeignKeyConstraint(field="category", name="marketing_target_category_tenant"),
            TenantForeignKeyConstraint(
                field="collection", name="marketing_target_collection_tenant"
            ),
            models.CheckConstraint(
                condition=Q(product__isnull=False, category__isnull=True, collection__isnull=True)
                | Q(product__isnull=True, category__isnull=False, collection__isnull=True)
                | Q(product__isnull=True, category__isnull=True, collection__isnull=False),
                name="marketing_target_exactly_one",
            ),
            models.UniqueConstraint(
                fields=["tenant", "campaign", "product"],
                condition=Q(product__isnull=False),
                name="marketing_target_product_unique",
            ),
            models.UniqueConstraint(
                fields=["tenant", "campaign", "category"],
                condition=Q(category__isnull=False),
                name="marketing_target_category_unique",
            ),
            models.UniqueConstraint(
                fields=["tenant", "campaign", "collection"],
                condition=Q(collection__isnull=False),
                name="marketing_target_collection_unique",
            ),
        ]


class FlashSale(TenantOwnedModel, TimestampedModel):
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=PromotionStatus.choices, default=PromotionStatus.DRAFT
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.CheckConstraint(
                condition=Q(status__in=PromotionStatus.values),
                name="marketing_flashsale_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(starts_at__lt=F("ends_at")), name="marketing_flashsale_window_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "ends_at"], name="marketing_flashsale_live"),
        ]


class FlashSaleItem(TenantOwnedModel, TimestampedModel):
    """A variant's flash price. ``units_sold`` counts units committed by placed, uncancelled
    orders; it changes only under this row's lock (placement and cancellation)."""

    sale = models.ForeignKey(FlashSale, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="+")
    sale_price = _money(validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3)
    quantity_limit = models.PositiveIntegerField(null=True, blank=True)
    per_order_limit = models.PositiveIntegerField(null=True, blank=True)
    units_sold = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="sale", name="marketing_flashitem_sale_tenant"),
            TenantForeignKeyConstraint(field="variant", name="marketing_flashitem_variant_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "sale", "variant"], name="marketing_flashitem_variant_unique"
            ),
            models.CheckConstraint(
                condition=Q(sale_price__gte=0), name="marketing_flashitem_price_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"),
                name="marketing_flashitem_currency_format",
            ),
            models.CheckConstraint(
                condition=Q(units_sold__gte=0), name="marketing_flashitem_sold_gte_0"
            ),
            models.CheckConstraint(
                condition=Q(quantity_limit__isnull=True) | Q(units_sold__lte=F("quantity_limit")),
                name="marketing_flashitem_within_limit",
            ),
            models.CheckConstraint(
                condition=Q(quantity_limit__isnull=True) | Q(quantity_limit__gt=0),
                name="marketing_flashitem_limit_gt_0",
            ),
            models.CheckConstraint(
                condition=Q(per_order_limit__isnull=True) | Q(per_order_limit__gt=0),
                name="marketing_flashitem_order_limit_gt_0",
            ),
        ]


class UsageStatus(models.TextChoices):
    COMMITTED = "committed", "Committed by an order"
    RELEASED = "released", "Released by cancellation"


class FlashSaleUsage(TenantOwnedModel, TimestampedModel):
    """Units of a limited flash sale an order took. Released exactly once on cancellation."""

    item = models.ForeignKey(FlashSaleItem, on_delete=models.PROTECT, related_name="usages")
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    status = models.CharField(
        max_length=16, choices=UsageStatus.choices, default=UsageStatus.COMMITTED
    )
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="item", name="marketing_flashusage_item_tenant"),
            TenantForeignKeyConstraint(field="order", name="marketing_flashusage_order_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "item", "order"], name="marketing_flashusage_unique"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="marketing_flashusage_quantity_gt_0"
            ),
            models.CheckConstraint(
                condition=(Q(status=UsageStatus.COMMITTED) & Q(released_at__isnull=True))
                | (Q(status=UsageStatus.RELEASED) & Q(released_at__isnull=False)),
                name="marketing_flashusage_status_consistent",
            ),
        ]


# --- Loyalty --------------------------------------------------------------------------------------


class LoyaltyProgram(TenantOwnedModel, TimestampedModel):
    """The store's single loyalty rule set. Points are whole numbers.

    Earning: ``earn_points`` for every ``earn_spend`` of eligible spend (items after discounts,
    excluding delivery and tax). Redemption: each point is worth ``point_value`` in ``currency``.
    """

    is_enabled = models.BooleanField(default=False)
    currency = models.CharField(max_length=3)
    earn_points = models.PositiveIntegerField(default=1)
    earn_spend = _money(default=100)
    point_value = _money(default=1)
    min_redeem_points = models.PositiveIntegerField(default=100)
    max_redeem_percent = models.PositiveIntegerField(
        default=50, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    expiry_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant"], name="marketing_loyalty_program_one"),
            models.CheckConstraint(
                condition=Q(earn_points__gt=0) & Q(earn_spend__gt=0) & Q(point_value__gt=0),
                name="marketing_loyalty_rates_positive",
            ),
            models.CheckConstraint(
                condition=Q(max_redeem_percent__gte=1) & Q(max_redeem_percent__lte=100),
                name="marketing_loyalty_cap_valid",
            ),
            models.CheckConstraint(
                condition=Q(expiry_days__isnull=True) | Q(expiry_days__gte=30),
                name="marketing_loyalty_expiry_valid",
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"),
                name="marketing_loyalty_currency_format",
            ),
        ]


class LoyaltyAccount(TenantOwnedModel, TimestampedModel):
    """Cached balances; every change appends a ledger row in the same transaction under this
    row's lock. ``balance`` is usable points (never negative). ``deficit`` records points that a
    refund reversed after they were already spent; future earnings repay it first."""

    customer = models.OneToOneField(User, on_delete=models.PROTECT, related_name="+")
    balance = models.PositiveIntegerField(default=0)
    deficit = models.PositiveIntegerField(default=0)
    lifetime_earned = models.PositiveIntegerField(default=0)
    lifetime_redeemed = models.PositiveIntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="customer", name="marketing_loyalty_account_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "customer"], name="marketing_loyalty_account_unique"
            ),
        ]


class LedgerKind(models.TextChoices):
    EARNED = "earned", "Earned"
    REDEEMED = "redeemed", "Redeemed at checkout"
    RESTORED = "restored", "Redemption restored"
    REVERSED = "reversed", "Reversed after refund"
    EXPIRED = "expired", "Expired"
    ADJUSTED = "adjusted", "Adjusted by staff"


class LoyaltyLedger(_AppendOnly, TenantOwnedModel):  # noqa: DJ008
    account = models.ForeignKey(LoyaltyAccount, on_delete=models.PROTECT, related_name="ledger")
    kind = models.CharField(max_length=16, choices=LedgerKind.choices)
    points = models.IntegerField(help_text="Change to the usable balance.")
    deficit_change = models.IntegerField(default=0)
    balance_after = models.PositiveIntegerField()
    deficit_after = models.PositiveIntegerField(default=0)
    order = models.ForeignKey(
        Order, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reference = models.CharField(max_length=120, help_text="Stable business key, unique.")
    note = models.CharField(max_length=200, blank=True)
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    occurred_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="account", name="marketing_ledger_account_tenant"),
            TenantForeignKeyConstraint(field="order", name="marketing_ledger_order_tenant"),
            TenantForeignKeyConstraint(field="actor", name="marketing_ledger_actor_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "reference"], name="marketing_ledger_reference_unique"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=LedgerKind.values), name="marketing_ledger_kind_valid"
            ),
            models.CheckConstraint(
                condition=~Q(points=0) | ~Q(deficit_change=0), name="marketing_ledger_nonzero"
            ),
            models.CheckConstraint(
                condition=~Q(kind=LedgerKind.ADJUSTED) | Q(actor__isnull=False),
                name="marketing_ledger_adjustment_actor",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "account", "occurred_at"], name="marketing_ledger_account"
            ),
            models.Index(fields=["tenant", "occurred_at"], name="marketing_ledger_time"),
        ]


class AwardStatus(models.TextChoices):
    PENDING = "pending", "Waiting for payment and delivery"
    EARNED = "earned", "Earned"
    CANCELLED = "cancelled", "Cancelled"


class LoyaltyAward(TenantOwnedModel, TimestampedModel):
    """Points an order can earn, fixed at placement from its immutable totals and the program
    rule at that moment. ``earned_points`` is what the ledger currently credits for the order."""

    account = models.ForeignKey(LoyaltyAccount, on_delete=models.PROTECT, related_name="awards")
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="+")
    points = models.PositiveIntegerField()
    earned_points = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=AwardStatus.choices, default=AwardStatus.PENDING
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="account", name="marketing_award_account_tenant"),
            TenantForeignKeyConstraint(field="order", name="marketing_award_order_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "order"], name="marketing_award_order_unique"
            ),
            models.CheckConstraint(
                condition=Q(status__in=AwardStatus.values), name="marketing_award_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(earned_points__lte=F("points")), name="marketing_award_within_points"
            ),
        ]


# --- SEO ------------------------------------------------------------------------------------------


class SeoSettings(TenantOwnedModel, TimestampedModel):
    """Store-level search and social defaults. Plain text only; the storefront escapes it."""

    home_title = models.CharField(max_length=70, blank=True)
    home_description = models.CharField(max_length=320, blank=True)
    social_image = models.ForeignKey(
        MediaAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    allow_indexing = models.BooleanField(
        default=True,
        help_text="Merchant preference. Indexing also needs the deployment's public URL "
        "and STOREFRONT_INDEXING=allow.",
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="social_image", name="marketing_seo_image_tenant"),
            models.UniqueConstraint(fields=["tenant"], name="marketing_seo_settings_one"),
        ]


# --- Tracking, consent and analytics --------------------------------------------------------------


class PurchaseTiming(models.TextChoices):
    PAYMENT_VERIFIED = "payment_verified", "When payment is verified or cash is collected"
    ORDER_PLACED = "order_placed", "When the order is placed"


class TrackingSettings(TenantOwnedModel, TimestampedModel):
    """Optional tracking. Everything external is off until a merchant switches it on, and even
    then runs only for visitors who granted the matching consent."""

    analytics_enabled = models.BooleanField(default=True)
    meta_enabled = models.BooleanField(default=False)
    meta_pixel_id = models.CharField(max_length=20, blank=True)
    meta_capi_enabled = models.BooleanField(default=False)
    meta_send_hashed_contact = models.BooleanField(default=False)
    meta_purchase_timing = models.CharField(
        max_length=20, choices=PurchaseTiming.choices, default=PurchaseTiming.PAYMENT_VERIFIED
    )
    meta_test_event_code = models.CharField(max_length=40, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant"], name="marketing_tracking_settings_one"),
            models.CheckConstraint(
                condition=Q(meta_pixel_id="") | Q(meta_pixel_id__regex=PIXEL_ID_PATTERN),
                name="marketing_tracking_pixel_format",
            ),
            models.CheckConstraint(
                condition=Q(meta_enabled=False) | ~Q(meta_pixel_id=""),
                name="marketing_tracking_enabled_needs_pixel",
            ),
            models.CheckConstraint(
                condition=Q(meta_purchase_timing__in=PurchaseTiming.values),
                name="marketing_tracking_timing_valid",
            ),
        ]


class ConsentRecord(_AppendOnly, TenantOwnedModel):  # noqa: DJ008
    """One consent decision. ``subject_digest`` is the SHA-256 of the browser's random consent
    id (the raw id lives only in the visitor's cookie). Strictly necessary storage is always on
    and therefore not recorded. No IP address, user agent or account link is kept. Rows are never
    updated; the retention clean-up deletes them after two years."""

    subject_digest = models.CharField(max_length=64, validators=[RegexValidator(SHA256_HEX)])
    analytics = models.BooleanField()
    marketing = models.BooleanField()
    policy_version = models.CharField(max_length=16)
    source = models.CharField(max_length=16)
    recorded_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.CheckConstraint(
                condition=Q(subject_digest__regex=SHA256_HEX), name="marketing_consent_digest"
            ),
            models.CheckConstraint(
                condition=Q(source__in=["banner", "preferences"]),
                name="marketing_consent_source_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "subject_digest", "recorded_at"], name="marketing_consent_subject"
            ),
            models.Index(fields=["tenant", "recorded_at"], name="marketing_consent_time"),
        ]


class EventName(models.TextChoices):
    PAGE_VIEW = "page_view", "Page view"
    PRODUCT_VIEW = "product_view", "Product view"
    CATEGORY_VIEW = "category_view", "Category view"
    COLLECTION_VIEW = "collection_view", "Collection view"
    LANDING = "landing", "Campaign landing (tagged link)"
    ADD_TO_CART = "add_to_cart", "Added to cart"
    CHECKOUT_STARTED = "checkout_started", "Checkout started"


class EventCounter(TenantOwnedModel):
    """Hourly aggregate. ``dimension`` is '' for the total, a product/category/collection id,
    or a normalised source key. ``consented`` splits counts by analytics consent."""

    bucket = models.DateTimeField(help_text="UTC hour start.")
    event = models.CharField(max_length=20, choices=EventName.choices)
    dimension = models.CharField(max_length=100, blank=True)
    consented = models.BooleanField()
    count = models.PositiveBigIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "bucket", "event", "dimension", "consented"],
                name="marketing_counter_unique",
            ),
            models.CheckConstraint(
                condition=Q(event__in=EventName.values), name="marketing_counter_event_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "event", "bucket"], name="marketing_counter_event"),
            models.Index(fields=["tenant", "bucket"], name="marketing_counter_bucket"),
        ]


class RecentEventCounter(TenantOwnedModel):
    """Per-minute event totals for the rolling 24-hour figures (D-083, Phase 12 correction).

    Totals only: no dimension, no identifier. Kept 25 hours, so the table holds at most about
    1,500 rows per event and consent flag, whatever the traffic.
    """

    minute = models.DateTimeField(help_text="UTC minute start.")
    event = models.CharField(max_length=20, choices=EventName.choices)
    consented = models.BooleanField()
    count = models.PositiveBigIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "minute", "event", "consented"],
                name="marketing_recent_counter_unique",
            ),
            models.CheckConstraint(
                condition=Q(event__in=EventName.values),
                name="marketing_recent_counter_event_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "minute"], name="marketing_recent_minute"),
        ]


class VisitorPresence(TenantOwnedModel):
    """The last time a consented visitor was seen, keyed by a keyed hash of their random visitor
    cookie. Kept 48 hours: enough for exact rolling-24-hour and per-day unique counts."""

    visitor_digest = models.CharField(max_length=64, validators=[RegexValidator(SHA256_HEX)])
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    last_order_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "visitor_digest"], name="marketing_presence_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "last_seen_at"], name="marketing_presence_seen"),
            models.Index(fields=["tenant", "last_order_at"], name="marketing_presence_order"),
        ]


class VisitorDailyStat(TenantOwnedModel):
    """Per store-local day: consented unique visitors and how many of them placed an order."""

    date = models.DateField()
    unique_visitors = models.PositiveIntegerField(default=0)
    converting_visitors = models.PositiveIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(fields=["tenant", "date"], name="marketing_daily_unique"),
        ]


class OrderAttribution(TenantOwnedModel):
    """The tracked marketing channel of a storefront order, recorded only with analytics
    consent: last tagged (UTM) landing within 30 days. Not proof of payment or of causation."""

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="+")
    source = models.CharField(max_length=64)
    medium = models.CharField(max_length=64, blank=True)
    campaign = models.CharField(max_length=64, blank=True)
    landed_at = models.DateTimeField(null=True, blank=True)
    recorded_at = models.DateTimeField()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="marketing_attribution_order_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "order"], name="marketing_attribution_order_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "recorded_at"], name="marketing_attribution_time"),
        ]


class DeliveryStatus(models.TextChoices):
    WAITING = "waiting", "Waiting for verified payment"
    PENDING = "pending", "Waiting to send"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed after retries"
    DISCARDED = "discarded", "Discarded (no longer authorised)"


class MetaEventDelivery(TenantOwnedModel, TimestampedModel):
    """One server-side Conversions API event. ``event_id`` is shared with the browser Pixel for
    deduplication. Browser identifiers are kept only until the event is delivered or discarded;
    the event body is built at send time and never stored."""

    event_name = models.CharField(max_length=32)
    event_id = models.CharField(max_length=80)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="+")
    consent_digest = models.CharField(max_length=64, validators=[RegexValidator(SHA256_HEX)])
    fbp = models.CharField(max_length=120, blank=True)
    fbc = models.CharField(max_length=200, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    source_url = models.CharField(max_length=300, blank=True)
    event_time = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=60, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="order", name="marketing_meta_order_tenant"),
            models.UniqueConstraint(fields=["tenant", "event_id"], name="marketing_meta_event_id"),
            models.CheckConstraint(
                condition=Q(status__in=DeliveryStatus.values), name="marketing_meta_status_valid"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "created_at"], name="marketing_meta_status"),
        ]
