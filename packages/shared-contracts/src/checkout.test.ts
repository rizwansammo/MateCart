import { describe, expect, it } from 'vitest';

import { cart, checkout } from './checkout';

describe('checkout contracts', () => {
  it('decodes decimal strings without converting money to numbers', () => {
    const value = cart({
      id: 'cart-id',
      lines: [
        {
          id: 'line-id',
          variant: 'variant-id',
          product_slug: 'shirt',
          product_name: 'Shirt',
          variant_title: 'Blue',
          sku: 'SHIRT-BLUE',
          kind: 'physical',
          quantity: '2.000',
          unit_price: '100.0000',
          currency: 'BDT',
          subtotal: '200.0000',
          available_quantity: '4.000',
          issue: '',
        },
      ],
      subtotal: '200.0000',
      currency: 'BDT',
      item_count: 2,
      valid: true,
    });
    expect(value.subtotal).toBe('200.0000');
    expect(value.lines[0]?.quantity).toBe('2.000');
  });

  it('rejects an invented checkout status', () => {
    expect(() =>
      checkout({
        id: 'checkout-id',
        status: 'paid',
        expires_at: '2026-10-26T10:00:00Z',
        contact_email: '',
        contact_phone: '',
        recipient_name: '',
        address_line_1: '',
        address_line_2: '',
        city: '',
        region: '',
        postal_code: '',
        country: '',
        delivery_notes: '',
        coupon_code: '',
        requires_shipping: false,
        currency: 'BDT',
        item_subtotal: '0.0000',
        discount_total: '0.0000',
        shipping_total: '0.0000',
        tax_total: '0.0000',
        grand_total: '0.0000',
        lines: [],
        shipping_methods: [],
        delivery_slots: [],
        selected_shipping_method: null,
        selected_delivery_slot: null,
        order_creation_available: false,
      }),
    ).toThrow('Invalid response');
  });
});
