/**
 * Runtime-validated marketing responses (Phase 12), mirroring the committed OpenAPI schema:
 * campaigns, flash sales, coupons, loyalty, SEO, tracking and first-party analytics. Money stays
 * a decimal string; nothing here calculates a price, a balance or a rate.
 */
import { array, boolean, number, object, string, type Decoder } from './auth';
import { decimal, isoDateTime, nullable } from './catalog';
import { managePage } from './manage';

function integer(value: unknown): number {
  const n = number(value);
  if (!Number.isInteger(n)) throw new Error('Invalid response');
  return n;
}
function oneOf<T extends string>(values: readonly T[]): Decoder<T> {
  return (value) => {
    const text = string(value);
    if (!(values as readonly string[]).includes(text)) throw new Error('Invalid response');
    return text as T;
  };
}
const isoDate: Decoder<string> = (value) => {
  const text = string(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) throw new Error('Invalid response');
  return text;
};

// --- Shared ---------------------------------------------------------------------------------------

export const PROMOTION_STATES = [
  'draft',
  'scheduled',
  'live',
  'paused',
  'ended',
  'cancelled',
] as const;
export type PromotionState = (typeof PROMOTION_STATES)[number];
export const PROMOTION_STATUSES = ['draft', 'active', 'paused', 'cancelled'] as const;
export type PromotionStatus = (typeof PROMOTION_STATUSES)[number];
export type PromotionAction = 'activate' | 'pause' | 'cancel';

export interface Amount {
  currency: string;
  amount: string;
}
const amount: Decoder<Amount> = (value) => {
  const o = object(value);
  return { currency: string(o.currency), amount: decimal(o.amount) };
};

export interface Performance {
  orders: number;
  units: string;
  by_currency: { currency: string; sales: string; discount: string }[];
}
const performance: Decoder<Performance> = (value) => {
  const o = object(value);
  return {
    orders: integer(o.orders),
    units: decimal(o.units),
    by_currency: array(o.by_currency, (entry) => {
      const e = object(entry);
      return {
        currency: string(e.currency),
        sales: decimal(e.sales),
        discount: decimal(e.discount),
      };
    }),
  };
};

// --- Live summary and overview --------------------------------------------------------------------

export interface LiveSummary {
  window_start: string;
  exact_start: string;
  now: string;
  unique_visitors: number;
  page_views: number;
  consented_page_views: number;
  product_views: number;
  add_to_cart: number;
  checkout_started: number;
  storefront_orders: number;
  converting_visitors: number;
  conversion_rate: string | null;
  consent_coverage: string | null;
}
export function liveSummary(value: unknown): LiveSummary {
  const o = object(value);
  return {
    window_start: isoDateTime(o.window_start),
    exact_start: isoDateTime(o.exact_start),
    now: isoDateTime(o.now),
    unique_visitors: integer(o.unique_visitors),
    page_views: integer(o.page_views),
    consented_page_views: integer(o.consented_page_views),
    product_views: integer(o.product_views),
    add_to_cart: integer(o.add_to_cart),
    checkout_started: integer(o.checkout_started),
    storefront_orders: integer(o.storefront_orders),
    converting_visitors: integer(o.converting_visitors),
    conversion_rate: nullable(decimal)(o.conversion_rate),
    consent_coverage: nullable(decimal)(o.consent_coverage),
  };
}

export interface TrackingStatus {
  browser_pixel: 'active' | 'off';
  server_events: 'active' | 'missing_credentials' | 'off';
  token_configured: boolean;
  public_url_configured: boolean;
  pending_events: number;
  failed_events: number;
  sent_events: number;
}
function trackingStatus(value: unknown): TrackingStatus {
  const o = object(value);
  return {
    browser_pixel: oneOf(['active', 'off'] as const)(o.browser_pixel),
    server_events: oneOf(['active', 'missing_credentials', 'off'] as const)(o.server_events),
    token_configured: boolean(o.token_configured),
    public_url_configured: boolean(o.public_url_configured),
    pending_events: integer(o.pending_events),
    failed_events: integer(o.failed_events),
    sent_events: integer(o.sent_events),
  };
}

