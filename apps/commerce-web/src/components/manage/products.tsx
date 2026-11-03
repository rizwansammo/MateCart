'use client';

import { useCallback, useEffect, useState } from 'react';
import type {
  ManagedPriceList,
  ManagedProduct,
  ManagedVariant,
  NamedEntity,
  ProductDetails,
  StaffSession,
} from '@matecart/shared-contracts';
import {
  managePage,
  managedCategory,
  managedPriceList,
  managedProduct,
  managedProductSummary,
  namedEntity,
  storefrontConfig,
} from '@matecart/shared-contracts';
import { Alert, Button, ConfirmDialog, TextField, errorMessage } from '@matecart/shared-ui';
import { TextArea, uploadImage } from '../appearance-app';
import {
  Badge,
  Card,
  Cell,
  Empty,
  Feedback,
  Filters,
  Loader,
  PRODUCT_STATUS,
  PageHeader,
  Pagination,
  SelectField,
  StatusBadge,
  Table,
  api,
  dateTime,
  money,
  quantity,
  query,
  useLoad,
} from './kit';

const PAGE_SIZE = 25;
const BASE = '/api/manage/catalog';

const TYPE_LABEL: Record<string, string> = {
  standard: 'Standard product',
  apparel: 'Clothing',
  grocery: 'Grocery',
  electronics: 'Electronics',
  book: 'Book',
};
/** Product types each preset offers (the API refuses the others). */
const PRESET_TYPES: Record<string, string[]> = {
  fashion: ['standard', 'apparel'],
  grocery: ['standard', 'grocery'],
  electronics: ['standard', 'electronics'],
  books: ['standard', 'book'],
  general: ['standard', 'apparel', 'grocery', 'electronics', 'book'],
};
const PACK_UNITS = [
  { value: 'g', label: 'g' },
  { value: 'kg', label: 'kg' },
  { value: 'ml', label: 'ml' },
  { value: 'l', label: 'l' },
  { value: 'pcs', label: 'pieces' },
];
const UNITS = [
  { value: 'each', label: 'Each (whole units)' },
  { value: 'kg', label: 'Kilogram' },
  { value: 'g', label: 'Gram' },
  { value: 'l', label: 'Litre' },
  { value: 'ml', label: 'Millilitre' },
  { value: 'm', label: 'Metre' },
];

function typesFor(preset: string) {
  return (PRESET_TYPES[preset] ?? ['standard']).map((value) => ({
    value,
    label: TYPE_LABEL[value] ?? value,
  }));
}

/** Tracking modes the store's modules allow (batch expiry, serial numbers). */
function useTrackingModes() {
  const [modes, setModes] = useState([{ value: 'none', label: 'Quantity only' }]);
  useEffect(() => {
    void api
      .get<unknown>('/api/storefront/config/')
      .then((value) => {
        const modules = storefrontConfig(value).modules;
        setModes([
          { value: 'none', label: 'Quantity only' },
          ...(modules.includes('batch_expiry')
            ? [{ value: 'batch', label: 'Batches and expiry dates' }]
            : []),
          ...(modules.includes('serial_numbers')
            ? [{ value: 'serial', label: 'Serial or IMEI numbers' }]
            : []),
        ]);
      })
      .catch(() => undefined);
  }, []);
  return modes;
}

