import type { StaffSession } from '@matecart/shared-contracts';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { isManagePath, navigation } from '@/lib/manage-routes';
import { AssistedOrderForm } from './assisted-order';
import { Overview } from './overview';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  upload: vi.fn(),
}));

vi.mock('@/lib/auth-client', () => ({ authApi: api }));

const ORDER_ID = '0b0f6d3e-1f6c-4a8e-9c3d-2a1b0c9d8e7f';

function session(permissions: string[]): StaffSession {
  return {
    user: { id: 'u1', full_name: 'Nadia', email: 'nadia@example.test' },
    role: 'order_handler',
    role_label: 'Order handler',
    permissions,
    store_name: 'Rahim Electronics',
    store_preset: 'electronics',
    store_country: 'BD',
    store_currency: 'BDT',
    store_timezone: 'Asia/Dhaka',
  };
}

function links(permissions: string[]): string[] {
  return navigation(permissions).flatMap((group) => group.links.map((link) => link.label));
}

describe('merchant navigation', () => {
  it('shows each role only the sections it may open', () => {
    const handler = links([
      'orders.manage',
      'couriers.manage',
      'messages.manage',
      'customers.view',
    ]);
    expect(handler).toEqual(
      expect.arrayContaining(['Orders', 'Customers', 'Shipments', 'Customer requests', 'Settings']),
    );
    expect(handler).not.toContain('Products');
    expect(handler).not.toContain('Reports');
    expect(handler).not.toContain('Team & access');

    const editor = links(['catalog.manage', 'content.manage']);
    expect(editor).toEqual(
      expect.arrayContaining(['Products', 'Catalogue', 'Inventory', 'Appearance']),
    );
    expect(editor).not.toContain('Orders');
    expect(editor).not.toContain('Customers');
  });

  it('accepts only known merchant paths', () => {
    expect(isManagePath('')).toBe(true);
    expect(isManagePath('orders/new')).toBe(true);
    expect(isManagePath(`orders/${ORDER_ID}`)).toBe(true);
    expect(isManagePath(`orders/${ORDER_ID}/invoice`)).toBe(true);
    expect(isManagePath('orders/not-a-uuid')).toBe(false);
    expect(isManagePath('marketing')).toBe(false);
    expect(isManagePath('sign-in')).toBe(true);
  });
});

const variant = {
  id: 'v1',
  sku: 'A56-128',
  product_name: 'Samsung Galaxy A56',
  variant_title: '128 GB',
  kind: 'physical',
  inventory_unit: 'each',
  unit_price: '42000.0000',
  currency: 'BDT',
  available_quantity: '3.000',
};

const quote = {
  valid: true,
  lines: [
    {
      variant: 'v1',
      sku: 'A56-128',
      product_name: 'Samsung Galaxy A56',
      variant_title: '128 GB',
      kind: 'physical',
      quantity: '1.000',
      unit_price: '42000.0000',
      currency: 'BDT',
      subtotal: '42000.0000',
      available_quantity: '3.000',
      issue: '',
    },
  ],
  totals: {
    currency: 'BDT',
    requires_shipping: true,
    item_subtotal: '42000.0000',
    discount_total: '0.0000',
    shipping_total: '60.0000',
    tax_total: '0.0000',
    grand_total: '42060.0000',
    coupon_code: '',
    shipping_method: null,
    delivery_slot: null,
  },
  shipping_methods: [
    {
      id: 'm1',
      name: 'Inside Dhaka',
      description: '',
      amount: '60.0000',
      currency: 'BDT',
      free_above: null,
    },
  ],
  delivery_slots: [],
  payment_methods: ['cod'],
};

describe('manual order creation', () => {
  beforeEach(() => {
    api.get.mockResolvedValue([variant]);
    api.post.mockImplementation((path: string) =>
      path.endsWith('/quote/')
        ? Promise.resolve(quote)
        : Promise.reject(
            Object.assign(new Error('Only 0 is currently available.'), { status: 409 }),
          ),
    );
  });

  it('shows server totals, needs attestation and reuses one key on retry', async () => {
    const user = userEvent.setup();
    render(<AssistedOrderForm session={session(['orders.manage'])} />);

    await user.click(await screen.findByRole('button', { name: 'Add' }));
    expect(await screen.findByText('৳42,060')).toBeInTheDocument();
    expect(screen.getByText(/stays unpaid until the cash is collected/)).toBeInTheDocument();

    await user.type(screen.getByLabelText('Customer name'), 'Rahim');
    await user.type(screen.getByLabelText('Phone number'), '01712345678');
    await user.type(screen.getByLabelText('Address'), 'House 7');
    await user.type(screen.getByLabelText('City'), 'Dhaka');
    const place = screen.getByRole('button', { name: 'Place order' });
    await waitFor(() => expect(screen.getByLabelText('Delivery method')).toHaveValue('m1'));
    expect(place).toBeDisabled();

    await user.click(screen.getByRole('checkbox'));
    expect(place).toBeEnabled();
    await user.click(place);
    await screen.findByRole('alert');
    await user.click(place);

    const submissions = api.post.mock.calls.filter(
      ([path]) => path === '/api/manage/orders/assisted/',
    );
    expect(submissions).toHaveLength(2);
    const [first, second] = submissions.map(([, body]) => body as Record<string, unknown>);
    expect(first?.idempotency_key).toBe(second?.idempotency_key);
    expect(first).toMatchObject({
      payment_method: 'cod',
      source: 'phone',
      customer_request_attested: true,
      shipping_method: 'm1',
      country: 'BD',
    });
    expect(first).not.toHaveProperty('grand_total');
    expect(first).not.toHaveProperty('unit_price');
  });
});

describe('overview', () => {
  it('shows honest empty states instead of figures', async () => {
    api.get.mockResolvedValue({
      start: '2026-10-05',
      end: '2026-11-03',
      orders: {
        total: 0,
        placed: 0,
        pending_payment: 0,
        confirmed: 0,
        processing: 0,
        cancelled: 0,
        delivered: 0,
      },
      operations: {
        awaiting_confirmation: 0,
        to_prepare: 0,
        pending_shipments: 0,
        in_transit: 0,
        delivery_failed: 0,
        fraud_review: 0,
      },
      aftersales: {
        open_returns: 0,
        open_exchanges: 0,
        open_warranty: 0,
        open_support: 0,
        pending_reviews: 0,
      },
      recent_orders: [],
      finance: {
        totals: [],
        cash: {
          cod: [],
          awaiting_collection_orders: 0,
          courier_records_pending: 0,
          courier_records_disputed: 0,
        },
      },
      inventory: null,
    });
    render(<Overview session={session(['orders.manage', 'reports.view'])} />);
    expect(await screen.findByText(/No sales in this period yet/)).toBeInTheDocument();
    expect(screen.getByText('No orders yet')).toBeInTheDocument();
    expect(screen.queryByText('Stock alerts')).not.toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith('/api/manage/dashboard/?days=30');
  });

  it('hides money from roles without reports access', async () => {
    api.get.mockResolvedValue({
      start: '2026-10-05',
      end: '2026-11-03',
      orders: null,
      operations: null,
      aftersales: null,
      recent_orders: null,
      finance: null,
      inventory: { low_stock: 0, out_of_stock: 0, expiring_batches: 0, low_stock_items: [] },
    });
    render(<Overview session={session(['catalog.manage', 'content.manage'])} />);
    expect(await screen.findByText('Stock alerts')).toBeInTheDocument();
    expect(screen.queryByText('Order value')).not.toBeInTheDocument();
    expect(screen.queryByText('Create manual order')).not.toBeInTheDocument();
  });
});
