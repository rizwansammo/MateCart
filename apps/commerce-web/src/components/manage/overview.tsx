'use client';

import { useCallback, useState } from 'react';
import type { Dashboard, StaffSession } from '@matecart/shared-contracts';
import { dashboard } from '@matecart/shared-contracts';
import { Alert } from '@matecart/shared-ui';
import {
  CASE_KIND,
  Card,
  Cell,
  Empty,
  Loader,
  ORDER_STATUS,
  PAYMENT_STATUS,
  PageHeader,
  SOURCE,
  Stat,
  StatusBadge,
  Table,
  api,
  dateTime,
  label,
  money,
  quantity,
  query,
  useLoad,
} from './kit';

const RANGES = [
  { days: 1, label: 'Today' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
];

function Finance({ data }: { data: NonNullable<Dashboard['finance']> }) {
  if (!data.totals.length && !data.cash.cod.length)
    return (
      <Card title="Sales and collections">
        <p className="text-sm text-muted">
          No sales in this period yet. Figures appear here as orders are placed and payments are
          recorded.
        </p>
      </Card>
    );
  return (
    <div className="grid gap-4">
      {data.totals.map((row) => (
        <div key={row.currency} className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Order value"
            value={money(row.order_value, row.currency)}
            hint="Placed and not cancelled. Not yet revenue."
          />
          <Stat
            label="Verified collected"
            value={money(row.collected, row.currency)}
            hint="Payments and cash recorded"
            href="/manage/reports"
          />
          <Stat label="Refunded" value={money(row.refunded, row.currency)} />
          <Stat label="Net collected" value={money(row.net_collected, row.currency)} />
        </div>
      ))}
      {data.cash.cod.map((row) => (
        <div key={row.currency} className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="COD to collect"
            value={money(row.awaiting_collection, row.currency)}
            hint={`${data.cash.awaiting_collection_orders} open orders · right now`}
          />
          <Stat
            label="Courier cash to remit"
            value={money(row.courier_remittance_pending, row.currency)}
            hint={`${data.cash.courier_records_pending} delivery records`}
            href="/manage/shipments"
          />
          <Stat
            label="Remittance shortfall"
            value={money(row.courier_remittance_disputed, row.currency)}
            hint={`${data.cash.courier_records_disputed} disputed`}
            tone={data.cash.courier_records_disputed ? 'danger' : undefined}
          />
        </div>
      ))}
    </div>
  );
}

export function Overview({ session }: { session: StaffSession }) {
  const can = (permission: string) => session.permissions.includes(permission);
  const [days, setDays] = useState(30);
  const load = useCallback(
    async () => dashboard(await api.get<unknown>(`/api/manage/dashboard/${query({ days })}`)),
    [days],
  );
  const { data, error, loading } = useLoad(load);
  const nothing = data && !data.orders && !data.finance && !data.inventory && !data.aftersales;
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow={session.store_name}
        title={`Welcome back, ${session.user.full_name || 'team member'}`}
        intro="Your store at a glance. Every figure comes from recorded orders, payments and stock."
        actions={
          <>
            {can('orders.manage') && (
              <a
                href="/manage/orders/new"
                className="inline-flex min-h-11 items-center rounded-base border border-ink bg-ink px-4 text-sm font-semibold text-surface hover:bg-ink-2"
              >
                Create manual order
              </a>
            )}
            {can('catalog.manage') && (
              <a
                href="/manage/products/new"
                className="inline-flex min-h-11 items-center rounded-base border border-line-strong bg-surface px-4 text-sm font-semibold hover:border-ink"
              >
                Add product
              </a>
            )}
          </>
        }
      />
      {(data?.orders || data?.finance) && (
        <div role="group" aria-label="Period" className="flex flex-wrap gap-2">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              aria-pressed={days === range.days}
              onClick={() => setDays(range.days)}
              className={`rounded-base border px-3 py-1.5 text-sm font-semibold ${days === range.days ? 'border-ink bg-ink text-surface' : 'border-line-strong bg-surface'}`}
            >
              {range.label}
            </button>
          ))}
        </div>
      )}
      {error && <Alert>{error}</Alert>}
      {loading && !data && <Loader>Loading your dashboard…</Loader>}
      {nothing && (
        <Card title="Your workspace">
          <p className="text-sm leading-6 text-muted">
            Your role does not include orders, stock or reports. Use the menu for the areas you look
            after, and keep your sign-in secure in{' '}
            <a className="font-semibold text-brand-deep" href="/manage/account/security">
              Account security
            </a>
            .
          </p>
        </Card>
      )}
      {data?.finance && <Finance data={data.finance} />}
      {data?.orders && data.operations && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Orders placed"
            value={data.orders.total}
            hint={`${data.orders.cancelled} cancelled`}
            href="/manage/orders"
          />
          <Stat
            label="Awaiting confirmation"
            value={data.operations.awaiting_confirmation}
            hint="New orders to review"
            href="/manage/orders?status=placed"
          />
          <Stat
            label="To prepare for shipping"
            value={data.operations.to_prepare}
            hint={`Being packed: ${data.operations.pending_shipments}`}
            href="/manage/shipments"
          />
          <Stat
            label="With the courier"
            value={data.operations.in_transit}
            hint={`${data.orders.delivered} delivered in this period`}
            href="/manage/shipments"
          />
          <Stat
            label="Failed deliveries"
            value={data.operations.delivery_failed}
            tone={data.operations.delivery_failed ? 'danger' : undefined}
            hint="Need a new attempt or return"
            href="/manage/shipments?status=delivery_failed"
          />
          <Stat
            label="Fraud review"
            value={data.operations.fraud_review}
            hint="Orders with risk signals"
            tone={data.operations.fraud_review ? 'danger' : undefined}
          />
          <Stat
            label="Confirmed"
            value={data.orders.confirmed}
            hint="Placed in this period, now confirmed"
          />
          <Stat
            label="Processing"
            value={data.orders.processing}
            hint="Placed in this period, now processing"
          />
        </div>
      )}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        {data?.recent_orders && (
          <Card
            title="Recent orders"
            flush
            actions={
              <a className="text-sm font-semibold text-brand-deep" href="/manage/orders">
                All orders →
              </a>
            }
          >
            {!data.recent_orders.length ? (
              <Empty title="No orders yet">
                Orders from your online store and manual orders you create appear here.
              </Empty>
            ) : (
              <Table head={['Order', 'Customer', 'Source', 'Total', 'Payment', 'Status']}>
                {data.recent_orders.map((row) => (
                  <tr key={row.id} className="hover:bg-canvas">
                    <Cell>
                      <a
                        className="font-semibold text-brand-deep"
                        href={`/manage/orders/${row.id}`}
                      >
                        {row.number}
                      </a>
                      <p className="text-xs text-muted">{dateTime(row.placed_at)}</p>
                    </Cell>
                    <Cell>{row.recipient_name || '—'}</Cell>
                    <Cell>{label(SOURCE, row.source)}</Cell>
                    <Cell className="tabular-nums">{money(row.grand_total, row.currency)}</Cell>
                    <Cell>
                      <StatusBadge value={row.payment_status} map={PAYMENT_STATUS} />
                    </Cell>
                    <Cell>
                      <StatusBadge value={row.status} map={ORDER_STATUS} />
                    </Cell>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        )}
        <div className="grid content-start gap-6">
          {data?.aftersales && (
            <Card
              title="Customer requests"
              actions={
                <a className="text-sm font-semibold text-brand-deep" href="/manage/aftersales">
                  Open →
                </a>
              }
            >
              <ul className="grid gap-2 text-sm">
                {(
                  [
                    ['return', data.aftersales.open_returns],
                    ['exchange', data.aftersales.open_exchanges],
                    ['warranty', data.aftersales.open_warranty],
                    ['support', data.aftersales.open_support],
                  ] as const
                ).map(([kind, count]) => (
                  <li key={kind} className="flex justify-between">
                    <span>Open {label(CASE_KIND, kind).toLowerCase()} requests</span>
                    <strong className="tabular-nums">{count}</strong>
                  </li>
                ))}
                <li className="flex justify-between">
                  <span>Reviews to moderate</span>
                  <strong className="tabular-nums">{data.aftersales.pending_reviews}</strong>
                </li>
              </ul>
            </Card>
          )}
          {data?.inventory && (
            <Card
              title="Stock alerts"
              actions={
                <a className="text-sm font-semibold text-brand-deep" href="/manage/inventory">
                  Inventory →
                </a>
              }
            >
              <div className="grid grid-cols-3 gap-2 text-center">
                {(
                  [
                    ['Low', data.inventory.low_stock],
                    ['Out', data.inventory.out_of_stock],
                    ['Expiring', data.inventory.expiring_batches],
                  ] as const
                ).map(([name, count]) => (
                  <div key={name} className="rounded-base bg-canvas p-3">
                    <p className="text-xl font-bold tabular-nums">{count}</p>
                    <p className="text-xs text-muted">{name}</p>
                  </div>
                ))}
              </div>
              {data.inventory.low_stock_items.length ? (
                <ul className="mt-4 divide-y divide-line text-sm">
                  {data.inventory.low_stock_items.map((item) => (
                    <li key={item.id} className="flex justify-between gap-3 py-2">
                      <span className="min-w-0">
                        <span className="block truncate font-semibold">{item.product_name}</span>
                        <span className="text-xs text-muted">
                          {item.sku} · {item.location_code}
                        </span>
                      </span>
                      <span className="text-right text-xs whitespace-nowrap">
                        <strong className="text-danger-fg">{quantity(item.sellable)}</strong>{' '}
                        sellable
                        <br />
                        {quantity(item.on_hand)} on hand · alert at{' '}
                        {quantity(item.low_stock_threshold)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 text-sm text-muted">
                  No item is below its low-stock alert. Set alerts on stock levels in Inventory.
                </p>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
