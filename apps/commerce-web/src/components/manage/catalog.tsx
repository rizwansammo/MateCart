'use client';

import { useCallback, useState } from 'react';
import type { ManagedCollection, ManagedProductSummary } from '@matecart/shared-contracts';
import {
  managePage,
  managedAttribute,
  managedCategory,
  managedCollection,
  managedProductSummary,
  namedEntity,
} from '@matecart/shared-contracts';
import { Alert, Button, ConfirmDialog, TextField, errorMessage } from '@matecart/shared-ui';
import {
  Badge,
  Card,
  Cell,
  Empty,
  Feedback,
  Loader,
  PageHeader,
  SelectField,
  Table,
  api,
  query,
  useLoad,
} from './kit';

const BASE = '/api/manage/catalog';
const TABS = [
  { key: 'categories', label: 'Categories' },
  { key: 'brands', label: 'Brands' },
  { key: 'collections', label: 'Collections' },
  { key: 'attributes', label: 'Attributes and options' },
] as const;
type Tab = (typeof TABS)[number]['key'];

function useAction() {
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<void>, message: string) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
      setNotice(message);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return { error, notice, busy, run };
}

function ActiveToggle({
  path,
  active,
  onDone,
}: {
  path: string;
  active: boolean;
  onDone: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <span className="inline-flex flex-col gap-1">
      <Button
        variant="secondary"
        className="min-h-9 px-3 text-xs"
        busy={busy}
        onClick={() => {
          setBusy(true);
          setError('');
          void api
            .patch(path, { is_active: !active })
            .then(onDone)
            .catch((e: unknown) => setError(errorMessage(e)))
            .finally(() => setBusy(false));
        }}
      >
        {active ? 'Hide' : 'Show'}
      </Button>
      {error && <span className="text-xs text-danger-fg">{error}</span>}
    </span>
  );
}

function DeleteButton({
  path,
  name,
  onDone,
}: {
  path: string;
  name: string;
  onDone: () => Promise<void>;
}) {
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');
  return (
    <>
      <Button variant="secondary" className="min-h-9 px-3 text-xs" onClick={() => setAsking(true)}>
        Delete
      </Button>
      {asking && (
        <ConfirmDialog
          title={`Delete ${name}?`}
          onCancel={() => setAsking(false)}
          onConfirm={async () => {
            try {
              await api.delete(path);
              setAsking(false);
              await onDone();
            } catch (e) {
              setError(errorMessage(e));
            }
          }}
        >
          <p>This cannot be undone. Anything still in use is protected and will not be deleted.</p>
          {error && (
            <div className="mt-3">
              <Alert>{error}</Alert>
            </div>
          )}
        </ConfirmDialog>
      )}
    </>
  );
}

