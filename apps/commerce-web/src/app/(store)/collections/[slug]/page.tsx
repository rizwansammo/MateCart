import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { CatalogListing } from '@/components/store/catalog-listing';
import { PAGE_SIZE } from '@/components/store/product-card';
import { apiQuery, parseListingQuery, type SearchParams } from '@/lib/catalog-query';
import { categoryLinks } from '@/lib/categories';
import { localize } from '@/lib/i18n';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree, getCollection, getProducts } from '@/lib/storefront-api';

interface Props {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const [{ slug }, { lang }] = await Promise.all([params, getStoreContext()]);
  const collection = await getCollection(slug);
  if (!collection) return {};
  return {
    title: localize(collection.translations, 'name', collection.name, lang),
    description:
      localize(collection.translations, 'description', collection.description, lang) || undefined,
  };
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
    getProducts(apiQuery(query, { collection: slug }, PAGE_SIZE[ctx.theme.preset.id])),
  ]);
  if (!collection || !results) notFound();

  const { t, lang } = ctx;
  const name = localize(collection.translations, 'name', collection.name, lang);
  return (
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
  );
}
