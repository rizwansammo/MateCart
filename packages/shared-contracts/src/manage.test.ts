import { describe, expect, it } from 'vitest';

import {
  ASSISTED_SOURCES,
  assistedQuote,
  dashboard,
  managePage,
  managedProduct,
  merchantCase,
  merchantOrderDetail,
  merchantOrderSummary,
  merchantShipment,
  salesReport,
} from './manage';

const order = {
  id: 'order-1',
  number: 'ORD-000001',
  status: 'placed',
  payment_method: 'cod',
  payment_status: 'unpaid',
  payment_due_at: null,
  currency: 'BDT',
  item_subtotal: '300.0000',
  discount_total: '0.0000',
  shipping_total: '60.0000',
  tax_total: '0.0000',
  grand_total: '360.0000',
  amount_paid: '0.0000',
  amount_refunded: '0.0000',
  contact_email: '',
  contact_phone: '+8801712345678',
  requires_shipping: true,
  recipient_name: 'Rahim',
  address_line_1: '1 Road',
  address_line_2: '',
  city: 'Dhaka',
  region: '',
  postal_code: '',
  country: 'BD',
  delivery_notes: '',
  shipping_method_name: 'Inside Dhaka',
  delivery_slot_label: '',
  delivery_slot_starts_at: null,
  delivery_slot_ends_at: null,
  coupon_code: '',
  placed_at: '2026-11-03T10:00:00Z',
  cancelled_at: null,
  cancel_reason: '',
  items: [],
  invoice_number: 'INV-000001',
  can_cancel: true,
  can_pay: false,
  shipment: null,
  events: [],
  refunds: [],
  source: 'facebook',
  assisted: {
    created_by_name: 'Nadia',
    created_at: '2026-11-03T10:00:00Z',
    source_reference: 'Page chat',
    customer_request_attested: true,
  },
  customer: null,
  payments: [],
  shipment_id: null,
  fraud: null,
  cases: [],
  allowed_transitions: ['confirmed', 'cancelled'],
  can_record_cod: false,
  refundable_amount: '0.0000',
};

describe('merchant dashboard contracts', () => {
  it('keeps money as decimal strings and permission-gated sections as null', () => {
    const value = dashboard({
      start: '2026-10-05',
      end: '2026-11-03',
      orders: null,
      operations: null,
      aftersales: null,
      recent_orders: null,
      finance: {
        totals: [
          {
            currency: 'BDT',
            order_value: '3000.0000',
            collected: '0.0000',
            refunded: '0.0000',
            net_collected: '0.0000',
          },
        ],
        cash: {
          cod: [
            {
              currency: 'BDT',
              awaiting_collection: '3000.0000',
              courier_remittance_pending: '0.0000',
              courier_remittance_disputed: '0.0000',
            },
          ],
          awaiting_collection_orders: 2,
          courier_records_pending: 0,
          courier_records_disputed: 0,
        },
      },
      inventory: null,
    });
    expect(value.orders).toBeNull();
    expect(value.finance?.totals[0]?.order_value).toBe('3000.0000');
    expect(value.finance?.cash.cod[0]?.awaiting_collection).toBe('3000.0000');
  });

  it('keeps physical and sellable stock apart in stock alerts', () => {
    const value = dashboard({
      start: '2026-11-03',
      end: '2026-11-03',
      orders: null,
      operations: null,
      aftersales: null,
      recent_orders: null,
      finance: null,
      inventory: {
        low_stock: 1,
        out_of_stock: 1,
        expiring_batches: 0,
        low_stock_items: [
          {
            id: 'level-1',
            variant: 'v1',
            sku: 'MILK-1',
            product_name: 'Milk',
            variant_title: '',
            location_code: 'MAIN',
            on_hand: '5.000',
            available: '5.000',
            sellable: '0.000',
            low_stock_threshold: '2.000',
          },
        ],
      },
    });
    expect(value.inventory?.low_stock_items[0]).toMatchObject({
      on_hand: '5.000',
      sellable: '0.000',
    });
    expect(() =>
      dashboard({
        start: '2026-11-03',
        end: '2026-11-03',
        orders: null,
        operations: null,
        aftersales: null,
        recent_orders: null,
        finance: null,
        inventory: {
          low_stock: 0,
          out_of_stock: 0,
          expiring_batches: 0,
          low_stock_items: [{ id: 'level-1', sellable: 0 }],
        },
      }),
    ).toThrow();
  });

  it('rejects floating-point money and fractional counts', () => {
    const report = {
      start: '2026-11-01',
      end: '2026-11-03',
      summary: [{ currency: 'BDT', order_value: 12.5, average_order_value: '1.0000' }],
      order_counts: [],
      totals: [],
      cash: {
        cod: [],
        awaiting_collection_orders: 0,
        courier_records_pending: 0,
        courier_records_disputed: 0,
      },
      by_status: [],
      by_payment_method: [],
      by_source: [],
      daily: [],
      top_products: [],
      aftersales: [],
      inventory: { low_stock: 0, out_of_stock: 0 },
    };
    expect(() => salesReport(report)).toThrow();
    expect(() =>
      salesReport({ ...report, summary: [], inventory: { low_stock: 1.5, out_of_stock: 0 } }),
    ).toThrow();
    expect(salesReport({ ...report, summary: [] }).inventory.low_stock).toBe(0);
  });
});

