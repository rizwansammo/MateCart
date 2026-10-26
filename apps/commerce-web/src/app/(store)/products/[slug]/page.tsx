import type { PresetId } from '@matecart/shared-ui';
import type { Metadata, Route } from 'next';
import { notFound } from 'next/navigation';

import { ProductGrid } from '@/components/store/product-card';
import {
  detailsLabel,
  FactTable,
  factRows,
  ProductHeader,
  ProductHighlights,
} from '@/components/store/product-details';
import { ProductExperience } from '@/components/store/product-experience';
import { Tabs } from '@/components/store/tabs';
import { Breadcrumbs, CONTAINER, PlainText, SectionHeading } from '@/components/store/ui';
import { secondaryLanguage } from '@/components/store/home-sections';
import type { SearchParams } from '@/lib/catalog-query';
import { localize } from '@/lib/i18n';
import { getStoreContext, publicOrigin } from '@/lib/store-context';
import { getProduct, getProducts } from '@/lib/storefront-api';

interface Props {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}

const RELATED: Record<PresetId, number> = {
  fashion: 4,
  grocery: 6,
  electronics: 4,
  general: 5,
  books: 5,
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const [{ slug }, { lang }] = await Promise.all([params, getStoreContext()]);
  const product = await getProduct(slug);
  if (!product) return {};
  const name = localize(product.translations, 'name', product.name, lang);
  const description =
    product.seo_description ||
    localize(product.translations, 'summary', product.summary, lang) ||
    localize(product.translations, 'description', product.description, lang).slice(0, 160) ||
    undefined;
  const image = product.images[0];
  return {
    title: product.seo_title || name,
    description,
    openGraph: {
      title: name,
      description,
      // Absolute image URLs need the public origin; without it, no image is advertised.
      images:
        image && publicOrigin() ? [{ url: image.url, alt: image.alt_text || name }] : undefined,
    },
  };
}

export default async function ProductPage({ params, searchParams }: Props) {
  const [{ slug }, search, ctx] = await Promise.all([params, searchParams, getStoreContext()]);
  const product = await getProduct(slug);
  if (!product) notFound();

  const { t, lang, theme } = ctx;
  const preset = theme.preset.id;
  const name = localize(product.translations, 'name', product.name, lang);
  const primary = product.categories.find((c) => c.is_primary) ?? product.categories[0];
  const requested = typeof search.variant === 'string' ? search.variant : null;
  const relatedPage = primary
    ? await getProducts(
        new URLSearchParams({ category: primary.slug, page_size: String(RELATED[preset] + 1) }),
      )
    : null;
  const related = (relatedPage?.results ?? [])
    .filter((p) => p.id !== product.id)
    .slice(0, RELATED[preset]);

  const description = localize(product.translations, 'description', product.description, lang);
  const facts = factRows(product, t, lang);
  const tabs = [
    ...(description
      ? [
          {
            id: 'description',
            label: t.description,
            content: (
              <PlainText text={description} className="max-w-[760px] text-[15px] leading-relaxed" />
            ),
          },
        ]
      : []),
    ...(facts.length > 0
      ? [{ id: 'details', label: detailsLabel(product, t), content: <FactTable rows={facts} /> }]
      : []),
  ];

  return (
    <div className={`${CONTAINER} flex flex-col gap-10 pt-5 pb-16`}>
      <Breadcrumbs
        label={t.home}
        items={[
          { label: t.home, href: '/' },
          ...(primary
            ? [{ label: primary.name, href: `/categories/${primary.slug}` as Route }]
            : []),
          { label: name },
        ]}
      />
      <ProductExperience
        product={product}
        preset={preset}
        lang={lang}
        today={new Date().toISOString().slice(0, 10)}
        requestedVariant={requested}
        header={<ProductHeader product={product} ctx={ctx} />}
        highlights={<ProductHighlights product={product} ctx={ctx} />}
      />
      {tabs.length > 0 && <Tabs label={name} tabs={tabs} />}
      {related.length > 0 && (
        <section aria-labelledby="related-products" className="flex flex-col gap-5">
          <SectionHeading preset={preset} id="related-products" title={t.related} />
          <ProductGrid
            products={related}
            preset={preset}
            lang={lang}
            t={t}
            secondaryLang={secondaryLanguage(ctx)}
          />
        </section>
      )}
    </div>
  );
}
