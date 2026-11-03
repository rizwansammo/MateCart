'use client';

import { useCallback, useEffect, useState } from 'react';
import type {
  ManagedProduct,
  ManagedProductSummary,
  StockLocation,
  StockMovement,
} from '@matecart/shared-contracts';
import {
  inventoryOperation,
  managePage,
  managedProduct,
  managedProductSummary,
  reconciliation,
  serialUnit,
  stockBatch,
  stockLevel,
  stockLocation,
  stockMovement,
} from '@matecart/shared-contracts';
import { Alert, Button, Dialog, TextField, errorMessage } from '@matecart/shared-ui';
import { TextArea } from '../appearance-app';
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
  date,
  dateTime,
  isoDay,
  newKey,
  quantity,
  query,
  useLoad,
} from './kit';

const BASE = '/api/manage/inventory';
const PAGE_SIZE = 25;
const TABS = [
  { key: 'levels', label: 'Stock levels' },
  { key: 'change', label: 'Receive or adjust' },
  { key: 'movements', label: 'Movement history' },
  { key: 'batches', label: 'Batches and expiry' },
  { key: 'serials', label: 'Serial numbers' },
  { key: 'locations', label: 'Locations' },
  { key: 'check', label: 'Reconciliation' },
] as const;
type Tab = (typeof TABS)[number]['key'];

const MOVEMENT_LABEL: Record<string, string> = {
  receipt: 'Received',
  adjustment: 'Count adjustment',
  damaged: 'Damaged',
  lost: 'Lost',
  returned: 'Returned to stock',
  transfer_out: 'Transferred out',
  transfer_in: 'Transferred in',
  reserved: 'Reserved for order',
  released: 'Reservation released',
  fulfilled: 'Shipped',
  reversal: 'Reversal',
};
const REVERSIBLE = new Set([
  'receipt',
  'adjustment',
  'damaged',
  'lost',
  'returned',
  'transfer_out',
  'transfer_in',
]);

function useLocations() {
  const load = useCallback(
    async () =>
      managePage(stockLocation)(await api.get<unknown>(`${BASE}/locations/?page_size=100`)).results,
    [],
  );
  return useLoad(load);
}

function signed(value: string): string {
  const shown = quantity(value);
  return value.startsWith('-') ? shown : `+${shown}`;
}

