import { describe, expect, it } from 'vitest';

import { productWith, variant } from '@/test/fixtures';

import { cleanUtm, parseConsent } from './consent';
import { isManagePath, marketingTabs, navigation, requiredPermission } from './manage-routes';
import { purchaseReportable } from './meta-pixel';
import {
  breadcrumbJsonLd,
  isIndexable,
  jsonLd,
  productJsonLd,
  seoText,
  trustedOrigin,
} from './seo';
import { parseSegment, segmentNames, sitemapIndex, urlSet } from './sitemap';
import { fromZonedInput, toZonedInput } from './zoned-time';

describe('canonical origin and indexing (D-085)', () => {
  it.each([
    undefined,
    '',
    'http://shop.example.com',
    'https://localhost:3400',
    'https://127.0.0.1',
    'https://store.localhost',
    'https://dev.test',
    'not a url',
    'https://user:pass@shop.example.org',
  ])('rejects %j as a public origin', (value) => {
    expect(trustedOrigin(value)).toBeNull();
  });
  it('accepts a public HTTPS origin and drops any path', () => {
    expect(trustedOrigin('https://shop.example.com/some/path')?.toString()).toBe(
      'https://shop.example.com/',
    );
  });
  it('needs the deployment, a trusted origin and the merchant to agree', () => {
    const origin = trustedOrigin('https://shop.example.com');
    expect(isIndexable({ deploymentAllows: true, origin, merchantAllows: true })).toBe(true);
    expect(isIndexable({ deploymentAllows: false, origin, merchantAllows: true })).toBe(false);
    expect(isIndexable({ deploymentAllows: true, origin: null, merchantAllows: true })).toBe(false);
    expect(isIndexable({ deploymentAllows: true, origin, merchantAllows: false })).toBe(false);
  });
  it('uses the SEO override in the default language and translations elsewhere', () => {
    expect(seoText('Custom', 'Name', 'en', 'en')).toBe('Custom');
    expect(seoText('Custom', 'নাম', 'bn', 'en')).toBe('নাম');
    expect(seoText('Custom', '', 'bn', 'en')).toBe('Custom');
    expect(seoText('', 'Name', 'en', 'en')).toBe('Name');
  });
});

describe('structured data', () => {
  it('escapes merchant text so it can never close the script element', () => {
    const text = jsonLd({ name: '</script><script>alert(1)</script>', note: 'a & b > c' });
    expect(text).not.toContain('<');
    expect(text).not.toContain('>');
    expect(JSON.parse(text)).toEqual({
      name: '</script><script>alert(1)</script>',
      note: 'a & b > c',
    });
  });
  it('describes only genuine offers and no invented rating', () => {
    const promotion = {
      kind: 'flash_sale' as const,
      name: 'Eid',
      badge: 'Flash sale',
      ends_at: '2026-11-10T18:00:00Z',
    };
    const product = productWith([
      variant(
        'v1',
        {},
        {
          sku: 'SKU-1',
          price: {
            currency: 'BDT',
            amount: '900.0000',
            compare_at_amount: '1000.0000',
            promotion,
          },
          in_stock: false,
        },
      ),
    ]);
    const data = productJsonLd(product, {
      name: 'Shirt',
      description: '',
      url: 'https://shop.example.com/products/shirt',
      origin: trustedOrigin('https://shop.example.com'),
      today: '2026-11-04',
      rating: null,
    });
    expect(data.offers).toMatchObject({
      price: '900',
      priceCurrency: 'BDT',
      availability: 'https://schema.org/OutOfStock',
      priceValidUntil: '2026-11-10',
    });
    expect(data).not.toHaveProperty('aggregateRating');
    expect(data).not.toHaveProperty('brand');
    const rated = productJsonLd(product, {
      name: 'Shirt',
      description: '',
      url: null,
      origin: null,
      today: '2026-11-04',
      rating: { average: 4.25, count: 3 },
    });
    expect(rated.aggregateRating).toMatchObject({ ratingValue: 4.3, reviewCount: 3 });
    expect(rated).not.toHaveProperty('url');
  });
  it('builds breadcrumbs only with a trusted origin', () => {
    const items = [
      { name: 'Home', path: '/' },
      { name: 'Shirts', path: '/categories/shirts' },
    ];
    expect(breadcrumbJsonLd(items, null)).toBeNull();
    expect(breadcrumbJsonLd(items, trustedOrigin('https://shop.example.com'))).toMatchObject({
      '@type': 'BreadcrumbList',
    });
  });
});