export function ProductList({ session }: { session: StaffSession }) {
  const [page, setPage] = useState(1);
  const [text, setText] = useState('');
  const [filters, setFilters] = useState({ q: '', status: '', product_type: '' });
  const load = useCallback(
    async () =>
      managePage(managedProductSummary)(
        await api.get<unknown>(
          `${BASE}/products/${query({ ...filters, page, page_size: PAGE_SIZE, ordering: '-updated_at' })}`,
        ),
      ),
    [filters, page],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <div>
      <PageHeader
        eyebrow="Sell"
        title="Products"
        intro="Create and publish what you sell. Only published products appear in your store."
        actions={
          <a
            href="/manage/products/new"
            className="inline-flex min-h-11 items-center rounded-base border border-ink bg-ink px-4 text-sm font-semibold text-surface hover:bg-ink-2"
          >
            Add product
          </a>
        }
      />
      <Feedback error={error} />
      <Card flush>
        <Filters>
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
                label="Search"
                placeholder="Name, SKU or tag"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>
            <Button type="submit" variant="secondary">
              Search
            </Button>
          </form>
          <SelectField
            label="Status"
            value={filters.status}
            onChange={(e) => {
              setPage(1);
              setFilters({ ...filters, status: e.target.value });
            }}
            options={[
              { value: '', label: 'All statuses' },
              ...Object.entries(PRODUCT_STATUS).map(([value, text]) => ({ value, label: text })),
            ]}
          />
          <SelectField
            label="Type"
            value={filters.product_type}
            onChange={(e) => {
              setPage(1);
              setFilters({ ...filters, product_type: e.target.value });
            }}
            options={[{ value: '', label: 'All types' }, ...typesFor(session.store_preset)]}
          />
        </Filters>
        {loading && !data ? (
          <Loader>Loading products…</Loader>
        ) : !data?.results.length ? (
          <Empty
            title={
              filters.q || filters.status || filters.product_type
                ? 'No products match'
                : 'No products yet'
            }
          >
            {filters.q || filters.status || filters.product_type
              ? 'Try another search or filter.'
              : 'Add your first product to start selling.'}
          </Empty>
        ) : (
          <>
            <Table
              head={['Product', 'Type', 'Variants', 'Price', 'Available', 'Status', 'Updated']}
            >
              {data.results.map((row) => (
                <tr key={row.id} className="hover:bg-canvas">
                  <Cell>
                    <a
                      className="font-semibold text-brand-deep"
                      href={`/manage/products/${row.id}`}
                    >
                      {row.name}
                    </a>
                  </Cell>
                  <Cell>{TYPE_LABEL[row.product_type] ?? row.product_type}</Cell>
                  <Cell className="tabular-nums">{row.variant_count}</Cell>
                  <Cell className="tabular-nums">
                    {row.price_range
                      ? row.price_range.min_amount === row.price_range.max_amount
                        ? money(row.price_range.min_amount, row.price_range.currency)
                        : `${money(row.price_range.min_amount, row.price_range.currency)} – ${money(row.price_range.max_amount, row.price_range.currency)}`
                      : '—'}
                  </Cell>
                  <Cell className="tabular-nums">{quantity(row.available)}</Cell>
                  <Cell>
                    <StatusBadge value={row.status} map={PRODUCT_STATUS} />
                  </Cell>
                  <Cell className="text-xs text-muted">{dateTime(row.updated_at)}</Cell>
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

/** Brands and categories for the editor's selects (first 100 each, by name). */
function useChoices() {
  const [brands, setBrands] = useState<NamedEntity[]>([]);
  const [categories, setCategories] = useState<NamedEntity[]>([]);
  useEffect(() => {
    void Promise.all([
      api.get<unknown>(`${BASE}/brands/?page_size=100`),
      api.get<unknown>(`${BASE}/categories/?page_size=100`),
    ])
      .then(([b, c]) => {
        setBrands(managePage(namedEntity)(b).results);
        setCategories(managePage(managedCategory)(c).results);
      })
      .catch(() => undefined);
  }, []);
  return { brands, categories };
}

export function ProductCreate({ session }: { session: StaffSession }) {
  const types = typesFor(session.store_preset);
  const modes = useTrackingModes();
  const { brands, categories } = useChoices();
  const [form, setForm] = useState({
    name: '',
    product_type: types[0]?.value ?? 'standard',
    brand: '',
    category: '',
    summary: '',
    track_inventory: true,
    tracking_mode: 'none',
    sku: '',
    barcode: '',
    amount: '',
    compare_at_amount: '',
    kind: 'physical',
    inventory_unit: 'each',
    net_quantity: '',
    net_unit: 'g',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (name: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [name]: e.target.value });
  return (
    <form
      className="grid max-w-4xl gap-6"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        void api
          .post<unknown>(`${BASE}/products/`, {
            name: form.name,
            product_type: form.product_type,
            brand: form.brand || null,
            summary: form.summary,
            track_inventory: form.kind === 'physical' && form.track_inventory,
            tracking_mode:
              form.kind === 'physical' && form.track_inventory ? form.tracking_mode : 'none',
            ...(form.category
              ? { categories: [form.category], primary_category: form.category }
              : {}),
            variants: [
              {
                sku: form.sku,
                kind: form.kind,
                ...(form.kind === 'digital' ? { digital_format: 'pdf' } : {}),
                barcode: form.barcode || null,
                inventory_unit: form.inventory_unit,
                ...(form.product_type === 'grocery' && form.net_quantity
                  ? { net_quantity: form.net_quantity, net_unit: form.net_unit }
                  : {}),
                price: {
                  amount: form.amount,
                  ...(form.compare_at_amount ? { compare_at_amount: form.compare_at_amount } : {}),
                },
              },
            ],
          })
          .then((value) => window.location.assign(`/manage/products/${managedProduct(value).id}`))
          .catch((err: unknown) => {
            setError(errorMessage(err));
            setBusy(false);
          });
      }}
    >
      <PageHeader
        eyebrow="Products"
        title="Add product"
        intro="Start with the essentials. You can add variants, photos and details after saving. New products are saved as drafts."
      />
      <Feedback error={error} />
      <Card title="Basics">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <TextField
              label="Product name"
              required
              maxLength={200}
              value={form.name}
              onChange={set('name')}
            />
          </div>
          <SelectField
            label="Product type"
            value={form.product_type}
            onChange={set('product_type')}
            options={types}
            hint="Your store's preset decides the available types."
          />
          <SelectField
            label="Brand"
            value={form.brand}
            onChange={set('brand')}
            options={[
              { value: '', label: 'No brand' },
              ...brands.map((b) => ({ value: b.id, label: b.name })),
            ]}
          />
          <SelectField
            label="Category"
            value={form.category}
            onChange={set('category')}
            options={[
              { value: '', label: 'No category yet' },
              ...categories.map((c) => ({ value: c.id, label: c.name })),
            ]}
          />
          <div className="sm:col-span-2">
            <TextArea
              label="Short summary"
              maxLength={500}
              value={form.summary}
              onChange={set('summary')}
            />
          </div>
        </div>
      </Card>
      <Card title="First variant, price and stock">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="SKU"
            required
            maxLength={64}
            pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
            value={form.sku}
            onChange={set('sku')}
            hint="Unique code in your store, e.g. TSHIRT-RED-M."
          />
          <TextField
            label="Barcode (optional)"
            maxLength={20}
            value={form.barcode}
            onChange={set('barcode')}
          />
          <TextField
            label={`Price (${session.store_currency || 'store currency'})`}
            required
            inputMode="decimal"
            pattern="^\d+(\.\d{1,4})?$"
            value={form.amount}
            onChange={set('amount')}
          />
          <TextField
            label="Compare-at price (optional)"
            inputMode="decimal"
            pattern="^\d+(\.\d{1,4})?$"
            value={form.compare_at_amount}
            onChange={set('compare_at_amount')}
            hint="The old price shown crossed out."
          />
          <SelectField
            label="Kind"
            value={form.kind}
            onChange={set('kind')}
            options={[
              { value: 'physical', label: 'Physical goods' },
              ...(form.product_type === 'book'
                ? [{ value: 'digital', label: 'Digital download (PDF)' }]
                : []),
            ]}
          />
          <SelectField
            label="Sold by"
            value={form.inventory_unit}
            onChange={set('inventory_unit')}
            options={UNITS}
          />
          {form.product_type === 'grocery' && (
            <div className="grid grid-cols-2 gap-2">
              <TextField
                label="Pack size"
                required
                inputMode="decimal"
                value={form.net_quantity}
                onChange={set('net_quantity')}
                hint="Needed to publish groceries."
              />
              <SelectField
                label="Unit"
                value={form.net_unit}
                onChange={set('net_unit')}
                options={PACK_UNITS}
              />
            </div>
          )}
        </div>
        {form.kind === 'physical' && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="flex items-center gap-3 text-sm">
              <input
                type="checkbox"
                className="accent-brand-deep"
                checked={form.track_inventory}
                onChange={(e) => setForm({ ...form, track_inventory: e.target.checked })}
              />
              Track stock for this product
            </label>
            {form.track_inventory && (
              <SelectField
                label="How units are tracked"
                value={form.tracking_mode}
                onChange={set('tracking_mode')}
                options={modes}
              />
            )}
          </div>
        )}
      </Card>
      <div>
        <Button type="submit" busy={busy}>
          Save draft
        </Button>
      </div>
    </form>
  );
}

const DETAIL_FIELDS: Record<
  string,
  { name: string; label: string; kind?: 'number' | 'boolean' | 'list' | 'date' | 'long' }[]
> = {
  apparel: [
    { name: 'audience', label: 'Audience (women, men, unisex, kids)' },
    { name: 'material', label: 'Material' },
    { name: 'fit', label: 'Fit' },
    { name: 'care_instructions', label: 'Care instructions', kind: 'long' },
    { name: 'size_guide_notes', label: 'Size guide notes', kind: 'long' },
  ],
  grocery: [
    { name: 'is_perishable', label: 'Perishable', kind: 'boolean' },
    { name: 'storage_instructions', label: 'Storage instructions' },
    { name: 'shelf_life_days', label: 'Shelf life (days)', kind: 'number' },
    { name: 'country_of_origin', label: 'Country of origin (2 letters)' },
    { name: 'ingredients', label: 'Ingredients', kind: 'long' },
    { name: 'allergens', label: 'Allergens' },
  ],
  electronics: [{ name: 'model_number', label: 'Model number' }],
  book: [
    { name: 'subtitle', label: 'Subtitle' },
    { name: 'authors', label: 'Authors (comma separated)', kind: 'list' },
    { name: 'publisher', label: 'Publisher' },
    { name: 'publication_date', label: 'Publication date', kind: 'date' },
    { name: 'edition', label: 'Edition' },
    { name: 'language', label: 'Language' },
    { name: 'genre', label: 'Genre' },
    { name: 'page_count', label: 'Pages', kind: 'number' },
    { name: 'series_name', label: 'Series' },
    { name: 'series_number', label: 'Number in series', kind: 'number' },
  ],
};

function detailsOf(product: ManagedProduct): ProductDetails {
  const key = product.product_type as 'apparel' | 'grocery' | 'electronics' | 'book';
  return (product[key] as ProductDetails | null | undefined) ?? {};
}

function PresetDetails({
  product,
  onSaved,
}: {
  product: ManagedProduct;
  onSaved: (p: ManagedProduct) => void;
}) {
  const fields = DETAIL_FIELDS[product.product_type];
  const [values, setValues] = useState<ProductDetails>(() => detailsOf(product));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!fields) return null;
  return (
    <Card title={`${TYPE_LABEL[product.product_type] ?? 'Product'} details`}>
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          const payload = Object.fromEntries(
            fields.map((field) => {
              const value = values[field.name];
              if (field.kind === 'number')
                return [
                  field.name,
                  value === '' || value === null || value === undefined ? null : Number(value),
                ];
              if (field.kind === 'date') return [field.name, value || null];
              return [
                field.name,
                value ?? (field.kind === 'boolean' ? false : field.kind === 'list' ? [] : ''),
              ];
            }),
          );
          void api
            .patch<unknown>(`${BASE}/products/${product.id}/`, { [product.product_type]: payload })
            .then((value) => onSaved(managedProduct(value)))
            .catch((err: unknown) => setError(errorMessage(err)))
            .finally(() => setBusy(false));
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {fields.map((field) =>
            field.kind === 'boolean' ? (
              <label key={field.name} className="flex items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  className="accent-brand-deep"
                  checked={values[field.name] === true}
                  onChange={(e) => setValues({ ...values, [field.name]: e.target.checked })}
                />
                {field.label}
              </label>
            ) : field.kind === 'long' ? (
              <div key={field.name} className="sm:col-span-2">
                <TextArea
                  label={field.label}
                  value={String(values[field.name] ?? '')}
                  onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
                />
              </div>
            ) : (
              <TextField
                key={field.name}
                label={field.label}
                type={field.kind === 'date' ? 'date' : 'text'}
                inputMode={field.kind === 'number' ? 'numeric' : undefined}
                value={
                  field.kind === 'list'
                    ? Array.isArray(values[field.name])
                      ? (values[field.name] as string[]).join(', ')
                      : ''
                    : String(values[field.name] ?? '')
                }
                onChange={(e) =>
                  setValues({
                    ...values,
                    [field.name]:
                      field.kind === 'list'
                        ? e.target.value
                            .split(',')
                            .map((s) => s.trim())
                            .filter(Boolean)
                        : e.target.value,
                  })
                }
              />
            ),
          )}
        </div>
        {error && <Alert>{error}</Alert>}
        <div>
          <Button type="submit" variant="secondary" busy={busy}>
            Save details
          </Button>
        </div>
      </form>
    </Card>
  );
}

function VariantRow({
  variant,
  priceLists,
  canSeeCosts,
  packSize,
  onChanged,
}: {
  variant: ManagedVariant;
  priceLists: ManagedPriceList[];
  canSeeCosts: boolean;
  /** Grocery variants carry a pack size, which publication requires. */
  packSize: boolean;
  onChanged: () => Promise<void>;
}) {
  const defaultList = priceLists.find((list) => list.is_default);
  const current = variant.prices.find((p) => p.price_list === defaultList?.id) ?? variant.prices[0];
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    sku: variant.sku,
    barcode: variant.barcode ?? '',
    is_active: variant.is_active,
    amount: current?.amount ?? '',
    compare_at_amount: current?.compare_at_amount ?? '',
    cost_amount: current?.cost_amount ?? '',
    net_quantity: variant.net_quantity ? quantity(variant.net_quantity) : '',
    net_unit: variant.net_unit || 'g',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!editing)
    return (
      <tr>
        <Cell>
          <span className="font-semibold">{variant.title || 'Default'}</span>
          <p className="text-xs text-muted">
            {variant.options.map((o) => `${o.name}: ${o.value}`).join(' · ')}
          </p>
        </Cell>
        <Cell>
          {variant.sku}
          {variant.barcode && <p className="text-xs text-muted">{variant.barcode}</p>}
        </Cell>
        <Cell className="tabular-nums">
          {current ? (
            money(current.amount, current.currency)
          ) : (
            <Badge tone="warning">No price</Badge>
          )}
          {current?.compare_at_amount && (
            <p className="text-xs text-muted line-through">
              {money(current.compare_at_amount, current.currency)}
            </p>
          )}
        </Cell>
        <Cell className="tabular-nums">
          {quantity(variant.stock.available)}
          <p className="text-xs text-muted">{quantity(variant.stock.reserved)} reserved</p>
        </Cell>
        <Cell>
          {variant.is_active ? <Badge tone="success">Active</Badge> : <Badge>Inactive</Badge>}
        </Cell>
        <Cell>
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Edit
          </Button>
        </Cell>
      </tr>
    );
  return (
    <tr>
      <td colSpan={6} className="bg-canvas p-4">
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            void (async () => {
              try {
                await api.patch(`${BASE}/variants/${variant.id}/`, {
                  sku: form.sku,
                  barcode: form.barcode || null,
                  is_active: form.is_active,
                  ...(packSize && form.net_quantity
                    ? { net_quantity: form.net_quantity, net_unit: form.net_unit }
                    : {}),
                });
                if (form.amount) {
                  await api.put(`/api/manage/pricing/variants/${variant.id}/prices/`, {
                    prices: [
                      {
                        price_list: defaultList?.id ?? null,
                        amount: form.amount,
                        compare_at_amount: form.compare_at_amount || null,
                        ...(canSeeCosts && form.cost_amount
                          ? { cost_amount: form.cost_amount }
                          : {}),
                      },
                    ],
                  });
                }
                setEditing(false);
                await onChanged();
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            })();
          }}
        >
          <p className="text-sm font-semibold">{variant.title || 'Default variant'}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <TextField
              label="SKU"
              required
              value={form.sku}
              onChange={(e) => setForm({ ...form, sku: e.target.value })}
            />
            <TextField
              label="Barcode"
              value={form.barcode}
              onChange={(e) => setForm({ ...form, barcode: e.target.value })}
            />
            <label className="flex items-center gap-3 self-end pb-3 text-sm">
              <input
                type="checkbox"
                className="accent-brand-deep"
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
              Available for sale
            </label>
            {packSize && (
              <div className="grid grid-cols-2 gap-2">
                <TextField
                  label="Pack size"
                  inputMode="decimal"
                  value={form.net_quantity}
                  onChange={(e) => setForm({ ...form, net_quantity: e.target.value })}
                />
                <SelectField
                  label="Unit"
                  value={form.net_unit}
                  onChange={(e) => setForm({ ...form, net_unit: e.target.value })}
                  options={PACK_UNITS}
                />
              </div>
            )}
            <TextField
              label={`Price${defaultList ? ` (${defaultList.currency})` : ''}`}
              inputMode="decimal"
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })}
            />
            <TextField
              label="Compare-at price"
              inputMode="decimal"
              value={form.compare_at_amount}
              onChange={(e) => setForm({ ...form, compare_at_amount: e.target.value })}
            />
            {canSeeCosts && (
              <TextField
                label="Cost price (internal)"
                inputMode="decimal"
                value={form.cost_amount}
                onChange={(e) => setForm({ ...form, cost_amount: e.target.value })}
                hint="Never shown to customers."
              />
            )}
          </div>
          <p className="text-xs text-muted">
            Price changes apply to new carts and orders; placed orders keep the price they were sold
            at.
          </p>
          {error && <Alert>{error}</Alert>}
          <div className="flex gap-2">
            <Button type="submit" busy={busy}>
              Save variant
            </Button>
            <Button type="button" variant="secondary" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      </td>
    </tr>
  );
}

