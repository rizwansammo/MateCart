import type { StaffSession } from '@matecart/shared-contracts';
import { ApiError } from '@matecart/shared-ui';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { isManagePath, navigation } from '@/lib/manage-routes';
import { AssistedOrderForm } from './assisted-order';
import { OrderDetail } from './orders';
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
    await waitFor(() => expect(place).toBeEnabled());
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
    expect(first?.expected_totals).toEqual({
      currency: 'BDT',
      item_subtotal: '42000.0000',
      discount_total: '0.0000',
      shipping_total: '60.0000',
      tax_total: '0.0000',
      grand_total: '42060.0000',
    });
  });
});

const confirmedCod = {
  id: ORDER_ID,
  number: 'ORD-000042',
  status: 'confirmed',
  payment_method: 'cod',
  payment_status: 'unpaid',
  payment_due_at: null,
  currency: 'BDT',
  item_subtotal: '1200.0000',
  discount_total: '0.0000',
  shipping_total: '60.0000',
  tax_total: '0.0000',
  grand_total: '1260.0000',
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
  invoice_number: 'INV-000042',
  can_cancel: false,
  can_pay: false,
  shipment: null,
  events: [],
  refunds: [],
  source: 'phone',
  assisted: null,
  customer: null,
  payments: [],
  shipment_id: null,
  fraud: null,
  cases: [],
  allowed_transitions: ['processing', 'cancelled'],
  can_record_cod: true,
  refundable_amount: '0.0000',
};

