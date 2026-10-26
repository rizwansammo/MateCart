import type { Page, PublicProductSummary } from '@matecart/shared-contracts';
import { PRODUCT_TYPES } from '@matecart/shared-contracts';
import type { Route } from 'next';
import Form from 'next/form';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { listingHref, pageWindow, type ListingQuery, type Sort } from '@/lib/catalog-query';
import { formatMoney } from '@/lib/money';
import type { StoreContext } from '@/lib/store-context';

import { secondaryLanguage } from './home-sections';
import { MenuDrawer } from './mobile-menu';
import { ProductGrid } from './product-card';
import { SortSelect } from './sort-select';
import { Breadcrumbs, CONTAINER, FOCUS, FilterIcon, type Crumb } from './ui';

/**
 * Product listing for /shop, /search, /categories/<slug> and /collections/<slug>: title and
 * count, filters, sorting, results and pagination. Every control is a link or a GET form over
 * the URL, so it works before JavaScript loads and Back/Forward restores the same results.
 */

export interface CategoryLink {
  label: string;
  href: Route;
  active: boolean;
  depth: number;
}

export interface ListingProps {
  ctx: StoreContext;
  pathname: string;
  query: ListingQuery;
  results: Page<PublicProductSummary>;
  title: string;
  intro?: string;
  crumbs: Crumb[];
  categoryLinks: CategoryLink[];
  /** Collections can use the merchant's order ("Featured"). */
  featured?: boolean;
  /** Product type filter, for stores that sell several kinds of product (General preset). */
  typeFilter?: boolean;
  /** Extra content above the results, such as the search box or subcategories. */
  before?: ReactNode;
}

const INDENT = ['pl-2.5', 'pl-6', 'pl-9'];
const CHIP = `inline-flex h-[30px] items-center gap-1.5 rounded-base px-2.5 text-[12.5px] font-semibold ${FOCUS}`;

