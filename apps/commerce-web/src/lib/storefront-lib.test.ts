import type { CategoryNode } from '@matecart/shared-contracts';
import { describe, expect, it } from 'vitest';

import { productWith, variant } from '@/test/fixtures';

import { apiQuery, listingHref, pageWindow, parseListingQuery } from './catalog-query';
import { categoryLinks, categoryPath } from './categories';
import { localize, messages, resolveLanguage } from './i18n';
import { formatMoney, fromUnits, priceLabel, saving, toUnits } from './money';
import {
  initialVariant,
  isPreorder,
  isPurchasable,
  optionState,
  packSize,
  selectOption,
} from './variants';

describe('money (decimal strings, never floats)', () => {
  it.each([
    ['1250.0000', '৳1,250'],
    ['1250.5000', '৳1,250.50'],
    ['0.0500', '৳0.05'],
    ['49999.0000', '৳49,999'],
    ['1234567.8900', '৳1,234,567.89'],
  ])('formats %s', (amount, expected) => {
    expect(formatMoney(amount, 'BDT')).toBe(expected);
  });
  it('uses the currency minor unit (JPY has none)', () => {
    expect(formatMoney('1500.0000', 'JPY')).toBe('¥1,500');
  });
  it('computes savings exactly and only for a real discount', () => {
    expect(saving({ currency: 'BDT', amount: '54999.0000', compare_at_amount: '59999.0000' })).toBe(
      '5000.0000',
    );
    expect(saving({ currency: 'BDT', amount: '0.3000', compare_at_amount: '0.1000' })).toBeNull();
    expect(saving({ currency: 'BDT', amount: '10.0000', compare_at_amount: null })).toBeNull();
    expect(fromUnits(toUnits('0.1000')! + toUnits('0.2000')!)).toBe('0.3000');
  });
  it('labels ranges with "From" the lowest price', () => {
    const t = messages('en');
    expect(
      priceLabel({ currency: 'BDT', min_amount: '92.0000', max_amount: '440.0000' }, t.from),
    ).toEqual({ text: 'From ৳92', isRange: true });
    expect(
      priceLabel({ currency: 'BDT', min_amount: '850.0000', max_amount: '850.0000' }, t.from),
    ).toEqual({ text: '৳850', isRange: false });
  });
});

describe('listing URLs and API queries', () => {
  it('keeps only valid values from the URL', () => {
    expect(
      parseListingQuery({
        q: '  tea  ',
        sort: 'rating',
        min_price: 'abc',
        max_price: '500',
        brand: '../x',
        type: 'weapon',
        page: '-3',
      }),
    ).toEqual({
      q: 'tea',
      sort: null,
      minPrice: null,
      maxPrice: '500',
      brand: null,
      type: null,
      page: 1,
    });
  });
  it('swaps a reversed price range and caps the page', () => {
    const query = parseListingQuery({ min_price: '900', max_price: '100.5', page: '99999' });
    expect([query.minPrice, query.maxPrice, query.page]).toEqual(['100.5', '900', 1000]);
  });
  it('sends filters, sorting and paging to the API for the whole catalogue', () => {
    const query = parseListingQuery({ q: 'mug', sort: 'price_low', min_price: '100', page: '3' });
    const params = apiQuery(query, { category: 'cookware' }, 24);
    expect(Object.fromEntries(params)).toEqual({
      category: 'cookware',
      q: 'mug',
      min_price: '100',
      ordering: 'price_low',
      page: '3',
      page_size: '24',
    });
  });
  it('uses the merchant order only for collections', () => {
    const none = parseListingQuery({});
    expect(apiQuery(none, { collection: 'picks' }, 10).get('ordering')).toBe('featured');
    expect(apiQuery(none, {}, 10).get('ordering')).toBe('newest');
    const featured = parseListingQuery({ sort: 'featured' });
    expect(apiQuery(featured, {}, 10).get('ordering')).toBe('newest');
  });
  it('builds shareable links that keep other filters and reset the page', () => {
    const query = parseListingQuery({ q: 'tea', min_price: '50', page: '4', sort: 'name' });
    expect(listingHref('/search', query, { sort: 'price_high' })).toBe(
      '/search?q=tea&min_price=50&sort=price_high',
    );
    expect(listingHref('/search', query, { page: 5 })).toBe(
      '/search?q=tea&min_price=50&sort=name&page=5',
    );
    expect(listingHref('/shop', parseListingQuery({}))).toBe('/shop');
  });
  it('windows long page lists', () => {
    expect(pageWindow(6, 12)).toEqual([1, null, 5, 6, 7, null, 12]);
    expect(pageWindow(1, 3)).toEqual([1, 2, 3]);
  });
});

