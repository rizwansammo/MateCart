import {
  THEMES,
  resolveTheme,
  type PresetId,
  type ThemeDefinition,
  type ThemeRegistry,
} from '@matecart/shared-ui';
import { render } from '@testing-library/react';
import type { Route } from 'next';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { parseListingQuery } from '@/lib/catalog-query';
import type { StoreContext } from '@/lib/store-context';
import { productWith, storeContext, summary, variant } from '@/test/fixtures';

import { CatalogListing } from './catalog-listing';
import { HomeSections } from './home-sections';
import { ProductExperience } from './product-experience';
import { StoreShell } from './store-shell';

/**
 * Phase 06.2 (D-069): one commerce engine, shared functional components, several visual themes.
 * A second theme of the same preset (test-only, never shipped) may change every presentation
 * variant, yet the storefront keeps exactly the same capabilities and data.
 */

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/shop',
}));
vi.mock('next/form', () => ({
  default: ({ action, children, ...rest }: { action: string; children: ReactNode }) => (
    <form action={action} {...rest}>
      {children}
    </form>
  ),
}));

function alternative(base: ThemeDefinition, id: string): ThemeDefinition {
  return {
    ...base,
    id,
    name: `${base.name} alternative (test)`,
    colors: { ...base.colors, canvas: '#FFFFFF' },
    presentation: {
      header: base.presentation.header === 'marketplace' ? 'editorial' : 'marketplace',
      hero: base.presentation.hero === 'carousel' ? 'spotlight' : 'carousel',
      card: base.presentation.card === 'spec' ? 'literary' : 'spec',
      sections: base.presentation.sections === 'quickshop' ? 'literary' : 'quickshop',
      product: base.presentation.product === 'marketplace' ? 'editorial' : 'marketplace',
    },
  };
}
const registry: ThemeRegistry = {
  ...THEMES,
  'fashion-test-alt': alternative(THEMES['fashion-editorial'], 'fashion-test-alt'),
  'books-test-alt': alternative(THEMES['books-literary'], 'books-test-alt'),
};

function contextWith(preset: PresetId, themeId: string): StoreContext {
  const base = storeContext(preset);
  return {
    ...base,
    config: { ...base.config, active_theme: themeId },
    theme: resolveTheme(preset, {}, themeId, registry),
  };
}

const categories = ['women', 'men'].map((slug) => ({
  name: slug,
  slug,
  description: '',
  image_url: null,
  translations: {},
  children: [],
}));
const products = [
  summary(),
  summary({ id: 's2', slug: 'linen-shirt', name: 'Linen Shirt', in_stock: false, authors: [] }),
];
const sections = [
  {
    section_type: 'hero' as const,
    title: 'New season',
    subtitle: '',
    body: '',
    image_url: null,
    collection: null,
    cta_label: 'Shop new in',
    cta_url: '/collections/new',
    translations: {},
  },
  {
    section_type: 'collection_highlight' as const,
    title: 'New in',
    subtitle: '',
    body: '',
    image_url: null,
    collection: { name: 'New', slug: 'new' },
    cta_label: '',
    cta_url: '',
    translations: {},
  },
];
const product = productWith(
  [variant('s', { size: 's' }), variant('m', { size: 'm' }, { in_stock: false })],
  {
    options: [
      {
        code: 'size',
        name: 'Size',
        values: [
          { code: 's', value: 'S', swatch: '' },
          { code: 'm', value: 'M', swatch: '' },
        ],
      },
    ],
  },
);

function storefront(ctx: StoreContext) {
  const results = { count: 30, page: 1, page_size: 24, results: products };
  return render(
    <StoreShell ctx={ctx} categories={categories}>
      <HomeSections ctx={ctx} data={{ sections, categories, collections: [null, products] }} />
      <CatalogListing
        ctx={ctx}
        pathname="/shop"
        query={parseListingQuery({ sort: 'price_low' })}
        results={results}
        title="All products"
        crumbs={[{ label: 'Home', href: '/' as Route }]}
        categoryLinks={categories.map((c) => ({
          label: c.name,
          href: `/categories/${c.slug}` as Route,
          active: false,
          depth: 0,
        }))}
        // Business decision, made by the page from the preset (never the theme).
        typeFilter={ctx.theme.preset.id === 'general'}
      />
      <ProductExperience
        product={product}
        look={ctx.theme.presentation.product}
        lang="en"
        today="2026-10-27"
        requestedVariant={null}
        header={null}
        highlights={null}
      />
    </StoreShell>,
  );
}

