import type { MetadataRoute } from 'next';
import { connection } from 'next/server';

import { PRIVATE_PATHS, absolute } from '@/lib/seo';
import { publicOrigin, storeIndexable } from '@/lib/store-context';
import { getStorefrontConfig } from '@/lib/storefront-api';

/**
 * robots.txt, decided per request (D-085): an indexable store allows its public pages and lists
 * its sitemap; every other deployment (development, preview, missing public URL, merchant opted
 * out, API unreachable) disallows everything.
 */
export default async function robots(): Promise<MetadataRoute.Robots> {
  await connection();
  const origin = publicOrigin();
  let indexable = false;
  try {
    indexable = origin !== null && storeIndexable(await getStorefrontConfig());
  } catch {
    indexable = false;
  }
  const sitemap = absolute(origin, '/sitemap.xml');
  if (!indexable || !sitemap) return { rules: { userAgent: '*', disallow: '/' } };
  return {
    rules: { userAgent: '*', allow: '/', disallow: PRIVATE_PATHS },
    sitemap,
  };
}