function Categories() {
  const load = useCallback(
    async () =>
      managePage(managedCategory)(await api.get<unknown>(`${BASE}/categories/?page_size=100`)),
    [],
  );
  const { data, error: loadError, loading, reload } = useLoad(load);
  const { error, notice, busy, run } = useAction();
  const [form, setForm] = useState({ name: '', parent: '', description: '' });
  const rows = data?.results ?? [];
  const names = Object.fromEntries(rows.map((row) => [row.id, row.name]));
  return (
    <div className="grid gap-6">
      <Feedback error={error || loadError} notice={notice} />
      <Card title="Add a category">
        <form
          className="grid gap-3 sm:grid-cols-3 sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.post(`${BASE}/categories/`, {
                name: form.name,
                parent: form.parent || null,
                description: form.description,
              });
              setForm({ name: '', parent: '', description: '' });
              await reload();
            }, 'Category added.');
          }}
        >
          <TextField
            label="Name"
            required
            maxLength={120}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <SelectField
            label="Inside"
            value={form.parent}
            onChange={(e) => setForm({ ...form, parent: e.target.value })}
            options={[
              { value: '', label: 'Top level' },
              ...rows.map((r) => ({ value: r.id, label: r.name })),
            ]}
          />
          <Button type="submit" busy={busy}>
            Add category
          </Button>
        </form>
      </Card>
      <Card title="Categories" flush>
        {loading && !data ? (
          <Loader />
        ) : !rows.length ? (
          <Empty title="No categories yet">Categories help customers browse your store.</Empty>
        ) : (
          <Table head={['Name', 'Inside', 'Visible', '']}>
            {rows.map((row) => (
              <tr key={row.id}>
                <Cell>
                  <span className="font-semibold">{row.name}</span>
                  <p className="text-xs text-muted">/{row.slug}</p>
                </Cell>
                <Cell>{row.parent ? (names[row.parent] ?? '—') : 'Top level'}</Cell>
                <Cell>
                  {row.is_active ? <Badge tone="success">Visible</Badge> : <Badge>Hidden</Badge>}
                </Cell>
                <Cell className="whitespace-nowrap">
                  <span className="flex gap-2">
                    <ActiveToggle
                      path={`${BASE}/categories/${row.id}/`}
                      active={row.is_active}
                      onDone={reload}
                    />
                    <DeleteButton
                      path={`${BASE}/categories/${row.id}/`}
                      name={row.name}
                      onDone={reload}
                    />
                  </span>
                </Cell>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function Brands() {
  const load = useCallback(
    async () => managePage(namedEntity)(await api.get<unknown>(`${BASE}/brands/?page_size=100`)),
    [],
  );
  const { data, error: loadError, loading, reload } = useLoad(load);
  const { error, notice, busy, run } = useAction();
  const [name, setName] = useState('');
  return (
    <div className="grid gap-6">
      <Feedback error={error || loadError} notice={notice} />
      <Card title="Add a brand">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.post(`${BASE}/brands/`, { name });
              setName('');
              await reload();
            }, 'Brand added.');
          }}
        >
          <div className="min-w-0 flex-1">
            <TextField
              label="Brand name"
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <Button type="submit" busy={busy}>
            Add brand
          </Button>
        </form>
      </Card>
      <Card title="Brands" flush>
        {loading && !data ? (
          <Loader />
        ) : !data?.results.length ? (
          <Empty title="No brands yet" />
        ) : (
          <Table head={['Name', 'Visible', '']}>
            {data.results.map((row) => (
              <tr key={row.id}>
                <Cell className="font-semibold">{row.name}</Cell>
                <Cell>
                  {row.is_active ? <Badge tone="success">Visible</Badge> : <Badge>Hidden</Badge>}
                </Cell>
                <Cell className="whitespace-nowrap">
                  <span className="flex gap-2">
                    <ActiveToggle
                      path={`${BASE}/brands/${row.id}/`}
                      active={row.is_active}
                      onDone={reload}
                    />
                    <DeleteButton
                      path={`${BASE}/brands/${row.id}/`}
                      name={row.name}
                      onDone={reload}
                    />
                  </span>
                </Cell>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function CollectionProducts({
  collection,
  onDone,
}: {
  collection: ManagedCollection;
  onDone: () => Promise<void>;
}) {
  const load = useCallback(
    async () =>
      managePage(managedProductSummary)(
        await api.get<unknown>(
          `${BASE}/products/${query({ collection: collection.id, page_size: 100 })}`,
        ),
      ).results,
    [collection.id],
  );
  const { data: members, setData } = useLoad(load);
  const [text, setText] = useState('');
  const [found, setFound] = useState<ManagedProductSummary[]>([]);
  const { error, busy, run } = useAction();
  const list = members ?? [];
  async function save(next: ManagedProductSummary[]) {
    await api.put(`${BASE}/collections/${collection.id}/products/`, {
      products: next.map((p) => p.id),
    });
    setData(next);
    await onDone();
  }
  return (
    <div className="grid gap-3 bg-canvas p-4">
      {error && <Alert>{error}</Alert>}
      <ul className="grid gap-1 text-sm">
        {list.map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-2">
            {p.name}
            <Button
              variant="secondary"
              className="min-h-8 px-2 text-xs"
              disabled={busy}
              onClick={() => void run(() => save(list.filter((x) => x.id !== p.id)), 'Removed.')}
            >
              Remove
            </Button>
          </li>
        ))}
        {!list.length && <li className="text-muted">No products in this collection.</li>}
      </ul>
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void api
            .get<unknown>(`${BASE}/products/${query({ q: text, page_size: 10 })}`)
            .then((value) => setFound(managePage(managedProductSummary)(value).results))
            .catch(() => setFound([]));
        }}
      >
        <div className="min-w-0 flex-1">
          <TextField
            label="Add products"
            placeholder="Search by name or SKU"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <Button type="submit" variant="secondary">
          Find
        </Button>
      </form>
      {found.length > 0 && (
        <ul className="grid gap-1 text-sm">
          {found
            .filter((p) => !list.some((x) => x.id === p.id))
            .map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-2">
                {p.name}
                <Button
                  variant="secondary"
                  className="min-h-8 px-2 text-xs"
                  disabled={busy}
                  onClick={() => void run(() => save([...list, p]), 'Added.')}
                >
                  Add
                </Button>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

function Collections() {
  const load = useCallback(
    async () =>
      managePage(managedCollection)(await api.get<unknown>(`${BASE}/collections/?page_size=100`)),
    [],
  );
  const { data, error: loadError, loading, reload } = useLoad(load);
  const { error, notice, busy, run } = useAction();
  const [form, setForm] = useState({ name: '', description: '' });
  const [open, setOpen] = useState('');
  return (
    <div className="grid gap-6">
      <Feedback error={error || loadError} notice={notice} />
      <Card title="Add a collection">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.post(`${BASE}/collections/`, form);
              setForm({ name: '', description: '' });
              await reload();
            }, 'Collection added.');
          }}
        >
          <div className="min-w-0 flex-1">
            <TextField
              label="Collection name"
              required
              maxLength={120}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <Button type="submit" busy={busy}>
            Add collection
          </Button>
        </form>
      </Card>
      <Card title="Collections" flush>
        {loading && !data ? (
          <Loader />
        ) : !data?.results.length ? (
          <Empty title="No collections yet">
            Group products for homepage sections, such as “New this week”.
          </Empty>
        ) : (
          <ul className="divide-y divide-line">
            {data.results.map((row) => (
              <li key={row.id}>
                <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                  <span>
                    <span className="font-semibold">{row.name}</span>
                    <span className="ml-2 text-xs text-muted">{row.product_count} products</span>
                    {!row.is_active && <Badge>Hidden</Badge>}
                  </span>
                  <span className="flex gap-2">
                    <Button
                      variant="secondary"
                      className="min-h-9 px-3 text-xs"
                      onClick={() => setOpen(open === row.id ? '' : row.id)}
                    >
                      {open === row.id ? 'Close' : 'Products'}
                    </Button>
                    <ActiveToggle
                      path={`${BASE}/collections/${row.id}/`}
                      active={row.is_active}
                      onDone={reload}
                    />
                    <DeleteButton
                      path={`${BASE}/collections/${row.id}/`}
                      name={row.name}
                      onDone={reload}
                    />
                  </span>
                </div>
                {open === row.id && <CollectionProducts collection={row} onDone={reload} />}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

const VALUE_TYPES = [
  { value: 'option', label: 'Choice from options (size, colour)' },
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'measurement', label: 'Measurement' },
  { value: 'boolean', label: 'Yes / no' },
];

function Attributes() {
  const load = useCallback(
    async () =>
      managePage(managedAttribute)(await api.get<unknown>(`${BASE}/attributes/?page_size=100`)),
    [],
  );
  const { data, error: loadError, loading, reload } = useLoad(load);
  const { error, notice, busy, run } = useAction();
  const [form, setForm] = useState({
    name: '',
    code: '',
    value_type: 'option',
    scope: 'variant',
    unit: '',
  });
  const [option, setOption] = useState<Record<string, string>>({});
  return (
    <div className="grid gap-6">
      <Feedback error={error || loadError} notice={notice} />
      <Card title="Add an attribute">
        <form
          className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5 lg:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api.post(`${BASE}/attributes/`, form);
              setForm({ name: '', code: '', value_type: 'option', scope: 'variant', unit: '' });
              await reload();
            }, 'Attribute added.');
          }}
        >
          <TextField
            label="Name"
            required
            maxLength={100}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <TextField
            label="Code"
            required
            maxLength={64}
            pattern="^[a-z0-9]+(?:-[a-z0-9]+)*$"
            value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value })}
            hint="Lowercase, e.g. size"
          />
          <SelectField
            label="Values"
            value={form.value_type}
            onChange={(e) => setForm({ ...form, value_type: e.target.value })}
            options={VALUE_TYPES}
          />
          <SelectField
            label="Used for"
            value={form.scope}
            onChange={(e) => setForm({ ...form, scope: e.target.value })}
            options={[
              { value: 'variant', label: 'Defining variants' },
              { value: 'specification', label: 'Specifications' },
            ]}
          />
          <Button type="submit" busy={busy}>
            Add attribute
          </Button>
        </form>
      </Card>
      <Card title="Attributes" flush>
        {loading && !data ? (
          <Loader />
        ) : !data?.results.length ? (
          <Empty title="No attributes yet">
            Attributes such as size or colour define product variants.
          </Empty>
        ) : (
          <ul className="divide-y divide-line">
            {data.results.map((attribute) => (
              <li key={attribute.id} className="grid gap-3 px-5 py-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    <span className="font-semibold">{attribute.name}</span>
                    <span className="ml-2 text-xs text-muted">
                      {attribute.code} ·{' '}
                      {attribute.scope === 'variant' ? 'variant option' : 'specification'} ·{' '}
                      {attribute.value_type}
                    </span>
                  </span>
                  <DeleteButton
                    path={`${BASE}/attributes/${attribute.id}/`}
                    name={attribute.name}
                    onDone={reload}
                  />
                </div>
                {attribute.value_type === 'option' && (
                  <>
                    <div className="flex flex-wrap gap-2">
                      {attribute.options.map((o) => (
                        <Badge key={o.id}>
                          {o.value} <span className="ml-1 text-muted">({o.code})</span>
                        </Badge>
                      ))}
                      {!attribute.options.length && (
                        <span className="text-xs text-muted">No options yet.</span>
                      )}
                    </div>
                    <form
                      className="flex flex-wrap items-end gap-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(async () => {
                          await api.post(`${BASE}/attributes/${attribute.id}/options/`, {
                            value: option[attribute.id] ?? '',
                          });
                          setOption({ ...option, [attribute.id]: '' });
                          await reload();
                        }, 'Option added.');
                      }}
                    >
                      <div className="w-56">
                        <TextField
                          label="New option"
                          required
                          maxLength={100}
                          value={option[attribute.id] ?? ''}
                          onChange={(e) => setOption({ ...option, [attribute.id]: e.target.value })}
                        />
                      </div>
                      <Button type="submit" variant="secondary" busy={busy}>
                        Add option
                      </Button>
                    </form>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function CatalogManager() {
  const [tab, setTab] = useState<Tab>(() => {
    if (typeof window === 'undefined') return 'categories';
    const wanted = new URLSearchParams(window.location.search).get('tab');
    return TABS.find((t) => t.key === wanted)?.key ?? 'categories';
  });
  return (
    <div>
      <PageHeader
        eyebrow="Sell"
        title="Catalogue"
        intro="Organise products into categories, brands and collections, and define the options variants are made from."
      />
      <div role="tablist" aria-label="Catalogue sections" className="mb-6 flex flex-wrap gap-2">
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
      {tab === 'categories' && <Categories />}
      {tab === 'brands' && <Brands />}
      {tab === 'collections' && <Collections />}
      {tab === 'attributes' && <Attributes />}
    </div>
  );
}
