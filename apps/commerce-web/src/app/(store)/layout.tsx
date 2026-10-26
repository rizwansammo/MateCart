import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { StoreShell } from '@/components/store/store-shell';
import { localize } from '@/lib/i18n';
import { getStoreContext, indexingAllowed, publicOrigin } from '@/lib/store-context';
import { getCategoryTree } from '@/lib/storefront-api';

// Prices, stock, publication and the customer's language change per request: always render fresh.
export const dynamic = 'force-dynamic';

export async function generateMetadata(): Promise<Metadata> {
  const { config, lang } = await getStoreContext();
  const name = config.store.name;
  const description =
    localize(config.translations, 'description', config.store.description, lang) ||
    localize(config.translations, 'tagline', config.store.tagline, lang) ||
    undefined;
  const indexable = indexingAllowed();
  return {
    metadataBase: publicOrigin(),
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
