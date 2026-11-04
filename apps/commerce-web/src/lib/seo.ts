/**
 * Search metadata helpers (Phase 12, D-085). Pure functions, shared by server metadata,
 * JSON-LD, robots and sitemap code and unit-tested on their own.
 *
 * - The canonical origin comes only from deployment configuration (`STOREFRONT_PUBLIC_URL`),
 *   never from the request's Host header, and must be public HTTPS. Without it nothing is
 *   indexable and no absolute URL is published.
 * - Language is chosen per visitor by cookie on one URL, so there are no per-language URLs to
 *   advertise: pages carry one canonical URL and no hreflang alternates (search engines see
 *   the store's default language).
 * - Merchant text is plain text. JSON-LD escapes `<` (and line separators) so no value can
 *   close the script element.
 */
import type { PublicProductDetail, PublicVariant } from '@matecart/shared-contracts';

const LOCAL_HOST =
  /^(localhost|.*\.localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1|.*\.local|.*\.test|.*\.invalid|.*\.example)$/i;

/** The trusted public origin, or null when missing, not HTTPS or not a public host. */
export function trustedOrigin(raw: string | undefined | null): URL | null {
  const text = raw?.trim();
  if (!text) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || LOCAL_HOST.test(url.hostname) || url.username || url.password)
    return null;
  return new URL(url.origin);
}

/** All three must agree: the deployment opted in, it has a trusted origin, the merchant allows. */
export function isIndexable(options: {
  deploymentAllows: boolean;
  origin: URL | null;
  merchantAllows: boolean;
}): boolean {
  return options.deploymentAllows && options.origin !== null && options.merchantAllows;
}

/** An absolute URL on the trusted origin, or null without one. */
export function absolute(origin: URL | null, path: string): string | null {
  if (!origin || !path.startsWith('/') || path.startsWith('//')) return null;
  return new URL(path, origin).toString();
}

/**
 * The merchant's search override applies to the store's default language (it is written in
 * it); other languages use their translated text, falling back to the override.
 */
export function seoText(
  override: string,
  localized: string,
  lang: string,
  defaultLanguage: string,
): string {
  if (override && (lang === defaultLanguage || !localized)) return override;
  return localized || override;
}

/** First ``limit`` characters of plain text on a word boundary (for descriptions). */
export function clip(text: string, limit = 160): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  if (flat.length <= limit) return flat;
  const cut = flat.slice(0, limit - 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > limit * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

const LINE_SEPARATOR = new RegExp(String.fromCharCode(0x2028), 'g');
const PARAGRAPH_SEPARATOR = new RegExp(String.fromCharCode(0x2029), 'g');

/** JSON for a `<script type="application/ld+json">` that no value can break out of. */
export function jsonLd(data: unknown): string {
  return JSON.stringify(data)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026')
    .replace(LINE_SEPARATOR, '\\u2028')
    .replace(PARAGRAPH_SEPARATOR, '\\u2029');
}

export function xmlEscape(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

const GTIN = /^(\d{8}|\d{12}|\d{13}|\d{14})$/;
const ISBN13 = /^97[89]\d{10}$/;

function availability(variant: PublicVariant, today: string): string {
  if (variant.release_date && variant.release_date > today && variant.in_stock)
    return 'https://schema.org/PreOrder';
  return variant.in_stock ? 'https://schema.org/InStock' : 'https://schema.org/OutOfStock';
}

function offer(variant: PublicVariant, url: string | null, today: string) {
  const price = variant.price;
  if (!price) return null;
  return {
    '@type': 'Offer',
    sku: variant.sku,
    price: price.amount.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, ''),
    priceCurrency: price.currency,
    availability: availability(variant, today),
    ...(price.promotion && price.promotion.kind !== 'featured'
      ? { priceValidUntil: price.promotion.ends_at.slice(0, 10) }
      : {}),
    ...(url ? { url } : {}),
  };
}

/** Schema.org Product from real catalogue data only; nothing missing is invented. */
export function productJsonLd(
  product: PublicProductDetail,
  options: {
    name: string;
    description: string;
    url: string | null;
    origin: URL | null;
    today: string;
    rating: { average: number; count: number } | null;
  },
): Record<string, unknown> {
  const variants = product.variants.filter((variant) => variant.price);
  const offers = variants
    .map((variant) => offer(variant, options.url, options.today))
    .filter((entry) => entry !== null);
  const first = variants[0];
  const image = product.images
    .map((img) => absolute(options.origin, img.url))
    .filter((url): url is string => url !== null);
  const barcode = first?.isbn && ISBN13.test(first.isbn) ? first.isbn : null;
  return {
    '@context': 'https://schema.org',
    '@type': product.product_type === 'book' ? ['Product', 'Book'] : 'Product',
    name: options.name,
    ...(options.description ? { description: options.description } : {}),
    ...(options.url ? { url: options.url } : {}),
    ...(image.length ? { image } : {}),
    ...(variants.length === 1 && first ? { sku: first.sku } : {}),
    ...(barcode ? { isbn: barcode, gtin13: barcode } : {}),
    ...(product.brand ? { brand: { '@type': 'Brand', name: product.brand.name } } : {}),
    ...(product.book?.authors.length
      ? { author: product.book.authors.map((name) => ({ '@type': 'Person', name })) }
      : {}),
    ...(offers.length === 1
      ? { offers: offers[0] }
      : offers.length > 1
        ? {
            offers: {
              '@type': 'AggregateOffer',
              priceCurrency: offers[0]?.priceCurrency,
              lowPrice: product.price?.min_amount.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, ''),
              highPrice: product.price?.max_amount.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, ''),
              offerCount: offers.length,
              offers,
            },
          }
        : {}),
    ...(options.rating && options.rating.count > 0
      ? {
          aggregateRating: {
            '@type': 'AggregateRating',
            ratingValue: Math.round(options.rating.average * 10) / 10,
            reviewCount: options.rating.count,
            bestRating: 5,
            worstRating: 1,
          },
        }
      : {}),
  };
}

export function isValidGtin(value: string | null | undefined): boolean {
  return !!value && GTIN.test(value);
}

export function breadcrumbJsonLd(
  items: { name: string; path: string }[],
  origin: URL | null,
): Record<string, unknown> | null {
  if (!origin || items.length < 2) return null;
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name: item.name,
      item: absolute(origin, item.path),
    })),
  };
}

/** Pages a crawler must never be sent to. */
export const PRIVATE_PATHS = [
  '/manage',
  '/account',
  '/cart',
  '/checkout',
  '/orders',
  '/api/',
  '/search',
];
