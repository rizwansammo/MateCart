import type { PresentationVariant } from '@matecart/shared-ui';

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
    return <HomeFallback ctx={ctx} products={newest?.results ?? []} />;
  }
  return <HomeSections ctx={ctx} data={data} />;
}
