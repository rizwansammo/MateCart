import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { CatalogListing } from '@/components/store/catalog-listing';
import { PAGE_SIZE } from '@/components/store/product-card';
import { apiQuery, parseListingQuery, type SearchParams } from '@/lib/catalog-query';
import { categoryLinks } from '@/lib/categories';
import { localize } from '@/lib/i18n';
import { TrackView } from '@/components/store/tracking';
import { pageMetadata } from '@/lib/page-metadata';
import { clip, seoText } from '@/lib/seo';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree, getCollection, getProducts } from '@/lib/storefront-api';

interface Props {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const [{ slug }, { lang, config }] = await Promise.all([params, getStoreContext()]);
  const collection = await getCollection(slug);
  if (!collection) return {};
  const defaultLanguage = config.store.default_language;
  return pageMetadata({
    title: seoText(
      collection.seo_title,
      localize(collection.translations, 'name', collection.name, lang),
      lang,
      defaultLanguage,
    ),
    description: seoText(
      collection.seo_description,
      clip(localize(collection.translations, 'description', collection.description, lang)),
      lang,
      defaultLanguage,
    ),
    path: `/collections/${collection.slug}`,
    image: collection.image_url ? { url: collection.image_url, alt: collection.name } : null,
  });
}

export default async function CollectionPage({ params, searchParams }: Props) {
  const [{ slug }, search, ctx, tree] = await Promise.all([
    params,
    searchParams,
    getStoreContext(),
    getCategoryTree(),
  ]);
  const query = parseListingQuery(search);
  const [collection, results] = await Promise.all([
    getCollection(slug),
    getProducts(apiQuery(query, { collection: slug }, PAGE_SIZE[ctx.theme.presentation.card])),
  ]);
  if (!collection || !results) notFound();

  const { t, lang } = ctx;
  const name = localize(collection.translations, 'name', collection.name, lang);
  return (
    <>
      <TrackView
        event="collection_view"
        objectId={collection.id}
        analytics={ctx.config.tracking.analytics}
      />
      <CatalogListing
        ctx={ctx}
        pathname={`/collections/${slug}`}
        query={query}
        results={results}
        title={name}
        intro={localize(collection.translations, 'description', collection.description, lang)}
        crumbs={[{ label: t.home, href: '/' }, { label: name }]}
        categoryLinks={categoryLinks(tree, null, lang)}
        featured
        typeFilter={ctx.theme.preset.id === 'general'}
      />
    </>
  );
}