describe('sitemaps', () => {
  const origin = new URL('https://shop.example.com');
  it('segments products and refuses unknown segment names', () => {
    expect(segmentNames({ count: 12001, page_size: 5000 })).toEqual([
      'pages',
      'products-1',
      'products-2',
      'products-3',
    ]);
    expect(parseSegment('pages.xml')).toEqual({ section: 'pages', page: 1 });
    expect(parseSegment('products-2.xml')).toEqual({ section: 'products', page: 2 });
    for (const bad of ['products-0', 'products-101', '../etc', 'accounts']) {
      expect(parseSegment(bad)).toBeNull();
    }
  });
  it('lists canonical public URLs only', () => {
    const xml = urlSet(
      origin,
      {
        count: 1,
        page_size: 5000,
        entries: [
          { type: 'category', slug: 'shirts', updated_at: '2026-11-01T00:00:00Z' },
          { type: 'page', slug: 'returns', updated_at: '2026-11-02T00:00:00Z' },
        ],
      },
      true,
    );
    expect(xml).toContain('<loc>https://shop.example.com/</loc>');
    expect(xml).toContain('<loc>https://shop.example.com/categories/shirts</loc>');
    expect(xml).toContain('<lastmod>2026-11-02T00:00:00.000Z</lastmod>');
    expect(xml).not.toMatch(/manage|account|checkout|cart/);
    expect(sitemapIndex(origin, ['pages'])).toContain(
      '<loc>https://shop.example.com/sitemaps/pages.xml</loc>',
    );
  });
});

describe('marketing routes', () => {
  it('maps each marketing page to the permission its API needs', () => {
    expect(requiredPermission('marketing')).toBeNull();
    expect(requiredPermission('marketing/campaigns')).toBe('promotions.manage');
    expect(requiredPermission('marketing/flash-sales/new')).toBe('promotions.manage');
    expect(requiredPermission('marketing/seo')).toBe('content.manage');
    expect(requiredPermission('marketing/analytics')).toBe('reports.view');
    expect(requiredPermission('marketing/tracking')).toBe('store.settings');
    expect(isManagePath('marketing/campaigns/0b0f6d3e-1f6c-4a8e-9c3d-2a1b0c9d8e7f')).toBe(true);
    expect(isManagePath('marketing/unknown')).toBe(false);
  });
  it('shows a content editor only the SEO tab and order handlers no marketing link', () => {
    expect(marketingTabs(['content.manage', 'catalog.manage']).map((tab) => tab.label)).toEqual([
      'Overview',
      'SEO',
    ]);
    const labels = (permissions: string[]) =>
      navigation(permissions).flatMap((group) => group.links.map((link) => link.label));
    expect(labels(['orders.manage'])).not.toContain('Marketing');
    expect(labels(['reports.view'])).toContain('Marketing');
  });
});

describe('consent and tracking helpers', () => {
  it('reads only well-formed consent cookies', () => {
    expect(parseConsent(`1:10:${'a'.repeat(32)}`)).toEqual({ analytics: true, marketing: false });
    expect(parseConsent('1:11:short')).toBeNull();
    expect(parseConsent(null)).toBeNull();
  });
  it('normalises campaign tags like the server', () => {
    expect(cleanUtm('Facebook Ads!')).toBe('facebookads');
    expect(cleanUtm('-x')).toBe('');
    expect(cleanUtm('a'.repeat(80))).toHaveLength(64);
  });
  it('never reports an unpaid cash-on-delivery order as a purchase in verified mode', () => {
    const report = purchaseReportable;
    expect(report({ status: 'placed', payment_status: 'unpaid' }, 'payment_verified')).toBe(false);
    expect(report({ status: 'confirmed', payment_status: 'paid' }, 'payment_verified')).toBe(true);
    expect(report({ status: 'placed', payment_status: 'unpaid' }, 'order_placed')).toBe(true);
    expect(report({ status: 'pending_payment', payment_status: 'unpaid' }, 'order_placed')).toBe(
      false,
    );
    expect(report({ status: 'cancelled', payment_status: 'unpaid' }, 'order_placed')).toBe(false);
  });
  it('converts store wall time to instants and back', () => {
    const iso = fromZonedInput('2026-11-05T18:00', 'Asia/Dhaka');
    expect(iso).toBe('2026-11-05T12:00:00.000Z');
    expect(toZonedInput(iso, 'Asia/Dhaka')).toBe('2026-11-05T18:00');
    expect(fromZonedInput('tomorrow', 'Asia/Dhaka')).toBeNull();
  });
});
