'use client';

import { useCallback, useState } from 'react';
import Link from 'next/link';
import {
  analyticsReport,
  ledgerEntries,
  loyaltyMemberPage,
  loyaltyStats,
  type Amount,
  type AnalyticsRange,
  type LedgerEntry,
  type LedgerKind,
  type LoyaltyMember,
  type LoyaltyStats,
  type PromotionResult,
  type StaffSession,
} from '@matecart/shared-contracts';
import { Button, Dialog, TextField, errorMessage } from '@matecart/shared-ui';
import { formatZoned } from '@/lib/zoned-time';
import {
  Badge,
  Card,
  Cell,
  Empty,
  Feedback,
  Filters,
  Loader,
  PageHeader,
  Pagination,
  ReauthDialog,
  SelectField,
  Stat,
  Table,
  api,
  date,
  dateTime,
  isoDay,
  money,
  newKey,
  query,
  quantity,
  useLoad,
} from './kit';

// --- Loyalty --------------------------------------------------------------------------------------

export const LEDGER_LABEL: Record<LedgerKind, string> = {
  earned: 'Earned',
  redeemed: 'Used at checkout',
  restored: 'Returned (order cancelled or refunded)',
  reversed: 'Reversed after refund',
  expired: 'Expired',
  adjusted: 'Adjusted by staff',
};

interface ProgramForm {
  earn_points: string;
  earn_spend: string;
  point_value: string;
  min_redeem_points: string;
  max_redeem_percent: string;
  expiry_days: string;
}

function programForm(stats: LoyaltyStats): ProgramForm {
  const p = stats.program;
  return {
    earn_points: String(p.earn_points),
    earn_spend: quantity(p.earn_spend),
    point_value: quantity(p.point_value),
    min_redeem_points: String(p.min_redeem_points),
    max_redeem_percent: String(p.max_redeem_percent),
    expiry_days: p.expiry_days === null ? '' : String(p.expiry_days),
  };
}