function AddVariant({
  product,
  onAdded,
}: {
  product: ManagedProduct;
  onAdded: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ sku: '', amount: '', options: {} as Record<string, string> });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!open)
    return (
      <Button variant="secondary" onClick={() => setOpen(true)}>
        Add variant
      </Button>
    );
  return (
    <form
      className="grid gap-3 rounded-base border border-line p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        void api
          .post(`${BASE}/products/${product.id}/variants/`, {
            sku: form.sku,
            ...(product.options.length ? { options: form.options } : {}),
            ...(form.amount ? { price: { amount: form.amount } } : {}),
          })
          .then(async () => {
            setOpen(false);
            setForm({ sku: '', amount: '', options: {} });
            await onAdded();
          })
          .catch((err: unknown) => setError(errorMessage(err)))
          .finally(() => setBusy(false));
      }}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField
          label="SKU"
          required
          value={form.sku}
          onChange={(e) => setForm({ ...form, sku: e.target.value })}
        />
        <TextField
          label="Price"
          inputMode="decimal"
          value={form.amount}
          onChange={(e) => setForm({ ...form, amount: e.target.value })}
        />
        {product.options.map((option) => (
          <TextField
            key={option.code}
            label={`${option.name} (option code)`}
            required
            value={form.options[option.code] ?? ''}
            onChange={(e) =>
              setForm({ ...form, options: { ...form.options, [option.code]: e.target.value } })
            }
            hint="Use the option's code from Catalogue › Attributes."
          />
        ))}
      </div>
      {!product.options.length && (
        <p className="text-xs text-muted">
          This product has no variant options (such as size or colour) yet. Each variant still needs
          its own SKU.
        </p>
      )}
      {error && <Alert>{error}</Alert>}
      <div className="flex gap-2">
        <Button type="submit" busy={busy}>
          Add variant
        </Button>
        <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Media({
  product,
  onSaved,
}: {
  product: ManagedProduct;
  onSaved: (p: ManagedProduct) => void;
}) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function save(media: { asset: string; variant: string | null; is_primary: boolean }[]) {
    onSaved(
      managedProduct(await api.put<unknown>(`${BASE}/products/${product.id}/media/`, { media })),
    );
  }
  const current = product.media.map((m) => ({
    asset: m.asset,
    variant: m.variant,
    is_primary: m.is_primary,
  }));
  return (
    <Card title="Photos">
      {error && <Alert>{error}</Alert>}
      {product.media.length ? (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {product.media.map((m) => (
            <li key={m.asset} className="grid gap-2 rounded-base border border-line p-2">
              {/* eslint-disable-next-line @next/next/no-img-element -- private, API-streamed asset */}
              <img
                src={m.url}
                alt={m.alt_text || product.name}
                className="aspect-square w-full rounded-base object-cover"
              />
              <div className="flex flex-wrap gap-1">
                {m.is_primary ? (
                  <Badge tone="success">Main photo</Badge>
                ) : (
                  <Button
                    variant="secondary"
                    className="min-h-8 px-2 text-xs"
                    onClick={() =>
                      void save(
                        current.map((c) => ({ ...c, is_primary: c.asset === m.asset })),
                      ).catch((e: unknown) => setError(errorMessage(e)))
                    }
                  >
                    Make main
                  </Button>
                )}
                <Button
                  variant="secondary"
                  className="min-h-8 px-2 text-xs"
                  onClick={() =>
                    void save(current.filter((c) => c.asset !== m.asset)).catch((e: unknown) =>
                      setError(errorMessage(e)),
                    )
                  }
                >
                  Remove
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">
          No photos yet. Customers see a placeholder until you add one.
        </p>
      )}
      <label className="mt-4 inline-flex min-h-11 cursor-pointer items-center rounded-base border border-line-strong px-4 text-sm font-semibold hover:border-ink">
        {busy ? 'Uploading…' : 'Upload photo'}
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="sr-only"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (!file) return;
            setBusy(true);
            setError('');
            void uploadImage(file, product.name)
              .then((asset) =>
                save([...current, { asset, variant: null, is_primary: !current.length }]),
              )
              .catch((err: unknown) => setError(errorMessage(err)))
              .finally(() => setBusy(false));
          }}
        />
      </label>
      <p className="mt-2 text-xs text-muted">
        PNG, JPEG or WebP. Files are checked before they are stored.
      </p>
    </Card>
  );
}