describe('cash on delivery collection', () => {
  beforeEach(() => {
    api.get.mockResolvedValue(confirmedCod);
    api.post.mockImplementation((path: string) =>
      Promise.resolve(
        path.endsWith('/cod-collection/')
          ? {
              ...confirmedCod,
              payment_status: 'paid',
              amount_paid: '1260.0000',
              can_record_cod: false,
            }
          : {},
      ),
    );
  });

  it('records cash only after an explicit, password-confirmed statement', async () => {
    const user = userEvent.setup();
    render(<OrderDetail id={ORDER_ID} session={session(['orders.manage'])} />);

    await user.click(await screen.findByRole('button', { name: 'Record cash received…' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('ORD-000042');
    expect(dialog).toHaveTextContent('৳1,260');
    expect(dialog).toHaveTextContent('(BDT)');
    expect(dialog).toHaveTextContent(/actually received this money/);
    expect(api.post).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Record cash received…' }));
    await user.type(screen.getByLabelText('Your password'), 'staff-passphrase');
    await user.click(screen.getByRole('button', { name: 'Record cash received' }));

    expect(await screen.findByText('Cash collection recorded.')).toBeInTheDocument();
    expect(api.post.mock.calls.map(([path]) => path)).toEqual([
      '/api/manage/account/reauthenticate/',
      `/api/manage/orders/${ORDER_ID}/cod-collection/`,
    ]);
    expect(screen.queryByRole('button', { name: 'Record cash received…' })).not.toBeInTheDocument();
  });
});

interface Deferred {
  body: { items: { quantity: string }[]; shipping_method: string | null };
  resolve: (value: unknown) => void;
}

/** A server quote priced from the request: `unit` per item plus 60 for a chosen method. */
function priced(body: Deferred['body'], unit = 42000) {
  const items = unit * (Number(body.items[0]?.quantity) || 0);
  const delivery = body.shipping_method ? 60 : 0;
  const amount = (value: number) => `${value}.0000`;
  return {
    ...quote,
    totals: {
      ...quote.totals,
      item_subtotal: amount(items),
      shipping_total: amount(delivery),
      grand_total: amount(items + delivery),
      shipping_method: body.shipping_method,
    },
  };
}

describe('manual order quote consistency', () => {
  let unit = 42000;
  let held: Deferred[] = [];
  let holdQuantity = '';
  let placement: () => Promise<unknown> = () => Promise.reject(new Error('Not now.'));

  beforeEach(() => {
    unit = 42000;
    held = [];
    holdQuantity = '';
    placement = () => Promise.reject(new Error('Not now.'));
    api.get.mockResolvedValue([variant]);
    api.post.mockImplementation((path: string, raw: unknown) => {
      if (!path.endsWith('/quote/')) return placement();
      const body = raw as Deferred['body'];
      if (body.items[0]?.quantity === holdQuantity) {
        return new Promise((resolve) => held.push({ body, resolve }));
      }
      return Promise.resolve(priced(body, unit));
    });
  });

  async function ready() {
    const user = userEvent.setup();
    render(<AssistedOrderForm session={session(['orders.manage'])} />);
    await user.click(await screen.findByRole('button', { name: 'Add' }));
    await user.type(screen.getByLabelText('Customer name'), 'Rahim');
    await user.type(screen.getByLabelText('Phone number'), '01712345678');
    await user.type(screen.getByLabelText('Address'), 'House 7');
    await user.type(screen.getByLabelText('City'), 'Dhaka');
    await user.click(screen.getByRole('checkbox'));
    const place = screen.getByRole('button', { name: 'Place order' });
    await waitFor(() => expect(place).toBeEnabled());
    expect(screen.getByText('৳42,060')).toBeInTheDocument();
    return { user, place, quantity: screen.getByLabelText('Quantity') };
  }

  function quoteCalls(quantity: string) {
    return api.post.mock.calls.filter(
      ([path, body]) =>
        path.endsWith('/quote/') && (body as Deferred['body']).items[0]?.quantity === quantity,
    ).length;
  }

  it('drops the shown total and blocks placement as soon as a price input changes', async () => {
    const { user, place, quantity } = await ready();

    await user.clear(quantity);
    await user.type(quantity, '2');

    // Before any new answer: the old total is gone and the order cannot be placed.
    expect(screen.queryByText('৳42,060')).not.toBeInTheDocument();
    expect(screen.getByText(/Calculating the total/)).toBeInTheDocument();
    expect(place).toBeDisabled();

    expect(await screen.findByText('৳84,060')).toBeInTheDocument();
    await waitFor(() => expect(place).toBeEnabled());
  });

  it('never restores a total from an older, slower answer', async () => {
    holdQuantity = '3';
    const { user, place, quantity } = await ready();

    await user.clear(quantity);
    await user.type(quantity, '3');
    await waitFor(() => expect(quoteCalls('3')).toBe(1));
    await user.clear(quantity);
    await user.type(quantity, '2');
    expect(await screen.findByText('৳84,060')).toBeInTheDocument();

    // The answer for 3 arrives last; it belongs to inputs that are no longer on the form.
    const late = held[0];
    expect(late).toBeDefined();
    late?.resolve(priced(late.body));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(screen.queryByText('৳126,060')).not.toBeInTheDocument();
    expect(screen.getByText('৳84,060')).toBeInTheDocument();
    expect(place).toBeEnabled();
    await user.click(place);
    const submitted = api.post.mock.calls.find(([path]) => path === '/api/manage/orders/assisted/');
    expect(submitted?.[1]).toMatchObject({
      items: [{ variant: 'v1', quantity: '2' }],
      expected_totals: { grand_total: '84060.0000' },
    });
  });

  it('makes the merchant review a total that changed during placement', async () => {
    const { user, place } = await ready();
    unit = 43000;
    placement = () =>
      Promise.reject(
        new ApiError(
          409,
          'quote_changed',
          'The total for this order changed after it was calculated. Review the new total and place the order again.',
        ),
      );

    await user.click(place);

    expect(await screen.findByText(/changed after it was calculated/)).toBeInTheDocument();
    expect(await screen.findByText('৳43,060')).toBeInTheDocument();
    expect(screen.queryByText('৳42,060')).not.toBeInTheDocument();
    await waitFor(() => expect(place).toBeEnabled());

    placement = () => Promise.reject(new Error('Not now.'));
    await user.click(place);

    const submissions = api.post.mock.calls
      .filter(([path]) => path === '/api/manage/orders/assisted/')
      .map(([, body]) => body as Record<string, { grand_total?: string } | string>);
    expect(submissions).toHaveLength(2);
    expect(submissions[0]?.expected_totals).toMatchObject({ grand_total: '42060.0000' });
    expect(submissions[1]?.expected_totals).toMatchObject({ grand_total: '43060.0000' });
    expect(submissions[0]?.idempotency_key).toBe(submissions[1]?.idempotency_key);
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
