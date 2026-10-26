import type { Invoice, Order } from '@matecart/shared-contracts';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { InvoiceApp } from './invoice-app';
import { OrderApp } from './order-app';

const api = vi.hoisted(() => ({
  getOrder: vi.fn(),
  cancelOrder: vi.fn(),
  startPayment: vi.fn(),
  getInvoice: vi.fn(),
}));

vi.mock('@/lib/checkout-client', () => api);

const order: Order = {
  id: 'order-1',
  number: 'ORD-000042',
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
  recipient_name: 'Rina Akter',
  address_line_1: '12 Lake Road',
  address_line_2: '',
  city: 'Dhaka',
  region: '',
  postal_code: '',
  country: 'BD',
  delivery_notes: '',
  shipping_method_name: 'Standard delivery',
  delivery_slot_label: 'Evening',
  delivery_slot_starts_at: null,
  delivery_slot_ends_at: null,
  coupon_code: '',
  placed_at: '2026-10-26T10:00:00Z',
  cancelled_at: null,
  cancel_reason: '',
  items: [
    {
      id: 'item-1',
      product_name: 'Green tea',
      variant_title: '100 g',
      sku: 'TEA-100',
      kind: 'physical',
      quantity: '2.000',
      unit_price: '150.0000',
      line_total: '300.0000',
    },
  ],
  invoice_number: 'INV-000042',
  can_cancel: true,
  can_pay: false,
  shipment: null,
};

const invoice: Invoice = {
  number: 'INV-000042',
  issued_at: order.placed_at,
  order_number: order.number,
  order_placed_at: order.placed_at,
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
  seller: { name: 'Rahim Grocery', legal_name: '', email: 'hello@example.test', phone: '' },
  buyer: { name: 'Rina Akter', city: 'Dhaka', country: 'BD' },
  items: order.items,
};

describe('order confirmation and invoice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getOrder.mockResolvedValue(order);
    api.getInvoice.mockResolvedValue(invoice);
  });

  it('confirms a cash-on-delivery order without claiming payment', async () => {
    render(<OrderApp id="order-1" lang="en" />);
    expect(
      await screen.findByRole('heading', { name: 'Thank you, your order is placed' }),
    ).toBeInTheDocument();
    expect(screen.getByText('ORD-000042')).toBeInTheDocument();
    expect(screen.getByText(/Cash on delivery — pay when your order arrives/)).toBeInTheDocument();
    expect(screen.getByText('Not paid yet')).toBeInTheDocument();
    expect(screen.queryByText('Paid')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View invoice' })).toHaveAttribute(
      'href',
      '/orders/order-1/invoice',
    );
  });

  it('cancels after confirmation and shows the new state', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    api.cancelOrder.mockResolvedValue({ ...order, status: 'cancelled', can_cancel: false });
    render(<OrderApp id="order-1" lang="en" />);
    await user.click(await screen.findByRole('button', { name: 'Cancel order' }));
    await waitFor(() => expect(api.cancelOrder).toHaveBeenCalledWith('order-1'));
    expect(await screen.findByRole('heading', { name: 'Order cancelled' })).toBeInTheDocument();
  });

  it('shows a pending online payment with retry controls in Bangla', async () => {
    api.getOrder.mockResolvedValue({
      ...order,
      status: 'pending_payment',
      payment_method: 'online',
      payment_due_at: '2026-10-26T10:30:00Z',
      can_pay: true,
    });
    render(<OrderApp id="order-1" lang="bn" />);
    expect(
      await screen.findByRole('heading', { name: 'আপনার পেমেন্ট সম্পন্ন করুন' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'এখনই পরিশোধ করুন' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'পেমেন্টের অবস্থা দেখুন' })).toBeEnabled();
  });

  it('shows a failed payment honestly', async () => {
    api.getOrder.mockResolvedValue({
      ...order,
      status: 'cancelled',
      payment_method: 'online',
      payment_status: 'failed',
      can_cancel: false,
    });
    render(<OrderApp id="order-1" lang="en" />);
    expect(await screen.findByText('Payment not completed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pay now' })).not.toBeInTheDocument();
  });

  it('shows real shipment progress and a genuine courier link', async () => {
    api.getOrder.mockResolvedValue({
      ...order,
      shipment: {
        reference: 'SHP-000042',
        status: 'in_transit',
        courier_name: 'Example Courier',
        tracking_number: 'TRACK-42',
        tracking_url: 'https://courier.example/track/42',
        dispatched_at: '2026-10-26T11:00:00Z',
        delivered_at: null,
        failed_at: null,
        events: [
          { occurred_at: '2026-10-26T11:00:00Z', status: 'dispatched', attempt_number: null },
          { occurred_at: '2026-10-26T12:00:00Z', status: 'in_transit', attempt_number: null },
        ],
      },
    });
    render(<OrderApp id="order-1" lang="en" />);
    expect((await screen.findAllByText('In transit')).length).toBe(2);
    expect(screen.getByText(/TRACK-42/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Track with courier' })).toHaveAttribute(
      'href',
      'https://courier.example/track/42',
    );
  });

  it('renders an unpaid printable invoice with the amount due', async () => {
    render(<InvoiceApp id="order-1" lang="en" />);
    expect(await screen.findByRole('heading', { name: 'Invoice' })).toBeInTheDocument();
    expect(screen.getByText('INV-000042')).toBeInTheDocument();
    expect(screen.getByText('Unpaid')).toBeInTheDocument();
    expect(screen.getByText('Amount due')).toBeInTheDocument();
    expect(screen.queryByText('Payment received')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Print or save as PDF' })).toBeInTheDocument();
  });

  it('shows received and refunded amounts on a paid invoice', async () => {
    api.getInvoice.mockResolvedValue({
      ...invoice,
      payment_status: 'partially_refunded',
      amount_paid: '360.0000',
      amount_refunded: '60.0000',
    });
    render(<InvoiceApp id="order-1" lang="bn" />);
    expect(await screen.findByText('আংশিক ফেরত')).toBeInTheDocument();
    expect(screen.getByText('পরিশোধিত অর্থ')).toBeInTheDocument();
    expect(screen.queryByText('বকেয়া')).not.toBeInTheDocument();
  });
});
