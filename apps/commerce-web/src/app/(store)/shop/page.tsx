import type { Metadata } from 'next';

import { CatalogListing } from '@/components/store/catalog-listing';
import { PAGE_SIZE } from '@/components/store/product-card';
import { apiQuery, parseListingQuery, type SearchParams } from '@/lib/catalog-query';
import { categoryLinks } from '@/lib/categories';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree, getProducts, StorefrontUnavailableError } from '@/lib/storefront-api';

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getStoreContext();
  return { title: t.allProducts };
}

export default async function ShopPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const [ctx, tree, params] = await Promise.all([
    getStoreContext(),
    getCategoryTree(),
    searchParams,
  ]);
  const query = parseListingQuery(params);
  const preset = ctx.theme.preset.id;
  const results = await getProducts(apiQuery(query, {}, PAGE_SIZE[preset]));
  if (!results) throw new StorefrontUnavailableError('/api/catalog/products/', 'HTTP 404');
  return (
    <CatalogListing
      ctx={ctx}
      pathname="/shop"
      query={query}
      results={results}
      title={ctx.t.allProducts}
      crumbs={[{ label: ctx.t.home, href: '/' }, { label: ctx.t.allProducts }]}
      categoryLinks={categoryLinks(tree, null, ctx.lang)}
      typeFilter={preset === 'general'}
    />
  );
}