function FilterPanel({
  ctx,
  pathname,
  query,
  categoryLinks,
  typeFilter,
  idPrefix,
}: Pick<ListingProps, 'ctx' | 'pathname' | 'query' | 'categoryLinks' | 'typeFilter'> & {
  idPrefix: string;
}) {
  const { t } = ctx;
  const heading = 'm-0 text-xs font-bold tracking-[0.08em] text-store-muted uppercase';
  const currency = ctx.config.store.currency;
  return (
    <div className="flex flex-col gap-7">
      {categoryLinks.length > 0 && (
        <nav aria-label={t.category} className="flex flex-col gap-1.5">
          <p className={heading}>{t.category}</p>
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {categoryLinks.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={link.active ? 'page' : undefined}
                  className={`flex min-h-[34px] items-center rounded-base pr-2.5 text-[13.5px] hover:bg-store-line/50 ${INDENT[Math.min(link.depth, 2)]} ${link.active ? 'bg-store-line/60 font-bold' : ''} ${FOCUS}`}
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}

      <Form action={pathname as Route} className="flex flex-col gap-2.5">
        <p className={heading}>{t.price}</p>
        {query.q && <input type="hidden" name="q" value={query.q} />}
        {query.brand && <input type="hidden" name="brand" value={query.brand} />}
        {query.type && <input type="hidden" name="type" value={query.type} />}
        {query.sort && <input type="hidden" name="sort" value={query.sort} />}
        <div className="grid grid-cols-2 gap-2">
          {(['min_price', 'max_price'] as const).map((name) => (
            <label key={name} className="flex flex-col gap-1 text-xs text-store-muted">
              {name === 'min_price' ? t.minPrice : t.maxPrice}
              <input
                id={`${idPrefix}-${name}`}
                name={name}
                type="number"
                inputMode="decimal"
                min={0}
                step="any"
                defaultValue={(name === 'min_price' ? query.minPrice : query.maxPrice) ?? ''}
                placeholder={currency}
                className="h-10 w-full rounded-base border border-store-line bg-store-surface px-2.5 text-sm text-store-ink"
              />
            </label>
          ))}
        </div>
        <button
          type="submit"
          className={`h-10 cursor-pointer rounded-base bg-store-action text-[13.5px] font-semibold text-store-on-action hover:bg-store-action-hover ${FOCUS}`}
        >
          {t.apply}
        </button>
      </Form>

      {typeFilter && (
        <nav aria-label={t.productType} className="flex flex-col gap-1.5">
          <p className={heading}>{t.productType}</p>
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
            {PRODUCT_TYPES.map((type) => {
              const active = query.type === type;
              return (
                <li key={type}>
                  <Link
                    href={listingHref(pathname, query, { type: active ? null : type }) as Route}
                    aria-current={active ? 'true' : undefined}
                    className={`${CHIP} border ${active ? 'border-store-ink bg-store-ink text-store-canvas' : 'border-store-line bg-store-surface'}`}
                  >
                    {t.productTypes[type] ?? type}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      )}
    </div>
  );
}

function Pagination({
  ctx,
  pathname,
  query,
  total,
}: {
  ctx: StoreContext;
  pathname: string;
  query: ListingQuery;
  total: number;
}) {
  if (total <= 1) return null;
  const { t } = ctx;
  const current = Math.min(query.page, total);
  const cell = `flex h-10 min-w-10 items-center justify-center rounded-base border px-3 text-sm font-semibold ${FOCUS}`;
  const href = (page: number) => listingHref(pathname, query, { page }) as Route;
  return (
    <nav aria-label={t.pagination} className="flex flex-col items-center gap-3 pt-4">
      <ul className="m-0 flex list-none flex-wrap items-center justify-center gap-1.5 p-0">
        <li>
          {current > 1 ? (
            <Link
              href={href(current - 1)}
              rel="prev"
              className={`${cell} border-store-line bg-store-surface`}
            >
              {t.previous}
            </Link>
          ) : (
            <span
              aria-disabled="true"
              className={`${cell} border-store-line text-store-muted opacity-50`}
            >
              {t.previous}
            </span>
          )}
        </li>
        {pageWindow(current, total).map((page, index) =>
          page === null ? (
            <li key={`gap-${index}`} aria-hidden="true" className="px-1 text-store-muted">
              …
            </li>
          ) : (
            <li key={page}>
              <Link
                href={href(page)}
                aria-current={page === current ? 'page' : undefined}
                className={`${cell} ${page === current ? 'border-store-ink bg-store-ink text-store-canvas' : 'border-store-line bg-store-surface'}`}
              >
                {page}
              </Link>
            </li>
          ),
        )}
        <li>
          {current < total ? (
            <Link
              href={href(current + 1)}
              rel="next"
              className={`${cell} border-store-line bg-store-surface`}
            >
              {t.next}
            </Link>
          ) : (
            <span
              aria-disabled="true"
              className={`${cell} border-store-line text-store-muted opacity-50`}
            >
              {t.next}
            </span>
          )}
        </li>
      </ul>
      <p className="m-0 text-xs text-store-muted">{t.pageOf(current, total)}</p>
    </nav>
  );
}

export function CatalogListing(props: ListingProps) {
  const { ctx, pathname, query, results, title, intro, crumbs, featured, before } = props;
  const { t } = ctx;
  const look = ctx.theme.presentation.sections;
  const currency = ctx.config.store.currency;
  const totalPages = Math.max(1, Math.ceil(results.count / results.page_size));

  const sorts: { value: Sort; label: string }[] = [
    ...(featured ? [{ value: 'featured' as const, label: t.sortFeatured }] : []),
    { value: 'newest', label: t.sortNewest },
    { value: 'price_low', label: t.sortPriceLow },
    { value: 'price_high', label: t.sortPriceHigh },
    { value: 'name', label: t.sortName },
  ];
  const activeSort = query.sort ?? (featured ? 'featured' : 'newest');

  const chips: { label: string; href: string }[] = [];
  if (query.brand) {
    const brand = results.results.find((p) => p.brand?.slug === query.brand)?.brand?.name;
    chips.push({
      label: `${t.brand}: ${brand ?? query.brand}`,
      href: listingHref(pathname, query, { brand: null }),
    });
  }
  if (query.type) {
    chips.push({
      label: t.productTypes[query.type] ?? query.type,
      href: listingHref(pathname, query, { type: null }),
    });
  }
  if (query.minPrice || query.maxPrice) {
    const low = query.minPrice ? formatMoney(query.minPrice, currency) : null;
    const high = query.maxPrice ? formatMoney(query.maxPrice, currency) : null;
    chips.push({
      label:
        low && high ? t.priceBetween(low, high) : low ? t.priceFrom(low) : t.priceUpTo(high ?? ''),
      href: listingHref(pathname, query, { minPrice: null, maxPrice: null }),
    });
  }
  const clearAll = listingHref(pathname, {
    ...query,
    brand: null,
    type: null,
    minPrice: null,
    maxPrice: null,
  });

  const titleClass = {
    editorial: 'text-[30px] sm:text-5xl',
    literary: 'text-[30px] sm:text-5xl',
    quickshop: 'text-[28px] sm:text-4xl',
    spec: 'text-[28px] sm:text-4xl',
    marketplace: 'text-[28px] sm:text-4xl',
  }[look];

  return (
    <div className={`${CONTAINER} flex flex-col gap-5 pt-6 pb-16`}>
      <Breadcrumbs items={crumbs} label={t.home} />
      {before}
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex min-w-[200px] flex-1 flex-col gap-1">
          <h1 className={`m-0 store-display leading-[1.05] tracking-[-0.02em] ${titleClass}`}>
            {title}
          </h1>
          {intro && <p className="m-0 max-w-[640px] text-sm text-store-muted">{intro}</p>}
          <p className="m-0 text-[13.5px] text-store-muted" aria-live="polite">
            {t.results(results.count)}
          </p>
        </div>
        <div className="lg:hidden">
          <MenuDrawer
            side="bottom"
            trigger={
              <>
                <FilterIcon width={15} height={15} /> {t.filters}
              </>
            }
            triggerLabel={t.filters}
            title={t.filters}
            closeLabel={t.close}
            triggerClassName="flex h-10 items-center gap-1.5 rounded-base border border-store-line bg-store-surface px-3.5 text-[13.5px] font-semibold"
          >
            <FilterPanel {...props} idPrefix="drawer" />
          </MenuDrawer>
        </div>
        <SortSelect
          label={t.sortBy}
          value={activeSort}
          options={sorts.map((sort) => ({
            ...sort,
            href: listingHref(pathname, query, { sort: sort.value }),
          }))}
          className="h-10 cursor-pointer rounded-base border border-store-line bg-store-surface px-3 text-[13.5px] font-medium text-store-ink"
        />
      </div>

      {chips.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap items-center gap-2 p-0">
          {chips.map((chip) => (
            <li key={chip.label}>
              <Link
                href={chip.href as Route}
                aria-label={t.removeFilter(chip.label)}
                className={`${CHIP} bg-store-ink text-store-canvas hover:opacity-90`}
              >
                {chip.label} <span aria-hidden="true">✕</span>
              </Link>
            </li>
          ))}
          <li>
            <Link href={clearAll as Route} className={`${CHIP} text-store-muted underline`}>
              {t.clearAll}
            </Link>
          </li>
        </ul>
      )}

      <div className="grid items-start gap-7 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="sticky top-40 hidden lg:block">
          <FilterPanel {...props} idPrefix="side" />
        </aside>
        <div className="flex min-w-0 flex-col gap-6">
          {results.results.length > 0 ? (
            <ProductGrid
              products={results.results}
              layout="listing"
              variant={ctx.theme.presentation.card}
              lang={ctx.lang}
              t={t}
              secondaryLang={secondaryLanguage(ctx)}
            />
          ) : (
            <div className="flex flex-col items-center gap-2.5 rounded-base border border-dashed border-store-line bg-store-surface px-6 py-14 text-center">
              <p className="m-0 text-lg font-bold">{t.noResults}</p>
              <p className="m-0 max-w-[380px] text-sm text-store-muted">
                {results.count > 0 ? t.pageOf(query.page, totalPages) : t.noResultsHint}
              </p>
              <div className="mt-1.5 flex flex-wrap justify-center gap-2">
                {chips.length > 0 && (
                  <Link
                    href={clearAll as Route}
                    className={`h-10 rounded-base bg-store-action px-4 text-[13.5px] leading-10 font-semibold text-store-on-action ${FOCUS}`}
                  >
                    {t.clearAll}
                  </Link>
                )}
                <Link
                  href={
                    results.count > 0
                      ? (listingHref(pathname, query, { page: 1 }) as Route)
                      : '/shop'
                  }
                  className={`h-10 rounded-base border border-store-line bg-store-surface px-4 text-[13.5px] leading-10 font-semibold ${FOCUS}`}
                >
                  {results.count > 0 ? t.pageOf(1, totalPages) : t.browseAll}
                </Link>
              </div>
            </div>
          )}
          <Pagination ctx={ctx} pathname={pathname} query={query} total={totalPages} />
        </div>
      </div>
    </div>
  );
}
