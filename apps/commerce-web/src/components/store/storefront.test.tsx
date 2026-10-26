import type { StorefrontSection } from '@matecart/shared-contracts';
import { PRESETS, PRESET_IDS, THEMES, type PresetId } from '@matecart/shared-ui';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Route } from 'next';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { parseListingQuery } from '@/lib/catalog-query';
import { productWith, storeContext, summary, variant } from '@/test/fixtures';

import { CatalogListing } from './catalog-listing';
import { hasRenderableSection, HomeFallback, HomeSections, trustItems } from './home-sections';
import { ProductCard } from './product-card';
import { ProductExperience } from './product-experience';
import { StoreShell } from './store-shell';
import { Tabs } from './tabs';

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }));
const checkout = vi.hoisted(() => ({
  addToCart: vi.fn(),
  getCart: vi.fn(),
  CART_UPDATED_EVENT: 'matecart:cart-updated',
}));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/shop' }));
vi.mock('@/lib/checkout-client', () => checkout);
vi.mock('next/form', () => ({
  default: ({ action, children, ...rest }: { action: string; children: ReactNode }) => (
    <form action={action} {...rest}>
      {children}
    </form>
  ),
}));

beforeEach(() => {
  router.push.mockReset();
  router.refresh.mockReset();
  checkout.addToCart.mockReset();
  checkout.addToCart.mockResolvedValue(undefined);
  checkout.getCart.mockResolvedValue({ item_count: 0 });
});

const categories = [
  {
    name: 'Books',
    slug: 'books',
    description: 'Poetry and fiction',
    image_url: null,
    translations: { bn: { name: 'বই' } },
    children: [],
  },
];

describe('the five preset storefronts', () => {
  it.each(PRESET_IDS)('%s renders its own theme on the server, without demo controls', (preset) => {
    const { container } = render(
      <StoreShell ctx={storeContext(preset)} categories={categories}>
        <p>Page</p>
      </StoreShell>,
    );
    const scope = container.querySelector(`[data-preset="${preset}"]`) as HTMLElement;
    expect(scope).not.toBeNull();
    expect(scope.style.getPropertyValue('--store-canvas')).toBe(
      THEMES[PRESETS[preset].defaultTheme].colors.canvas,
    );
    expect(scope.style.getPropertyValue('--store-header')).toBe(
      THEMES[PRESETS[preset].defaultTheme].colors.header,
    );
    // Real store data only: announcement, brand, categories and published footer pages.
    expect(screen.getByText('Sample prices')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Review Store/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: 'Books' }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: 'Shipping' }).length).toBeGreaterThan(0);
    // No prototype switcher and no shopping features that do not exist yet.
    const text = container.textContent ?? '';
    for (const absent of ['Design system', 'Merchant', 'Wishlist', 'Compare', 'Bag', 'Basket']) {
      expect(text).not.toContain(absent);
    }
    expect(screen.queryByText(/facebook|whatsapp|instagram/i)).toBeNull();
  });

  it('uses each preset’s own search wording and brand treatment', () => {
    const placeholders = (preset: PresetId) => {
      const { unmount } = render(
        <StoreShell ctx={storeContext(preset)} categories={categories}>
          {null}
        </StoreShell>,
      );
      const values = screen.getAllByRole('searchbox').map((box) => box.getAttribute('placeholder'));
      const brand = screen.getAllByRole('link', { name: /Review Store/ })[0]?.textContent;
      unmount();
      return { values: [...new Set(values)], brand };
    };
    expect(placeholders('books').values).toEqual(['Title, author or ISBN']);
    expect(placeholders('grocery').values).toEqual(['Search products']);
    expect(placeholders('electronics').brand).toBe('Review Store_');
  });

  it('shows the language switch only when the store has two languages', () => {
    const { unmount } = render(
      <StoreShell ctx={storeContext('general')} categories={[]}>
        {null}
      </StoreShell>,
    );
    expect(screen.getAllByRole('button', { name: 'Language: বাংলা' }).length).toBeGreaterThan(0);
    unmount();
    render(
      <StoreShell ctx={storeContext('general', 'en', { languages: ['en'] })} categories={[]}>
        {null}
      </StoreShell>,
    );
    expect(screen.queryByRole('button', { name: /Language/ })).toBeNull();
  });

  it('stores the chosen language and re-renders on the server', async () => {
    render(
      <StoreShell ctx={storeContext('general')} categories={[]}>
        {null}
      </StoreShell>,
    );
    await userEvent.click(screen.getAllByRole('button', { name: 'Language: বাংলা' })[0]!);
    expect(document.cookie).toContain('mc_lang=bn');
    expect(router.refresh).toHaveBeenCalledTimes(1);
    document.cookie = 'mc_lang=; max-age=0; path=/';
  });

  it('renders Bangla interface text and merchant translations, with fallback', () => {
    const { container } = render(
      <StoreShell ctx={storeContext('grocery', 'bn')} categories={categories}>
        {null}
      </StoreShell>,
    );
    expect(container.querySelector('[lang="bn"]')).not.toBeNull();
    expect(screen.getByText('নমুনা দাম')).toBeInTheDocument(); // translated announcement
    expect(screen.getAllByRole('link', { name: 'বই' }).length).toBeGreaterThan(0); // category
    expect(screen.getAllByRole('link', { name: 'শিপিং' }).length).toBeGreaterThan(0); // footer page
    expect(screen.getAllByRole('link', { name: 'সব পণ্য' }).length).toBeGreaterThan(0); // UI text
  });

  it('shows contact details only when the merchant configured them', () => {
    const { unmount } = render(
      <StoreShell ctx={storeContext('general')} categories={[]}>
        {null}
      </StoreShell>,
    );
    expect(screen.queryByRole('link', { name: /mailto|@/ })).toBeNull();
    unmount();
    render(
      <StoreShell
        ctx={storeContext('general', 'en', { contact_email: 'help@shop.test' })}
        categories={[]}
      >
        {null}
      </StoreShell>,
    );
    expect(screen.getByRole('link', { name: /help@shop.test/ })).toHaveAttribute(
      'href',
      'mailto:help@shop.test',
    );
  });
});