export interface MarketingOverview {
  live_campaigns: number | null;
  scheduled_campaigns: number | null;
  live_flash_sales: number | null;
  scheduled_flash_sales: number | null;
  active_coupons: number | null;
  loyalty_enabled: boolean | null;
  loyalty_members: number | null;
  flash_sales_module: boolean;
  loyalty_module: boolean;
  tracking: TrackingStatus | null;
  analytics_enabled: boolean | null;
  live: LiveSummary | null;
  seo_indexing_preference: boolean | null;
}
export function marketingOverview(value: unknown): MarketingOverview {
  const o = object(value);
  return {
    live_campaigns: nullable(integer)(o.live_campaigns),
    scheduled_campaigns: nullable(integer)(o.scheduled_campaigns),
    live_flash_sales: nullable(integer)(o.live_flash_sales),
    scheduled_flash_sales: nullable(integer)(o.scheduled_flash_sales),
    active_coupons: nullable(integer)(o.active_coupons),
    loyalty_enabled: nullable(boolean)(o.loyalty_enabled),
    loyalty_members: nullable(integer)(o.loyalty_members),
    flash_sales_module: boolean(o.flash_sales_module),
    loyalty_module: boolean(o.loyalty_module),
    tracking: nullable(trackingStatus)(o.tracking),
    analytics_enabled: nullable(boolean)(o.analytics_enabled),
    live: nullable(liveSummary)(o.live),
    seo_indexing_preference: nullable(boolean)(o.seo_indexing_preference),
  };
}

export interface LookupRow {
  id: string;
  kind: 'product' | 'category' | 'collection' | 'variant';
  name: string;
  detail: string;
  regular_amount: string | null;
  currency: string;
}
export function lookupRows(value: unknown): LookupRow[] {
  return array(value, (entry) => {
    const o = object(entry);
    return {
      id: string(o.id),
      kind: oneOf(['product', 'category', 'collection', 'variant'] as const)(o.kind),
      name: string(o.name),
      detail: string(o.detail),
      regular_amount: nullable(decimal)(o.regular_amount),
      currency: string(o.currency),
    };
  });
}

// --- Campaigns ------------------------------------------------------------------------------------

export interface Campaign {
  id: string;
  name: string;
  description: string;
  kind: 'discount' | 'featured';
  discount_type: '' | 'percent' | 'fixed';
  value: string | null;
  currency: string;
  badge_text: string;
  starts_at: string;
  ends_at: string;
  status: PromotionStatus;
  state: PromotionState;
  target_count: number;
  performance: Performance | null;
  created_at: string;
  updated_at: string;
}
export function campaign(value: unknown): Campaign {
  const o = object(value);
  return {
    id: string(o.id),
    name: string(o.name),
    description: string(o.description),
    kind: oneOf(['discount', 'featured'] as const)(o.kind),
    discount_type: oneOf(['', 'percent', 'fixed'] as const)(o.discount_type),
    value: nullable(decimal)(o.value),
    currency: string(o.currency),
    badge_text: string(o.badge_text),
    starts_at: isoDateTime(o.starts_at),
    ends_at: isoDateTime(o.ends_at),
    status: oneOf(PROMOTION_STATUSES)(o.status),
    state: oneOf(PROMOTION_STATES)(o.state),
    target_count: integer(o.target_count),
    performance: nullable(performance)(o.performance),
    created_at: isoDateTime(o.created_at),
    updated_at: isoDateTime(o.updated_at),
  };
}
export const campaignPage = managePage(campaign);

export interface TargetRef {
  id: string;
  name: string;
  kind: 'product' | 'category' | 'collection';
}
export interface PreviewRow {
  variant: string;
  sku: string;
  product_name: string;
  product_status: string;
  currency: string;
  regular_amount: string | null;
  promotional_amount: string | null;
  applies: boolean;
}
export interface CampaignDetail extends Campaign {
  targets: TargetRef[];
  preview: { products: number; variants: number; rows: PreviewRow[] };
}
export function campaignDetail(value: unknown): CampaignDetail {
  const o = object(value);
  const preview = object(o.preview);
  return {
    ...campaign(value),
    targets: array(o.targets, (entry) => {
      const t = object(entry);
      return {
        id: string(t.id),
        name: string(t.name),
        kind: oneOf(['product', 'category', 'collection'] as const)(t.kind),
      };
    }),
    preview: {
      products: integer(preview.products),
      variants: integer(preview.variants),
      rows: array(preview.rows, (entry) => {
        const r = object(entry);
        return {
          variant: string(r.variant),
          sku: string(r.sku),
          product_name: string(r.product_name),
          product_status: string(r.product_status),
          currency: string(r.currency),
          regular_amount: nullable(decimal)(r.regular_amount),
          promotional_amount: nullable(decimal)(r.promotional_amount),
          applies: boolean(r.applies),
        };
      }),
    },
  };
}