describe('languages', () => {
  it('uses the customer choice only when the store offers it', () => {
    expect(resolveLanguage('bn', ['en', 'bn'], 'en')).toBe('bn');
    expect(resolveLanguage('bn', ['en'], 'en')).toBe('en');
    expect(resolveLanguage('xx', ['en', 'bn'], 'bn')).toBe('bn');
    expect(resolveLanguage(undefined, ['en', 'bn'], 'bn')).toBe('bn');
  });
  it('falls back to the default text when a translation is missing or blank', () => {
    const translations = { bn: { name: 'গীতাঞ্জলি', summary: '  ' } };
    expect(localize(translations, 'name', 'Gitanjali', 'bn')).toBe('গীতাঞ্জলি');
    expect(localize(translations, 'summary', 'Song offerings', 'bn')).toBe('Song offerings');
    expect(localize(translations, 'name', 'Gitanjali', 'en')).toBe('Gitanjali');
  });
  it('has Bangla interface text for every English message', () => {
    const en = messages('en');
    const bn = messages('bn');
    expect(Object.keys(bn).sort()).toEqual(Object.keys(en).sort());
    expect(bn.addToCart).not.toBe(en.addToCart);
    expect(messages('fr')).toBe(en);
  });
});

describe('category tree', () => {
  const node = (slug: string, children: CategoryNode[] = []): CategoryNode => ({
    name: slug,
    slug,
    description: '',
    image_url: null,
    translations: { bn: { name: `${slug}-bn` } },
    children,
  });
  const tree = [node('home', [node('cookware', [node('pans')]), node('lighting')]), node('books')];
  it('finds the path to a category', () => {
    expect(categoryPath(tree, 'pans')?.map((n) => n.slug)).toEqual(['home', 'cookware', 'pans']);
    expect(categoryPath(tree, 'missing')).toBeNull();
  });
  it('opens only the current branch in the sidebar', () => {
    const links = categoryLinks(tree, 'cookware', 'bn');
    expect(links.map((l) => [l.label, l.depth, l.active])).toEqual([
      ['home-bn', 0, false],
      ['cookware-bn', 1, true],
      ['pans-bn', 2, false],
      ['lighting-bn', 1, false],
      ['books-bn', 0, false],
    ]);
  });
});

describe('variant selection', () => {
  const product = productWith([
    variant('s-red', { size: 's', colour: 'red' }, { in_stock: false }),
    variant('s-blue', { size: 's', colour: 'blue' }),
    variant('m-red', { size: 'm', colour: 'red' }),
    variant('m-blue', { size: 'm', colour: 'blue' }, { price: null }),
    variant('l-red', { size: 'l', colour: 'red' }, { in_stock: false }),
  ]);
  it('starts on the requested variant, else the first purchasable one', () => {
    expect(initialVariant(product, 'm-red')?.id).toBe('m-red');
    expect(initialVariant(product, 'nope')?.id).toBe('s-blue');
    expect(initialVariant(productWith([]), null)).toBeNull();
  });
  it('is purchasable only with a price and stock', () => {
    expect(product.variants.map(isPurchasable)).toEqual([false, true, true, false, false]);
  });
  it('prefers a variant that can be bought when an option changes', () => {
    const sBlue = product.variants[1]!;
    expect(selectOption(product, sBlue, 'size', 'm').id).toBe('m-red'); // m-blue has no price
    expect(selectOption(product, sBlue, 'colour', 'red').id).toBe('m-red'); // s-red sold out
    const mRed = product.variants[2]!;
    expect(selectOption(product, mRed, 'size', 's').id).toBe('s-blue');
    // Nothing purchasable in L: show the real (sold out) variant rather than pretend.
    expect(selectOption(product, mRed, 'size', 'l').id).toBe('l-red');
  });
  it('marks a value unavailable only when no purchasable variant has it', () => {
    const sBlue = product.variants[1]!;
    expect(optionState(product, sBlue, 'size', 's')).toBe('selected');
    expect(optionState(product, sBlue, 'size', 'm')).toBe('available');
    expect(optionState(product, sBlue, 'size', 'l')).toBe('unavailable');
    expect(optionState(product, sBlue, 'colour', 'red')).toBe('available');
  });
  it('tells pre-orders apart and formats pack sizes', () => {
    const preorder = variant('p', {}, { release_date: '2027-03-15' });
    expect(isPreorder(preorder, '2026-10-26')).toBe(true);
    expect(isPreorder(preorder, '2027-03-15')).toBe(false);
    expect(packSize(variant('r', {}, { net_quantity: '1.500', net_unit: 'kg' }))).toBe('1.5 kg');
    expect(packSize(variant('e', {}, { net_quantity: '12.000', net_unit: 'pcs' }))).toBe('12 pcs');
    expect(packSize(variant('x', {}))).toBeNull();
  });
});
