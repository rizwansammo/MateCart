'use client';

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { Route } from 'next';
import Link from 'next/link';
import {
  campaignDetail,
  campaignPage,
  couponPage,
  flashSaleDetail,
  flashSalePage,
  lookupRows,
  managedCoupon,
  type Campaign,
  type CampaignDetail,
  type FlashSaleDetail,
  type LookupRow,
  type ManagedCoupon,
  type Performance,
  type PromotionAction,
  type PromotionState,
} from '@matecart/shared-contracts';
import { Alert, Button, ConfirmDialog, Dialog, TextField, errorMessage } from '@matecart/shared-ui';
import { formatZoned, fromZonedInput, toZonedInput } from '@/lib/zoned-time';
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
  SelectField,
  Table,
  api,
  money,
  query,
  quantity,
  useLoad,
} from './kit';

export const STACKING_POLICY =
  'One promotion per item: a live flash sale wins, otherwise the campaign giving the lowest price. Items at a promotional price never take a coupon as well. Order totals are always calculated by the server.';

const STATE_LABEL: Record<PromotionState, string> = {
  draft: 'Draft',
  scheduled: 'Scheduled',
  live: 'Live',
  paused: 'Paused',
  ended: 'Ended',
  cancelled: 'Cancelled',
};
const STATE_TONE: Record<PromotionState, 'neutral' | 'info' | 'success' | 'warning' | 'danger'> = {
  draft: 'neutral',
  scheduled: 'info',
  live: 'success',
  paused: 'warning',
  ended: 'neutral',
  cancelled: 'danger',
};

export function StateBadge({ state }: { state: PromotionState }) {
  return <Badge tone={STATE_TONE[state]}>{STATE_LABEL[state]}</Badge>;
}

function performanceText(performance: Performance | null): ReactNode {
  if (!performance || performance.orders === 0)
    return <span className="text-muted">No orders</span>;
  return (
    <span>
      {performance.orders} orders · {quantity(performance.units)} units
      {performance.by_currency.map((row) => (
        <span key={row.currency} className="block text-xs text-muted">
          {money(row.sales, row.currency)} sold · {money(row.discount, row.currency)} given
        </span>
      ))}
    </span>
  );
}

const ACTION_COPY: Record<PromotionAction, { label: string; title: string; body: string }> = {
  activate: {
    label: 'Activate',
    title: 'Activate this promotion?',
    body: 'Prices change automatically at the start time and return to normal at the end. Customers already reviewing a checkout keep the price they reviewed for up to 15 minutes.',
  },
  pause: {
    label: 'Pause',
    title: 'Pause this promotion?',
    body: 'Regular prices return immediately. Orders already placed keep the price they were placed at.',
  },
  cancel: {
    label: 'Cancel promotion',
    title: 'Cancel this promotion?',
    body: 'A cancelled promotion cannot be restarted. Orders already placed are not changed.',
  },
};

function actionsFor(state: PromotionState): PromotionAction[] {
  if (state === 'draft' || state === 'paused') return ['activate', 'cancel'];
  if (state === 'scheduled' || state === 'live') return ['pause', 'cancel'];
  return [];
}

function StatusActions({
  state,
  onAction,
}: {
  state: PromotionState;
  onAction: (action: PromotionAction) => Promise<void>;
}) {
  const [pending, setPending] = useState<PromotionAction | null>(null);
  const actions = actionsFor(state);
  if (!actions.length) return null;
  return (
    <>
      {actions.map((action) => (
        <Button
          key={action}
          variant={action === 'cancel' ? 'danger' : action === 'activate' ? 'primary' : 'secondary'}
          onClick={() => setPending(action)}
        >
          {ACTION_COPY[action].label}
        </Button>
      ))}
      {pending && (
        <ConfirmDialog
          title={ACTION_COPY[pending].title}
          confirmLabel={ACTION_COPY[pending].label}
          onCancel={() => setPending(null)}
          onConfirm={async () => {
            await onAction(pending);
            setPending(null);
          }}
        >
          {ACTION_COPY[pending].body}
        </ConfirmDialog>
      )}
    </>
  );
}

function LockedNote({ state }: { state: PromotionState }) {
  if (state === 'live')
    return <Alert tone="info">This promotion is live. Pause it before changing it.</Alert>;
  if (state === 'ended' || state === 'cancelled')
    return (
      <Alert tone="info">
        This promotion has {state === 'ended' ? 'ended' : 'been cancelled'} and can no longer be
        changed. Its results stay available.
      </Alert>
    );
  return null;
}

