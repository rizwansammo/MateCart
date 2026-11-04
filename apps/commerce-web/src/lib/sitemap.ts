/**
 * Sitemap documents (D-085), built from the API's published-records feed. Pure functions; the
 * route handlers decide whether the store is indexable at all and never publish localhost URLs.
 */
import type { SitemapFeed } from '@matecart/shared-contracts';

import { absolute, xmlEscape } from './seo';

const PATHS: Record<SitemapFeed['entries'][number]['type'], string> = {
  product: '/products/',
  category: '/categories/',
  collection: '/collections/',
  page: '/pages/',
};

/** Segment names: `pages` (home, categories, collections, pages) and `products-<n>`. */
export function segmentNames(feed: Pick<SitemapFeed, 'count' | 'page_size'>): string[] {
  const pages = Math.min(100, Math.ceil(feed.count / Math.max(1, feed.page_size)));
  return ['pages', ...Array.from({ length: pages }, (_, index) => `products-${index + 1}`)];
}

/** `pages` → the pages section; `products-<n>` → that page of products; anything else null. */
export function parseSegment(name: string): { section: 'pages' | 'products'; page: number } | null {
  const file = name.endsWith('.xml') ? name.slice(0, -4) : name;
  if (file === 'pages') return { section: 'pages', page: 1 };
  const match = /^products-([1-9]\d{0,2})$/.exec(file);
  if (!match) return null;
  const page = Number(match[1]);
  return page <= 100 ? { section: 'products', page } : null;
}

export function sitemapIndex(origin: URL, names: string[]): string {
  const items = names
    .map((name) => absolute(origin, `/sitemaps/${name}.xml`))
    .filter((url): url is string => url !== null)
    .map((url) => `  <sitemap><loc>${xmlEscape(url)}</loc></sitemap>`);
  return `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${items.join('\n')}\n</sitemapindex>\n`;
}

export function urlSet(origin: URL, feed: SitemapFeed, includeHome: boolean): string {
  const rows: string[] = [];
  if (includeHome) {
    const home = absolute(origin, '/');
    if (home) rows.push(`  <url><loc>${xmlEscape(home)}</loc></url>`);
  }
  for (const entry of feed.entries) {
    const url = absolute(origin, `${PATHS[entry.type]}${entry.slug}`);
    if (!url) continue;
    const modified = new Date(entry.updated_at);
    const lastmod = Number.isNaN(modified.getTime())
      ? ''
      : `<lastmod>${modified.toISOString()}</lastmod>`;
    rows.push(`  <url><loc>${xmlEscape(url)}</loc>${lastmod}</url>`);
  }
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${rows.join('\n')}\n</urlset>\n`;
}

export function xmlResponse(body: string): Response {
  return new Response(body, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  });
}
