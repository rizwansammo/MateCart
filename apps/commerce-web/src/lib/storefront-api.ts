import 'server-only';

import {
  array,
  categoryNode,
  page,
  publicCollection,
  publicProductDetail,
  publicProductSummary,
  storefrontConfig,
  storefrontPage,
  storefrontSection,
  STORE_PRESETS,
  type CategoryNode,
  type Decoder,
  type Page,
  type PublicCollection,
  type PublicProductDetail,
  type PublicProductSummary,
  type StorefrontConfig,
  type StorefrontPage,
  type StorefrontSection,
  type StorePreset,
} from '@matecart/shared-contracts';
import { connection } from 'next/server';
import { cache } from 'react';

import { readServerUrl } from './env';

/**
 * Server-side reads of the public storefront and catalogue APIs (Phase 06).
 *
 * Only anonymous public endpoints are called and no browser cookies are forwarded, so a rendered
 * page can never contain one customer's data. Responses are not cached across requests (prices,
 * stock and publication change at any time); `cache()` only de-duplicates calls within one render.
 * Every response is decoded by the shared contracts before it reaches a component.
 */

/** The API could not be reached or answered unexpectedly; the error boundary shows a retry page. */
export class StorefrontUnavailableError extends Error {
  constructor(path: string, detail: string) {
    super(`Storefront data unavailable (${path}): ${detail}`);
    this.name = 'StorefrontUnavailableError';
  }
}

const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,138}[a-z0-9])?$/;

/** Slugs are checked before they are put in an API path; anything else is simply not found. */
export function isSlug(value: string): boolean {
  return SLUG.test(value);
}

async function request<T>(path: string, decode: Decoder<T>): Promise<T | null> {
  // Store data is read per request, never while building: this also keeps Next.js's own
  // control-flow signals out of the network error handling below.
  await connection();
  const base = readServerUrl('COMMERCE_API_INTERNAL_URL');
  if (!base) throw new StorefrontUnavailableError(path, 'COMMERCE_API_INTERNAL_URL is not set');
  let response: Response;
  try {
    response = await fetch(new URL(path, base), {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    throw new StorefrontUnavailableError(path, 'no response');
  }
  if (response.status === 404) return null;
  if (!response.ok) throw new StorefrontUnavailableError(path, `HTTP ${response.status}`);
  try {
    return decode(await response.json());
  } catch {
    throw new StorefrontUnavailableError(path, 'unexpected response');
  }
}

async function required<T>(path: string, decode: Decoder<T>): Promise<T> {
  const result = await request(path, decode);
  if (result === null) throw new StorefrontUnavailableError(path, 'HTTP 404');
  return result;
}

/**
 * Development-only preset preview for local smoke checks of all five presets without changing
 * the store (`STOREFRONT_PREVIEW_PRESET=books pnpm dev:web`). Ignored by production builds.
 */
function previewPreset(): StorePreset | null {
  if (process.env.NODE_ENV !== 'development') return null;
  const value = process.env.STOREFRONT_PREVIEW_PRESET?.trim();
  return (STORE_PRESETS as readonly string[]).includes(value ?? '') ? (value as StorePreset) : null;
}

export const getStorefrontConfig = cache(async (): Promise<StorefrontConfig> => {
  const config = await required('/api/storefront/config/', storefrontConfig);
  const preview = previewPreset();
  return preview ? { ...config, preset: preview } : config;
});

export const getHomeSections = cache((): Promise<StorefrontSection[]> =>
  required('/api/storefront/home/', (value) => array(value, storefrontSection)),
);

export const getCategoryTree = cache((): Promise<CategoryNode[]> =>
  required('/api/catalog/categories/', (value) => array(value, categoryNode)),
);

export function getProducts(query: URLSearchParams): Promise<Page<PublicProductSummary> | null> {
  return request(`/api/catalog/products/?${query.toString()}`, page(publicProductSummary));
}

export const getProduct = cache(async (slug: string): Promise<PublicProductDetail | null> =>
  isSlug(slug) ? request(`/api/catalog/products/${slug}/`, publicProductDetail) : null,
);

export const getCollection = cache(async (slug: string): Promise<PublicCollection | null> =>
  isSlug(slug) ? request(`/api/catalog/collections/${slug}/`, publicCollection) : null,
);

export const getContentPage = cache(async (slug: string): Promise<StorefrontPage | null> =>
  isSlug(slug) ? request(`/api/storefront/pages/${slug}/`, storefrontPage) : null,
);
