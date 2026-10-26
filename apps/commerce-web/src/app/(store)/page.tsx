import type { PresetId } from '@matecart/shared-ui';

import { HomeFallback, HomeSections } from '@/components/store/home-sections';
import { apiQuery, parseListingQuery } from '@/lib/catalog-query';
import { getStoreContext } from '@/lib/store-context';
import { getCategoryTree, getHomeSections, getProducts } from '@/lib/storefront-api';

/** Products shown by a collection highlight: one or two full rows of the preset's grid. */
const HIGHLIGHT_SIZE: Record<PresetId, number> = {
  fashion: 4,
  grocery: 12,
  electronics: 8,
  general: 10,
  books: 5,
};

export default async function StoreHomePage() {
  const [ctx, sections, categories] = await Promise.all([
    getStoreContext(),
    getHomeSections(),
    getCategoryTree(),
  ]);
  const size = HIGHLIGHT_SIZE[ctx.theme.preset.id];
  const firstPage = parseListingQuery({});

  if (sections.length === 0) {
    const newest = await getProducts(apiQuery(firstPage, {}, size));
    return <HomeFallback ctx={ctx} products={newest?.results ?? []} />;
  }

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
  return <HomeSections ctx={ctx} data={{ sections, categories, collections }} />;
}