export function LoyaltyManager({ session }: { session: StaffSession }) {
  const load = useCallback(
    async () => loyaltyStats(await api.get<unknown>('/api/manage/marketing/loyalty/')),
    [],
  );
  const { data, setData, error, loading } = useLoad(load);
  const [form, setForm] = useState<ProgramForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const values = form ?? (data ? programForm(data) : null);

  async function patch(body: Record<string, unknown>, message: string) {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      setData(loyaltyStats(await api.patch<unknown>('/api/manage/marketing/loyalty/', body)));
      setForm(null);
      setNotice(message);
    } catch (e) {
      setFailure(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Marketing"
        title="Loyalty points"
        intro="Registered customers with a verified email or phone earn points on paid and delivered orders and use them as a discount. Points have no cash value and can't be transferred. Guest and manual orders never earn points."
      />
      <Feedback error={error || failure} notice={notice} />
      {loading && !data && <Loader>Loading loyalty…</Loader>}
      {data && values && (
        <>
          {!data.module_enabled && (
            <p className="rounded-base border border-line bg-canvas p-4 text-sm">
              The Loyalty points module is off, so customers can’t earn or use points.{' '}
              <Link href="/manage/settings" className="font-semibold underline">
                Turn it on in Settings
              </Link>{' '}
              before switching the program on.
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Program"
              value={data.program.is_enabled ? 'On' : 'Off'}
              hint={data.module_enabled ? 'Module on' : 'Module off'}
            />
            <Stat label="Members" value={data.members} />
            <Stat
              label="Usable points"
              value={data.outstanding_points}
              hint={`1 point = ${quantity(data.program.point_value)} ${data.program.currency}`}
            />
            <Stat
              label="Waiting orders"
              value={data.pending_awards}
              hint="Points not usable until paid and delivered"
            />
          </div>
          {data.outstanding_deficit > 0 && (
            <p className="text-sm text-muted">
              {data.outstanding_deficit} points are recorded as a deficit: refunds reversed points
              that customers had already spent. Future earnings repay a deficit first.
            </p>
          )}
          <Card
            title="Program rules"
            actions={
              <Button
                variant={data.program.is_enabled ? 'secondary' : 'primary'}
                busy={busy}
                disabled={!data.module_enabled && !data.program.is_enabled}
                onClick={() =>
                  void patch(
                    { is_enabled: !data.program.is_enabled },
                    data.program.is_enabled
                      ? 'Program switched off. Existing points stay with customers.'
                      : 'Program switched on.',
                  )
                }
              >
                {data.program.is_enabled ? 'Switch program off' : 'Switch program on'}
              </Button>
            }
          >
            <form
              className="grid gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void patch(
                  {
                    earn_points: Number(values.earn_points),
                    earn_spend: values.earn_spend.trim(),
                    point_value: values.point_value.trim(),
                    min_redeem_points: Number(values.min_redeem_points),
                    max_redeem_percent: Number(values.max_redeem_percent),
                    expiry_days: values.expiry_days.trim() ? Number(values.expiry_days) : null,
                  },
                  'Program rules saved. They apply to orders placed from now on.',
                );
              }}
            >
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <TextField
                  label="Points earned"
                  inputMode="numeric"
                  value={values.earn_points}
                  onChange={(e) => setForm({ ...values, earn_points: e.target.value })}
                />
                <TextField
                  label={`For every (${data.program.currency})`}
                  inputMode="decimal"
                  value={values.earn_spend}
                  hint="Items after discounts; delivery and tax don't earn."
                  onChange={(e) => setForm({ ...values, earn_spend: e.target.value })}
                />
                <TextField
                  label={`One point is worth (${data.program.currency})`}
                  inputMode="decimal"
                  value={values.point_value}
                  onChange={(e) => setForm({ ...values, point_value: e.target.value })}
                />
                <TextField
                  label="Minimum points to use"
                  inputMode="numeric"
                  value={values.min_redeem_points}
                  onChange={(e) => setForm({ ...values, min_redeem_points: e.target.value })}
                />
                <TextField
                  label="Most of an order paid with points (%)"
                  inputMode="numeric"
                  value={values.max_redeem_percent}
                  hint="Of the items after any coupon."
                  onChange={(e) => setForm({ ...values, max_redeem_percent: e.target.value })}
                />
                <TextField
                  label="Expire after inactivity (days)"
                  inputMode="numeric"
                  value={values.expiry_days}
                  hint="Empty = never. At least 30 days."
                  onChange={(e) => setForm({ ...values, expiry_days: e.target.value })}
                />
              </div>
              <p className="text-sm leading-6 text-muted">
                Example: a {values.earn_spend || '?'} {data.program.currency} order earns{' '}
                {values.earn_points || '?'} points once it is paid in full and delivered. Points
                from an order are reduced in proportion to any refund. Points used on an order come
                back if it is cancelled or fully refunded.
              </p>
              <div>
                <Button type="submit" busy={busy} disabled={!form}>
                  Save rules
                </Button>
              </div>
            </form>
          </Card>
          {session.permissions.includes('customers.view') ? (
            <LoyaltyMembers />
          ) : (
            <Card title="Members">
              <p className="text-sm text-muted">
                Viewing member balances needs customer access, which your role doesn’t have.
              </p>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function LoyaltyMembers() {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<LoyaltyMember | null>(null);
  const load = useCallback(
    async () =>
      loyaltyMemberPage(
        await api.get<unknown>(
          `/api/manage/marketing/loyalty/members/${query({ search, page, page_size: 25 })}`,
        ),
      ),
    [search, page],
  );
  const { data, error, loading, reload } = useLoad(load);
  const forbidden = error && data === null && /permission/i.test(error);
  return (
    <Card title="Members" flush>
      <Filters>
        <TextField
          label="Search by name, email or phone"
          type="search"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
      </Filters>
      {forbidden ? (
        <Empty title="Member balances are not available for your role" />
      ) : error ? (
        <div className="p-4">
          <Feedback error={error} />
        </div>
      ) : loading && !data ? (
        <Loader>Loading members…</Loader>
      ) : !data || data.results.length === 0 ? (
        <Empty title="No members yet">Customers appear here once they earn or use points.</Empty>
      ) : (
        <>
          <Table head={['Customer', 'Usable', 'Earned', 'Used', 'Last activity', '']}>
            {data.results.map((row) => (
              <tr key={row.customer}>
                <Cell>
                  {row.name || 'Customer'}
                  <span className="block text-xs text-muted">{row.email ?? ''}</span>
                </Cell>
                <Cell className="tabular-nums">
                  {row.balance}
                  {row.deficit > 0 && (
                    <span className="block text-xs text-warning-fg">Deficit {row.deficit}</span>
                  )}
                </Cell>
                <Cell className="tabular-nums">{row.lifetime_earned}</Cell>
                <Cell className="tabular-nums">{row.lifetime_redeemed}</Cell>
                <Cell>{dateTime(row.last_activity_at)}</Cell>
                <Cell>
                  <Button variant="secondary" onClick={() => setOpen(row)}>
                    History
                  </Button>
                </Cell>
              </tr>
            ))}
          </Table>
          <Pagination
            page={data.page}
            pageSize={data.page_size}
            count={data.count}
            onPage={setPage}
          />
        </>
      )}
      {open && (
        <MemberDialog member={open} onClose={() => setOpen(null)} onChanged={() => void reload()} />
      )}
    </Card>
  );
}

function MemberDialog({
  member,
  onClose,
  onChanged,
}: {
  member: LoyaltyMember;
  onClose: () => void;
  onChanged: () => void;
}) {
  const load = useCallback(
    async () =>
      ledgerEntries(
        await api.get<unknown>(`/api/manage/marketing/loyalty/members/${member.customer}/`),
      ),
    [member.customer],
  );
  const { data, error, loading, reload } = useLoad(load);
  const [points, setPoints] = useState('');
  const [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState('');
  const [key] = useState(() => newKey('loyalty-adjust'));
  const amount = Number(points);
  const valid = Number.isInteger(amount) && amount !== 0 && reason.trim().length >= 5;

  if (confirming)
    return (
      <ReauthDialog
        title="Confirm points adjustment"
        confirmLabel="Adjust points"
        description={
          <>
            {amount > 0 ? 'Add' : 'Remove'} <strong className="text-ink">{Math.abs(amount)}</strong>{' '}
            points {amount > 0 ? 'to' : 'from'} {member.name || member.email || 'this customer'}.
            Reason: “{reason.trim()}”. The change is recorded with your name and can’t be deleted.
          </>
        }
        onClose={() => setConfirming(false)}
        onConfirm={async () => {
          await api.post('/api/manage/marketing/loyalty/adjust/', {
            customer: member.customer,
            points: amount,
            reason: reason.trim(),
            idempotency_key: key,
          });
          setConfirming(false);
          setPoints('');
          setReason('');
          setNotice('Points adjusted.');
          onChanged();
          await reload();
        }}
      />
    );

  return (
    <Dialog title={member.name || member.email || 'Member'} onClose={onClose}>
      <div className="grid gap-5">
        <Feedback error={error} notice={notice} />
        {loading && !data ? <Loader>Loading history…</Loader> : <LedgerList entries={data ?? []} />}
        <form
          className="grid gap-3 border-t border-line pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) setConfirming(true);
          }}
        >
          <p className="text-sm font-semibold">Adjust points</p>
          <TextField
            label="Points (negative to remove)"
            inputMode="numeric"
            value={points}
            hint="A removal can't exceed the usable balance."
            onChange={(e) => setPoints(e.target.value)}
          />
          <TextField
            label="Reason"
            required
            minLength={5}
            maxLength={200}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div>
            <Button type="submit" disabled={!valid}>
              Continue
            </Button>
          </div>
        </form>
      </div>
    </Dialog>
  );
}

export function LedgerList({
  entries,
  labels = LEDGER_LABEL,
}: {
  entries: LedgerEntry[];
  labels?: Record<LedgerKind, string>;
}) {
  if (entries.length === 0) return <p className="text-sm text-muted">No points activity yet.</p>;
  return (
    <ul className="divide-y divide-line text-sm">
      {entries.map((entry) => (
        <li key={entry.id} className="flex items-start justify-between gap-3 py-2">
          <span className="min-w-0">
            {labels[entry.kind]}
            {entry.order_number && <span className="text-muted"> · {entry.order_number}</span>}
            <span className="block text-xs text-muted">
              {dateTime(entry.occurred_at)}
              {entry.note && ` · ${entry.note}`}
            </span>
          </span>
          <span className="text-right tabular-nums">
            <span className={entry.points >= 0 ? 'font-semibold' : 'font-semibold text-danger-fg'}>
              {entry.points > 0 ? '+' : ''}
              {entry.points}
            </span>
            <span className="block text-xs text-muted">Balance {entry.balance_after}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

// --- Analytics ------------------------------------------------------------------------------------

function amounts(rows: Amount[]): string {
  return rows.length ? rows.map((row) => money(row.amount, row.currency)).join(' · ') : '—';
}

function rate(value: string | null): string {
  return value === null ? '—' : `${value}%`;
}

function ResultsTable({ rows, empty }: { rows: PromotionResult[]; empty: string }) {
  if (rows.length === 0) return <Empty title={empty} />;
  return (
    <Table head={['Name', 'Orders', 'Units', 'Sales', 'Discount given']}>
      {rows.map((row) => (
        <tr key={row.id}>
          <Cell>{row.name}</Cell>
          <Cell>{row.orders}</Cell>
          <Cell>{quantity(row.units)}</Cell>
          <Cell>{row.by_currency.map((c) => money(c.sales, c.currency)).join(' · ')}</Cell>
          <Cell>{row.by_currency.map((c) => money(c.discount, c.currency)).join(' · ')}</Cell>
        </tr>
      ))}
    </Table>
  );
}

export function AnalyticsReportView({ timeZone }: { timeZone: string }) {
  const [range, setRange] = useState<AnalyticsRange>('7d');
  const [start, setStart] = useState(isoDay(-13));
  const [end, setEnd] = useState(isoDay());
  const [applied, setApplied] = useState({ range: '7d' as AnalyticsRange, start: '', end: '' });
  const load = useCallback(
    async () =>
      analyticsReport(
        await api.get<unknown>(
          `/api/manage/marketing/analytics/${query(
            applied.range === 'custom'
              ? { range: 'custom', start: applied.start, end: applied.end }
              : { range: applied.range },
          )}`,
        ),
      ),
    [applied],
  );
  const { data, error, loading } = useLoad(load);
  const peak = data ? Math.max(1, ...data.series.map((point) => point.page_views)) : 1;
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Marketing"
        title="Marketing analytics"
        intro="Traffic and engagement from first-party counters; orders, sales and collections from the order and payment records. Sales reports stay in Reports."
      />
      <Card>
        <form
          className="grid gap-3 sm:grid-cols-4 sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            setApplied({ range, start, end });
          }}
        >
          <SelectField
            label="Period"
            value={range}
            onChange={(e) => {
              const next = (['24h', '7d', '30d', 'custom'] as const).find(
                (value) => value === e.target.value,
              );
              if (!next) return;
              setRange(next);
              if (next !== 'custom') setApplied({ range: next, start: '', end: '' });
            }}
            options={[
              { value: '24h', label: 'Last 24 hours' },
              { value: '7d', label: 'Last 7 days' },
              { value: '30d', label: 'Last 30 days' },
              { value: 'custom', label: 'Custom (up to 90 days)' },
            ]}
          />
          {range === 'custom' && (
            <>
              <TextField
                label="From"
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
              <TextField
                label="To"
                type="date"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
              <Button type="submit">Show</Button>
            </>
          )}
        </form>
        {data && (
          <p className="mt-3 text-xs text-muted">
            {data.rolling
              ? `Last 24 hours. Exact: unique visitors and orders since ${formatZoned(data.start, timeZone)}. Per-minute counts: page, product, cart and checkout totals from ${formatZoned(data.counter_start, timeZone)}. Hourly buckets: the table by hour, product engagement and traffic sources from ${formatZoned(data.breakdown_start, timeZone)}.`
              : `Store days ${date(data.first_day)} – ${date(data.last_day)} (${timeZone}).`}
          </p>
        )}
      </Card>
      <Feedback error={error} />
      {loading && !data && <Loader>Loading analytics…</Loader>}
      {data && (
        <>
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Traffic">
            <Stat
              label="Page views"
              value={data.traffic.page_views}
              hint={`${rate(data.traffic.consent_coverage)} with analytics consent`}
            />
            <Stat
              label={data.rolling ? 'Unique visitors' : 'Daily unique visitors (sum)'}
              value={data.traffic.unique_visitors}
              hint={
                data.rolling
                  ? 'Consented visitors only'
                  : 'Consented visitors; someone visiting on two days counts twice'
              }
            />
            <Stat label="Product views" value={data.traffic.product_views} />
            <Stat label="Category and collection views" value={data.traffic.category_views} />
          </section>
          <Card title="Cart and checkout funnel">
            <Table head={['Step', 'Count', 'Definition']}>
              <tr>
                <Cell>Product views</Cell>
                <Cell>{data.funnel.product_views}</Cell>
                <Cell className="text-xs text-muted">Product pages opened (all visitors)</Cell>
              </tr>
              <tr>
                <Cell>Added to cart</Cell>
                <Cell>{data.funnel.add_to_cart}</Cell>
                <Cell className="text-xs text-muted">
                  Successful add-to-cart requests on the server
                </Cell>
              </tr>
              <tr>
                <Cell>Checkouts started</Cell>
                <Cell>{data.funnel.checkout_started}</Cell>
                <Cell className="text-xs text-muted">New checkouts created on the server</Cell>
              </tr>
              <tr>
                <Cell>Online store orders</Cell>
                <Cell>{data.funnel.storefront_orders}</Cell>
                <Cell className="text-xs text-muted">
                  Orders placed on the website (order records)
                </Cell>
              </tr>
              <tr>
                <Cell>Visitor conversion</Cell>
                <Cell>{rate(data.funnel.conversion_rate)}</Cell>
                <Cell className="text-xs text-muted">
                  {data.funnel.converting_visitors} consented visitors who ordered ÷ consented
                  unique visitors
                </Cell>
              </tr>
            </Table>
          </Card>
          <Card title="Orders and money">
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat
                label="Online store orders"
                value={data.orders.storefront_orders}
                hint={`${data.orders.storefront_cancelled} cancelled · ${data.orders.assisted_orders} manual orders not counted here`}
              />
              <Stat
                label="Order value (not cancelled)"
                value={amounts(data.orders.order_value)}
                hint="Placed, not necessarily paid"
              />
              <Stat
                label="Verified collections"
                value={amounts(data.orders.collected)}
                hint="Verified payments and recorded cash on online store orders"
              />
            </div>
          </Card>
          <Card title={data.rolling ? 'By hour (hourly buckets)' : 'By day'} flush>
            {data.series.every((point) => point.page_views === 0 && point.add_to_cart === 0) ? (
              <Empty title="No visits recorded in this period" />
            ) : (
              <Table head={['When', 'Page views', 'Added to cart', 'Checkouts', 'Unique visitors']}>
                {data.series.map((point) => (
                  <tr key={point.bucket}>
                    <Cell className="whitespace-nowrap">
                      {data.rolling ? formatZoned(point.bucket, timeZone) : date(point.bucket)}
                    </Cell>
                    <Cell>
                      <span className="flex items-center gap-2">
                        <span
                          aria-hidden="true"
                          className="inline-block h-2 rounded-badge bg-brand-deep"
                          style={{ width: `${Math.round((point.page_views / peak) * 80)}px` }}
                        />
                        {point.page_views}
                      </span>
                    </Cell>
                    <Cell>{point.add_to_cart}</Cell>
                    <Cell>{point.checkout_started}</Cell>
                    <Cell>{point.unique_visitors ?? '—'}</Cell>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Product engagement" flush>
              {data.top_products.length === 0 ? (
                <Empty title="No product views yet" />
              ) : (
                <Table head={['Product', 'Views', 'Added to cart']}>
                  {data.top_products.map((row) => (
                    <tr key={row.product}>
                      <Cell>{row.name}</Cell>
                      <Cell>{row.views}</Cell>
                      <Cell>{row.add_to_cart}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Traffic sources" flush>
              <p className="px-5 pt-4 text-xs leading-5 text-muted">
                Tracked with consent from tagged links (UTM); the last tagged visit within 30 days
                before an order. Not proof of payment or of what caused a sale. Manual orders from
                Facebook or WhatsApp are never attributed here.
              </p>
              {data.sources.length === 0 ? (
                <Empty title="No tagged visits yet" />
              ) : (
                <Table head={['Source / medium / campaign', 'Landings', 'Orders', 'Order value']}>
                  {data.sources.map((row) => (
                    <tr key={`${row.utm_source}/${row.utm_medium}/${row.utm_campaign}`}>
                      <Cell>
                        {row.utm_source}
                        <span className="block text-xs text-muted">
                          {[row.utm_medium, row.utm_campaign].filter(Boolean).join(' / ') || '—'}
                        </span>
                      </Cell>
                      <Cell>{row.landings}</Cell>
                      <Cell>{row.orders}</Cell>
                      <Cell>{amounts(row.order_value)}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>
          <Card title="Campaign results" flush>
            <ResultsTable rows={data.campaigns} empty="No campaign sales in this period" />
          </Card>
          <Card title="Flash sale results" flush>
            <ResultsTable rows={data.flash_sales} empty="No flash sale sales in this period" />
          </Card>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Coupon use" flush>
              {data.coupons.length === 0 ? (
                <Empty title="No coupons used in this period">
                  {data.active_coupons} coupons are active.
                </Empty>
              ) : (
                <Table head={['Code', 'Uses', 'Discount']}>
                  {data.coupons.map((row) => (
                    <tr key={`${row.code}-${row.currency}`}>
                      <Cell className="font-mono">{row.code}</Cell>
                      <Cell>{row.uses}</Cell>
                      <Cell>{money(row.discount, row.currency)}</Cell>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            <Card title="Loyalty activity">
              <dl className="grid grid-cols-2 gap-3 text-sm">
                {(
                  [
                    ['Earned', data.loyalty.earned],
                    ['Used', data.loyalty.redeemed],
                    ['Returned', data.loyalty.restored],
                    ['Reversed', data.loyalty.reversed],
                    ['Expired', data.loyalty.expired],
                    ['Adjusted', data.loyalty.adjusted],
                    ['Orders waiting to earn', data.loyalty.pending_awards],
                  ] as const
                ).map(([name, value]) => (
                  <div key={name}>
                    <dt className="text-xs font-semibold text-muted">{name}</dt>
                    <dd className="mt-1 tabular-nums">{value}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          </div>
          <p className="text-xs text-muted">
            <Badge>Note</Badge> Browser events are signals, not records: totals here never replace
            the sales figures in{' '}
            <Link href="/manage/reports" className="underline">
              Reports
            </Link>
            .
          </p>
        </>
      )}
    </div>
  );
}
