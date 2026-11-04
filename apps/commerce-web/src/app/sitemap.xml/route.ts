import { connection } from 'next/server';

import { segmentNames, sitemapIndex, xmlResponse } from '@/lib/sitemap';
import { publicOrigin, storeIndexable } from '@/lib/store-context';
import { getSitemapFeed, getStorefrontConfig } from '@/lib/storefront-api';

export const dynamic = 'force-dynamic';

/** Sitemap index: only for an indexable store with a trusted public origin (D-085). */
export async function GET(): Promise<Response> {
  await connection();
  const origin = publicOrigin();
  const config = await getStorefrontConfig();
  if (!origin || !storeIndexable(config)) return new Response('Not found', { status: 404 });
  const feed = await getSitemapFeed('pages', 1);
  if (!feed) return new Response('Not found', { status: 404 });
  return xmlResponse(sitemapIndex(origin, segmentNames(feed)));
}
