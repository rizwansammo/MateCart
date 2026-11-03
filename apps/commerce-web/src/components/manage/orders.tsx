'use client';

import { useCallback, useState } from 'react';
import type { MerchantOrderDetail, StaffSession } from '@matecart/shared-contracts';
import {
  ORDER_SOURCES,
  invoice as decodeInvoice,
  fraudAssessment,
  managePage,
  merchantOrderDetail,
  merchantOrderSummary,
} from '@matecart/shared-contracts';
import { Button, ConfirmDialog, TextField, errorMessage } from '@matecart/shared-ui';
import { toUnits } from '@/lib/money';
import { TextArea } from '../appearance-app';
import {
  CASE_KIND,
  CASE_STATUS,
  Card,
  Cell,
  Detail,
  Empty,
  FRAUD_STATUS,
  Feedback,
  Filters,
  Loader,
  ORDER_STATUS,
  PAYMENT_METHOD,
  PAYMENT_STATUS,
  PageHeader,
  Pagination,
  ReauthDialog,
  SHIPMENT_STATUS,
  SOURCE,
  SelectField,
  StatusBadge,
  Table,
  api,
  date,
  dateTime,
  label,
  money,
  newKey,
  quantity,
  query,
  useLoad,
} from './kit';

const PAGE_SIZE = 25;

function initialFilter(name: string): string {
  if (typeof window === 'undefined') return '';
  return new URLSearchParams(window.location.search).get(name) ?? '';
}

function options(map: Record<string, string>, all: string) {
  return [
    { value: '', label: all },
    ...Object.entries(map).map(([value, text]) => ({ value, label: text })),
  ];
}

