import { describe, expect, it } from 'vitest';

import {
  analyticsReport,
  campaignDetail,
  campaignPage,
  consentDecision,
  customerLoyalty,
  flashSaleDetail,
  liveSummary,
  loyaltyStats,
  marketingOverview,
  seoSettings,
  sitemapFeed,
  trackingSettings,
} from './marketing';

const live = {
  window_start: '2026-11-03T05:00:00Z',
  exact_start: '2026-11-03T05:12:00Z',
  now: '2026-11-04T05:12:00Z',
  unique_visitors: 12,
  page_views: 140,
  consented_page_views: 70,
  product_views: 40,
  add_to_cart: 9,
  checkout_started: 4,
  storefront_orders: 2,
  converting_visitors: 1,
  conversion_rate: '8.3',
  consent_coverage: '50.0',
};
const status = {
  browser_pixel: 'off',
  server_events: 'missing_credentials',
  token_configured: false,
  public_url_configured: false,
  pending_events: 0,
  failed_events: 0,
  sent_events: 0,
};
const campaign = {
  id: 'c1',
  name: 'Winter',
  description: '',
  kind: 'discount',
  discount_type: 'percent',
  value: '10.0000',
  currency: '',
  badge_text: 'Sale',
  starts_at: '2026-11-01T00:00:00Z',
  ends_at: '2026-11-30T00:00:00Z',
  status: 'active',
  state: 'live',
  target_count: 1,
  performance: {
    orders: 2,
    units: '3.000',
    by_currency: [{ currency: 'BDT', sales: '900.0000', discount: '100.0000' }],
  },
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
};

