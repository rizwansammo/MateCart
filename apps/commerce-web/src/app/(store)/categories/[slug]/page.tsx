import type { Metadata, Route } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { CatalogListing } from '@/components/store/catalog-listing';
import { PAGE_SIZE } from '@/components/store/product-card';
import { FOCUS } from '@/components/store/ui';
import { apiQuery, parseListingQuery, type SearchParams } from '@/lib/catalog-query';
import { categoryLinks, categoryPath } from '@/lib/categories';
import { localize } from '@/lib/i18n';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree, getProducts, isSlug } from '@/lib/storefront-api';

interface Props {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}

async function load(slug: string) {
  if (!isSlug(slug)) return null;
  const path = categoryPath(await getCategoryTree(), slug);
  const category = path?.at(-1);
  return path && category ? { path, category } : null;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const [{ slug }, { lang }] = await Promise.all([params, getStoreContext()]);
  const found = await load(slug);
  if (!found) return {};
  const { category } = found;
  return {
    title: localize(category.translations, 'name', category.name, lang),
    description:
      localize(category.translations, 'description', category.description, lang) || undefined,
  };
}

export default async function CategoryPage({ params, searchParams }: Props) {
  const [{ slug }, search, ctx, tree] = await Promise.all([
    params,
    searchParams,
    getStoreContext(),
    getCategoryTree(),
  ]);
  const found = await load(slug);
  if (!found) notFound();
  const { path, category } = found;
  const query = parseListingQuery(search);
  const results = await getProducts(
    apiQuery(query, { category: slug }, PAGE_SIZE[ctx.theme.preset.id]),
  );
  if (!results) notFound();

  const { t, lang } = ctx;
  const name = (node: typeof category) => localize(node.translations, 'name', node.name, lang);
  const children = category.children;
  return (
    <CatalogListing
      ctx={ctx}
      pathname={`/categories/${slug}`}
      query={query}
      results={results}
      title={name(category)}
      intro={localize(category.translations, 'description', category.description, lang)}
      crumbs={[
        { label: t.home, href: '/' },
        ...path
          .slice(0, -1)
          .map((node) => ({ label: name(node), href: `/categories/${node.slug}` as Route })),
        { label: name(category) },
      ]}
      categoryLinks={categoryLinks(tree, slug, lang)}
      typeFilter={ctx.theme.preset.id === 'general'}
      before={
        children.length > 0 ? (
          <nav aria-label={t.subcategories} className="lg:hidden">
            <ul className="m-0 flex list-none gap-2 overflow-x-auto overflow-y-hidden p-0 pb-1">
              {children.map((child) => (
                <li key={child.slug} className="flex-none">
                  <Link
                    href={`/categories/${child.slug}` as Route}
                    className={`flex h-9 items-center rounded-base border border-store-line bg-store-surface px-3.5 text-[13px] font-semibold ${FOCUS}`}
                  >
                    {name(child)}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ) : undefined
      }
    />
  );
}
