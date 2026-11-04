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
          regular_unit_price: '120.0000',
          promotion_kind: 'campaign',
          promotion_name: 'Winter',
        },
      ],
      subtotal: '200.0000',
      currency: 'BDT',
      item_count: 2,
      valid: true,
    });
    expect(value.subtotal).toBe('200.0000');
    expect(value.lines[0]?.quantity).toBe('2.000');
    expect(value.lines[0]?.regular_unit_price).toBe('120.0000');
  });

  it('decodes loyalty redemption and promotional lines (Phase 12)', () => {
    const base = checkoutBody();
    const value = checkout({
      ...base,
      status: 'draft',
      loyalty_points: 200,
      loyalty_discount: '200.0000',
      loyalty: { balance: 500, point_value: '1.0000', min_redeem_points: 100, max_points: 300 },
      lines: [
        {
          variant: 'v',
          product_name: 'Shirt',
          variant_title: '',
          sku: 'S',
          kind: 'physical',
          quantity: '1.000',
          unit_price: '90.0000',
          line_subtotal: '90.0000',
          regular_unit_price: '100.0000',
          promotion_kind: 'flash_sale',
          promotion_name: 'Flash',
          coupon_eligible: false,
        },
      ],
    });
    expect(value.loyalty?.max_points).toBe(300);
    expect(value.lines[0]?.coupon_eligible).toBe(false);
    expect(() => checkout({ ...base, status: 'draft', loyalty_discount: 200 })).toThrow();
  });

  it('rejects an invented checkout status', () => {
    expect(() => checkout({ ...checkoutBody(), status: 'paid' })).toThrow('Invalid response');
  });
});

function checkoutBody(): Record<string, unknown> {
  return {
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
    payment_methods: [],
    order_creation_available: false,
    order: null,
    loyalty_points: 0,
    loyalty_discount: '0.0000',
    loyalty: null,
  };
}