/** What a customer can do and see, independent of layout and styling. */
function capabilities(container: HTMLElement) {
  const sorted = (values: Iterable<string>) => [...new Set(values)].sort();
  return {
    // How search is reached (a search box, or a link to the search page on small screens) is
    // presentation; that it can be reached is a capability (see `search` below).
    links: sorted(
      [...container.querySelectorAll('a[href]')]
        .map((a) => a.getAttribute('href') ?? '')
        .filter((href) => href !== '/search'),
    ),
    forms: sorted(
      [...container.querySelectorAll('form')].map(
        (form) =>
          `${form.getAttribute('action')}:${[...form.querySelectorAll('input, select')]
            .map((field) => field.getAttribute('name') ?? '')
            .sort()
            .join(',')}`,
      ),
    ),
    prices: sorted(
      [...container.querySelectorAll('*')]
        .filter((el) => el.children.length === 0 && /৳/.test(el.textContent ?? ''))
        .map((el) => el.textContent ?? ''),
    ),
    options: sorted(
      [...container.querySelectorAll('button[aria-pressed]')].map(
        (b) => `${b.textContent}:${b.getAttribute('aria-pressed')}`,
      ),
    ),
    purchase: [...container.querySelectorAll('button')]
      .filter((b) => b.textContent === 'Add to cart')
      .map((b) => (b as HTMLButtonElement).disabled),
    stock:
      sorted(
        [...container.querySelectorAll('*')]
          .filter((el) => el.children.length === 0)
          .map((el) => el.textContent ?? '')
          .filter((text) => ['In stock', 'Out of stock', 'Sold out'].includes(text)),
      ).length > 0,
    language: container.querySelector('button[aria-label^="Language"]') !== null,
    search:
      container.querySelector('form[action="/search"] input[name="q"]') !== null ||
      container.querySelector('a[href="/search"]') !== null,
    sort: container.querySelector('select') !== null,
  };
}

describe('functional parity between themes of one preset (D-069)', () => {
  it.each([
    ['fashion', 'fashion-editorial', 'fashion-test-alt'],
    ['books', 'books-literary', 'books-test-alt'],
  ] as const)('%s: %s and %s differ only in presentation', (preset, approved, other) => {
    const first = storefront(contextWith(preset, approved));
    const firstHtml = first.container.innerHTML;
    const firstCapabilities = capabilities(first.container);
    first.unmount();
    const second = storefront(contextWith(preset, other));

    // Different design: theme marker, structure and markup.
    expect(second.container.querySelector('[data-theme]')?.getAttribute('data-theme')).toBe(other);
    expect(firstHtml).toContain(`data-theme="${approved}"`);
    expect(second.container.innerHTML).not.toBe(firstHtml);
    // Same business preset and the same capabilities and data.
    expect(second.container.querySelector('[data-preset]')?.getAttribute('data-preset')).toBe(
      preset,
    );
    expect(capabilities(second.container)).toEqual(firstCapabilities);
    expect(firstCapabilities.links).toEqual(
      expect.arrayContaining([
        '/',
        '/shop',
        '/account',
        '/categories/women',
        '/products/gitanjali',
      ]),
    );
    expect(firstCapabilities.purchase.every((disabled) => disabled)).toBe(true);
  });

  it('keeps business wording on the preset, whatever the header variant', () => {
    const view = storefront(contextWith('books', 'books-test-alt'));
    const placeholders = [...view.container.querySelectorAll('input[name="q"]')].map((input) =>
      input.getAttribute('placeholder'),
    );
    expect(new Set(placeholders)).toEqual(new Set(['Title, author or ISBN']));
  });

  it('never changes the data it renders', () => {
    const before = structuredClone({ products, sections, product, categories });
    storefront(contextWith('fashion', 'fashion-test-alt'));
    expect({ products, sections, product, categories }).toEqual(before);
  });
});