// --- Flash sales ----------------------------------------------------------------------------------

export interface FlashSale {
  id: string;
  name: string;
  description: string;
  starts_at: string;
  ends_at: string;
  status: PromotionStatus;
  state: PromotionState;
  item_count: number;
  performance: Performance | null;
  created_at: string;
  updated_at: string;
}
export function flashSale(value: unknown): FlashSale {
  const o = object(value);
  return {
    id: string(o.id),
    name: string(o.name),
    description: string(o.description),
    starts_at: isoDateTime(o.starts_at),
    ends_at: isoDateTime(o.ends_at),
    status: oneOf(PROMOTION_STATUSES)(o.status),
    state: oneOf(PROMOTION_STATES)(o.state),
    item_count: integer(o.item_count),
    performance: nullable(performance)(o.performance),
    created_at: isoDateTime(o.created_at),
    updated_at: isoDateTime(o.updated_at),
  };
}
export const flashSalePage = managePage(flashSale);

export interface FlashItem {
  id: string;
  variant: string;
  sku: string;
  product_name: string;
  variant_title: string;
  sale_price: string;
  currency: string;
  regular_amount: string | null;
  quantity_limit: number | null;
  per_order_limit: number | null;
  units_sold: string;
}
export interface FlashSaleDetail extends FlashSale {
  items: FlashItem[];
  module_enabled: boolean;
}
export function flashSaleDetail(value: unknown): FlashSaleDetail {
  const o = object(value);
  return {
    ...flashSale(value),
    items: array(o.items, (entry) => {
      const i = object(entry);
      return {
        id: string(i.id),
        variant: string(i.variant),
        sku: string(i.sku),
        product_name: string(i.product_name),
        variant_title: string(i.variant_title),
        sale_price: decimal(i.sale_price),
        currency: string(i.currency),
        regular_amount: nullable(decimal)(i.regular_amount),
        quantity_limit: nullable(integer)(i.quantity_limit),
        per_order_limit: nullable(integer)(i.per_order_limit),
        units_sold: decimal(i.units_sold),
      };
    }),
    module_enabled: boolean(o.module_enabled),
  };
}

// --- Coupons --------------------------------------------------------------------------------------

export interface ManagedCoupon {
  id: string;
  code: string;
  discount_type: 'percent' | 'fixed';
  value: string;
  currency: string;
  minimum_subtotal: string;
  starts_at: string | null;
  ends_at: string | null;
  max_redemptions: number | null;
  times_redeemed: number;
  is_active: boolean;
  created_at: string;
}
export function managedCoupon(value: unknown): ManagedCoupon {
  const o = object(value);
  return {
    id: string(o.id),
    code: string(o.code),
    discount_type: oneOf(['percent', 'fixed'] as const)(o.discount_type),
    value: decimal(o.value),
    currency: string(o.currency),
    minimum_subtotal: decimal(o.minimum_subtotal),
    starts_at: nullable(isoDateTime)(o.starts_at),
    ends_at: nullable(isoDateTime)(o.ends_at),
    max_redemptions: nullable(integer)(o.max_redemptions),
    times_redeemed: integer(o.times_redeemed),
    is_active: boolean(o.is_active),
    created_at: isoDateTime(o.created_at),
  };
}
export const couponPage = managePage(managedCoupon);

// --- Loyalty --------------------------------------------------------------------------------------

export interface LoyaltyProgram {
  is_enabled: boolean;
  currency: string;
  earn_points: number;
  earn_spend: string;
  point_value: string;
  min_redeem_points: number;
  max_redeem_percent: number;
  expiry_days: number | null;
  updated_at: string;
}
export interface LoyaltyStats {
  program: LoyaltyProgram;
  module_enabled: boolean;
  members: number;
  outstanding_points: number;
  outstanding_deficit: number;
  pending_awards: number;
}
export function loyaltyStats(value: unknown): LoyaltyStats {
  const o = object(value);
  const p = object(o.program);
  return {
    program: {
      is_enabled: boolean(p.is_enabled),
      currency: string(p.currency),
      earn_points: integer(p.earn_points),
      earn_spend: decimal(p.earn_spend),
      point_value: decimal(p.point_value),
      min_redeem_points: integer(p.min_redeem_points),
      max_redeem_percent: integer(p.max_redeem_percent),
      expiry_days: nullable(integer)(p.expiry_days),
      updated_at: isoDateTime(p.updated_at),
    },
    module_enabled: boolean(o.module_enabled),
    members: integer(o.members),
    outstanding_points: integer(o.outstanding_points),
    outstanding_deficit: integer(o.outstanding_deficit),
    pending_awards: integer(o.pending_awards),
  };
}

