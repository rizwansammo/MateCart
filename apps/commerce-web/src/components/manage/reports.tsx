'use client';

import { useCallback, useState } from 'react';
import type { SalesReport } from '@matecart/shared-contracts';
import { salesReport } from '@matecart/shared-contracts';
import { Button, TextField } from '@matecart/shared-ui';
import {
  CASE_KIND,
  CASE_STATUS,
  Card,
  Cell,
  Empty,
  Feedback,
  Loader,
  ORDER_STATUS,
  PAYMENT_METHOD,
  PageHeader,
  SOURCE,
  Stat,
  Table,
  api,
  date,
  label,
  money,
  quantity,
  query,
  useLoad,
} from './kit';

function csvCell(value: string | number): string {
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** The server's daily rows as a CSV file; values are copied, never recalculated. */
function downloadDaily(report: SalesReport) {
  const rows = [
    ['date', 'currency', 'orders', 'order_value'],
    ...report.daily.map((r) => [r.date, r.currency, r.orders, r.value]),
  ];
  const blob = new Blob([rows.map((row) => row.map(csvCell).join(',')).join('\n')], {
    type: 'text/csv',
  });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `sales-${report.start}-to-${report.end}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

export function Reports() {
  const [range, setRange] = useState<{ days?: number; start?: string; end?: string }>({ days: 30 });
  const [custom, setCustom] = useState({ start: '', end: '' });
  const load = useCallback(
    async () => salesReport(await api.get<unknown>(`/api/manage/reports/sales/${query(range)}`)),
    [range],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Operate"
        title="Reports"
        intro="Sales and collections from your recorded orders and payments, by your store's calendar days. Order value is not revenue until money is verified as collected."
        actions={
          data &&
          data.daily.length > 0 && (
            <Button variant="secondary" onClick={() => downloadDaily(data)}>
              Download daily CSV
            </Button>
          )
        }
      />
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          {[7, 30, 90].map((days) => (
            <button
              key={days}
              type="button"
              aria-pressed={range.days === days}
              onClick={() => setRange({ days })}
              className={`rounded-base border px-3 py-2 text-sm font-semibold ${range.days === days ? 'border-ink bg-ink text-surface' : 'border-line-strong'}`}
            >
              Last {days} days
            </button>
          ))}
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setRange({ start: custom.start, end: custom.end });
            }}
          >
            <TextField
              label="From"
              type="date"
              required
              value={custom.start}
              onChange={(e) => setCustom({ ...custom, start: e.target.value })}
            />
            <TextField
              label="To"
              type="date"
              required
              value={custom.end}
              onChange={(e) => setCustom({ ...custom, end: e.target.value })}
            />
            <Button type="submit" variant="secondary">
              Show
            </Button>
          </form>
        </div>
      </Card>
      <Feedback error={error} />
      {loading && !data && <Loader>Preparing your report…</Loader>}
      {data && (
        <>
          <p className="text-sm text-muted">
            {date(data.start)} – {date(data.end)}
          </p>
          {!data.summary.length && !data.totals.length ? (
            <Card>
              <Empty title="No sales in this period">
                Choose another period, or come back after your first orders.
              </Empty>
            </Card>
          ) : (
            data.totals.map((row) => {
              const summary = data.summary.find((s) => s.currency === row.currency);
              const count = data.order_counts.find((c) => c.currency === row.currency)?.orders ?? 0;
              return (
                <div
                  key={row.currency}
                  className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6"
                >
                  <Stat label="Orders" value={count} hint="Not cancelled" />
                  <Stat label="Order value" value={money(row.order_value, row.currency)} />
                  <Stat
                    label="Average order"
                    value={money(summary?.average_order_value, row.currency)}
                  />
                  <Stat label="Verified collected" value={money(row.collected, row.currency)} />
                  <Stat label="Refunded" value={money(row.refunded, row.currency)} />
                  <Stat label="Net collected" value={money(row.net_collected, row.currency)} />
                </div>
              );
            })
          )}
          {data.cash.cod.map((row) => (
            <div key={row.currency} className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <Stat
                label="COD still to collect (now)"
                value={money(row.awaiting_collection, row.currency)}
                hint={`${data.cash.awaiting_collection_orders} orders`}
              />
              <Stat
                label="Courier cash not remitted"
                value={money(row.courier_remittance_pending, row.currency)}
                hint={`${data.cash.courier_records_pending} records`}
              />
              <Stat
                label="Remittance shortfall"
                value={money(row.courier_remittance_disputed, row.currency)}
                hint={`${data.cash.courier_records_disputed} disputed`}
                tone={data.cash.courier_records_disputed ? 'danger' : undefined}
              />
            </div>
          ))}
          <div className="grid gap-6 xl:grid-cols-3">
            <Card title="Orders by status" flush>
              {!data.by_status.length ? (
                <Empty title="No orders" />
              ) : (
                <Table head={['Status', 'Orders']}>
                  {data.by_status.map((row) => (
                    <tr key={row.key}>
                      <Cell>{label(ORDER_STATUS, row.key)}</Cell>
                      <Cell className="tabular-nums">{row.orders}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Payment methods" flush>
              {!data.by_payment_method.length ? (
                <Empty title="No orders" />
              ) : (
                <Table head={['Method', 'Orders', 'Value']}>
                  {data.by_payment_method.map((row) => (
                    <tr key={`${row.key}-${row.currency}`}>
                      <Cell>{label(PAYMENT_METHOD, row.key)}</Cell>
                      <Cell className="tabular-nums">{row.orders}</Cell>
                      <Cell className="tabular-nums">{money(row.value, row.currency)}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Order sources" flush>
              {!data.by_source.length ? (
                <Empty title="No orders" />
              ) : (
                <Table head={['Source', 'Orders', 'Value']}>
                  {data.by_source.map((row) => (
                    <tr key={`${row.key}-${row.currency}`}>
                      <Cell>{label(SOURCE, row.key)}</Cell>
                      <Cell className="tabular-nums">{row.orders}</Cell>
                      <Cell className="tabular-nums">{money(row.value, row.currency)}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
          <div className="grid gap-6 xl:grid-cols-2">
            <Card title="Best-selling products" flush>
              {!data.top_products.length ? (
                <Empty title="No products sold in this period" />
              ) : (
                <Table head={['Product', 'Qty', 'Orders', 'Sales']}>
                  {data.top_products.map((row) => (
                    <tr key={`${row.variant}-${row.currency}`}>
                      <Cell>
                        {row.product_name}
                        <p className="text-xs text-muted">{row.sku}</p>
                      </Cell>
                      <Cell className="tabular-nums">{quantity(row.quantity)}</Cell>
                      <Cell className="tabular-nums">{row.orders}</Cell>
                      <Cell className="tabular-nums">{money(row.revenue, row.currency)}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Daily orders" flush>
              {!data.daily.length ? (
                <Empty title="No orders in this period" />
              ) : (
                <Table head={['Day', 'Orders', 'Order value']}>
                  {data.daily.map((row) => (
                    <tr key={`${row.date}-${row.currency}`}>
                      <Cell>{date(row.date)}</Cell>
                      <Cell className="tabular-nums">{row.orders}</Cell>
                      <Cell className="tabular-nums">{money(row.value, row.currency)}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
          <div className="grid gap-6 xl:grid-cols-2">
            <Card title="Returns, exchanges and requests" flush>
              {!data.aftersales.length ? (
                <Empty title="No requests opened in this period" />
              ) : (
                <Table head={['Kind', 'Status', 'Requests']}>
                  {data.aftersales.map((row) => (
                    <tr key={`${row.kind}-${row.status}`}>
                      <Cell>{label(CASE_KIND, row.kind)}</Cell>
                      <Cell>{label(CASE_STATUS, row.status)}</Cell>
                      <Cell className="tabular-nums">{row.cases}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Inventory now">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Low stock" value={data.inventory.low_stock} href="/manage/inventory" />
                <Stat
                  label="Out of stock"
                  value={data.inventory.out_of_stock}
                  href="/manage/inventory"
                />
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