function Levels({ locations }: { locations: StockLocation[] }) {
  const product =
    typeof window === 'undefined'
      ? ''
      : (new URLSearchParams(window.location.search).get('product') ?? '');
  const [filters, setFilters] = useState({ product, location: '', alert: '' });
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      managePage(stockLevel)(
        await api.get<unknown>(
          `${BASE}/levels/${query({
            product: filters.product,
            location: filters.location,
            low_stock: filters.alert === 'low' ? 'true' : '',
            out_of_stock: filters.alert === 'out' ? 'true' : '',
            page,
            page_size: PAGE_SIZE,
          })}`,
        ),
      ),
    [filters, page],
  );
  const { data, error, loading, reload } = useLoad(load);
  const [threshold, setThreshold] = useState<{ id: string; value: string } | null>(null);
  const [saveError, setSaveError] = useState('');
  return (
    <Card flush>
      <Feedback error={error || saveError} />
      <Filters>
        <SelectField
          label="Location"
          value={filters.location}
          onChange={(e) => {
            setPage(1);
            setFilters({ ...filters, location: e.target.value });
          }}
          options={[
            { value: '', label: 'All locations' },
            ...locations.map((l) => ({ value: l.id, label: l.name })),
          ]}
        />
        <SelectField
          label="Show"
          value={filters.alert}
          onChange={(e) => {
            setPage(1);
            setFilters({ ...filters, alert: e.target.value });
          }}
          options={[
            { value: '', label: 'All stock' },
            { value: 'low', label: 'Low stock' },
            { value: 'out', label: 'Out of stock' },
          ]}
        />
        {filters.product && (
          <div className="flex items-end">
            <Button variant="secondary" onClick={() => setFilters({ ...filters, product: '' })}>
              Show all products
            </Button>
          </div>
        )}
      </Filters>
      {loading && !data ? (
        <Loader>Loading stock…</Loader>
      ) : !data?.results.length ? (
        <Empty title="No stock levels">
          Stock levels appear after the first receipt of a tracked product.
        </Empty>
      ) : (
        <>
          <Table
            head={['Product', 'Location', 'On hand', 'Reserved', 'Available', 'Low-stock alert']}
          >
            {data.results.map((row) => (
              <tr key={row.id}>
                <Cell>
                  <span className="font-semibold">{row.variant.product_name}</span>
                  <p className="text-xs text-muted">
                    {[row.variant.title, row.variant.sku].filter(Boolean).join(' · ')}
                  </p>
                </Cell>
                <Cell>{row.location.name}</Cell>
                <Cell className="tabular-nums">{quantity(row.on_hand)}</Cell>
                <Cell className="tabular-nums">{quantity(row.reserved)}</Cell>
                <Cell className="tabular-nums">
                  {quantity(row.available)} {row.is_low && <Badge tone="danger">Low</Badge>}
                </Cell>
                <Cell>
                  {threshold?.id === row.id ? (
                    <form
                      className="flex items-end gap-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        setSaveError('');
                        void api
                          .patch(`${BASE}/levels/${row.id}/`, {
                            low_stock_threshold: threshold.value === '' ? null : threshold.value,
                          })
                          .then(async () => {
                            setThreshold(null);
                            await reload();
                          })
                          .catch((err: unknown) => setSaveError(errorMessage(err)));
                      }}
                    >
                      <div className="w-24">
                        <TextField
                          label="Alert at"
                          inputMode="decimal"
                          value={threshold.value}
                          onChange={(e) => setThreshold({ id: row.id, value: e.target.value })}
                        />
                      </div>
                      <Button type="submit" className="min-h-9 px-3 text-xs">
                        Save
                      </Button>
                    </form>
                  ) : (
                    <button
                      type="button"
                      className="text-sm font-semibold text-brand-deep"
                      onClick={() =>
                        setThreshold({
                          id: row.id,
                          value: row.low_stock_threshold ? quantity(row.low_stock_threshold) : '',
                        })
                      }
                    >
                      {row.low_stock_threshold ? quantity(row.low_stock_threshold) : 'Set alert'}
                    </button>
                  )}
                </Cell>
              </tr>
            ))}
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function VariantPicker({ onPick }: { onPick: (product: ManagedProduct, variant: string) => void }) {
  const [text, setText] = useState('');
  const [results, setResults] = useState<ManagedProductSummary[]>([]);
  const [product, setProduct] = useState<ManagedProduct | null>(null);
  const [error, setError] = useState('');
  // Lives inside the stock-change form, so it searches on Enter or click without a nested form.
  function find() {
    setError('');
    void api
      .get<unknown>(`/api/manage/catalog/products/${query({ q: text, page_size: 10 })}`)
      .then((value) => setResults(managePage(managedProductSummary)(value).results))
      .catch((err: unknown) => setError(errorMessage(err)));
  }
  return (
    <div className="grid gap-3">
      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <TextField
            label="Product"
            placeholder="Search by name or SKU"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                find();
              }
            }}
          />
        </div>
        <Button type="button" variant="secondary" onClick={find}>
          Find
        </Button>
      </div>
      {error && <Alert>{error}</Alert>}
      {results.length > 0 && !product && (
        <ul className="divide-y divide-line rounded-base border border-line text-sm">
          {results.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                className="w-full px-3 py-2 text-left hover:bg-canvas"
                onClick={() => {
                  void api
                    .get<unknown>(`/api/manage/catalog/products/${row.id}/`)
                    .then((value) => {
                      const detail = managedProduct(value);
                      setProduct(detail);
                      if (detail.variants.length === 1 && detail.variants[0])
                        onPick(detail, detail.variants[0].id);
                    })
                    .catch((err: unknown) => setError(errorMessage(err)));
                }}
              >
                {row.name}{' '}
                <span className="text-xs text-muted">· {row.variant_count} variant(s)</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {product && (
        <div className="grid gap-2 rounded-base border border-line p-3 text-sm">
          <p>
            <strong>{product.name}</strong>{' '}
            <button type="button" className="ml-2 text-brand-deep" onClick={() => setProduct(null)}>
              Change
            </button>
          </p>
          {!product.track_inventory && <Alert>This product does not track stock.</Alert>}
          {product.variants.length > 1 && (
            <SelectField
              label="Variant"
              defaultValue=""
              onChange={(e) => e.target.value && onPick(product, e.target.value)}
              options={[
                { value: '', label: 'Choose a variant' },
                ...product.variants.map((v) => ({
                  value: v.id,
                  label: `${v.title || 'Default'} · ${v.sku}`,
                })),
              ]}
            />
          )}
        </div>
      )}
    </div>
  );
}

function StockChange({ locations }: { locations: StockLocation[] }) {
  const [mode, setMode] = useState<'receipt' | 'adjustment' | 'transfer'>('receipt');
  const [target, setTarget] = useState<{ product: ManagedProduct; variant: string } | null>(null);
  const [form, setForm] = useState({
    location: '',
    to_location: '',
    quantity: '',
    movement_type: 'adjustment',
    reason: '',
    reference_id: '',
    batch_code: '',
    expires_on: '',
    manufactured_on: '',
    serials: '',
  });
  const [key, setKey] = useState(() => newKey('stock'));
  const [error, setError] = useState('');
  const [result, setResult] = useState<StockMovement[] | null>(null);
  const [busy, setBusy] = useState(false);
  const mode_ = target?.product.tracking_mode ?? 'none';
  const set = (name: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [name]: e.target.value });
  const serials = form.serials.split(/\s+/).filter(Boolean);
  function body(): [string, Record<string, unknown>] {
    const common = {
      variant: target?.variant,
      quantity:
        mode_ === 'serial' && mode !== 'adjustment' ? String(serials.length) : form.quantity,
      reason: form.reason,
      reference_type: form.reference_id ? 'manual' : '',
      reference_id: form.reference_id,
      idempotency_key: key,
      ...(mode_ === 'serial' ? { serials } : {}),
    };
    const batch =
      mode_ === 'batch' && form.batch_code
        ? {
            code: form.batch_code,
            expires_on: form.expires_on || null,
            manufactured_on: form.manufactured_on || null,
          }
        : null;
    if (mode === 'receipt')
      return [`${BASE}/receipts/`, { ...common, location: form.location || null, batch }];
    if (mode === 'adjustment')
      return [
        `${BASE}/adjustments/`,
        { ...common, location: form.location || null, movement_type: form.movement_type, batch },
      ];
    return [
      `${BASE}/transfers/`,
      {
        ...common,
        from_location: form.location,
        to_location: form.to_location,
        batch: mode_ === 'batch' ? form.batch_code || null : null,
      },
    ];
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card title="Record a stock change">
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!target) return;
            setBusy(true);
            setError('');
            const [path, payload] = body();
            void api
              .post<unknown>(path, payload)
              .then((value) => {
                setResult(inventoryOperation(value).movements);
                setKey(newKey('stock'));
                setForm({
                  ...form,
                  quantity: '',
                  reason: '',
                  reference_id: '',
                  batch_code: '',
                  expires_on: '',
                  manufactured_on: '',
                  serials: '',
                });
              })
              .catch((err: unknown) => setError(errorMessage(err)))
              .finally(() => setBusy(false));
          }}
        >
          <div role="radiogroup" aria-label="Kind of change" className="flex flex-wrap gap-2">
            {(
              [
                ['receipt', 'Receive stock'],
                ['adjustment', 'Adjust or write off'],
                ['transfer', 'Transfer'],
              ] as const
            ).map(([value, text]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={mode === value}
                onClick={() => setMode(value)}
                className={`rounded-base border px-3 py-1.5 text-sm font-semibold ${mode === value ? 'border-ink bg-ink text-surface' : 'border-line-strong'}`}
              >
                {text}
              </button>
            ))}
          </div>
          <VariantPicker onPick={(product, variant) => setTarget({ product, variant })} />
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField
              label={mode === 'transfer' ? 'From location' : 'Location'}
              required={mode === 'transfer'}
              value={form.location}
              onChange={set('location')}
              options={[
                { value: '', label: mode === 'transfer' ? 'Choose' : 'Default location' },
                ...locations
                  .filter((l) => l.is_active)
                  .map((l) => ({ value: l.id, label: l.name })),
              ]}
            />
            {mode === 'transfer' && (
              <SelectField
                label="To location"
                required
                value={form.to_location}
                onChange={set('to_location')}
                options={[
                  { value: '', label: 'Choose' },
                  ...locations
                    .filter((l) => l.is_active)
                    .map((l) => ({ value: l.id, label: l.name })),
                ]}
              />
            )}
            {mode === 'adjustment' && (
              <SelectField
                label="Type"
                value={form.movement_type}
                onChange={set('movement_type')}
                options={[
                  { value: 'adjustment', label: 'Stock count correction (+ or −)' },
                  { value: 'damaged', label: 'Damaged (removes stock)' },
                  { value: 'lost', label: 'Lost (removes stock)' },
                  { value: 'returned', label: 'Returned to stock' },
                ]}
              />
            )}
            {!(mode_ === 'serial' && mode !== 'adjustment') && (
              <TextField
                label="Quantity"
                required
                inputMode="decimal"
                value={form.quantity}
                onChange={set('quantity')}
                hint={
                  mode === 'adjustment' && form.movement_type === 'adjustment'
                    ? 'Use a minus sign to reduce, e.g. -2.'
                    : undefined
                }
              />
            )}
          </div>
          {mode_ === 'batch' && (
            <div className="grid gap-4 sm:grid-cols-3">
              <TextField
                label="Batch code"
                required={mode !== 'adjustment'}
                maxLength={64}
                value={form.batch_code}
                onChange={set('batch_code')}
              />
              {mode !== 'transfer' && (
                <TextField
                  label="Expires on"
                  type="date"
                  value={form.expires_on}
                  onChange={set('expires_on')}
                />
              )}
              {mode === 'receipt' && (
                <TextField
                  label="Made on"
                  type="date"
                  value={form.manufactured_on}
                  onChange={set('manufactured_on')}
                />
              )}
            </div>
          )}
          {mode_ === 'serial' && (
            <TextArea
              label="Serial or IMEI numbers"
              required
              value={form.serials}
              onChange={set('serials')}
              hint={`One per line or separated by spaces. ${serials.length} entered.`}
            />
          )}
          <TextArea
            label="Reason"
            required={mode === 'adjustment'}
            maxLength={500}
            value={form.reason}
            onChange={set('reason')}
          />
          <TextField
            label="Supplier or reference (optional)"
            maxLength={100}
            value={form.reference_id}
            onChange={set('reference_id')}
          />
          {error && <Alert>{error}</Alert>}
          <div>
            <Button type="submit" busy={busy} disabled={!target}>
              Record change
            </Button>
          </div>
          <p className="text-xs text-muted">
            Every change is written to the stock ledger with your name. Mistakes are corrected with
            a reversal, never by editing history.
          </p>
        </form>
      </Card>
      <Card title="Last change">
        {result ? (
          <ul className="grid gap-2 text-sm">
            {result.map((m) => (
              <li key={m.id}>
                <strong>{MOVEMENT_LABEL[m.movement_type] ?? m.movement_type}</strong> {m.sku} at{' '}
                {m.location_code}: {signed(m.on_hand_delta)} → {quantity(m.on_hand_after)} on hand
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">The recorded ledger entries appear here.</p>
        )}
      </Card>
    </div>
  );
}

function Movements() {
  const [type, setType] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      managePage(stockMovement)(
        await api.get<unknown>(
          `${BASE}/movements/${query({ movement_type: type, page, page_size: PAGE_SIZE })}`,
        ),
      ),
    [type, page],
  );
  const { data, error, loading, reload } = useLoad(load);
  const [reversing, setReversing] = useState<StockMovement | null>(null);
  const [reason, setReason] = useState('');
  const [reverseError, setReverseError] = useState('');
  const [reverseKey, setReverseKey] = useState(() => newKey('reverse'));
  const reversed = new Set((data?.results ?? []).map((m) => m.reversal_of).filter(Boolean));
  return (
    <Card flush>
      <Feedback error={error} />
      <Filters>
        <SelectField
          label="Type"
          value={type}
          onChange={(e) => {
            setPage(1);
            setType(e.target.value);
          }}
          options={[
            { value: '', label: 'All movements' },
            ...Object.entries(MOVEMENT_LABEL).map(([value, text]) => ({ value, label: text })),
          ]}
        />
      </Filters>
      {loading && !data ? (
        <Loader>Loading history…</Loader>
      ) : !data?.results.length ? (
        <Empty title="No stock movements yet" />
      ) : (
        <>
          <Table
            head={[
              'When',
              'Type',
              'SKU',
              'Location',
              'Change',
              'On hand after',
              'Batch / serial',
              'Reason',
              '',
            ]}
          >
            {data.results.map((m) => (
              <tr key={m.id}>
                <Cell className="text-xs whitespace-nowrap">{dateTime(m.occurred_at)}</Cell>
                <Cell>{MOVEMENT_LABEL[m.movement_type] ?? m.movement_type}</Cell>
                <Cell>{m.sku}</Cell>
                <Cell>{m.location_code}</Cell>
                <Cell className="tabular-nums">
                  {m.on_hand_delta !== '0.000'
                    ? signed(m.on_hand_delta)
                    : `${signed(m.reserved_delta)} reserved`}
                </Cell>
                <Cell className="tabular-nums">{quantity(m.on_hand_after)}</Cell>
                <Cell className="text-xs">{m.batch ?? m.serial ?? '—'}</Cell>
                <Cell className="text-xs">{m.reason}</Cell>
                <Cell>
                  {REVERSIBLE.has(m.movement_type) &&
                    !m.reversal_of &&
                    !reversed.has(m.id) &&
                    !m.serial && (
                      <Button
                        variant="secondary"
                        className="min-h-8 px-2 text-xs"
                        onClick={() => {
                          setReversing(m);
                          setReason('');
                          setReverseError('');
                        }}
                      >
                        Reverse
                      </Button>
                    )}
                </Cell>
              </tr>
            ))}
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
        </>
      )}
      {reversing && (
        <Dialog title="Reverse this change?" onClose={() => setReversing(null)}>
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              void api
                .post(`${BASE}/movements/${reversing.id}/reverse/`, {
                  reason,
                  idempotency_key: reverseKey,
                })
                .then(async () => {
                  setReversing(null);
                  setReverseKey(newKey('reverse'));
                  await reload();
                })
                .catch((err: unknown) => setReverseError(errorMessage(err)));
            }}
          >
            <p className="text-sm text-muted">
              A compensating entry is added for the whole operation; the original stays in the
              history. Stock held by orders and serial-number changes are corrected with an
              adjustment instead.
            </p>
            <TextArea
              label="Reason"
              required
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            {reverseError && <Alert>{reverseError}</Alert>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setReversing(null)}>
                Cancel
              </Button>
              <Button type="submit">Reverse</Button>
            </div>
          </form>
        </Dialog>
      )}
    </Card>
  );
}

