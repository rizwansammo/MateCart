import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { storeConfig, summary } from '@/test/fixtures';

import {
  getContentPage,
  getProduct,
  getProducts,
  getStorefrontConfig,
  isSlug,
  StorefrontUnavailableError,
} from './storefront-api';

// Outside a request there is no Next.js request scope; reads are request-time by design.
vi.mock('next/server', () => ({ connection: () => Promise.resolve() }));

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubEnv('COMMERCE_API_INTERNAL_URL', 'http://api.internal:8000');
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

describe('public storefront data', () => {
  it('calls only public endpoints, anonymously and without caching across requests', async () => {
    fetchMock.mockReturnValueOnce(json({ count: 1, page: 1, page_size: 24, results: [summary()] }));
    const page = await getProducts(new URLSearchParams({ q: 'tea', ordering: 'newest' }));
    expect(page?.results[0]?.slug).toBe('gitanjali');
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe(
      'http://api.internal:8000/api/catalog/products/?q=tea&ordering=newest',
    );
    expect(init?.cache).toBe('no-store');
    expect(init?.credentials).toBeUndefined();
    expect(JSON.stringify(init?.headers)).not.toMatch(/cookie|authorization/i);
  });
  it('treats unknown or malformed slugs as not found without leaving the public paths', async () => {
    for (const slug of ['../manage/catalog/products', 'Shirt', 'a/b', '', '-x']) {
      expect(isSlug(slug)).toBe(false);
      expect(await getProduct(slug)).toBeNull();
      expect(await getContentPage(slug)).toBeNull();
    }
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockReturnValueOnce(
      json({ error: { code: 'not_found', message: 'Not found' } }, 404),
    );
    expect(await getProduct('draft-product')).toBeNull();
    expect(String(fetchMock.mock.calls[0]![0])).toBe(
      'http://api.internal:8000/api/catalog/products/draft-product/',
    );
  });
  it('turns API failures and unexpected payloads into an unavailable store', async () => {
    fetchMock.mockReturnValueOnce(json({}, 500));
    await expect(getProducts(new URLSearchParams())).rejects.toBeInstanceOf(
      StorefrontUnavailableError,
    );
    fetchMock.mockReturnValueOnce(json({ count: 'many' }));
    await expect(getProducts(new URLSearchParams())).rejects.toBeInstanceOf(
      StorefrontUnavailableError,
    );
    fetchMock.mockRejectedValueOnce(new TypeError('connect ECONNREFUSED'));
    await expect(getProducts(new URLSearchParams())).rejects.toThrow(/no response/);
  });
  it('rejects responses that would point the page at other hosts', async () => {
    const image = {
      url: 'https://tracker.invalid/p.png',
      alt_text: '',
      width: 1,
      height: 1,
      variant: null,
    };
    fetchMock.mockReturnValueOnce(
      json({ count: 1, page: 1, page_size: 1, results: [summary({ image })] }),
    );
    await expect(getProducts(new URLSearchParams())).rejects.toBeInstanceOf(
      StorefrontUnavailableError,
    );
  });
  it('ignores the development preset preview outside development', async () => {
    vi.stubEnv('STOREFRONT_PREVIEW_PRESET', 'books');
    fetchMock.mockImplementation(() => json(storeConfig()));
    expect((await getStorefrontConfig()).preset).toBe('general');
    vi.stubEnv('NODE_ENV', 'development');
    expect((await getStorefrontConfig()).preset).toBe('books');
    vi.stubEnv('STOREFRONT_PREVIEW_PRESET', 'casino');
    expect((await getStorefrontConfig()).preset).toBe('general');
  });
});