export function OrderList() {
  const [page, setPage] = useState(1);
  const [text, setText] = useState(() => initialFilter('q'));
  const [filters, setFilters] = useState(() => ({
    q: initialFilter('q'),
    status: initialFilter('status'),
    payment_status: initialFilter('payment_status'),
    source: initialFilter('source'),
    placed_from: '',
    placed_to: '',
  }));
  const load = useCallback(
    async () =>
      managePage(merchantOrderSummary)(
        await api.get<unknown>(
          `/api/manage/orders/${query({ ...filters, page, page_size: PAGE_SIZE })}`,
        ),
      ),
    [filters, page],
  );
  const { data, error, loading } = useLoad(load);
  const set = (name: keyof typeof filters, value: string) => {
    setPage(1);
    setFilters((current) => ({ ...current, [name]: value }));
  };
  return (
    <div>
      <PageHeader
        eyebrow="Sell"
        title="Orders"
        intro="Every order from your online store and the orders your team enters for customers. Newest first."
        actions={
          <a
            href="/manage/orders/new"
            className="inline-flex min-h-11 items-center rounded-base border border-ink bg-ink px-4 text-sm font-semibold text-surface hover:bg-ink-2"
          >
            Create manual order
          </a>
        }
      />
      <Feedback error={error} />
      <Card flush>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            set('q', text.trim());
          }}
          className="flex flex-wrap items-end gap-3 border-b border-line p-4"
        >
          <div className="min-w-0 flex-1">
            <TextField
              label="Search orders"
              placeholder="Order number, name, phone or email"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>
        <Filters>
          <SelectField
            label="Order status"
            value={filters.status}
            onChange={(e) => set('status', e.target.value)}
            options={options(ORDER_STATUS, 'All statuses')}
          />
          <SelectField
            label="Payment"
            value={filters.payment_status}
            onChange={(e) => set('payment_status', e.target.value)}
            options={options(PAYMENT_STATUS, 'All payments')}
          />
          <SelectField
            label="Source"
            value={filters.source}
            onChange={(e) => set('source', e.target.value)}
            options={[
              { value: '', label: 'All sources' },
              ...ORDER_SOURCES.map((value) => ({ value, label: label(SOURCE, value) })),
            ]}
          />
          <div className="grid grid-cols-2 gap-2">
            <TextField
              label="From"
              type="date"
              value={filters.placed_from}
              onChange={(e) => set('placed_from', e.target.value)}
            />
            <TextField
              label="To"
              type="date"
              value={filters.placed_to}
              onChange={(e) => set('placed_to', e.target.value)}
            />
          </div>
        </Filters>
        {loading && !data ? (
          <Loader>Loading orders…</Loader>
        ) : !data?.results.length ? (
          <Empty title="No orders match">
            {Object.values(filters).some(Boolean)
              ? 'Try clearing a filter or searching for something else.'
              : 'When customers buy, or when you create a manual order, it appears here.'}
          </Empty>
        ) : (
          <>
            <Table
              head={[
                'Order',
                'Customer',
                'Source',
                'Items',
                'Total',
                'Payment',
                'Status',
                'Shipment',
              ]}
            >
              {data.results.map((row) => (
                <tr key={row.id} className="hover:bg-canvas">
                  <Cell>
                    <a className="font-semibold text-brand-deep" href={`/manage/orders/${row.id}`}>
                      {row.number}
                    </a>
                    <p className="text-xs text-muted">{dateTime(row.placed_at)}</p>
                  </Cell>
                  <Cell>
                    {row.recipient_name || '—'}
                    <p className="text-xs text-muted">{row.contact_phone}</p>
                  </Cell>
                  <Cell>{label(SOURCE, row.source)}</Cell>
                  <Cell className="tabular-nums">{row.item_count}</Cell>
                  <Cell className="tabular-nums">{money(row.grand_total, row.currency)}</Cell>
                  <Cell>
                    <StatusBadge value={row.payment_status} map={PAYMENT_STATUS} />
                    <p className="mt-1 text-xs text-muted">
                      {label(PAYMENT_METHOD, row.payment_method)}
                    </p>
                  </Cell>
                  <Cell>
                    <StatusBadge value={row.status} map={ORDER_STATUS} />
                  </Cell>
                  <Cell>
                    <StatusBadge value={row.shipment_status} map={SHIPMENT_STATUS} />
                  </Cell>
                </tr>
              ))}
            </Table>
            <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}

const CANCEL_REASONS = [
  { value: 'customer_request', label: 'Customer asked to cancel' },
  { value: 'out_of_stock', label: 'Item not available' },
  { value: 'unreachable', label: 'Customer unreachable' },
  { value: 'suspected_fraud', label: 'Suspected fake order' },
  { value: 'other', label: 'Other reason' },
];

const TRANSITION_LABEL: Record<string, string> = {
  confirmed: 'Confirm order',
  processing: 'Mark as processing',
  cancelled: 'Cancel order',
};

function RefundForm({
  order,
  onDone,
}: {
  order: MerchantOrderDetail;
  onDone: (message: string) => Promise<void>;
}) {
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [returnCase, setReturnCase] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [key, setKey] = useState(() => newKey('refund'));
  const returns = order.cases.filter((c) => c.kind === 'return');
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setConfirming(true);
      }}
    >
      <p className="text-sm text-muted">
        Up to {money(order.refundable_amount, order.currency)} can be refunded: only money that was
        verified as paid or collected. Cash refunds are recorded as manual refunds.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={`Amount (${order.currency})`}
          inputMode="decimal"
          required
          pattern="^\d+(\.\d{1,4})?$"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        {returns.length > 0 && (
          <SelectField
            label="For a return request"
            value={returnCase}
            onChange={(e) => setReturnCase(e.target.value)}
            options={[
              { value: '', label: 'Not for a return' },
              ...returns.map((c) => ({
                value: c.id,
                label: `${c.subject} · ${label(CASE_STATUS, c.status)}`,
              })),
            ]}
            hint="A return can only be resolved with refunds attributed to it here."
          />
        )}
      </div>
      <TextArea
        label="Reason"
        required
        maxLength={300}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <div>
        <Button type="submit" variant="secondary">
          Refund…
        </Button>
      </div>
      {confirming && (
        <ReauthDialog
          title="Confirm refund"
          description={`Refund ${money(amount || '0', order.currency)} on ${order.number}. Money leaves the store, so confirm with your password.`}
          confirmLabel="Refund"
          onClose={() => setConfirming(false)}
          onConfirm={async () => {
            await api.post(`/api/manage/orders/${order.id}/refunds/`, {
              amount,
              reason,
              idempotency_key: key,
              return_case: returnCase || null,
            });
            setConfirming(false);
            setAmount('');
            setReason('');
            setKey(newKey('refund'));
            await onDone('Refund recorded.');
          }}
        />
      )}
    </form>
  );
}