export function ProductEditor({ id, session }: { id: string; session: StaffSession }) {
  const load = useCallback(
    async () => managedProduct(await api.get<unknown>(`${BASE}/products/${id}/`)),
    [id],
  );
  const { data: product, setData, error: loadError, loading } = useLoad(load);
  const { brands, categories } = useChoices();
  const [priceLists, setPriceLists] = useState<ManagedPriceList[]>([]);
  const [form, setForm] = useState<{
    name: string;
    summary: string;
    description: string;
    brand: string;
    category: string;
    tags: string;
  } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [archiving, setArchiving] = useState(false);
  const canSeeCosts = session.permissions.includes('catalog.costs');
  useEffect(() => {
    void api
      .get<unknown>('/api/manage/pricing/price-lists/?page_size=100')
      .then((value) => setPriceLists(managePage(managedPriceList)(value).results))
      .catch(() => undefined);
  }, []);
  const values =
    form ??
    (product && {
      name: product.name,
      summary: product.summary,
      description: product.description,
      brand: product.brand?.id ?? '',
      category: product.categories.find((c) => c.is_primary)?.id ?? product.categories[0]?.id ?? '',
      tags: product.tags.join(', '),
    });
  const reload = async () => {
    const fresh = await api.get<unknown>(`${BASE}/products/${id}/`);
    setData(managedProduct(fresh));
  };
  async function status(next: string, message: string) {
    setBusy(next);
    setError('');
    setNotice('');
    try {
      setData(
        managedProduct(await api.post<unknown>(`${BASE}/products/${id}/status/`, { status: next })),
      );
      setNotice(message);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy('');
    }
  }
  if (loading && !product) return <Loader>Loading product…</Loader>;
  if (!product || !values)
    return <Feedback error={loadError || 'This product could not be loaded.'} />;
  const set = (name: keyof typeof values) => (e: { target: { value: string } }) =>
    setForm({ ...values, [name]: e.target.value });
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow={`Products · ${TYPE_LABEL[product.product_type] ?? product.product_type}`}
        title={product.name}
        intro={
          product.status === 'archived'
            ? 'Archived: not shown in your store'
            : product.status === 'active' && product.published_at
              ? `Published ${dateTime(product.published_at)}`
              : 'Draft: not shown in your store yet'
        }
        actions={
          <>
            <StatusBadge value={product.status} map={PRODUCT_STATUS} />
            {product.status !== 'active' && (
              <Button
                busy={busy === 'active'}
                disabled={product.publication_problems.length > 0}
                onClick={() => void status('active', 'Product published.')}
              >
                Publish
              </Button>
            )}
            {product.status === 'active' && (
              <Button
                variant="secondary"
                busy={busy === 'draft'}
                onClick={() => void status('draft', 'Product moved back to draft.')}
              >
                Unpublish
              </Button>
            )}
            {product.status !== 'archived' && (
              <Button variant="secondary" onClick={() => setArchiving(true)}>
                Archive
              </Button>
            )}
          </>
        }
      />
      <Feedback error={error} notice={notice} />
      {product.publication_problems.length > 0 && product.status !== 'active' && (
        <Alert tone="info">Before publishing: {product.publication_problems.join(' ')}</Alert>
      )}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-6">
          <Card title="Basics">
            <form
              className="grid gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy('basics');
                setError('');
                setNotice('');
                void api
                  .patch<unknown>(`${BASE}/products/${id}/`, {
                    name: values.name,
                    summary: values.summary,
                    description: values.description,
                    brand: values.brand || null,
                    tags: values.tags
                      .split(',')
                      .map((t) => t.trim())
                      .filter(Boolean),
                    categories: values.category ? [values.category] : [],
                    primary_category: values.category || null,
                  })
                  .then((value) => {
                    setData(managedProduct(value));
                    setForm(null);
                    setNotice('Product saved.');
                  })
                  .catch((err: unknown) => setError(errorMessage(err)))
                  .finally(() => setBusy(''));
              }}
            >
              <TextField
                label="Product name"
                required
                maxLength={200}
                value={values.name}
                onChange={set('name')}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <SelectField
                  label="Brand"
                  value={values.brand}
                  onChange={set('brand')}
                  options={[
                    { value: '', label: 'No brand' },
                    ...brands.map((b) => ({ value: b.id, label: b.name })),
                  ]}
                />
                <SelectField
                  label="Category"
                  value={values.category}
                  onChange={set('category')}
                  options={[
                    { value: '', label: 'No category' },
                    ...categories.map((c) => ({ value: c.id, label: c.name })),
                  ]}
                />
              </div>
              <TextArea
                label="Short summary"
                maxLength={500}
                value={values.summary}
                onChange={set('summary')}
              />
              <TextArea
                label="Description"
                maxLength={20000}
                rows={8}
                value={values.description}
                onChange={set('description')}
                hint="Plain text. Line breaks are kept."
              />
              <TextField
                label="Tags (comma separated)"
                value={values.tags}
                onChange={set('tags')}
              />
              <div>
                <Button type="submit" busy={busy === 'basics'}>
                  Save
                </Button>
              </div>
            </form>
          </Card>
          <Card
            title="Variants, prices and stock"
            flush
            actions={<AddVariant product={product} onAdded={reload} />}
          >
            <Table head={['Variant', 'SKU', 'Price', 'Available', 'Status', '']}>
              {product.variants.map((variant) => (
                <VariantRow
                  key={`${variant.id}-${product.updated_at}`}
                  variant={variant}
                  priceLists={priceLists}
                  canSeeCosts={canSeeCosts}
                  packSize={product.product_type === 'grocery'}
                  onChanged={reload}
                />
              ))}
            </Table>
            <p className="border-t border-line px-5 py-3 text-xs text-muted">
              Stock is changed in Inventory with receipts and adjustments, never by editing a number
              here.
            </p>
          </Card>
          <PresetDetails
            key={product.updated_at}
            product={product}
            onSaved={(p) => {
              setData(p);
              setNotice('Details saved.');
            }}
          />
        </div>
        <div className="grid content-start gap-6">
          <Media
            product={product}
            onSaved={(p) => {
              setData(p);
              setNotice('Photos updated.');
            }}
          />
          <Card title="Stock tracking">
            <p className="text-sm">
              {product.track_inventory
                ? 'Stock is tracked.'
                : 'Stock is not tracked; the product is always available.'}
            </p>
            {product.track_inventory && (
              <p className="mt-1 text-sm text-muted">
                Units:{' '}
                {product.tracking_mode === 'batch'
                  ? 'batches with expiry dates'
                  : product.tracking_mode === 'serial'
                    ? 'serial or IMEI numbers'
                    : 'quantity only'}
              </p>
            )}
            <a
              className="mt-3 inline-block text-sm font-semibold text-brand-deep"
              href={`/manage/inventory?product=${product.id}`}
            >
              View stock levels →
            </a>
          </Card>
        </div>
      </div>
      {archiving && (
        <ConfirmDialog
          title={`Archive ${product.name}?`}
          confirmLabel="Archive"
          onCancel={() => setArchiving(false)}
          onConfirm={async () => {
            await status('archived', 'Product archived.');
            setArchiving(false);
          }}
        >
          Archived products leave your store but stay in past orders, invoices and stock history.
        </ConfirmDialog>
      )}
    </div>
  );
}
