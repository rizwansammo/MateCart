import type { PresentationVariant } from '@matecart/shared-ui';
import type { Metadata } from 'next';

import { JsonLd } from '@/components/store/json-ld';
import { localize } from '@/lib/i18n';
import { pageMetadata } from '@/lib/page-metadata';
import { absolute } from '@/lib/seo';
import { publicOrigin } from '@/lib/store-context';

import { hasRenderableSection, HomeFallback, HomeSections } from '@/components/store/home-sections';
import { apiQuery, parseListingQuery } from '@/lib/catalog-query';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree, getHomeSections, getProducts } from '@/lib/storefront-api';

/** Products shown by a collection highlight: one or two full rows of the card variant's grid. */
const HIGHLIGHT_SIZE: Record<PresentationVariant, number> = {
  editorial: 4,
  quickshop: 12,
  spec: 8,
  marketplace: 10,
  literary: 5,
};

export async function generateMetadata(): Promise<Metadata> {
  const { config, lang } = await getStoreContext();
  const store = config.store;
  const description =
    (lang === store.default_language ? config.seo.home_description : '') ||
    localize(config.translations, 'description', store.description, lang) ||
    localize(config.translations, 'tagline', store.tagline, lang) ||
    config.seo.home_description;
  const title = (lang === store.default_language ? config.seo.home_title : '') || store.name;
  const image = config.seo.social_image_url ?? config.theme.logo_url;
  return pageMetadata({
    title,
    absoluteTitle: true,
    description,
    path: '/',
    image: image ? { url: image, alt: store.name } : null,
  });
}

function storeJsonLd(name: string, logo: string | null): Record<string, unknown> | null {
  const origin = publicOrigin();
  const url = absolute(origin, '/');
  if (!url) return null;
  const logoUrl = logo ? absolute(origin, logo) : null;
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'OnlineStore',
        '@id': `${url}#store`,
        name,
        url,
        ...(logoUrl ? { logo: logoUrl } : {}),
      },
      {
        '@type': 'WebSite',
        name,
        url,
        publisher: { '@id': `${url}#store` },
        potentialAction: {
          '@type': 'SearchAction',
          target: `${url}search?q={search_term_string}`,
          'query-input': 'required name=search_term_string',
        },
      },
    ],
  };
}

export default async function StoreHomePage() {
  const [ctx, sections, categories] = await Promise.all([
    getStoreContext(),
    getHomeSections(),
    getCategoryTree(),
  ]);
  const size = HIGHLIGHT_SIZE[ctx.theme.presentation.card];
  const firstPage = parseListingQuery({});

  // All collection highlights are fetched at once, not one after another.
  const collections = await Promise.all(
    sections.map(async (section) => {
      if (section.section_type !== 'collection_highlight' || !section.collection) return null;
      const page = await getProducts(
        apiQuery(firstPage, { collection: section.collection.slug }, size),
      );
      return page?.results ?? null;
    }),
  );
  const data = { sections, categories, collections };

  // No sections, or none with anything to show: open on the store and its newest products.
  // Only this case makes the extra request; normal homepages do not.
  if (!hasRenderableSection(data, ctx.lang)) {
    const newest = await getProducts(apiQuery(firstPage, {}, size));
    return (
      <>
        <JsonLd data={storeJsonLd(ctx.config.store.name, ctx.config.theme.logo_url)} />
        <HomeFallback ctx={ctx} products={newest?.results ?? []} />
      </>
    );
  }
  return (
    <>
      <JsonLd data={storeJsonLd(ctx.config.store.name, ctx.config.theme.logo_url)} />
      <HomeSections ctx={ctx} data={data} />
    </>
  );
}
