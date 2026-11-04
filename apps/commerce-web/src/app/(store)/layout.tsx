import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { StoreShell } from '@/components/store/store-shell';
import { localize } from '@/lib/i18n';
import { getStoreContext, publicOrigin, storeIndexable } from '@/lib/store-context';
import { getCategoryTree } from '@/lib/storefront-api';

// Prices, stock, publication and the customer's language change per request: always render fresh.
export const dynamic = 'force-dynamic';

export async function generateMetadata(): Promise<Metadata> {
  const { config, lang } = await getStoreContext();
  const name = config.store.name;
  // Store-wide fallback description; pages set their own (D-085).
  const description =
    localize(config.translations, 'description', config.store.description, lang) ||
    localize(config.translations, 'tagline', config.store.tagline, lang) ||
    undefined;
  // Indexing needs the deployment opt-in, a trusted public HTTPS origin and the merchant's
  // preference; development and preview stores stay noindex.
  const indexable = storeIndexable(config);
  const origin = publicOrigin();
  return {
    // Only the trusted origin, never the request Host, makes URLs absolute.
    metadataBase: origin,
    title: { default: name, template: `%s · ${name}` },
    description,
    applicationName: name,
    icons: config.theme.favicon_url ? { icon: config.theme.favicon_url } : undefined,
    openGraph: {
      siteName: name,
      type: 'website',
      locale: lang === 'bn' ? 'bn_BD' : 'en_US',
      description,
    },
    robots: { index: indexable, follow: indexable },
  };
}

export default async function StoreLayout({ children }: { children: ReactNode }) {
  const [ctx, categories] = await Promise.all([getStoreContext(), getCategoryTree()]);
  return (
    <StoreShell ctx={ctx} categories={categories}>
      {children}
    </StoreShell>
  );
}