describe('product cards', () => {
  const t = storeContext('general').t;
  it.each([
    ['fashion', 'aspect-[3/4]'],
    ['grocery', 'aspect-square'],
    ['electronics', 'aspect-[4/3]'],
    ['general', 'aspect-square'],
    ['books', 'aspect-[2/3]'],
  ] as const)('%s cards use the approved image shape', (preset, aspect) => {
    const { container } = render(
      <ProductCard
        product={summary()}
        variant={storeContext(preset).theme.presentation.card}
        lang="en"
        t={t}
      />,
    );
    expect(container.querySelector(`.${CSS.escape(aspect)}`)).not.toBeNull();
    expect(screen.getByRole('link')).toHaveAttribute('href', '/products/gitanjali');
    expect(screen.getByText('From ৳250')).toBeInTheDocument();
    // No invented ratings, reviews, discounts or stock counts.
    expect(container.textContent).not.toMatch(/★|review|% off|only \d+ left/i);
  });
  it('shows authors on book cards and the other language on grocery cards', () => {
    const { unmount } = render(
      <ProductCard product={summary()} variant="literary" lang="en" t={t} />,
    );
    expect(screen.getByText('Rabindranath Tagore')).toBeInTheDocument();
    unmount();
    render(
      <ProductCard product={summary()} variant="quickshop" lang="en" t={t} secondaryLang="bn" />,
    );
    expect(screen.getByText('গীতাঞ্জলি')).toHaveAttribute('lang', 'bn');
  });
  it('marks sold-out and unpriced products honestly', () => {
    const { unmount } = render(
      <ProductCard product={summary({ in_stock: false })} variant="editorial" lang="en" t={t} />,
    );
    expect(screen.getByText('Sold out')).toBeInTheDocument();
    unmount();
    render(
      <ProductCard product={summary({ price: null })} variant="marketplace" lang="en" t={t} />,
    );
    expect(screen.getByText('Price not available')).toBeInTheDocument();
  });
  it('uses the translated name in Bangla', () => {
    render(
      <ProductCard
        product={summary()}
        variant="marketplace"
        lang="bn"
        t={storeContext('general', 'bn').t}
      />,
    );
    expect(screen.getByText('গীতাঞ্জলি')).toBeInTheDocument();
  });
});