describe('marketing contracts', () => {
  it('decodes the overview and the rolling 24-hour summary', () => {
    const overview = marketingOverview({
      live_campaigns: 1,
      scheduled_campaigns: 0,
      live_flash_sales: null,
      scheduled_flash_sales: null,
      active_coupons: 2,
      loyalty_enabled: false,
      loyalty_members: 0,
      flash_sales_module: false,
      loyalty_module: true,
      tracking: status,
      analytics_enabled: true,
      live,
      seo_indexing_preference: null,
    });
    expect(overview.live?.unique_visitors).toBe(12);
    expect(overview.tracking?.server_events).toBe('missing_credentials');
    expect(() => liveSummary({ ...live, page_views: '140' })).toThrow();
    expect(() => liveSummary({ ...live, unique_visitors: 1.5 })).toThrow();
  });

  it('decodes campaigns with preview and performance and refuses unknown states', () => {
    const page = campaignPage({ count: 1, page: 1, page_size: 25, results: [campaign] });
    expect(page.results[0]?.performance?.orders).toBe(2);
    const detail = campaignDetail({
      ...campaign,
      targets: [{ id: 'p1', name: 'Shirt', kind: 'product' }],
      preview: {
        products: 1,
        variants: 1,
        rows: [
          {
            variant: 'v1',
            sku: 'S-1',
            product_name: 'Shirt',
            product_status: 'active',
            currency: 'BDT',
            regular_amount: '1000.0000',
            promotional_amount: '900.0000',
            applies: true,
          },
        ],
      },
    });
    expect(detail.preview.rows[0]?.promotional_amount).toBe('900.0000');
    expect(() =>
      campaignPage({
        count: 1,
        page: 1,
        page_size: 25,
        results: [{ ...campaign, state: 'forever' }],
      }),
    ).toThrow();
  });

  it('decodes flash sale items with money as strings', () => {
    const detail = flashSaleDetail({
      ...campaign,
      item_count: 1,
      items: [
        {
          id: 'i1',
          variant: 'v1',
          sku: 'S-1',
          product_name: 'Shirt',
          variant_title: 'M',
          sale_price: '499.0000',
          currency: 'BDT',
          regular_amount: '999.0000',
          quantity_limit: 50,
          per_order_limit: 2,
          units_sold: '3.000',
        },
      ],
      module_enabled: true,
    });
    expect(detail.items[0]?.sale_price).toBe('499.0000');
    expect(() =>
      flashSaleDetail({
        ...campaign,
        item_count: 0,
        items: [{ sale_price: 499 }],
        module_enabled: true,
      }),
    ).toThrow();
  });

  it('decodes loyalty for merchants and customers', () => {
    const stats = loyaltyStats({
      program: {
        is_enabled: true,
        currency: 'BDT',
        earn_points: 1,
        earn_spend: '100.0000',
        point_value: '1.0000',
        min_redeem_points: 100,
        max_redeem_percent: 50,
        expiry_days: null,
        updated_at: '2026-11-01T00:00:00Z',
      },
      module_enabled: true,
      members: 3,
      outstanding_points: 500,
      outstanding_deficit: 0,
      pending_awards: 1,
    });
    expect(stats.program.max_redeem_percent).toBe(50);
    const mine = customerLoyalty({
      enabled: true,
      balance: 120,
      deficit: 0,
      pending: 30,
      lifetime_earned: 150,
      lifetime_redeemed: 30,
      point_value: '1.0000',
      currency: 'BDT',
      earn_points: 1,
      earn_spend: '100.0000',
      min_redeem_points: 100,
      expiry_days: 365,
      entries: [
        {
          id: 'e1',
          kind: 'earned',
          points: 150,
          deficit_change: 0,
          balance_after: 150,
          deficit_after: 0,
          order_number: 'ORD-000001',
          note: '',
          occurred_at: '2026-11-02T00:00:00Z',
        },
      ],
    });
    expect(mine.entries[0]?.kind).toBe('earned');
    expect(() =>
      customerLoyalty({ ...mine, entries: [{ ...mine.entries[0], kind: 'gifted' }] }),
    ).toThrow();
  });

  it('decodes SEO, tracking, consent and the sitemap feed', () => {
    const seo = seoSettings({
      home_title: 'Shop',
      home_description: '',
      social_image: null,
      social_image_url: null,
      allow_indexing: true,
      can_change_indexing: false,
      items: [
        {
          id: 'c1',
          kind: 'category',
          name: 'Shirts',
          slug: 'shirts',
          public: true,
          seo_title: '',
          seo_description: '',
        },
      ],
      products_missing_seo: 2,
      active_products: 5,
    });
    expect(seo.items[0]?.kind).toBe('category');
    const tracking = trackingSettings({
      analytics_enabled: true,
      meta_enabled: false,
      meta_pixel_id: '',
      meta_capi_enabled: false,
      meta_send_hashed_contact: false,
      meta_purchase_timing: 'payment_verified',
      meta_test_event_code: '',
      status,
      consent_records_30d: 4,
      consent_marketing_30d: 1,
      consent_analytics_30d: 2,
    });
    expect(tracking.status.browser_pixel).toBe('off');
    expect(
      consentDecision({ analytics: true, marketing: false, policy_version: '1' }).marketing,
    ).toBe(false);
    const feed = sitemapFeed({
      count: 1,
      page_size: 5000,
      entries: [{ type: 'product', slug: 'oxford-shirt', updated_at: '2026-11-01T00:00:00Z' }],
    });
    expect(feed.entries[0]?.slug).toBe('oxford-shirt');
    expect(() =>
      sitemapFeed({
        count: 1,
        page_size: 5000,
        entries: [{ type: 'product', slug: '../admin', updated_at: '2026-11-01T00:00:00Z' }],
      }),
    ).toThrow();
  });

  it('decodes the analytics report and rejects numeric money', () => {
    const report = {
      range: '7d',
      rolling: false,
      start: '2026-10-28T00:00:00+06:00',
      counter_start: '2026-10-28T00:00:00+06:00',
      breakdown_start: '2026-10-28T00:00:00+06:00',
      end: '2026-11-04T05:00:00Z',
      first_day: '2026-10-28',
      last_day: '2026-11-03',
      traffic: {
        page_views: 10,
        consented_page_views: 5,
        consent_coverage: '50.0',
        unique_visitors: 3,
        product_views: 4,
        category_views: 1,
      },
      funnel: {
        product_views: 4,
        add_to_cart: 2,
        checkout_started: 1,
        storefront_orders: 1,
        converting_visitors: 1,
        conversion_rate: '33.3',
      },
      orders: {
        storefront_orders: 1,
        storefront_cancelled: 0,
        assisted_orders: 2,
        order_value: [{ currency: 'BDT', amount: '500.0000', orders: 1 }],
        collected: [],
      },
      series: [
        {
          bucket: '2026-10-28',
          page_views: 1,
          add_to_cart: 0,
          checkout_started: 0,
          unique_visitors: 1,
        },
      ],
      top_products: [{ product: 'p1', name: 'Shirt', views: 4, add_to_cart: 2 }],
      sources: [
        {
          utm_source: 'facebook',
          utm_medium: 'cpc',
          utm_campaign: 'eid',
          landings: 3,
          orders: 1,
          order_value: [{ currency: 'BDT', amount: '500.0000' }],
        },
      ],
      campaigns: [],
      flash_sales: [],
      coupons: [{ code: 'EID', currency: 'BDT', uses: 1, discount: '50.0000' }],
      loyalty: {
        earned: 0,
        redeemed: 0,
        restored: 0,
        reversed: 0,
        expired: 0,
        adjusted: 0,
        pending_awards: 0,
      },
      active_coupons: 1,
    };
    expect(analyticsReport(report).sources[0]?.utm_source).toBe('facebook');
    expect(() =>
      analyticsReport({
        ...report,
        orders: { ...report.orders, order_value: [{ currency: 'BDT', amount: 500, orders: 1 }] },
      }),
    ).toThrow();
  });
});