export const LEDGER_KINDS = [
  'earned',
  'redeemed',
  'restored',
  'reversed',
  'expired',
  'adjusted',
] as const;
export type LedgerKind = (typeof LEDGER_KINDS)[number];
export interface LedgerEntry {
  id: string;
  kind: LedgerKind;
  points: number;
  deficit_change: number;
  balance_after: number;
  deficit_after: number;
  order_number: string | null;
  note: string;
  occurred_at: string;
}
export function ledgerEntry(value: unknown): LedgerEntry {
  const o = object(value);
  return {
    id: string(o.id),
    kind: oneOf(LEDGER_KINDS)(o.kind),
    points: integer(o.points),
    deficit_change: integer(o.deficit_change),
    balance_after: integer(o.balance_after),
    deficit_after: integer(o.deficit_after),
    order_number: nullable(string)(o.order_number),
    note: string(o.note),
    occurred_at: isoDateTime(o.occurred_at),
  };
}
export function ledgerEntries(value: unknown): LedgerEntry[] {
  return array(value, ledgerEntry);
}

export interface LoyaltyMember {
  customer: string;
  name: string;
  email: string | null;
  balance: number;
  deficit: number;
  lifetime_earned: number;
  lifetime_redeemed: number;
  last_activity_at: string | null;
}
export const loyaltyMemberPage = managePage<LoyaltyMember>((value) => {
  const o = object(value);
  return {
    customer: string(o.customer),
    name: string(o.name),
    email: nullable(string)(o.email),
    balance: integer(o.balance),
    deficit: integer(o.deficit),
    lifetime_earned: integer(o.lifetime_earned),
    lifetime_redeemed: integer(o.lifetime_redeemed),
    last_activity_at: nullable(isoDateTime)(o.last_activity_at),
  };
});

export interface CustomerLoyalty {
  enabled: boolean;
  balance: number;
  deficit: number;
  pending: number;
  lifetime_earned: number;
  lifetime_redeemed: number;
  point_value: string | null;
  currency: string | null;
  earn_points: number | null;
  earn_spend: string | null;
  min_redeem_points: number | null;
  expiry_days: number | null;
  entries: LedgerEntry[];
}
export function customerLoyalty(value: unknown): CustomerLoyalty {
  const o = object(value);
  return {
    enabled: boolean(o.enabled),
    balance: integer(o.balance),
    deficit: integer(o.deficit),
    pending: integer(o.pending),
    lifetime_earned: integer(o.lifetime_earned),
    lifetime_redeemed: integer(o.lifetime_redeemed),
    point_value: nullable(decimal)(o.point_value),
    currency: nullable(string)(o.currency),
    earn_points: nullable(integer)(o.earn_points),
    earn_spend: nullable(decimal)(o.earn_spend),
    min_redeem_points: nullable(integer)(o.min_redeem_points),
    expiry_days: nullable(integer)(o.expiry_days),
    entries: array(o.entries, ledgerEntry),
  };
}

// --- SEO ------------------------------------------------------------------------------------------