function Batches() {
  const [window_, setWindow] = useState('');
  const [page, setPage] = useState(1);
  const today = isoDay();
  const load = useCallback(
    async () =>
      managePage(stockBatch)(
        await api.get<unknown>(
          `${BASE}/batches/${query({ expires_before: window_ ? isoDay(Number(window_)) : '', page, page_size: PAGE_SIZE })}`,
        ),
      ),
    [window_, page],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <Card flush>
      <Feedback error={error} />
      <Filters>
        <SelectField
          label="Expiring"
          value={window_}
          onChange={(e) => {
            setPage(1);
            setWindow(e.target.value);
          }}
          options={[
            { value: '', label: 'All batches, soonest first' },
            { value: '1', label: 'Expired or expiring today' },
            { value: '8', label: 'Within 7 days' },
            { value: '31', label: 'Within 30 days' },
          ]}
        />
      </Filters>
      {loading && !data ? (
        <Loader />
      ) : !data?.results.length ? (
        <Empty title="No batches">
          Batches are created when you receive batch-tracked products.
        </Empty>
      ) : (
        <>
          <Table head={['Batch', 'SKU', 'Expires', 'Received', 'Stock by location']}>
            {data.results.map((b) => {
              const expired = b.expires_on !== null && b.expires_on < today;
              return (
                <tr key={b.id}>
                  <Cell className="font-semibold">{b.code}</Cell>
                  <Cell>{b.sku}</Cell>
                  <Cell>
                    {date(b.expires_on)} {expired && <Badge tone="danger">Expired, not sold</Badge>}
                  </Cell>
                  <Cell>{date(b.received_on)}</Cell>
                  <Cell className="text-xs">
                    {b.levels
                      .map((l) => `${l.location_code}: ${quantity(l.on_hand)}`)
                      .join(' · ') || '—'}
                  </Cell>
                </tr>
              );
            })}
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function Serials() {
  const [filters, setFilters] = useState({ status: '', q: '' });
  const [text, setText] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      managePage(serialUnit)(
        await api.get<unknown>(
          `${BASE}/serials/${query({ ...filters, page, page_size: PAGE_SIZE })}`,
        ),
      ),
    [filters, page],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <Card flush>
      <Feedback error={error} />
      <Filters>
        <SelectField
          label="Status"
          value={filters.status}
          onChange={(e) => {
            setPage(1);
            setFilters({ ...filters, status: e.target.value });
          }}
          options={[
            { value: '', label: 'All' },
            ...['available', 'reserved', 'sold', 'damaged', 'lost'].map((v) => ({
              value: v,
              label: v[0]?.toUpperCase() + v.slice(1),
            })),
          ]}
        />
        <form
          className="flex items-end gap-2 sm:col-span-2"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            setFilters({ ...filters, q: text.trim() });
          }}
        >
          <div className="min-w-0 flex-1">
            <TextField
              label="Find serial or IMEI"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>
      </Filters>
      {loading && !data ? (
        <Loader />
      ) : !data?.results.length ? (
        <Empty title="No serial numbers" />
      ) : (
        <>
          <Table head={['Serial', 'SKU', 'Status', 'Updated']}>
            {data.results.map((s) => (
              <tr key={s.id}>
                <Cell className="font-mono text-xs">{s.serial}</Cell>
                <Cell>{s.sku}</Cell>
                <Cell>
                  <Badge
                    tone={
                      s.status === 'available'
                        ? 'success'
                        : s.status === 'sold'
                          ? 'neutral'
                          : s.status === 'reserved'
                            ? 'info'
                            : 'danger'
                    }
                  >
                    {s.status}
                  </Badge>
                </Cell>
                <Cell className="text-xs">{dateTime(s.updated_at)}</Cell>
              </tr>
            ))}
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function Locations({
  locations,
  reload,
}: {
  locations: StockLocation[];
  reload: () => Promise<void>;
}) {
  const [form, setForm] = useState({ code: '', name: '', city: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <div className="grid gap-6">
      <Card title="Add a location">
        <form
          className="grid gap-3 sm:grid-cols-4 sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            void api
              .post(`${BASE}/locations/`, form)
              .then(async () => {
                setForm({ code: '', name: '', city: '' });
                await reload();
              })
              .catch((err: unknown) => setError(errorMessage(err)))
              .finally(() => setBusy(false));
          }}
        >
          <TextField
            label="Code"
            required
            maxLength={32}
            value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value })}
          />
          <TextField
            label="Name"
            required
            maxLength={120}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <TextField
            label="City"
            maxLength={100}
            value={form.city}
            onChange={(e) => setForm({ ...form, city: e.target.value })}
          />
          <Button type="submit" busy={busy}>
            Add location
          </Button>
        </form>
        {error && (
          <div className="mt-3">
            <Alert>{error}</Alert>
          </div>
        )}
      </Card>
      <Card title="Locations" flush>
        <Table head={['Name', 'Code', 'City', 'Status']}>
          {locations.map((l) => (
            <tr key={l.id}>
              <Cell className="font-semibold">{l.name}</Cell>
              <Cell>{l.code}</Cell>
              <Cell>{l.city || '—'}</Cell>
              <Cell>
                {l.is_default && <Badge tone="info">Default</Badge>}{' '}
                {l.is_active ? <Badge tone="success">Active</Badge> : <Badge>Inactive</Badge>}
              </Cell>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

function Reconcile() {
  const load = useCallback(
    async () => reconciliation(await api.get<unknown>(`${BASE}/reconciliation/`)),
    [],
  );
  const { data, error, loading, reload } = useLoad(load);
  return (
    <Card
      title="Ledger reconciliation"
      actions={
        <Button variant="secondary" onClick={() => void reload()}>
          Check again
        </Button>
      }
    >
      <Feedback error={error} />
      {loading && !data ? (
        <Loader>Checking the stock ledger…</Loader>
      ) : data?.consistent ? (
        <Alert tone="success">Stock levels match the movement ledger exactly.</Alert>
      ) : data ? (
        <div className="grid gap-3">
          <Alert>
            Some stock levels differ from the ledger. Contact NetaMate support before adjusting.
          </Alert>
          <Table head={['Kind', 'Expected', 'Actual']}>
            {data.discrepancies.map((d, i) => (
              <tr key={i}>
                <Cell>{d.kind}</Cell>
                <Cell className="tabular-nums">{quantity(d.expected)}</Cell>
                <Cell className="tabular-nums">{quantity(d.actual)}</Cell>
              </tr>
            ))}
          </Table>
        </div>
      ) : null}
    </Card>
  );
}

export function InventoryManager() {
  const [tab, setTab] = useState<Tab>(() => {
    if (typeof window === 'undefined') return 'levels';
    const wanted = new URLSearchParams(window.location.search).get('tab');
    return TABS.find((t) => t.key === wanted)?.key ?? 'levels';
  });
  const { data: locations, reload } = useLocations();
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set('tab', tab);
    window.history.replaceState(null, '', url);
  }, [tab]);
  return (
    <div>
      <PageHeader
        eyebrow="Sell"
        title="Inventory"
        intro="Stock on hand, reserved for orders and available to sell, with the full movement history. Expired batch stock is never sold."
      />
      <div role="tablist" aria-label="Inventory sections" className="mb-6 flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-base border px-3 py-1.5 text-sm font-semibold ${tab === t.key ? 'border-ink bg-ink text-surface' : 'border-line-strong bg-surface'}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'levels' && <Levels locations={locations ?? []} />}
      {tab === 'change' && <StockChange locations={locations ?? []} />}
      {tab === 'movements' && <Movements />}
      {tab === 'batches' && <Batches />}
      {tab === 'serials' && <Serials />}
      {tab === 'locations' && <Locations locations={locations ?? []} reload={reload} />}
      {tab === 'check' && <Reconcile />}
    </div>
  );
}