function section(
  section_type: StorefrontSection['section_type'],
  extra: Partial<StorefrontSection> = {},
) {
  return {
    section_type,
    title: `${section_type} title`,
    subtitle: '',
    body: '',
    image_url: null,
    collection: null,
    cta_label: '',
    cta_url: '',
    translations: {},
    ...extra,
  } satisfies StorefrontSection;
}

describe('homepage sections', () => {
  it('renders sections in the merchant’s order and skips empty ones', () => {
    const sections = [
      section('trust_strip', { subtitle: 'Genuine products · Cash on delivery' }),
      section('hero', { cta_label: 'Shop now', cta_url: '/collections/new' }),
      section('collection_highlight', { collection: { name: 'New', slug: 'new' } }),
      section('category_showcase'),
      section('brand_story', { body: 'Our story.' }),
    ];
    const { container } = render(
      <HomeSections
        ctx={storeContext('general')}
        data={{ sections, categories: [], collections: [null, null, [], null, null] }}
      />,
    );
    const headings = [...container.querySelectorAll('h1, h2')].map((h) => h.textContent);
    // The empty collection and the showcase without categories are left out.
    expect(headings).toEqual(['trust_strip title', 'hero title', 'brand_story title']);
    expect(screen.getByRole('link', { name: 'Shop now' })).toHaveAttribute(
      'href',
      '/collections/new',
    );
    expect(screen.getByText('Genuine products')).toBeInTheDocument();
  });
  it('shows collection products and categories when they exist', () => {
    render(
      <HomeSections
        ctx={storeContext('books')}
        data={{
          sections: [
            section('category_showcase'),
            section('collection_highlight', { collection: { name: 'New', slug: 'new' } }),
          ],
          categories,
          collections: [null, [summary()]],
        }}
      />,
    );
    expect(screen.getByRole('link', { name: /Books/ })).toHaveAttribute(
      'href',
      '/categories/books',
    );
    expect(screen.getByRole('link', { name: /Gitanjali/ })).toHaveAttribute(
      'href',
      '/products/gitanjali',
    );
    expect(
      screen.getAllByRole('link', { name: /View all/ }).map((a) => a.getAttribute('href')),
    ).toContain('/collections/new');
  });
  it('never links a call to action to another site', () => {
    render(
      <HomeSections
        ctx={storeContext('fashion')}
        data={{
          sections: [section('hero', { cta_label: 'Win', cta_url: '//evil.test/x' })],
          categories: [],
          collections: [null],
        }}
      />,
    );
    expect(screen.queryByRole('link', { name: 'Win' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Shop all' })).toHaveAttribute('href', '/shop');
  });
  it('uses translations with fallback to the default text', () => {
    render(
      <HomeSections
        ctx={storeContext('grocery', 'bn')}
        data={{
          sections: [
            section('hero', {
              subtitle: 'Fresh daily',
              translations: { bn: { title: 'তাজা বাজার' } },
            }),
          ],
          categories: [],
          collections: [null],
        }}
      />,
    );
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('তাজা বাজার');
    expect(screen.getByText('Fresh daily')).toBeInTheDocument();
  });
  it('splits trust items on lines and middle dots', () => {
    expect(trustItems(section('trust_strip', { subtitle: 'A · B', body: 'C\n\nD' }), 'en')).toEqual(
      ['A', 'B', 'C', 'D'],
    );
  });
});

describe('homepage fallback (06.1)', () => {
  const emptyTrust = section('trust_strip', { subtitle: ' · ', body: '' });
  const emptyCollection = section('collection_highlight', {
    collection: { name: 'New', slug: 'new' },
  });
  it.each([
    ['no sections', [], [], []],
    ['only a showcase without categories', [section('category_showcase')], [], [null]],
    ['only an empty collection', [emptyCollection], [], [[]]],
    ['only a collection that could not be loaded', [emptyCollection], [], [null]],
    ['only an empty trust strip', [emptyTrust], [], [null]],
  ] as const)('falls back with %s', (_label, sections, cats, collections) => {
    const data = {
      sections: [...sections],
      categories: [...cats],
      collections: collections.map((c) => (c ? [...c] : null)),
    };
    expect(hasRenderableSection(data, 'en')).toBe(false);
  });
  it('keeps the normal homepage when any section can render', () => {
    const data = {
      sections: [emptyTrust, section('category_showcase'), emptyCollection, section('brand_story')],
      categories: [],
      collections: [null, null, [], null],
    };
    expect(hasRenderableSection(data, 'en')).toBe(true);
    expect(
      hasRenderableSection({ ...data, sections: [section('hero')], collections: [null] }, 'en'),
    ).toBe(true);
    const withProducts = {
      sections: [emptyCollection],
      categories: [],
      collections: [[summary()]],
    };
    expect(hasRenderableSection(withProducts, 'en')).toBe(true);
  });
  it('skips only the empty sections and keeps the merchant order', () => {
    const { container } = render(
      <HomeSections
        ctx={storeContext('electronics')}
        data={{
          sections: [emptyTrust, section('hero'), emptyCollection, section('brand_story')],
          categories: [],
          collections: [null, null, [], null],
        }}
      />,
    );
    const headings = [...container.querySelectorAll('h1, h2')].map((h) => h.textContent);
    expect(headings).toEqual(['hero title', 'brand_story title']);
  });
  it.each(PRESET_IDS)('%s renders both the fallback and a normal homepage', (preset) => {
    const ctx = storeContext(preset);
    const fallback = render(<HomeFallback ctx={ctx} products={[summary()]} />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Review Store');
    expect(screen.getByText('Everyday goods')).toBeInTheDocument(); // configured tagline
    expect(screen.getByRole('link', { name: /Gitanjali/ })).toHaveAttribute(
      'href',
      '/products/gitanjali',
    );
    fallback.unmount();
    render(
      <HomeSections
        ctx={ctx}
        data={{
          sections: [section('hero'), emptyCollection],
          categories: [],
          collections: [null, []],
        }}
      />,
    );
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('hero title');
  });
});

describe('product page', () => {
  const product = productWith(
    [
      variant('s', { size: 's' }, { in_stock: false }),
      variant(
        'm',
        { size: 'm' },
        {
          price: { currency: 'BDT', amount: '1250.0000', compare_at_amount: '1400.0000' },
        },
      ),
      variant(
        'l',
        { size: 'l' },
        { price: { currency: 'BDT', amount: '1300.0000', compare_at_amount: null } },
      ),
    ],
    {
      options: [
        {
          code: 'size',
          name: 'Size',
          values: ['s', 'm', 'l'].map((code) => ({ code, value: code.toUpperCase(), swatch: '' })),
        },
      ],
    },
  );
  const view = (requested: string | null = null, today = '2026-10-26') =>
    render(
      <ProductExperience
        product={product}
        look="editorial"
        lang="en"
        today={today}
        requestedVariant={requested}
        header={<h1>Shirt</h1>}
        highlights={null}
      />,
    );

  it('opens on a purchasable variant with its real price and saving', () => {
    view();
    expect(screen.getByText('৳1,250')).toBeInTheDocument();
    expect(screen.getByText('৳1,400')).toBeInTheDocument();
    expect(screen.getByText('You save ৳150')).toBeInTheDocument();
    expect(screen.getByText('In stock')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'M' })).toHaveAttribute('aria-pressed', 'true');
  });
  it('switches variant, price and stock, and keeps the choice in the URL', async () => {
    const replace = vi.spyOn(window.history, 'replaceState');
    view();
    await userEvent.click(screen.getByRole('button', { name: 'L' }));
    expect(screen.getByText('৳1,300')).toBeInTheDocument();
    expect(screen.queryByText(/You save/)).toBeNull();
    expect(String(replace.mock.calls.at(-1)?.[2])).toContain('variant=l');
    await userEvent.click(screen.getByRole('button', { name: /^S/ }));
    expect(screen.getByText('Out of stock')).toBeInTheDocument();
  });
  it('keeps the purchase action disabled for a sold-out selection', () => {
    view('s');
    expect(screen.getByRole('button', { name: /^S/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Out of stock')).toBeInTheDocument();
    const add = screen.getByRole('button', { name: 'Add to cart' });
    expect(add).toBeDisabled();
  });
  it('adds the selected variant and quantity to the cart', async () => {
    const user = userEvent.setup();
    view();
    await user.click(screen.getByRole('button', { name: 'L' }));
    const quantity = screen.getByRole('spinbutton', { name: 'Quantity' });
    await user.clear(quantity);
    await user.type(quantity, '2');
    await user.click(screen.getByRole('button', { name: 'Add to cart' }));
    expect(checkout.addToCart).toHaveBeenCalledWith('l', '2');
    expect(await screen.findByText('Added to your cart.')).toBeInTheDocument();
  });
  it('labels pre-orders, digital formats and missing prices', () => {
    const special = productWith([
      variant('pre', {}, { release_date: '2027-03-15', isbn: '9780306406157' }),
      variant('ebook', {}, { kind: 'digital', digital_format: 'epub' }),
      variant('none', {}, { price: null }),
    ]);
    const { rerender } = render(
      <ProductExperience
        product={special}
        look="literary"
        lang="en"
        today="2026-10-26"
        requestedVariant="pre"
        header={null}
        highlights={null}
      />,
    );
    expect(screen.getByText(/Pre-order · Releases 15 March 2027/)).toBeInTheDocument();
    expect(screen.getByText('9780306406157')).toBeInTheDocument();
    rerender(
      <ProductExperience
        product={special}
        look="literary"
        lang="en"
        today="2026-10-26"
        requestedVariant="ebook"
        header={null}
        highlights={null}
        key="ebook"
      />,
    );
    expect(screen.getByText('Digital (EPUB)')).toBeInTheDocument();
    rerender(
      <ProductExperience
        product={special}
        look="literary"
        lang="en"
        today="2026-10-26"
        requestedVariant="none"
        header={null}
        highlights={null}
        key="none"
      />,
    );
    expect(screen.getByText('Price not available')).toBeInTheDocument();
    expect(screen.getAllByText('Not available').length).toBeGreaterThan(0);
  });
  it.each([
    ['2026-10-26', 'Pre-order · Releases 27 October 2026'], // the day before (store time)
    ['2026-10-27', 'In stock'], // release day in the store's time zone: no longer a pre-order
    ['2026-10-28', 'In stock'],
  ])('labels a release on 27 October with the store date %s (06.1)', (today, label) => {
    const book = productWith([
      variant('pb', {}, { release_date: '2026-10-27' }),
      variant('ebook', {}, { kind: 'digital', digital_format: 'epub' }),
      variant('plain', {}, { in_stock: false }),
    ]);
    const show = (id: string) =>
      render(
        <ProductExperience
          product={book}
          look="literary"
          lang="en"
          today={today}
          requestedVariant={id}
          header={null}
          highlights={null}
        />,
      );
    const first = show('pb');
    expect(screen.getByText(label)).toBeInTheDocument();
    first.unmount();
    // Digital and regular variants do not depend on the date.
    const digital = show('ebook');
    expect(screen.getByText('In stock')).toBeInTheDocument();
    digital.unmount();
    show('plain');
    expect(screen.getByText('Out of stock')).toBeInTheDocument();
  });
  it('lets keyboard users move between tabs', () => {
    render(
      <Tabs
        label="Shirt"
        tabs={[
          { id: 'a', label: 'Description', content: <p>About</p> },
          { id: 'b', label: 'Details', content: <p>Facts</p> },
        ]}
      />,
    );
    const [first, second] = screen.getAllByRole('tab');
    expect(screen.getByText('Facts').parentElement).not.toBeVisible();
    fireEvent.keyDown(first!, { key: 'ArrowRight' });
    expect(second).toHaveAttribute('aria-selected', 'true');
    expect(second).toHaveFocus();
    expect(screen.getByText('Facts')).toBeVisible();
  });
});

describe('product listing', () => {
  const ctx = storeContext('general');
  const results = {
    count: 50,
    page: 2,
    page_size: 24,
    results: [summary({ brand: { name: 'Nova', slug: 'nova' } })],
  };
  const listing = (
    params: Record<string, string>,
    extra: { featured?: boolean; empty?: boolean } = {},
  ) =>
    render(
      <CatalogListing
        ctx={ctx}
        pathname="/shop"
        query={parseListingQuery(params)}
        results={extra.empty ? { ...results, count: 0, results: [] } : results}
        title="All products"
        crumbs={[{ label: 'Home', href: '/' }, { label: 'All products' }]}
        categoryLinks={[
          { label: 'Books', href: '/categories/books' as Route, active: false, depth: 0 },
        ]}
        featured={extra.featured}
        typeFilter
      />,
    );

  it('shows the full count, active filters and shareable page links', () => {
    listing({ brand: 'nova', min_price: '100', page: '2', sort: 'price_low' });
    expect(screen.getByText('50 products')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Remove filter: Brand: Nova' })).toHaveAttribute(
      'href',
      '/shop?min_price=100&sort=price_low',
    );
    expect(screen.getByRole('link', { name: 'Remove filter: From ৳100' })).toHaveAttribute(
      'href',
      '/shop?brand=nova&sort=price_low',
    );
    const pages = within(screen.getByRole('navigation', { name: 'Pages' }));
    expect(pages.getByRole('link', { name: '3' })).toHaveAttribute(
      'href',
      '/shop?brand=nova&min_price=100&sort=price_low&page=3',
    );
    expect(pages.getByRole('link', { name: '2' })).toHaveAttribute('aria-current', 'page');
  });
  it('changes the sort through the URL and offers "Featured" only for collections', async () => {
    const { unmount } = listing({ q: 'mug' });
    const select = screen.getByRole('combobox', { name: 'Sort by' });
    expect(within(select).queryByRole('option', { name: 'Featured' })).toBeNull();
    await userEvent.selectOptions(select, 'price_high');
    expect(router.push).toHaveBeenCalledWith('/shop?q=mug&sort=price_high');
    unmount();
    listing({}, { featured: true });
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toHaveValue('featured');
  });
  it('explains empty results and offers a way back', () => {
    listing({ type: 'book' }, { empty: true });
    expect(screen.getByText('No products match')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Clear all' })[0]).toHaveAttribute('href', '/shop');
    expect(screen.getByRole('link', { name: 'Browse all products' })).toHaveAttribute(
      'href',
      '/shop',
    );
  });
  it('keeps the current search in the price filter form', () => {
    listing({ q: 'tea', sort: 'name' });
    const forms = document.querySelectorAll('form[action="/shop"]');
    expect(forms.length).toBeGreaterThan(0);
    const form = forms[0] as HTMLFormElement;
    expect((form.elements.namedItem('q') as HTMLInputElement).value).toBe('tea');
    expect((form.elements.namedItem('sort') as HTMLInputElement).value).toBe('name');
  });
});
