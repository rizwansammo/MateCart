import { connection } from 'next/server';

import { parseSegment, urlSet, xmlResponse } from '@/lib/sitemap';
import { publicOrigin, storeIndexable } from '@/lib/store-context';
import { getSitemapFeed, getStorefrontConfig } from '@/lib/storefront-api';

export const dynamic = 'force-dynamic';

/** One sitemap segment: `pages.xml` or `products-<n>.xml`, from published records only. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ name: string }> },
): Promise<Response> {
  await connection();
  const segment = parseSegment((await params).name);
  const origin = publicOrigin();
  if (!segment || !origin) return new Response('Not found', { status: 404 });
  if (!storeIndexable(await getStorefrontConfig()))
    return new Response('Not found', { status: 404 });
  const feed = await getSitemapFeed(segment.section, segment.page);
  if (!feed || (segment.section === 'products' && feed.entries.length === 0))
    return new Response('Not found', { status: 404 });
  return xmlResponse(urlSet(origin, feed, segment.section === 'pages'));
}