describe('merchant order contracts', () => {
  it('decodes assisted provenance and the allowed actions', () => {
    const detail = merchantOrderDetail(order);
    expect(detail.source).toBe('facebook');
    expect(detail.assisted?.customer_request_attested).toBe(true);
    expect(detail.allowed_transitions).toEqual(['confirmed', 'cancelled']);
    // Phase 14 added Instagram and website chat sources; anything else is still refused.
    expect(merchantOrderDetail({ ...order, source: 'instagram' }).source).toBe('instagram');
    expect(merchantOrderDetail({ ...order, source: 'live_chat' }).source).toBe('live_chat');
    expect(() => merchantOrderDetail({ ...order, source: 'telegram' })).toThrow();
  });

  it('pages merchant order summaries', () => {
    const page = managePage(merchantOrderSummary)({
      count: 1,
      page: 1,
      page_size: 25,
      results: [
        {
          id: 'order-1',
          number: 'ORD-000001',
          status: 'placed',
          payment_method: 'cod',
          payment_status: 'unpaid',
          currency: 'BDT',
          grand_total: '360.0000',
          placed_at: '2026-11-03T10:00:00Z',
          source: 'storefront',
          recipient_name: 'Rahim',
          contact_phone: '',
          item_count: 2,
          shipment_status: null,
        },
      ],
    });
    expect(page.results[0]?.item_count).toBe(2);
  });

  it('offers only staff-assisted sources for manual orders', () => {
    expect(ASSISTED_SOURCES).not.toContain('storefront');
    expect(ASSISTED_SOURCES).toContain('whatsapp');
  });

  it('decodes a server quote without computing totals', () => {
    const quote = assistedQuote({
      valid: false,
      lines: [
        {
          variant: 'v1',
          sku: 'A56',
          product_name: 'Galaxy A56',
          variant_title: '',
          kind: 'physical',
          quantity: '3.000',
          unit_price: '42000.0000',
          currency: 'BDT',
          subtotal: '126000.0000',
          available_quantity: '1.000',
          issue: 'insufficient_stock',
        },
      ],
      totals: null,
      shipping_methods: [],
      delivery_slots: [],
      payment_methods: [],
    });
    expect(quote.totals).toBeNull();
    expect(quote.lines[0]?.issue).toBe('insufficient_stock');
  });
});

describe('fulfillment, after-sales and catalogue contracts', () => {
  it('refuses non-HTTPS tracking links', () => {
    const shipment = {
      id: 's1',
      order_id: 'order-1',
      order_number: 'ORD-000001',
      reference: 'SHP-000001',
      status: 'packed',
      courier_name: 'Manual',
      tracking_number: '',
      tracking_url: '',
      dispatched_at: null,
      delivered_at: null,
      failed_at: null,
      operational_notes: '',
      events: [],
      items: [],
      bookings: [],
      cod: null,
      fraud: null,
      payment_method: 'cod',
      next_statuses: ['ready_for_dispatch'],
      serial_allocations: [],
      courier_booking_available: false,
    };
    expect(merchantShipment(shipment).next_statuses).toEqual(['ready_for_dispatch']);
    expect(() => merchantShipment({ ...shipment, tracking_url: 'http://track.example' })).toThrow();
  });

  it('decodes a case with its valid next steps and refund evidence', () => {
    const value = merchantCase({
      id: 'c1',
      kind: 'return',
      status: 'awaiting_refund',
      order_item: 'i1',
      quantity: '1.000',
      subject: 'Wrong size',
      reason: 'Too small',
      decision_reason: '',
      refund: null,
      replacement_order: null,
      created_at: '2026-11-03T10:00:00Z',
      updated_at: '2026-11-03T10:00:00Z',
      messages: [],
      order_id: 'order-1',
      order_number: 'ORD-000001',
      product_name: 'Shirt',
      sku: 'SHIRT-M',
      currency: 'BDT',
      customer_name: 'Rina',
      next_statuses: ['resolved'],
      required_refund: '500.0000',
      attributed_refunds: [{ id: 'r1', amount: '500.0000', currency: 'BDT', status: 'succeeded' }],
    });
    expect(value.next_statuses).toEqual(['resolved']);
    expect(value.attributed_refunds[0]?.status).toBe('succeeded');
  });

  it('accepts only same-origin product media', () => {
    const product = {
      id: 'p1',
      name: 'Tee',
      slug: 'tee',
      product_type: 'apparel',
      status: 'draft',
      brand: null,
      summary: '',
      description: '',
      tags: [],
      seo_title: '',
      seo_description: '',
      track_inventory: true,
      tracking_mode: 'none',
      translations: {},
      published_at: null,
      archived_at: null,
      categories: [],
      options: [],
      variants: [],
      specifications: [],
      media: [
        {
          asset: 'a1',
          variant: null,
          position: 0,
          is_primary: true,
          alt_text: '',
          width: 4,
          height: 3,
          url: '/api/catalog/media/00000000-0000-0000-0000-000000000001/',
        },
      ],
      apparel: {
        audience: 'women',
        material: 'Cotton',
        fit: '',
        care_instructions: '',
        size_guide: null,
        size_guide_notes: '',
      },
      grocery: null,
      electronics: null,
      book: null,
      publication_problems: ['Add a price.'],
      created_at: '2026-11-03T10:00:00Z',
      updated_at: '2026-11-03T10:00:00Z',
    };
    expect(managedProduct(product).apparel?.material).toBe('Cotton');
    expect(() =>
      managedProduct({
        ...product,
        media: [{ ...product.media[0], url: 'https://evil.example/x.png' }],
      }),
    ).toThrow();
  });
});