export function OrderDetail({ id, session }: { id: string; session: StaffSession }) {
  const can = (permission: string) => session.permissions.includes(permission);
  const load = useCallback(
    async () => merchantOrderDetail(await api.get<unknown>(`/api/manage/orders/${id}/`)),
    [id],
  );
  const { data: order, setData, error: loadError, loading } = useLoad(load);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [cancelling, setCancelling] = useState(false);
  const [cancelReason, setCancelReason] = useState('customer_request');

  async function run(name: string, action: () => Promise<void>, message: string) {
    setBusy(name);
    setError('');
    setNotice('');
    try {
      await action();
      setNotice(message);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy('');
    }
  }
  async function refresh() {
    setData(merchantOrderDetail(await api.get<unknown>(`/api/manage/orders/${id}/`)));
  }
  async function transition(status: string, reason = '') {
    setData(
      merchantOrderDetail(
        await api.post<unknown>(`/api/manage/orders/${id}/transition/`, { status, reason }),
      ),
    );
  }

  if (loading && !order) return <Loader>Loading order…</Loader>;
  if (!order) return <Feedback error={loadError || 'This order could not be loaded.'} />;
  const address = [
    order.address_line_1,
    order.address_line_2,
    [order.city, order.region, order.postal_code].filter(Boolean).join(', '),
    order.country,
  ].filter(Boolean);
  const unpaidCod =
    order.payment_method === 'cod' &&
    order.payment_status === 'unpaid' &&
    order.status !== 'cancelled';
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow={`Orders · ${label(SOURCE, order.source)}`}
        title={order.number}
        intro={`Placed ${dateTime(order.placed_at)}`}
        actions={
          <>
            {order.allowed_transitions
              .filter((status) => status !== 'cancelled')
              .map((status) => (
                <Button
                  key={status}
                  busy={busy === status}
                  onClick={() => void run(status, () => transition(status), 'Order updated.')}
                >
                  {TRANSITION_LABEL[status] ?? status}
                </Button>
              ))}
            {order.allowed_transitions.includes('cancelled') && (
              <Button variant="danger" onClick={() => setCancelling(true)}>
                Cancel order
              </Button>
            )}
            {order.invoice_number && (
              <a
                href={`/manage/orders/${order.id}/invoice`}
                className="inline-flex min-h-11 items-center rounded-base border border-line-strong bg-surface px-4 text-sm font-semibold hover:border-ink"
              >
                Invoice {order.invoice_number}
              </a>
            )}
          </>
        }
      />
      <div className="flex flex-wrap gap-2">
        <StatusBadge value={order.status} map={ORDER_STATUS} />
        <StatusBadge value={order.payment_status} map={PAYMENT_STATUS} />
        {order.shipment && <StatusBadge value={order.shipment.status} map={SHIPMENT_STATUS} />}
        {order.fraud && order.fraud.status !== 'clear' && (
          <StatusBadge value={order.fraud.status} map={FRAUD_STATUS} />
        )}
      </div>
      <Feedback error={error} notice={notice} />
      {unpaidCod && (
        <p className="rounded-base border border-warning-fg/20 bg-warning-bg p-3 text-sm text-warning-fg">
          Cash on delivery: this order stays unpaid until cash is actually collected and recorded.
        </p>
      )}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-6">
          <Card title="Items" flush>
            <Table head={['Product', 'SKU', 'Qty', 'Unit price', 'Total']}>
              {order.items.map((item) => (
                <tr key={item.id}>
                  <Cell>
                    <span className="font-semibold">{item.product_name}</span>
                    {item.variant_title && (
                      <p className="text-xs text-muted">{item.variant_title}</p>
                    )}
                    {item.kind === 'digital' && <p className="text-xs text-muted">Digital</p>}
                  </Cell>
                  <Cell>{item.sku}</Cell>
                  <Cell className="tabular-nums">{quantity(item.quantity)}</Cell>
                  <Cell className="tabular-nums">{money(item.unit_price, order.currency)}</Cell>
                  <Cell className="tabular-nums">{money(item.line_total, order.currency)}</Cell>
                </tr>
              ))}
            </Table>
            <dl className="grid gap-1.5 border-t border-line p-5 text-sm sm:ml-auto sm:max-w-sm">
              {(
                [
                  ['Items', order.item_subtotal],
                  ['Discount', order.discount_total, order.coupon_code],
                  ['Delivery', order.shipping_total, order.shipping_method_name],
                  ['Tax', order.tax_total],
                ] as const
              ).map(([name, value, note]) => (
                <div key={name} className="flex justify-between gap-4">
                  <dt className="text-muted">
                    {name}
                    {note ? ` · ${note}` : ''}
                  </dt>
                  <dd className="tabular-nums">
                    {name === 'Discount' && value !== '0.0000' ? '−' : ''}
                    {money(value, order.currency)}
                  </dd>
                </div>
              ))}
              <div className="flex justify-between gap-4 border-t border-line pt-2 font-bold">
                <dt>Total</dt>
                <dd className="tabular-nums">{money(order.grand_total, order.currency)}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted">Verified paid</dt>
                <dd className="tabular-nums">{money(order.amount_paid, order.currency)}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted">Refunded</dt>
                <dd className="tabular-nums">{money(order.amount_refunded, order.currency)}</dd>
              </div>
            </dl>
          </Card>
          <Card title="Payment">
            <dl className="grid gap-4 sm:grid-cols-2">
              <Detail label="Method">{label(PAYMENT_METHOD, order.payment_method)}</Detail>
              <Detail label="Status">
                <StatusBadge value={order.payment_status} map={PAYMENT_STATUS} />
              </Detail>
            </dl>
            {order.payments.length > 0 && (
              <ul className="mt-4 divide-y divide-line text-sm">
                {order.payments.map((p) => (
                  <li key={p.id} className="flex flex-wrap justify-between gap-2 py-2">
                    <span>
                      {label(
                        { payment: 'Payment', cod_collection: 'Cash collected', refund: 'Refund' },
                        p.kind,
                      )}{' '}
                      · {p.provider} · {dateTime(p.occurred_at)}
                    </span>
                    <span className="tabular-nums">
                      {money(p.amount, p.currency)}{' '}
                      <StatusBadge
                        value={p.status}
                        map={{ succeeded: 'Verified', failed: 'Failed' }}
                      />
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {order.can_record_cod && (
              <div className="mt-4">
                <Button
                  variant="secondary"
                  busy={busy === 'cod'}
                  onClick={() =>
                    void run(
                      'cod',
                      async () =>
                        setData(
                          merchantOrderDetail(
                            await api.post<unknown>(`/api/manage/orders/${id}/cod-collection/`),
                          ),
                        ),
                      'Cash collection recorded.',
                    )
                  }
                >
                  Record cash received ({money(order.grand_total, order.currency)})
                </Button>
                <p className="mt-2 text-xs text-muted">
                  Only when the full amount was received in person. Courier collections are recorded
                  on the shipment.
                </p>
              </div>
            )}
          </Card>
          {order.refunds.length > 0 && (
            <Card title="Refunds" flush>
              <Table head={['Requested', 'Amount', 'Method', 'Status', 'Reason']}>
                {order.refunds.map((r) => (
                  <tr key={r.id}>
                    <Cell>{dateTime(r.created_at)}</Cell>
                    <Cell className="tabular-nums">{money(r.amount, r.currency)}</Cell>
                    <Cell>{r.method}</Cell>
                    <Cell>
                      <StatusBadge
                        value={r.status}
                        map={{ pending: 'Pending', succeeded: 'Refunded', failed: 'Failed' }}
                      />
                    </Cell>
                    <Cell>{r.reason}</Cell>
                  </tr>
                ))}
              </Table>
            </Card>
          )}
          {(toUnits(order.refundable_amount) ?? 0n) > 0n && (
            <Card title="Refund">
              <RefundForm
                order={order}
                onDone={async (message) => {
                  await refresh();
                  setNotice(message);
                }}
              />
            </Card>
          )}
          <Card title="History">
            <ol className="grid gap-3 text-sm">
              {order.events.map((event, index) => (
                <li key={`${event.occurred_at}-${index}`} className="flex gap-3">
                  <span
                    aria-hidden="true"
                    className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand-deep"
                  />
                  <span>
                    <strong>{event.event.replaceAll('_', ' ')}</strong>
                    {event.to_status && ` → ${label(ORDER_STATUS, event.to_status)}`}
                    {event.reason && (
                      <span className="text-muted"> · {event.reason.replaceAll('_', ' ')}</span>
                    )}
                    <span className="block text-xs text-muted">{dateTime(event.occurred_at)}</span>
                  </span>
                </li>
              ))}
            </ol>
          </Card>
        </div>
        <div className="grid content-start gap-6">
          <Card title="Customer and delivery">
            <dl className="grid gap-3">
              <Detail label="Recipient">{order.recipient_name || '—'}</Detail>
              <Detail label="Phone">{order.contact_phone || '—'}</Detail>
              <Detail label="Email">{order.contact_email || '—'}</Detail>
              {order.requires_shipping && (
                <Detail label="Address">{address.join(', ') || '—'}</Detail>
              )}
              {order.delivery_notes && (
                <Detail label="Delivery notes">{order.delivery_notes}</Detail>
              )}
              {order.delivery_slot_label && (
                <Detail label="Delivery time">{order.delivery_slot_label}</Detail>
              )}
              <Detail label="Customer account">
                {order.customer ? (
                  can('customers.view') ? (
                    <a className="text-brand-deep" href={`/manage/customers/${order.customer.id}`}>
                      {order.customer.full_name || 'View customer'}
                    </a>
                  ) : (
                    order.customer.full_name || 'Registered customer'
                  )
                ) : (
                  'Guest or manual order — no account'
                )}
              </Detail>
            </dl>
          </Card>
          <Card title="Order source">
            <dl className="grid gap-3">
              <Detail label="Source">{label(SOURCE, order.source)}</Detail>
              {order.assisted && (
                <>
                  <Detail label="Entered by">
                    {order.assisted.created_by_name || 'Staff member'} ·{' '}
                    {dateTime(order.assisted.created_at)}
                  </Detail>
                  {order.assisted.source_reference && (
                    <Detail label="Reference">{order.assisted.source_reference}</Detail>
                  )}
                  <Detail label="Customer request">
                    Staff confirmed the customer asked for this order. This is a staff statement,
                    not a customer signature.
                  </Detail>
                </>
              )}
            </dl>
          </Card>
          {order.requires_shipping && (
            <Card title="Fulfillment">
              {order.shipment ? (
                <dl className="grid gap-3">
                  <Detail label="Shipment">
                    {order.shipment_id && can('couriers.manage') ? (
                      <a
                        className="text-brand-deep"
                        href={`/manage/shipments/${order.shipment_id}`}
                      >
                        {order.shipment.reference}
                      </a>
                    ) : (
                      order.shipment.reference
                    )}
                  </Detail>
                  <Detail label="Status">
                    <StatusBadge value={order.shipment.status} map={SHIPMENT_STATUS} />
                  </Detail>
                  <Detail label="Courier">{order.shipment.courier_name || 'Not assigned'}</Detail>
                  {order.shipment.tracking_number && (
                    <Detail label="Tracking">{order.shipment.tracking_number}</Detail>
                  )}
                </dl>
              ) : ['confirmed', 'processing'].includes(order.status) ? (
                can('couriers.manage') ? (
                  <div className="grid gap-2">
                    <p className="text-sm text-muted">
                      Ready to prepare a shipment for picking and packing.
                    </p>
                    <Button
                      busy={busy === 'ship'}
                      onClick={() =>
                        void run(
                          'ship',
                          async () => {
                            await api.post(`/api/manage/fulfillment/orders/${id}/shipment/`, {});
                            await refresh();
                          },
                          'Shipment prepared.',
                        )
                      }
                    >
                      Prepare shipment
                    </Button>
                  </div>
                ) : (
                  <p className="text-sm text-muted">Waiting for a teammate with courier access.</p>
                )
              ) : (
                <p className="text-sm text-muted">
                  {order.status === 'cancelled'
                    ? 'Cancelled orders are not shipped.'
                    : 'Confirm the order before preparing a shipment.'}
                </p>
              )}
            </Card>
          )}
          <Card title="Risk review">
            {order.fraud ? (
              <dl className="grid gap-3">
                <Detail label="Status">
                  <StatusBadge value={order.fraud.status} map={FRAUD_STATUS} />
                </Detail>
                <Detail label="Score">{order.fraud.score}</Detail>
                <Detail label="Checked">{dateTime(order.fraud.assessed_at)}</Detail>
              </dl>
            ) : (
              <p className="text-sm text-muted">
                Not checked yet. Signals are explainable and never block an order by themselves.
              </p>
            )}
            {order.status !== 'cancelled' && (
              <Button
                className="mt-4"
                variant="secondary"
                busy={busy === 'risk'}
                onClick={() =>
                  void run(
                    'risk',
                    async () => {
                      fraudAssessment(
                        await api.post<unknown>(
                          `/api/manage/fulfillment/orders/${id}/fraud-assessment/`,
                          {},
                        ),
                      );
                      await refresh();
                    },
                    'Risk check updated.',
                  )
                }
              >
                {order.fraud ? 'Re-check signals' : 'Check risk signals'}
              </Button>
            )}
          </Card>
          {order.cases.length > 0 && (
            <Card title="Customer requests">
              <ul className="grid gap-2 text-sm">
                {order.cases.map((c) => (
                  <li key={c.id}>
                    <a
                      className="font-semibold text-brand-deep"
                      href={`/manage/aftersales?case=${c.id}`}
                    >
                      {label(CASE_KIND, c.kind)}: {c.subject}
                    </a>
                    <span className="block text-xs text-muted">
                      {label(CASE_STATUS, c.status)} · {date(c.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
      {cancelling && (
        <ConfirmDialog
          title={`Cancel ${order.number}?`}
          confirmLabel="Cancel order"
          onCancel={() => setCancelling(false)}
          onConfirm={async () => {
            await run(
              'cancel',
              () => transition('cancelled', cancelReason),
              'Order cancelled. Reserved stock, coupon and delivery time were released.',
            );
            setCancelling(false);
          }}
        >
          <p>
            Reserved stock, any coupon use and the delivery time are released. Dispatched goods
            cannot be cancelled here, and money already collected must be refunded separately.
          </p>
          <div className="mt-4">
            <SelectField
              label="Reason"
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              options={CANCEL_REASONS}
            />
          </div>
        </ConfirmDialog>
      )}
    </div>
  );
}

export function OrderInvoice({ id }: { id: string }) {
  const load = useCallback(
    async () => decodeInvoice(await api.get<unknown>(`/api/manage/orders/${id}/invoice/`)),
    [id],
  );
  const { data: invoice, error, loading } = useLoad(load);
  if (loading && !invoice) return <Loader>Loading invoice…</Loader>;
  if (!invoice) return <Feedback error={error || 'This invoice could not be loaded.'} />;
  const buyer = invoice.buyer;
  return (
    <article className="mx-auto max-w-3xl rounded-base border border-line bg-surface p-6 print:border-0 print:p-0">
      <div className="mb-6 flex flex-wrap justify-between gap-3 print:hidden">
        <a className="text-sm font-semibold text-brand-deep" href={`/manage/orders/${id}`}>
          ← Back to order
        </a>
        <Button variant="secondary" onClick={() => window.print()}>
          Print or save as PDF
        </Button>
      </div>
      <header className="flex flex-wrap justify-between gap-6">
        <div>
          <h1 className="text-2xl font-bold">Invoice {invoice.number}</h1>
          <p className="mt-1 text-sm text-muted">
            Issued {date(invoice.issued_at)} · Order {invoice.order_number}
          </p>
        </div>
        <div className="text-sm sm:text-right">
          <p className="font-semibold">{invoice.seller.legal_name || invoice.seller.name}</p>
          {invoice.seller.email && <p>{invoice.seller.email}</p>}
          {invoice.seller.phone && <p>{invoice.seller.phone}</p>}
        </div>
      </header>
      {invoice.order_cancelled && (
        <p className="mt-4 rounded-base bg-danger-bg p-3 text-sm text-danger-fg">
          This order was cancelled.
        </p>
      )}
      <section className="mt-6 text-sm">
        <p className="text-muted">Bill to</p>
        {[
          buyer.name,
          buyer.address_line_1,
          buyer.address_line_2,
          buyer.city,
          buyer.country,
          buyer.phone,
          buyer.email,
        ]
          .filter(Boolean)
          .map((line) => (
            <p key={line}>{line}</p>
          ))}
      </section>
      <div className="mt-6">
        <Table head={['Item', 'Qty', 'Unit price', 'Amount']}>
          {invoice.items.map((item) => (
            <tr key={item.id}>
              <Cell>
                {item.product_name}
                {item.variant_title && <span className="text-muted"> · {item.variant_title}</span>}
              </Cell>
              <Cell className="tabular-nums">{quantity(item.quantity)}</Cell>
              <Cell className="tabular-nums">{money(item.unit_price, invoice.currency)}</Cell>
              <Cell className="tabular-nums">{money(item.line_total, invoice.currency)}</Cell>
            </tr>
          ))}
        </Table>
      </div>
      <dl className="mt-4 ml-auto grid max-w-xs gap-1 text-sm">
        {(
          [
            ['Items', invoice.item_subtotal],
            ['Discount', invoice.discount_total],
            ['Delivery', invoice.shipping_total],
            ['Tax', invoice.tax_total],
            ['Total', invoice.grand_total],
            ['Paid', invoice.amount_paid],
            ['Refunded', invoice.amount_refunded],
          ] as const
        ).map(([name, value]) => (
          <div
            key={name}
            className={`flex justify-between ${name === 'Total' ? 'border-t border-line pt-1 font-bold' : ''}`}
          >
            <dt>{name}</dt>
            <dd className="tabular-nums">{money(value, invoice.currency)}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-sm text-muted">
        {label(PAYMENT_METHOD, invoice.payment_method)} ·{' '}
        {label(PAYMENT_STATUS, invoice.payment_status)}
      </p>
    </article>
  );
}
