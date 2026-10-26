import { describe, expect, it } from 'vitest';

import { invoice, order, paymentStart } from './orders';

const base = {
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
  contact_email: 'buyer@example.test',
  contact_phone: '',
  requires_shipping: true,
  recipient_name: 'Buyer',
  address_line_1: '1 Road',
  address_line_2: '',
  city: 'Dhaka',
  region: '',
  postal_code: '',
  country: 'BD',
  delivery_notes: '',
  shipping_method_name: 'Standard delivery',
  delivery_slot_label: '',
  delivery_slot_starts_at: null,
  delivery_slot_ends_at: null,
  coupon_code: '',
  placed_at: '2026-10-26T10:00:00Z',
  cancelled_at: null,
  cancel_reason: '',
  items: [
    {
      id: 'item-1',
      product_name: 'Tea',
      variant_title: '',
      sku: 'TEA',
      kind: 'physical',
      quantity: '2.000',
      unit_price: '150.0000',
      line_total: '300.0000',
    },
  ],
  invoice_number: 'INV-000001',
  can_cancel: true,
  can_pay: false,
  shipment: null,
};

describe('order contracts', () => {
  it('keeps money as decimal strings', () => {
    const value = order(base);
    expect(value.grand_total).toBe('360.0000');
    expect(value.items[0]?.line_total).toBe('300.0000');
  });

  it('rejects an invented order or payment status', () => {
    expect(() => order({ ...base, status: 'shipped' })).toThrow('Invalid response');
    expect(() => order({ ...base, payment_status: 'pending' })).toThrow('Invalid response');
    expect(() => order({ ...base, payment_method: 'emi' })).toThrow('Invalid response');
  });

  it('accepts only https payment redirects', () => {
    expect(paymentStart({ redirect_url: 'https://pay.example/1' }).redirect_url).toBe(
      'https://pay.example/1',
    );
    expect(() => paymentStart({ redirect_url: 'javascript:alert(1)' })).toThrow();
  });

  it('decodes truthful shipment tracking and rejects unsafe links', () => {
    const shipped = order({
      ...base,
      shipment: {
        reference: 'SHP-000001',
        status: 'in_transit',
        courier_name: 'Courier',
        tracking_number: 'TRACK-1',
        tracking_url: 'https://courier.example/track/1',
        dispatched_at: base.placed_at,
        delivered_at: null,
        failed_at: null,
        events: [{ occurred_at: base.placed_at, status: 'in_transit', attempt_number: null }],
      },
    });
    expect(shipped.shipment?.status).toBe('in_transit');
    expect(() =>
      order({
        ...base,
        shipment: {
          ...shipped.shipment,
          tracking_url: 'javascript:alert(1)',
        },
      }),
    ).toThrow();
  });

  it('decodes an invoice snapshot', () => {
    const value = invoice({
      number: 'INV-000001',
      issued_at: base.placed_at,
      order_number: base.number,
      order_placed_at: base.placed_at,
      currency: 'BDT',
      item_subtotal: '300.0000',
      discount_total: '0.0000',
      shipping_total: '60.0000',
      tax_total: '0.0000',
      grand_total: '360.0000',
      payment_method: 'cod',
      payment_status: 'unpaid',
      amount_paid: '0.0000',
      amount_refunded: '0.0000',
      order_cancelled: false,
      seller: { name: 'Store', legal_name: '', email: '', phone: '', country: 'BD' },
      buyer: { name: 'Buyer', city: 'Dhaka' },
      items: base.items,
    });
    expect(value.seller.name).toBe('Store');
    expect(value.amount_paid).toBe('0.0000');
  });
});
