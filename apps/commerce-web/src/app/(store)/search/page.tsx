import type { Metadata } from 'next';
import Form from 'next/form';

import { CatalogListing } from '@/components/store/catalog-listing';
import { PAGE_SIZE } from '@/components/store/product-card';
import { Breadcrumbs, CONTAINER, FOCUS, SearchIcon } from '@/components/store/ui';
import { apiQuery, parseListingQuery, type SearchParams } from '@/lib/catalog-query';
import { categoryLinks } from '@/lib/categories';
import type { StoreContext } from '@/lib/store-context';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree, getProducts, StorefrontUnavailableError } from '@/lib/storefront-api';

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getStoreContext();
  // Result pages are endless combinations of words: keep them out of search engines.
  return { title: t.searchTitle, robots: { index: false, follow: true } };
}

function SearchBox({ ctx, value }: { ctx: StoreContext; value: string }) {
  const placeholder =
    ctx.theme.preset.id === 'books' ? ctx.t.searchBooksPlaceholder : ctx.t.searchPlaceholder;
  return (
    <Form
      action="/search"
      role="search"
      className="flex h-[50px] items-center gap-2.5 rounded-base border-[1.5px] border-store-ink bg-store-surface px-3.5"
    >
      <SearchIcon width={18} height={18} className="flex-none text-store-muted" />
      <label htmlFor="search-page-q" className="sr-only">
        {ctx.t.searchPlaceholder}
      </label>
      <input
        id="search-page-q"
        name="q"
        type="search"
        defaultValue={value}
        placeholder={placeholder}
        maxLength={100}
        autoComplete="off"
        // A visitor who opens the search page has come to type.
        autoFocus={!value}
        className="min-w-0 flex-1 bg-transparent text-[15px] outline-none"
      />
      <button
        type="submit"
        className={`h-9 cursor-pointer rounded-base bg-store-action px-4 text-[13.5px] font-semibold text-store-on-action hover:bg-store-action-hover ${FOCUS}`}
      >
        {ctx.t.search}
      </button>
    </Form>
  );
}

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const [ctx, tree, params] = await Promise.all([
    getStoreContext(),
    getCategoryTree(),
    searchParams,
  ]);
  const query = parseListingQuery(params);
  const { t } = ctx;
  const crumbs = [{ label: t.home, href: '/' as const }, { label: t.searchTitle }];

  if (!query.q) {
    return (
      <div className={`${CONTAINER} flex flex-col gap-5 pt-6 pb-16`}>
        <Breadcrumbs items={crumbs} label={t.home} />
        <h1 className="m-0 store-display text-[30px] tracking-[-0.02em] sm:text-4xl">
          {t.searchTitle}
        </h1>
        <p className="m-0 text-sm text-store-muted">{t.searchPrompt}</p>
        <SearchBox ctx={ctx} value="" />
      </div>
    );
  }

  const results = await getProducts(apiQuery(query, {}, PAGE_SIZE[ctx.theme.presentation.card]));
  if (!results) throw new StorefrontUnavailableError('/api/catalog/products/', 'HTTP 404');
  return (
    <CatalogListing
      ctx={ctx}
      pathname="/search"
      query={query}
      results={results}
      title={t.resultsFor(query.q)}
      crumbs={crumbs}
      categoryLinks={categoryLinks(tree, null, ctx.lang)}
      // Feature availability follows the business preset, never the theme (D-069).
      typeFilter={ctx.theme.preset.id === 'general'}
      before={<SearchBox ctx={ctx} value={query.q} />}
    />
  );
}