export type SeoItemKind = 'category' | 'collection' | 'page';
export interface SeoItem {
  id: string;
  kind: SeoItemKind;
  name: string;
  slug: string;
  public: boolean;
  seo_title: string;
  seo_description: string;
}
export interface SeoSettings {
  home_title: string;
  home_description: string;
  social_image: string | null;
  social_image_url: string | null;
  allow_indexing: boolean;
  can_change_indexing: boolean;
  items: SeoItem[];
  products_missing_seo: number;
  active_products: number;
}
export function seoSettings(value: unknown): SeoSettings {
  const o = object(value);
  return {
    home_title: string(o.home_title),
    home_description: string(o.home_description),
    social_image: nullable(string)(o.social_image),
    social_image_url: nullable(string)(o.social_image_url),
    allow_indexing: boolean(o.allow_indexing),
    can_change_indexing: boolean(o.can_change_indexing),
    items: array(o.items, (entry) => {
      const i = object(entry);
      return {
        id: string(i.id),
        kind: oneOf(['category', 'collection', 'page'] as const)(i.kind),
        name: string(i.name),
        slug: string(i.slug),
        public: boolean(i.public),
        seo_title: string(i.seo_title),
        seo_description: string(i.seo_description),
      };
    }),
    products_missing_seo: integer(o.products_missing_seo),
    active_products: integer(o.active_products),
  };
}

// --- Tracking -------------------------------------------------------------------------------------

export type PurchaseTiming = 'payment_verified' | 'order_placed';
export interface TrackingSettings {
  analytics_enabled: boolean;
  meta_enabled: boolean;
  meta_pixel_id: string;
  meta_capi_enabled: boolean;
  meta_send_hashed_contact: boolean;
  meta_purchase_timing: PurchaseTiming;
  meta_test_event_code: string;
  status: TrackingStatus;
  consent_records_30d: number;
  consent_marketing_30d: number;
  consent_analytics_30d: number;
}
export function trackingSettings(value: unknown): TrackingSettings {
  const o = object(value);
  return {
    analytics_enabled: boolean(o.analytics_enabled),
    meta_enabled: boolean(o.meta_enabled),
    meta_pixel_id: string(o.meta_pixel_id),
    meta_capi_enabled: boolean(o.meta_capi_enabled),
    meta_send_hashed_contact: boolean(o.meta_send_hashed_contact),
    meta_purchase_timing: oneOf(['payment_verified', 'order_placed'] as const)(
      o.meta_purchase_timing,
    ),
    meta_test_event_code: string(o.meta_test_event_code),
    status: trackingStatus(o.status),
    consent_records_30d: integer(o.consent_records_30d),
    consent_marketing_30d: integer(o.consent_marketing_30d),
    consent_analytics_30d: integer(o.consent_analytics_30d),
  };
}

// --- Analytics ------------------------------------------------------------------------------------