function StateFilters({
  state,
  search,
  onChange,
}: {
  state: string;
  search: string;
  onChange: (state: string, search: string) => void;
}) {
  return (
    <Filters>
      <SelectField
        label="Status"
        value={state}
        onChange={(e) => onChange(e.target.value, search)}
        options={[
          { value: '', label: 'All' },
          ...Object.entries(STATE_LABEL).map(([value, text]) => ({ value, label: text })),
        ]}
      />
      <TextField
        label="Search by name"
        type="search"
        value={search}
        onChange={(e) => onChange(state, e.target.value)}
      />
    </Filters>
  );
}

// --- Campaigns ------------------------------------------------------------------------------------

export function CampaignList({ timeZone }: { timeZone: string }) {
  const [state, setState] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      campaignPage(
        await api.get<unknown>(
          `/api/manage/marketing/campaigns/${query({ state, search, page, page_size: 25 })}`,
        ),
      ),
    [state, search, page],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <div>
      <PageHeader
        eyebrow="Marketing"
        title="Campaigns"
        intro={`Percentage or fixed discounts, or featured product groups, for a set time. ${STACKING_POLICY}`}
        actions={
          <Link
            href="/manage/marketing/campaigns/new"
            className="inline-flex min-h-11 items-center rounded-base border border-ink bg-ink px-4 text-sm font-semibold text-surface"
          >
            New campaign
          </Link>
        }
      />
      <Feedback error={error} />
      <Card flush>
        <StateFilters
          state={state}
          search={search}
          onChange={(nextState, nextSearch) => {
            setState(nextState);
            setSearch(nextSearch);
            setPage(1);
          }}
        />
        {loading && !data ? (
          <Loader>Loading campaigns…</Loader>
        ) : !data || data.results.length === 0 ? (
          <Empty title="No campaigns yet">
            Create a campaign to discount products, categories or collections for a set time.
          </Empty>
        ) : (
          <>
            <Table head={['Campaign', 'Offer', 'Window (store time)', 'Status', 'Results']}>
              {data.results.map((row) => (
                <tr key={row.id}>
                  <Cell>
                    <Link
                      href={`/manage/marketing/campaigns/${row.id}` as Route}
                      className="font-semibold underline-offset-2 hover:underline"
                    >
                      {row.name}
                    </Link>
                    <span className="block text-xs text-muted">{row.target_count} targets</span>
                  </Cell>
                  <Cell>{offerText(row)}</Cell>
                  <Cell className="text-xs">
                    {formatZoned(row.starts_at, timeZone)}
                    <br />
                    {formatZoned(row.ends_at, timeZone)}
                  </Cell>
                  <Cell>
                    <StateBadge state={row.state} />
                  </Cell>
                  <Cell>{performanceText(row.performance)}</Cell>
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
      </Card>
    </div>
  );
}

function offerText(row: Campaign): string {
  if (row.kind === 'featured') return `Featured · “${row.badge_text || 'Featured'}”`;
  if (row.discount_type === 'percent') return `${quantity(row.value)}% off`;
  return `${money(row.value, row.currency)} off`;
}

interface CampaignForm {
  name: string;
  description: string;
  kind: 'discount' | 'featured';
  discount_type: 'percent' | 'fixed';
  value: string;
  badge_text: string;
  starts_at: string;
  ends_at: string;
  targets: { id: string; name: string; kind: 'product' | 'category' | 'collection' }[];
}

function emptyCampaign(timeZone: string): CampaignForm {
  const start = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  const end = new Date(Date.now() + 8 * 24 * 60 * 60 * 1000).toISOString();
  return {
    name: '',
    description: '',
    kind: 'discount',
    discount_type: 'percent',
    value: '',
    badge_text: '',
    starts_at: toZonedInput(start, timeZone),
    ends_at: toZonedInput(end, timeZone),
    targets: [],
  };
}

function campaignForm(detail: CampaignDetail, timeZone: string): CampaignForm {
  return {
    name: detail.name,
    description: detail.description,
    kind: detail.kind,
    discount_type: detail.discount_type === 'fixed' ? 'fixed' : 'percent',
    value: detail.value === null ? '' : quantity(detail.value),
    badge_text: detail.badge_text,
    starts_at: toZonedInput(detail.starts_at, timeZone),
    ends_at: toZonedInput(detail.ends_at, timeZone),
    targets: detail.targets,
  };
}

export function CampaignEditor({ id, timeZone }: { id: string; timeZone: string }) {
  const creating = id === 'new';
  const [detail, setDetail] = useState<CampaignDetail | null>(null);
  const [form, setForm] = useState<CampaignForm | null>(() =>
    creating ? emptyCampaign(timeZone) : null,
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (creating) return;
    void api
      .get<unknown>(`/api/manage/marketing/campaigns/${id}/`)
      .then((value) => {
        const decoded = campaignDetail(value);
        setDetail(decoded);
        setForm(campaignForm(decoded, timeZone));
      })
      .catch((e: unknown) => setError(errorMessage(e)));
  }, [creating, id, timeZone]);

  const locked = detail !== null && ['live', 'ended', 'cancelled'].includes(detail.state);

  async function save() {
    if (!form) return;
    const starts = fromZonedInput(form.starts_at, timeZone);
    const ends = fromZonedInput(form.ends_at, timeZone);
    if (!starts || !ends) {
      setError('Enter a start and end date and time.');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    const body = {
      name: form.name.trim(),
      description: form.description.trim(),
      kind: form.kind,
      discount_type: form.kind === 'discount' ? form.discount_type : '',
      value: form.kind === 'discount' ? form.value.trim() || null : null,
      badge_text: form.badge_text.trim(),
      starts_at: starts,
      ends_at: ends,
      targets: {
        products: form.targets.filter((t) => t.kind === 'product').map((t) => t.id),
        categories: form.targets.filter((t) => t.kind === 'category').map((t) => t.id),
        collections: form.targets.filter((t) => t.kind === 'collection').map((t) => t.id),
      },
    };
    try {
      if (creating) {
        const created = campaignDetail(
          await api.post<unknown>('/api/manage/marketing/campaigns/', body),
        );
        window.location.assign(`/manage/marketing/campaigns/${created.id}`);
        return;
      }
      const updated = campaignDetail(
        await api.patch<unknown>(`/api/manage/marketing/campaigns/${id}/`, body),
      );
      setDetail(updated);
      setForm(campaignForm(updated, timeZone));
      setNotice('Campaign saved. Check the preview below.');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(action: PromotionAction) {
    setError('');
    setNotice('');
    try {
      const updated = campaignDetail(
        await api.post<unknown>(`/api/manage/marketing/campaigns/${id}/status/`, { action }),
      );
      setDetail(updated);
      setForm(campaignForm(updated, timeZone));
      setNotice(`Campaign is now ${STATE_LABEL[updated.state].toLowerCase()}.`);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!form) return error ? <Feedback error={error} /> : <Loader>Loading campaign…</Loader>;
  const set = (patch: Partial<CampaignForm>) => setForm({ ...form, ...patch });
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Campaigns"
        title={creating ? 'New campaign' : detail?.name || 'Campaign'}
        intro={STACKING_POLICY}
        actions={
          detail && (
            <>
              <StateBadge state={detail.state} />
              <StatusActions state={detail.state} onAction={act} />
            </>
          )
        }
      />
      <Feedback error={error} notice={notice} />
      {detail && <LockedNote state={detail.state} />}
      <form
        className="grid gap-6"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <fieldset disabled={locked} className="grid gap-6">
          <Card title="Offer">
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label="Name"
                required
                maxLength={120}
                value={form.name}
                onChange={(e) => set({ name: e.target.value })}
              />
              <SelectField
                label="Type"
                value={form.kind}
                onChange={(e) =>
                  set({ kind: e.target.value === 'featured' ? 'featured' : 'discount' })
                }
                options={[
                  { value: 'discount', label: 'Price discount' },
                  { value: 'featured', label: 'Featured products (badge only, no price change)' },
                ]}
              />
              {form.kind === 'discount' && (
                <>
                  <SelectField
                    label="Discount"
                    value={form.discount_type}
                    onChange={(e) =>
                      set({ discount_type: e.target.value === 'fixed' ? 'fixed' : 'percent' })
                    }
                    options={[
                      { value: 'percent', label: 'Percentage off' },
                      { value: 'fixed', label: 'Fixed amount off (store currency)' },
                    ]}
                  />
                  <TextField
                    label={form.discount_type === 'percent' ? 'Percent off' : 'Amount off'}
                    inputMode="decimal"
                    required
                    value={form.value}
                    hint={
                      form.discount_type === 'percent'
                        ? 'Above 0 and below 100.'
                        : 'Items this would make free or negative are left at their regular price.'
                    }
                    onChange={(e) => set({ value: e.target.value })}
                  />
                </>
              )}
              <TextField
                label="Badge text"
                maxLength={24}
                value={form.badge_text}
                hint={`Shown on product cards. Empty uses “${form.kind === 'featured' ? 'Featured' : 'Sale'}”.`}
                onChange={(e) => set({ badge_text: e.target.value })}
              />
              <TextField
                label="Internal description"
                maxLength={500}
                value={form.description}
                onChange={(e) => set({ description: e.target.value })}
              />
              <TextField
                label={`Starts (${timeZone})`}
                type="datetime-local"
                required
                value={form.starts_at}
                onChange={(e) => set({ starts_at: e.target.value })}
              />
              <TextField
                label={`Ends (${timeZone})`}
                type="datetime-local"
                required
                value={form.ends_at}
                hint="At most 366 days after the start."
                onChange={(e) => set({ ends_at: e.target.value })}
              />
            </div>
          </Card>
          <Card title="Applies to">
            <TargetPicker
              kind="targets"
              selected={form.targets.map((t) => t.id)}
              onPick={(row) => {
                if (row.kind === 'variant' || form.targets.some((t) => t.id === row.id)) return;
                set({ targets: [...form.targets, { id: row.id, name: row.name, kind: row.kind }] });
              }}
            />
            {form.targets.length === 0 ? (
              <p className="mt-4 text-sm text-muted">
                Nothing chosen yet. A campaign needs at least one product, category (includes its
                subcategories) or collection before it can be activated.
              </p>
            ) : (
              <ul className="mt-4 flex flex-wrap gap-2">
                {form.targets.map((target) => (
                  <li
                    key={target.id}
                    className="flex items-center gap-2 rounded-badge border border-line px-2 py-1 text-sm"
                  >
                    <span className="text-xs text-muted capitalize">{target.kind}</span>
                    {target.name}
                    <button
                      type="button"
                      className="text-muted hover:text-ink"
                      aria-label={`Remove ${target.name}`}
                      onClick={() =>
                        set({ targets: form.targets.filter((t) => t.id !== target.id) })
                      }
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <div>
            <Button type="submit" busy={busy}>
              {creating ? 'Create as draft' : 'Save changes'}
            </Button>
          </div>
        </fieldset>
      </form>
      {detail && (
        <Card title="Preview" flush>
          <p className="px-5 pt-4 text-sm text-muted">
            {detail.preview.products} products · {detail.preview.variants} variants. Prices as if
            the campaign were live now and nothing else applied; a live flash sale would still win.
          </p>
          {detail.preview.rows.length === 0 ? (
            <Empty title="No products matched" />
          ) : (
            <Table head={['Product', 'SKU', 'Regular', 'Campaign price', 'Applies']}>
              {detail.preview.rows.map((row) => (
                <tr key={row.variant}>
                  <Cell>
                    {row.product_name}
                    {row.product_status !== 'active' && (
                      <span className="block text-xs text-muted">Not published</span>
                    )}
                  </Cell>
                  <Cell className="font-mono text-xs">{row.sku}</Cell>
                  <Cell>{money(row.regular_amount, row.currency)}</Cell>
                  <Cell>{money(row.promotional_amount, row.currency)}</Cell>
                  <Cell>{row.applies ? 'Yes' : 'No'}</Cell>
                </tr>
              ))}
            </Table>
          )}
          {detail.performance && (
            <p className="border-t border-line px-5 py-3 text-sm">
              Results: {performanceText(detail.performance)}
            </p>
          )}
        </Card>
      )}
    </div>
  );
}

function TargetPicker({
  kind,
  selected,
  onPick,
}: {
  kind: 'targets' | 'variants';
  selected: string[];
  onPick: (row: LookupRow) => void;
}) {
  const [text, setText] = useState('');
  const [rows, setRows] = useState<LookupRow[] | null>(null);
  const [error, setError] = useState('');
  async function search() {
    setError('');
    try {
      setRows(
        lookupRows(
          await api.get<unknown>(`/api/manage/marketing/lookup/${query({ kind, q: text.trim() })}`),
        ),
      );
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <TextField
            label={
              kind === 'variants'
                ? 'Find a product or SKU'
                : 'Find products, categories or collections'
            }
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                void search();
              }
            }}
          />
        </div>
        <Button type="button" variant="secondary" onClick={() => void search()}>
          Search
        </Button>
      </div>
      {error && <Alert>{error}</Alert>}
      {rows !== null &&
        (rows.length === 0 ? (
          <p className="text-sm text-muted">Nothing found.</p>
        ) : (
          <ul className="max-h-64 divide-y divide-line overflow-y-auto rounded-base border border-line">
            {rows.map((row) => (
              <li
                key={`${row.kind}-${row.id}`}
                className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
              >
                <span className="min-w-0">
                  <span className="mr-2 text-xs text-muted capitalize">{row.kind}</span>
                  {row.name}
                  <span className="block text-xs text-muted">
                    {row.detail}
                    {row.regular_amount !== null && ` · ${money(row.regular_amount, row.currency)}`}
                  </span>
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={selected.includes(row.id)}
                  onClick={() => onPick(row)}
                >
                  {selected.includes(row.id) ? 'Added' : 'Add'}
                </Button>
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}

// --- Flash sales ----------------------------------------------------------------------------------

export function FlashSaleList({ timeZone }: { timeZone: string }) {
  const [state, setState] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      flashSalePage(
        await api.get<unknown>(
          `/api/manage/marketing/flash-sales/${query({ state, search, page, page_size: 25 })}`,
        ),
      ),
    [state, search, page],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <div>
      <PageHeader
        eyebrow="Marketing"
        title="Flash sales"
        intro="Short sales (up to 14 days) with a fixed sale price per variant, optional unit limits and a countdown on the store. Turn on the Flash sales module in Settings to activate one."
        actions={
          <Link
            href="/manage/marketing/flash-sales/new"
            className="inline-flex min-h-11 items-center rounded-base border border-ink bg-ink px-4 text-sm font-semibold text-surface"
          >
            New flash sale
          </Link>
        }
      />
      <Feedback error={error} />
      <Card flush>
        <StateFilters
          state={state}
          search={search}
          onChange={(nextState, nextSearch) => {
            setState(nextState);
            setSearch(nextSearch);
            setPage(1);
          }}
        />
        {loading && !data ? (
          <Loader>Loading flash sales…</Loader>
        ) : !data || data.results.length === 0 ? (
          <Empty title="No flash sales yet" />
        ) : (
          <>
            <Table head={['Flash sale', 'Window (store time)', 'Items', 'Status', 'Results']}>
              {data.results.map((row) => (
                <tr key={row.id}>
                  <Cell>
                    <Link
                      href={`/manage/marketing/flash-sales/${row.id}` as Route}
                      className="font-semibold underline-offset-2 hover:underline"
                    >
                      {row.name}
                    </Link>
                  </Cell>
                  <Cell className="text-xs">
                    {formatZoned(row.starts_at, timeZone)}
                    <br />
                    {formatZoned(row.ends_at, timeZone)}
                  </Cell>
                  <Cell>{row.item_count}</Cell>
                  <Cell>
                    <StateBadge state={row.state} />
                  </Cell>
                  <Cell>{performanceText(row.performance)}</Cell>
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
      </Card>
    </div>
  );
}

interface FlashItemForm {
  variant: string;
  label: string;
  regular: string | null;
  currency: string;
  sale_price: string;
  quantity_limit: string;
  per_order_limit: string;
  units_sold: string;
}

function flashForm(detail: FlashSaleDetail, timeZone: string) {
  return {
    name: detail.name,
    description: detail.description,
    starts_at: toZonedInput(detail.starts_at, timeZone),
    ends_at: toZonedInput(detail.ends_at, timeZone),
    items: detail.items.map<FlashItemForm>((item) => ({
      variant: item.variant,
      label: `${item.product_name}${item.variant_title ? ` · ${item.variant_title}` : ''} (${item.sku})`,
      regular: item.regular_amount,
      currency: item.currency,
      sale_price: quantity(item.sale_price),
      quantity_limit: item.quantity_limit === null ? '' : String(item.quantity_limit),
      per_order_limit: item.per_order_limit === null ? '' : String(item.per_order_limit),
      units_sold: item.units_sold,
    })),
  };
}

export function FlashSaleEditor({ id, timeZone }: { id: string; timeZone: string }) {
  const creating = id === 'new';
  const [detail, setDetail] = useState<FlashSaleDetail | null>(null);
  const [form, setForm] = useState<ReturnType<typeof flashForm> | null>(() =>
    creating
      ? {
          name: '',
          description: '',
          starts_at: toZonedInput(new Date(Date.now() + 3600_000).toISOString(), timeZone),
          ends_at: toZonedInput(new Date(Date.now() + 2 * 86_400_000).toISOString(), timeZone),
          items: [],
        }
      : null,
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (creating) return;
    void api
      .get<unknown>(`/api/manage/marketing/flash-sales/${id}/`)
      .then((value) => {
        const decoded = flashSaleDetail(value);
        setDetail(decoded);
        setForm(flashForm(decoded, timeZone));
      })
      .catch((e: unknown) => setError(errorMessage(e)));
  }, [creating, id, timeZone]);

  const locked = detail !== null && ['live', 'ended', 'cancelled'].includes(detail.state);

  async function save() {
    if (!form) return;
    const starts = fromZonedInput(form.starts_at, timeZone);
    const ends = fromZonedInput(form.ends_at, timeZone);
    if (!starts || !ends) {
      setError('Enter a start and end date and time.');
      return;
    }
    const limit = (text: string) => (text.trim() ? Number(text.trim()) : null);
    setBusy(true);
    setError('');
    setNotice('');
    const body = {
      name: form.name.trim(),
      description: form.description.trim(),
      starts_at: starts,
      ends_at: ends,
      items: form.items.map((item) => ({
        variant: item.variant,
        sale_price: item.sale_price.trim(),
        quantity_limit: limit(item.quantity_limit),
        per_order_limit: limit(item.per_order_limit),
      })),
    };
    try {
      if (creating) {
        const created = flashSaleDetail(
          await api.post<unknown>('/api/manage/marketing/flash-sales/', body),
        );
        window.location.assign(`/manage/marketing/flash-sales/${created.id}`);
        return;
      }
      const updated = flashSaleDetail(
        await api.patch<unknown>(`/api/manage/marketing/flash-sales/${id}/`, body),
      );
      setDetail(updated);
      setForm(flashForm(updated, timeZone));
      setNotice('Flash sale saved.');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(action: PromotionAction) {
    setError('');
    setNotice('');
    try {
      const updated = flashSaleDetail(
        await api.post<unknown>(`/api/manage/marketing/flash-sales/${id}/status/`, { action }),
      );
      setDetail(updated);
      setForm(flashForm(updated, timeZone));
      setNotice(`Flash sale is now ${STATE_LABEL[updated.state].toLowerCase()}.`);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!form) return error ? <Feedback error={error} /> : <Loader>Loading flash sale…</Loader>;
  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch });
  const setItem = (index: number, patch: Partial<FlashItemForm>) =>
    set({ items: form.items.map((item, i) => (i === index ? { ...item, ...patch } : item)) });
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Flash sales"
        title={creating ? 'New flash sale' : detail?.name || 'Flash sale'}
        intro={STACKING_POLICY}
        actions={
          detail && (
            <>
              <StateBadge state={detail.state} />
              <StatusActions state={detail.state} onAction={act} />
            </>
          )
        }
      />
      <Feedback error={error} notice={notice} />
      {detail && !detail.module_enabled && (
        <Alert tone="info">
          The Flash sales module is off, so this sale cannot be activated and no sale price is
          shown.{' '}
          <Link href="/manage/settings" className="font-semibold underline">
            Turn it on in Settings
          </Link>
          .
        </Alert>
      )}
      {detail && <LockedNote state={detail.state} />}
      <form
        className="grid gap-6"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <fieldset disabled={locked} className="grid gap-6">
          <Card title="Sale">
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label="Name"
                required
                maxLength={120}
                value={form.name}
                hint="Customers see this name next to the countdown."
                onChange={(e) => set({ name: e.target.value })}
              />
              <TextField
                label="Internal description"
                maxLength={500}
                value={form.description}
                onChange={(e) => set({ description: e.target.value })}
              />
              <TextField
                label={`Starts (${timeZone})`}
                type="datetime-local"
                required
                value={form.starts_at}
                onChange={(e) => set({ starts_at: e.target.value })}
              />
              <TextField
                label={`Ends (${timeZone})`}
                type="datetime-local"
                required
                value={form.ends_at}
                hint="At most 14 days after the start."
                onChange={(e) => set({ ends_at: e.target.value })}
              />
            </div>
          </Card>
          <Card title="Items">
            <TargetPicker
              kind="variants"
              selected={form.items.map((item) => item.variant)}
              onPick={(row) =>
                set({
                  items: [
                    ...form.items,
                    {
                      variant: row.id,
                      label: `${row.name} (${row.detail})`,
                      regular: row.regular_amount,
                      currency: row.currency,
                      sale_price: '',
                      quantity_limit: '',
                      per_order_limit: '',
                      units_sold: '0',
                    },
                  ],
                })
              }
            />
            {form.items.length === 0 ? (
              <p className="mt-4 text-sm text-muted">Add the variants on sale.</p>
            ) : (
              <ul className="mt-4 grid gap-4">
                {form.items.map((item, index) => (
                  <li key={item.variant} className="rounded-base border border-line p-4">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-semibold">
                        {item.label}
                        <span className="block text-xs font-normal text-muted">
                          Regular {money(item.regular, item.currency)} · {quantity(item.units_sold)}{' '}
                          sold at the sale price
                        </span>
                      </p>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => set({ items: form.items.filter((_, i) => i !== index) })}
                      >
                        Remove
                      </Button>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-3">
                      <TextField
                        label="Sale price"
                        inputMode="decimal"
                        required
                        value={item.sale_price}
                        hint="Must be below the regular price."
                        onChange={(e) => setItem(index, { sale_price: e.target.value })}
                      />
                      <TextField
                        label="Units at this price"
                        inputMode="numeric"
                        value={item.quantity_limit}
                        hint="Empty = no limit."
                        onChange={(e) => setItem(index, { quantity_limit: e.target.value })}
                      />
                      <TextField
                        label="Most per order"
                        inputMode="numeric"
                        value={item.per_order_limit}
                        hint="Larger quantities pay the regular price."
                        onChange={(e) => setItem(index, { per_order_limit: e.target.value })}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <div>
            <Button type="submit" busy={busy}>
              {creating ? 'Create as draft' : 'Save changes'}
            </Button>
          </div>
        </fieldset>
      </form>
      {detail?.performance && (
        <Card title="Results">
          <p className="text-sm">{performanceText(detail.performance)}</p>
        </Card>
      )}
    </div>
  );
}

// --- Coupons --------------------------------------------------------------------------------------

export function CouponManager({ timeZone }: { timeZone: string }) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<ManagedCoupon | 'new' | null>(null);
  const [notice, setNotice] = useState('');
  const load = useCallback(
    async () =>
      couponPage(
        await api.get<unknown>(
          `/api/manage/marketing/coupons/${query({ search, page, page_size: 25 })}`,
        ),
      ),
    [search, page],
  );
  const { data, error, loading, reload } = useLoad(load);
  return (
    <div>
      <PageHeader
        eyebrow="Marketing"
        title="Coupons"
        intro="Codes customers enter at checkout. Coupons apply only to items at their regular price; items in a campaign or flash sale are excluded."
        actions={<Button onClick={() => setEditing('new')}>New coupon</Button>}
      />
      <Feedback error={error} notice={notice} />
      <Card flush>
        <Filters>
          <TextField
            label="Search by code"
            type="search"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </Filters>
        {loading && !data ? (
          <Loader>Loading coupons…</Loader>
        ) : !data || data.results.length === 0 ? (
          <Empty title="No coupons yet" />
        ) : (
          <>
            <Table
              head={['Code', 'Discount', 'Minimum', 'Valid (store time)', 'Used', 'Status', '']}
            >
              {data.results.map((row) => (
                <tr key={row.id}>
                  <Cell className="font-mono font-semibold">{row.code}</Cell>
                  <Cell>
                    {row.discount_type === 'percent'
                      ? `${quantity(row.value)}%`
                      : money(row.value, row.currency)}
                  </Cell>
                  <Cell>
                    {Number(row.minimum_subtotal) > 0 ? quantity(row.minimum_subtotal) : '—'}
                  </Cell>
                  <Cell className="text-xs">
                    {row.starts_at ? formatZoned(row.starts_at, timeZone) : 'Now'} –{' '}
                    {row.ends_at ? formatZoned(row.ends_at, timeZone) : 'no end'}
                  </Cell>
                  <Cell>
                    {row.times_redeemed}
                    {row.max_redemptions !== null && ` / ${row.max_redemptions}`}
                  </Cell>
                  <Cell>
                    <Badge tone={row.is_active ? 'success' : 'neutral'}>
                      {row.is_active ? 'Active' : 'Paused'}
                    </Badge>
                  </Cell>
                  <Cell>
                    <Button variant="secondary" onClick={() => setEditing(row)}>
                      Edit
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
      </Card>
      {editing && (
        <CouponDialog
          coupon={editing === 'new' ? null : editing}
          timeZone={timeZone}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            setNotice(`Coupon ${saved.code} saved.`);
            void reload();
          }}
        />
      )}
    </div>
  );
}

function CouponDialog({
  coupon,
  timeZone,
  onClose,
  onSaved,
}: {
  coupon: ManagedCoupon | null;
  timeZone: string;
  onClose: () => void;
  onSaved: (coupon: ManagedCoupon) => void;
}) {
  const used = (coupon?.times_redeemed ?? 0) > 0;
  const [form, setForm] = useState({
    code: coupon?.code ?? '',
    discount_type: coupon?.discount_type ?? 'percent',
    value: coupon ? quantity(coupon.value) : '',
    minimum_subtotal: coupon ? quantity(coupon.minimum_subtotal) : '0',
    starts_at: toZonedInput(coupon?.starts_at, timeZone),
    ends_at: toZonedInput(coupon?.ends_at, timeZone),
    max_redemptions:
      coupon?.max_redemptions === null || !coupon ? '' : String(coupon.max_redemptions),
    is_active: coupon?.is_active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch });
  const when = (value: string) => (value ? fromZonedInput(value, timeZone) : null);

  async function submit() {
    setBusy(true);
    setError('');
    const shared = {
      starts_at: when(form.starts_at),
      ends_at: when(form.ends_at),
      max_redemptions: form.max_redemptions.trim() ? Number(form.max_redemptions) : null,
      is_active: form.is_active,
    };
    const money = used
      ? {}
      : { value: form.value.trim(), minimum_subtotal: form.minimum_subtotal.trim() || '0' };
    try {
      const saved = managedCoupon(
        coupon
          ? await api.patch<unknown>(`/api/manage/marketing/coupons/${coupon.id}/`, {
              ...shared,
              ...money,
            })
          : await api.post<unknown>('/api/manage/marketing/coupons/', {
              code: form.code.trim().toUpperCase(),
              discount_type: form.discount_type,
              ...shared,
              ...money,
            }),
      );
      onSaved(saved);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title={coupon ? `Coupon ${coupon.code}` : 'New coupon'} onClose={onClose}>
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        {!coupon && (
          <>
            <TextField
              label="Code"
              required
              maxLength={32}
              value={form.code}
              hint="Letters, digits, - and _; customers type it at checkout."
              onChange={(e) => set({ code: e.target.value.toUpperCase() })}
            />
            <SelectField
              label="Discount type"
              value={form.discount_type}
              onChange={(e) =>
                set({ discount_type: e.target.value === 'fixed' ? 'fixed' : 'percent' })
              }
              options={[
                { value: 'percent', label: 'Percentage of eligible items' },
                { value: 'fixed', label: 'Fixed amount (store currency)' },
              ]}
            />
          </>
        )}
        {used && (
          <Alert tone="info">
            This coupon has been used {coupon?.times_redeemed} times, so its discount and minimum
            stay as they were (past orders keep their meaning). Create a new coupon for a different
            offer.
          </Alert>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label={form.discount_type === 'percent' ? 'Percent off' : 'Amount off'}
            inputMode="decimal"
            required
            disabled={used}
            value={form.value}
            onChange={(e) => set({ value: e.target.value })}
          />
          <TextField
            label="Minimum cart subtotal"
            inputMode="decimal"
            disabled={used}
            value={form.minimum_subtotal}
            onChange={(e) => set({ minimum_subtotal: e.target.value })}
          />
          <TextField
            label={`Starts (${timeZone})`}
            type="datetime-local"
            value={form.starts_at}
            hint="Optional."
            onChange={(e) => set({ starts_at: e.target.value })}
          />
          <TextField
            label={`Ends (${timeZone})`}
            type="datetime-local"
            value={form.ends_at}
            hint="Optional."
            onChange={(e) => set({ ends_at: e.target.value })}
          />
          <TextField
            label="Usage limit"
            inputMode="numeric"
            value={form.max_redemptions}
            hint="Empty = unlimited. Cancelled orders give their use back."
            onChange={(e) => set({ max_redemptions: e.target.value })}
          />
        </div>
        <label className="flex items-center gap-3 text-sm font-semibold">
          <input
            type="checkbox"
            className="h-4 w-4"
            checked={form.is_active}
            onChange={(e) => set({ is_active: e.target.checked })}
          />
          Active
        </label>
        {error && <Alert>{error}</Alert>}
        <div className="flex justify-end gap-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={busy}>
            Save coupon
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
