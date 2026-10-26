import { PRODUCT_TYPES, type ProductType } from '@matecart/shared-contracts';

import { toUnits } from './money';

/**
 * Product listing state lives in the URL (`/shop?sort=price_low&min_price=500&page=2`), so
 * results can be shared, bookmarked and navigated with Back and Forward. Every value is checked
 * here; anything invalid is dropped rather than sent to the API. Filtering, sorting and paging
 * always happen in the API over the whole catalogue, never over the current page.
 */

export const SORTS = ['newest', 'price_low', 'price_high', 'name', 'featured'] as const;
export type Sort = (typeof SORTS)[number];

export interface ListingQuery {
  q: string;
  sort: Sort | null;
  minPrice: string | null;
  maxPrice: string | null;
  brand: string | null;
  type: ProductType | null;
  page: number;
}

export type SearchParams = Record<string, string | string[] | undefined>;

const PRICE = /^\d{1,13}(\.\d{1,2})?$/;
const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,138}[a-z0-9])?$/;
const MAX_PAGE = 1000;

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value)?.trim() ?? '';
}

export function parseListingQuery(params: SearchParams): ListingQuery {
  const sort = first(params.sort);
  const type = first(params.type);
  const brand = first(params.brand);
  let minPrice: string | null = PRICE.test(first(params.min_price))
    ? first(params.min_price)
    : null;
  let maxPrice: string | null = PRICE.test(first(params.max_price))
    ? first(params.max_price)
    : null;
  if (minPrice && maxPrice && (toUnits(minPrice) ?? 0n) > (toUnits(maxPrice) ?? 0n)) {
    [minPrice, maxPrice] = [maxPrice, minPrice];
  }
  const page = Number.parseInt(first(params.page), 10);
  return {
    q: first(params.q).slice(0, 100),
    sort: (SORTS as readonly string[]).includes(sort) ? (sort as Sort) : null,
    minPrice,
    maxPrice,
    brand: SLUG.test(brand) ? brand : null,
    type: (PRODUCT_TYPES as readonly string[]).includes(type) ? (type as ProductType) : null,
    page: Number.isFinite(page) && page >= 1 ? Math.min(page, MAX_PAGE) : 1,
  };
}

/** The API request for a listing; `scope` fixes the category or collection of the page. */
export function apiQuery(
  query: ListingQuery,
  scope: { category?: string; collection?: string },
  pageSize: number,
): URLSearchParams {
  const params = new URLSearchParams();
  if (scope.category) params.set('category', scope.category);
  if (scope.collection) params.set('collection', scope.collection);
  if (query.q) params.set('q', query.q);
  if (query.brand) params.set('brand', query.brand);
  if (query.type) params.set('product_type', query.type);
  if (query.minPrice) params.set('min_price', query.minPrice);
  if (query.maxPrice) params.set('max_price', query.maxPrice);
  const sort = query.sort ?? (scope.collection ? 'featured' : 'newest');
  // "Featured" is the merchant's collection order; elsewhere it falls back to newest.
  params.set('ordering', sort === 'featured' && !scope.collection ? 'newest' : sort);
  params.set('page', String(query.page));
  params.set('page_size', String(pageSize));
  return params;
}

/** A listing URL with `changes` applied. Changing anything but the page returns to page 1. */
export function listingHref(
  pathname: string,
  query: ListingQuery,
  changes: Partial<ListingQuery> = {},
): string {
  const next = { ...query, ...changes };
  if (!('page' in changes)) next.page = 1;
  const params = new URLSearchParams();
  if (next.q) params.set('q', next.q);
  if (next.brand) params.set('brand', next.brand);
  if (next.type) params.set('type', next.type);
  if (next.minPrice) params.set('min_price', next.minPrice);
  if (next.maxPrice) params.set('max_price', next.maxPrice);
  if (next.sort) params.set('sort', next.sort);
  if (next.page > 1) params.set('page', String(next.page));
  const text = params.toString();
  return text ? `${pathname}?${text}` : pathname;
}

/** Page numbers to show around `current`, with null for a gap: 1 … 4 5 6 … 12. */
export function pageWindow(current: number, total: number): (number | null)[] {
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const sorted = [...pages].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
  const result: (number | null)[] = [];
  for (const n of sorted) {
    const previous = result[result.length - 1];
    if (typeof previous === 'number' && n - previous > 1) result.push(null);
    result.push(n);
  }
  return result;
}