export type AnalyticsRange = '24h' | '7d' | '30d' | 'custom';
export interface PromotionResult extends Performance {
  id: string;
  name: string;
}
export interface AnalyticsReport {
  range: AnalyticsRange;
  rolling: boolean;
  start: string;
  /** Traffic and funnel totals start here (per-minute counters for the last 24 hours). */
  counter_start: string;
  /** Series, top products and sources start here (hourly buckets for the last 24 hours). */
  breakdown_start: string;
  end: string;
  first_day: string | null;
  last_day: string | null;
  traffic: {
    page_views: number;
    consented_page_views: number;
    consent_coverage: string | null;
    unique_visitors: number;
    product_views: number;
    category_views: number;
  };
  funnel: {
    product_views: number;
    add_to_cart: number;
    checkout_started: number;
    storefront_orders: number;
    converting_visitors: number;
    conversion_rate: string | null;
  };
  orders: {
    storefront_orders: number;
    storefront_cancelled: number;
    assisted_orders: number;
    order_value: (Amount & { orders: number })[];
    collected: Amount[];
  };
  series: {
    bucket: string;
    page_views: number;
    add_to_cart: number;
    checkout_started: number;
    unique_visitors: number | null;
  }[];
  top_products: { product: string; name: string; views: number; add_to_cart: number }[];
  sources: {
    utm_source: string;
    utm_medium: string;
    utm_campaign: string;
    landings: number;
    orders: number;
    order_value: Amount[];
  }[];
  campaigns: PromotionResult[];
  flash_sales: PromotionResult[];
  coupons: { code: string; currency: string; uses: number; discount: string }[];
  loyalty: {
    earned: number;
    redeemed: number;
    restored: number;
    reversed: number;
    expired: number;
    adjusted: number;
    pending_awards: number;
  };
  active_coupons: number;
}
const promotionResult: Decoder<PromotionResult> = (value) => {
  const o = object(value);
  return { ...performance(value), id: string(o.id), name: string(o.name) };
};
export function analyticsReport(value: unknown): AnalyticsReport {
  const o = object(value);
  const t = object(o.traffic);
  const f = object(o.funnel);
  const ord = object(o.orders);
  const l = object(o.loyalty);
  return {
    range: oneOf(['24h', '7d', '30d', 'custom'] as const)(o.range),
    rolling: boolean(o.rolling),
    start: isoDateTime(o.start),
    counter_start: isoDateTime(o.counter_start),
    breakdown_start: isoDateTime(o.breakdown_start),
    end: isoDateTime(o.end),
    first_day: nullable(isoDate)(o.first_day),
    last_day: nullable(isoDate)(o.last_day),
    traffic: {
      page_views: integer(t.page_views),
      consented_page_views: integer(t.consented_page_views),
      consent_coverage: nullable(decimal)(t.consent_coverage),
      unique_visitors: integer(t.unique_visitors),
      product_views: integer(t.product_views),
      category_views: integer(t.category_views),
    },
    funnel: {
      product_views: integer(f.product_views),
      add_to_cart: integer(f.add_to_cart),
      checkout_started: integer(f.checkout_started),
      storefront_orders: integer(f.storefront_orders),
      converting_visitors: integer(f.converting_visitors),
      conversion_rate: nullable(decimal)(f.conversion_rate),
    },
    orders: {
      storefront_orders: integer(ord.storefront_orders),
      storefront_cancelled: integer(ord.storefront_cancelled),
      assisted_orders: integer(ord.assisted_orders),
      order_value: array(ord.order_value, (entry) => ({
        ...amount(entry),
        orders: integer(object(entry).orders),
      })),
      collected: array(ord.collected, amount),
    },
    series: array(o.series, (entry) => {
      const s = object(entry);
      return {
        bucket: string(s.bucket),
        page_views: integer(s.page_views),
        add_to_cart: integer(s.add_to_cart),
        checkout_started: integer(s.checkout_started),
        unique_visitors: nullable(integer)(s.unique_visitors),
      };
    }),
    top_products: array(o.top_products, (entry) => {
      const p = object(entry);
      return {
        product: string(p.product),
        name: string(p.name),
        views: integer(p.views),
        add_to_cart: integer(p.add_to_cart),
      };
    }),
    sources: array(o.sources, (entry) => {
      const s = object(entry);
      return {
        utm_source: string(s.utm_source),
        utm_medium: string(s.utm_medium),
        utm_campaign: string(s.utm_campaign),
        landings: integer(s.landings),
        orders: integer(s.orders),
        order_value: array(s.order_value, amount),
      };
    }),
    campaigns: array(o.campaigns, promotionResult),
    flash_sales: array(o.flash_sales, promotionResult),
    coupons: array(o.coupons, (entry) => {
      const c = object(entry);
      return {
        code: string(c.code),
        currency: string(c.currency),
        uses: integer(c.uses),
        discount: decimal(c.discount),
      };
    }),
    loyalty: {
      earned: integer(l.earned),
      redeemed: integer(l.redeemed),
      restored: integer(l.restored),
      reversed: integer(l.reversed),
      expired: integer(l.expired),
      adjusted: integer(l.adjusted),
      pending_awards: integer(l.pending_awards),
    },
    active_coupons: integer(o.active_coupons),
  };
}

// --- Storefront: consent and sitemap --------------------------------------------------------------

export interface ConsentDecision {
  analytics: boolean;
  marketing: boolean;
  policy_version: string;
}
export function consentDecision(value: unknown): ConsentDecision {
  const o = object(value);
  return {
    analytics: boolean(o.analytics),
    marketing: boolean(o.marketing),
    policy_version: string(o.policy_version),
  };
}

const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,138}[a-z0-9])?$/;
export interface SitemapFeed {
  count: number;
  page_size: number;
  entries: {
    type: 'product' | 'category' | 'collection' | 'page';
    slug: string;
    updated_at: string;
  }[];
}
export function sitemapFeed(value: unknown): SitemapFeed {
  const o = object(value);
  return {
    count: integer(o.count),
    page_size: integer(o.page_size),
    entries: array(o.entries, (entry) => {
      const e = object(entry);
      const slug = string(e.slug);
      // Only path-safe slugs ever become sitemap URLs.
      if (!SLUG.test(slug)) throw new Error('Invalid response');
      return {
        type: oneOf(['product', 'category', 'collection', 'page'] as const)(e.type),
        slug,
        updated_at: isoDateTime(e.updated_at),
      };
    }),
  };
}
