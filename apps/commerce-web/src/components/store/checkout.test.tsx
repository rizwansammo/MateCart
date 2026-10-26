import type { Cart, Checkout } from '@matecart/shared-contracts';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CartApp } from './cart-app';
import { CartLink } from './cart-link';
import { CheckoutApp } from './checkout-app';

const push = vi.fn();
const api = vi.hoisted(() => ({
  getCart: vi.fn(),
  updateCartItem: vi.fn(),
  removeCartItem: vi.fn(),
  createCheckout: vi.fn(),
  getCheckout: vi.fn(),
  updateCheckout: vi.fn(),
  reviewCheckout: vi.fn(),
  CART_UPDATED_EVENT: 'matecart:cart-updated',
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/checkout-client', () => api);

const cart: Cart = {
  id: 'cart-1',
  lines: [
    {
      id: 'line-1',
      variant: 'variant-1',
      product_slug: 'tea',
      product_name: 'Green tea',
      variant_title: '100 g',
      sku: 'TEA-100',
      kind: 'physical',
      quantity: '2.000',
      unit_price: '150.0000',
      currency: 'BDT',
      subtotal: '300.0000',
      available_quantity: '9.000',
      issue: '',
    },
  ],
  subtotal: '300.0000',
  currency: 'BDT',
  item_count: 2,
  valid: true,
};

const checkout: Checkout = {
  id: 'checkout-1',
  status: 'draft',
  expires_at: '2026-10-26T12:30:00Z',
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
  requires_shipping: true,
  currency: 'BDT',
  item_subtotal: '300.0000',
  discount_total: '0.0000',
  shipping_total: '0.0000',
  tax_total: '0.0000',
  grand_total: '300.0000',
  lines: [
    {
      variant: 'variant-1',
      product_name: 'Green tea',
      variant_title: '100 g',
      sku: 'TEA-100',
      kind: 'physical',
      quantity: '2.000',
      unit_price: '150.0000',
      line_subtotal: '300.0000',
    },
  ],
  shipping_methods: [],
  delivery_slots: [],
  selected_shipping_method: null,
  selected_delivery_slot: null,
  order_creation_available: false,
};

describe('cart and checkout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getCart.mockResolvedValue(cart);
    api.createCheckout.mockResolvedValue(checkout);
    api.getCheckout.mockResolvedValue(checkout);
    api.updateCheckout.mockResolvedValue(checkout);
  });

  it('shows authoritative cart totals and starts checkout', async () => {
    const user = userEvent.setup();
    render(<CartApp lang="en" />);
    expect(await screen.findByText('Green tea')).toBeInTheDocument();
    expect(screen.getAllByText(/300/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Continue to checkout' }));
    await waitFor(() => expect(api.createCheckout).toHaveBeenCalledOnce());
    expect(push).toHaveBeenCalledWith('/checkout/checkout-1');
  });

  it('shows the persistent cart count in storefront navigation', async () => {
    render(<CartLink label="Cart" compact />);
    expect(await screen.findByRole('link', { name: 'Cart: 2' })).toBeInTheDocument();
  });

  it('updates cart quantities and removes lines', async () => {
    const user = userEvent.setup();
    api.updateCartItem.mockResolvedValue({ ...cart, item_count: 3 });
    api.removeCartItem.mockResolvedValue({ ...cart, lines: [], item_count: 0 });
    render(<CartApp lang="en" />);
    const quantity = await screen.findByRole('spinbutton', { name: 'Quantity: Green tea' });
    await user.clear(quantity);
    await user.type(quantity, '3');
    await user.tab();
    await waitFor(() => expect(api.updateCartItem).toHaveBeenCalledWith('line-1', '3'));
    await user.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(api.removeCartItem).toHaveBeenCalledWith('line-1'));
  });

  it('renders the shipping form and server totals in Bangla', async () => {
    render(<CheckoutApp id="checkout-1" lang="bn" />);
    expect(await screen.findByRole('heading', { name: 'চেকআউট' })).toBeInTheDocument();
    expect(screen.getByText('ডেলিভারি ঠিকানা')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'যাচাই করে স্টক সংরক্ষণ করুন' })).toBeEnabled();
    expect(screen.getAllByText('৳300').length).toBeGreaterThan(0);
  });

  it('states the Phase 08 boundary after review without claiming an order', async () => {
    const user = userEvent.setup();
    const reviewed = { ...checkout, status: 'reviewed' as const };
    api.updateCheckout.mockResolvedValue(checkout);
    api.reviewCheckout.mockResolvedValue(reviewed);
    render(<CheckoutApp id="checkout-1" lang="en" />);
    await screen.findByRole('heading', { name: 'Checkout' });
    await user.click(screen.getByRole('button', { name: 'Review and reserve stock' }));
    expect(await screen.findByText(/No order or payment has been created/)).toBeInTheDocument();
    expect(api.reviewCheckout).toHaveBeenCalledWith('checkout-1', expect.any(String));
  });
});
