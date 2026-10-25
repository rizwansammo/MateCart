import { describe, expect, it } from 'vitest';
import {
  categoryNode,
  decimal,
  inventoryOperation,
  page,
  publicProductDetail,
  publicProductSummary,
  stockLevel,
} from './catalog';

const summary = {
  id: '01a17556-19cd-765f-9e28-700044901b91',
  slug: 'oxford-shirt',
  name: 'Oxford Shirt',
  summary: '',
  product_type: 'apparel',
  brand: null,
  price: { currency: 'BDT', min_amount: '1200.0000', max_amount: '1250.0000' },
  image: {
    url: '/api/catalog/media/01a17556-19e9-775f-9347-4a9e33e66841/',
    alt_text: 'Front',
    width: 800,
    height: 1000,
    variant: null,
  },
  in_stock: true,
  translations: { bn: { name: 'অক্সফোর্ড শার্ট' } },
};

describe('catalogue response boundaries', () => {
  it('accepts a public product page and keeps money as strings', () => {
    const decoded = page(publicProductSummary)({
      count: 1,
      page: 1,
      page_size: 25,
      results: [summary],
    });
    expect(decoded.results[0]?.price?.min_amount).toBe('1200.0000');
    expect(decoded.results[0]?.translations.bn?.name).toBe('অক্সফোর্ড শার্ট');
  });
  it.each([1250, '12,50', '1e3', ''])('rejects non-decimal money (%j)', (value) => {
    expect(() => decimal(value)).toThrow();
  });
  it('rejects media URLs that leave the API origin', () => {
    const image = { ...summary.image, url: 'https://tracker.invalid/pixel.png' };
    expect(() => publicProductSummary({ ...summary, image })).toThrow();
  });
  it('rejects unknown product types', () => {
    expect(() => publicProductSummary({ ...summary, product_type: 'weapon' })).toThrow();
  });
  it('decodes a product detail with variants and specifications', () => {
    const detail = publicProductDetail({
      ...summary,
      description: 'Cotton shirt',
      seo_title: '',
      seo_description: '',
      categories: [{ name: 'Shirts', slug: 'shirts', is_primary: true }],
      options: [{ code: 'size', name: 'Size', values: [{ code: 'm', value: 'M', swatch: '' }] }],
      variants: [
        {
          id: 'v1',
          sku: 'OX-M',
          title: 'M',
          options: [{ attribute: 'size', option: 'm', value: 'M' }],
          kind: 'physical',
          digital_format: '',
          net_quantity: null,
          net_unit: '',
          release_date: null,
          price: { currency: 'BDT', amount: '1250.0000', compare_at_amount: '1400.0000' },
          in_stock: true,
        },
      ],
      specifications: [
        {
          attribute: 'material',
          name: 'Material',
          value_type: 'text',
          unit: '',
          value: 'Cotton',
          display: 'Cotton',
        },
      ],
      images: [summary.image],
      book: null,
    });
    expect(detail.variants[0]?.price?.compare_at_amount).toBe('1400.0000');
    expect(detail.specifications[0]?.display).toBe('Cotton');
  });
  it('decodes nested category trees', () => {
    const node = categoryNode({
      name: 'Men',
      slug: 'men',
      description: '',
      image_url: null,
      translations: {},
      children: [
        {
          name: 'Shirts',
          slug: 'shirts',
          description: '',
          image_url: null,
          translations: {},
          children: [],
        },
      ],
    });
    expect(node.children[0]?.slug).toBe('shirts');
  });
});

describe('inventory response boundaries', () => {
  const movement = {
    id: 'm1',
    operation_id: 'op1',
    occurred_at: '2026-10-26T10:00:00Z',
    movement_type: 'receipt',
    variant: 'v1',
    sku: 'OX-M',
    location: 'l1',
    location_code: 'MAIN',
    on_hand_delta: '5.000',
    reserved_delta: '0.000',
    on_hand_after: '5.000',
    reserved_after: '0.000',
    batch: null,
    serial: null,
    reversal_of: null,
    reason: 'PO 1',
  };
  it('decodes an idempotent operation replay', () => {
    const operation = inventoryOperation({
      operation_id: 'op1',
      replayed: true,
      movements: [movement],
    });
    expect(operation.replayed).toBe(true);
    expect(operation.movements[0]?.on_hand_after).toBe('5.000');
  });
  it('rejects unknown movement types', () => {
    expect(() =>
      inventoryOperation({
        operation_id: 'op1',
        replayed: false,
        movements: [{ ...movement, movement_type: 'teleport' }],
      }),
    ).toThrow();
  });
  it('rejects numeric quantities in stock levels', () => {
    const level = {
      id: 's1',
      variant: { id: 'v1', sku: 'OX-M', title: 'M', product: 'p1', product_name: 'Oxford Shirt' },
      location: { id: 'l1', code: 'MAIN', name: 'Main location' },
      on_hand: '5.000',
      reserved: '0.000',
      available: '5.000',
      low_stock_threshold: null,
      is_low: false,
      updated_at: '2026-10-26T10:00:00Z',
    };
    expect(stockLevel(level).available).toBe('5.000');
    expect(() => stockLevel({ ...level, available: 5 })).toThrow();
  });
});
