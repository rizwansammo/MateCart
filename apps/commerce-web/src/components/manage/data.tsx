'use client';

import { useCallback, useEffect, useId, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import type { Route } from 'next';
import Link from 'next/link';
import {
  MIGRATION_SCOPES,
  compensation,
  dataOverview,
  historicalCustomerPage,
  historicalOrderPage,
  importItemPage,
  isJobActive,
  sourceConnection,
  sourceConnections,
  transferJob,
  transferJobPage,
  type ConflictStrategy,
  type DataOverview,
  type ImportItem,
  type ItemAction,
  type ItemStatus,
  type JobKind,
  type JobStatus,
  type MigrationScope,
  type Page,
  type SourceConnection,
  type SourcePlatform,
  type SourceStatus,
  type StaffSession,
  type TransferFile,
  type TransferJob,
} from '@matecart/shared-contracts';
import { Alert, ApiError, Button, Dialog, TextField, errorMessage } from '@matecart/shared-ui';
import {
  Badge,
  Card,
  Cell,
  Empty,
  Feedback,
  Loader,
  PageHeader,
  Pagination,
  ReauthDialog,
  SelectField,
  Table,
  api,
  dateTime,
  money,
  query,
  useLoad,
} from './kit';

/**
 * /manage/data/* — file imports, store migrations, exports and read-only imported history
 * (Phase 15, D-105 to D-109). Every figure comes from the API; nothing is imported before a
 * person approves the preview.
 */

const BASE = '/api/manage/data';
/** Jobs that still change on their own are refreshed this often. */
export const JOB_POLL_MS = 2000;

type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';
type Section = 'import' | 'migrate' | 'export' | 'history' | 'jobs';

const SECTIONS: Record<string, Section> = {
  '': 'import',
  migrate: 'migrate',
  export: 'export',
  history: 'history',
  jobs: 'jobs',
};

const JOB_STATUS: Record<JobStatus, { label: string; tone: Tone }> = {
  analysing: { label: 'Reading and validating', tone: 'info' },
  review: { label: 'Awaiting approval', tone: 'warning' },
  queued: { label: 'Queued', tone: 'info' },
  running: { label: 'Running', tone: 'info' },
  completed: { label: 'Completed', tone: 'success' },
  completed_with_errors: { label: 'Completed with errors', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
};
const JOB_KIND: Record<JobKind, string> = {
  import: 'File import',
  export: 'Export',
  migration: 'Store migration',
};
const ACTION: Record<ItemAction, { label: string; tone: Tone }> = {
  create: { label: 'Create', tone: 'success' },
  update: { label: 'Update', tone: 'info' },
  skip: { label: 'Skip', tone: 'neutral' },
  conflict: { label: 'Needs decision', tone: 'warning' },
  invalid: { label: 'Invalid', tone: 'danger' },
};
const ITEM_STATUS: Record<ItemStatus, { label: string; tone: Tone }> = {
  pending: { label: 'Pending', tone: 'neutral' },
  done: { label: 'Done', tone: 'success' },
  skipped: { label: 'Skipped', tone: 'neutral' },
  failed: { label: 'Failed', tone: 'danger' },
};
const ITEM_FILTERS: { value: string; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'create', label: 'To create' },
  { value: 'update', label: 'To update' },
  { value: 'skip', label: 'To skip' },
  { value: 'conflict', label: 'Needs decision' },
  { value: 'invalid', label: 'Invalid' },
  { value: 'warning', label: 'Warnings' },
];
const STRATEGIES: { value: ConflictStrategy; label: string; hint: string }[] = [
  {
    value: 'skip',
    label: 'Create new, skip existing',
    hint: 'New records are created. Records that already exist in your store (same SKU or code) are left exactly as they are.',
  },
  {
    value: 'update',
    label: 'Create new, update existing',
    hint: 'New records are created. Existing ones get the new name, description, tags and categories. Their stock, SKUs and status never change; prices only if you tick the option below.',
  },
  {
    value: 'manual',
    label: 'Decide each existing one',
    hint: 'New records are created. For every record that already exists you choose skip or update in the preview before approving.',
  },
];
const PLATFORM: Record<SourcePlatform, string> = {
  woocommerce: 'WooCommerce',
  shopify: 'Shopify',
};
const SOURCE_STATUS: Record<SourceStatus, { label: string; tone: Tone }> = {
  connected: { label: 'Connected', tone: 'success' },
  unverified: { label: 'Not verified', tone: 'neutral' },
  invalid_credentials: { label: 'Invalid credentials', tone: 'danger' },
  permission_missing: { label: 'Missing read permission', tone: 'danger' },
  unreachable: { label: 'Unreachable', tone: 'warning' },
  refused: { label: 'Refused', tone: 'danger' },
};
const SCOPE: Record<MigrationScope, { label: string; permissions: string[] }> = {
  categories: { label: 'Categories', permissions: ['catalog.manage'] },
  products: { label: 'Products', permissions: ['catalog.manage'] },
  customers: { label: 'Customers (read-only history)', permissions: ['customers.view'] },
  orders: {
    label: 'Orders (read-only history)',
    permissions: ['orders.manage', 'customers.view'],
  },
  coupons: { label: 'Coupons', permissions: ['promotions.manage'] },
};
const COUNT_LABEL: Record<string, string> = {
  categories: 'Categories',
  products: 'Products',
  customers: 'Customers',
  orders: 'Orders',
  coupons: 'Coupons',
};

const BANNER: Record<Tone, string> = {
  neutral: 'border-line bg-canvas text-ink',
  info: 'border-info-fg/20 bg-info-bg text-info-fg',
  success: 'border-success-fg/20 bg-success-bg text-success-fg',
  warning: 'border-warning-fg/20 bg-warning-bg text-warning-fg',
  danger: 'border-danger-fg/20 bg-danger-bg text-danger-fg',
};

function downloadUrl(file: TransferFile): string {
  return `${BASE}/files/${encodeURIComponent(file.id)}/download/`;
}

function jobUrl(id: string): string {
  return `/manage/data/jobs/${encodeURIComponent(id)}`;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, '')} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} bytes`;
}

function expired(file: TransferFile): boolean {
  return new Date(file.expires_at).getTime() <= Date.now();
}

function can(session: StaffSession, permissions: string[]): boolean {
  return permissions.every((p) => session.permissions.includes(p));
}

function jobTitle(job: TransferJob, overview: DataOverview): string {
  if (job.kind === 'export') {
    const name = overview.exports.find((e) => e.key === job.entity)?.name ?? job.entity;
    return `${name} export`;
  }
  if (job.kind === 'migration') {
    return job.connection_name ? `Migration from ${job.connection_name}` : 'Store migration';
  }
  return job.input_file ? `Import of ${job.input_file.display_name}` : 'File import';
}

/** Refreshes while ``active``; stops when it is not and when the component unmounts. */
function usePoll(active: boolean, refresh: () => Promise<unknown>) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => {
      void refresh().finally(() => setTick((n) => n + 1));
    }, JOB_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [active, refresh, tick]);
}

type Pending = { title: string; description: string; action: () => Promise<void> };

/**
 * Runs an action; when the server asks for a recent sign-in, asks for the password and runs it
 * again. Other failures go to ``onError``.
 */
function useSensitive(onError: (error: unknown) => void) {
  const [pending, setPending] = useState<Pending | null>(null);
  const run = useCallback(
    async (title: string, description: string, action: () => Promise<void>) => {
      try {
        await action();
      } catch (e) {
        if (e instanceof ApiError && e.code === 'reauthentication_required') {
          setPending({ title, description, action });
        } else {
          onError(e);
        }
      }
    },
    [onError],
  );
  const dialog = pending ? (
    <ReauthDialog
      title={pending.title}
      description={pending.description}
      onClose={() => setPending(null)}
      onConfirm={async () => {
        const next = pending;
        setPending(null);
        await run(next.title, next.description, next.action);
      }}
    />
  ) : null;
  return { run, dialog };
}

function Banner({ tone, title, children }: { tone: Tone; title: string; children?: ReactNode }) {
  return (
    <div role="status" className={`rounded-base border p-4 text-sm leading-6 ${BANNER[tone]}`}>
      <p className="font-semibold">{title}</p>
      {children && <div className="mt-1">{children}</div>}
    </div>
  );
}

function Check({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 h-4 w-4 flex-none"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div className="min-w-0">
        <label htmlFor={id} className={`text-sm font-semibold ${disabled ? 'text-muted' : ''}`}>
          {label}
        </label>
        {hint && <p className="text-xs leading-5 text-muted">{hint}</p>}
      </div>
    </div>
  );
}

function JobStatusBadge({ status }: { status: JobStatus }) {
  const state = JOB_STATUS[status];
  return <Badge tone={state.tone}>{state.label}</Badge>;
}

/** A file link; files of sensitive jobs need a password confirmation first (downloads are recorded). */
function FileDownload({
  file,
  sensitive,
  label,
}: {
  file: TransferFile;
  sensitive: boolean;
  label: string;
}) {
  const [confirming, setConfirming] = useState(false);
  if (expired(file)) {
    return (
      <span className="text-sm text-muted">
        {label}: expired {dateTime(file.expires_at)}
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      {sensitive ? (
        <button
          type="button"
          className="font-semibold text-brand-deep hover:underline"
          onClick={() => setConfirming(true)}
        >
          {label}
        </button>
      ) : (
        <a href={downloadUrl(file)} className="font-semibold text-brand-deep hover:underline">
          {label}
        </a>
      )}
      <span className="text-xs text-muted">
        {formatBytes(file.size_bytes)} · available until {dateTime(file.expires_at)}
      </span>
      {confirming && (
        <ReauthDialog
          title="Download personal data"
          description="This file contains customer or order data. Confirm your password; every download is recorded."
          confirmLabel="Download"
          onClose={() => setConfirming(false)}
          onConfirm={async () => {
            setConfirming(false);
            window.location.assign(downloadUrl(file));
          }}
        />
      )}
    </span>
  );
}

// --- Workspace ----------------------------------------------------------------------------------

interface Tab {
  key: Section;
  href: string;
  label: string;
}

function dataTabs(overview: DataOverview): Tab[] {
  const exportable = overview.exports.some((e) => e.allowed);
  const tabs: Tab[] = [];
  if (overview.can_import) tabs.push({ key: 'import', href: '/manage/data', label: 'Import' });
  if (overview.can_migrate)
    tabs.push({ key: 'migrate', href: '/manage/data/migrate', label: 'Store migration' });
  if (exportable) tabs.push({ key: 'export', href: '/manage/data/export', label: 'Exports' });
  if (overview.can_view_customers || overview.can_view_orders)
    tabs.push({ key: 'history', href: '/manage/data/history', label: 'Imported history' });
  if (overview.can_import || overview.can_migrate || exportable)
    tabs.push({ key: 'jobs', href: '/manage/data/jobs', label: 'All jobs' });
  return tabs;
}

export function DataWorkspace({ path, session }: { path: string; session: StaffSession }) {
  const [, section = '', id = ''] = path.split('/');
  const load = useCallback(async () => dataOverview(await api.get<unknown>(`${BASE}/`)), []);
  const { data: overview, error } = useLoad(load);
  if (!overview) return error ? <Alert>{error}</Alert> : <Loader>Loading data tools…</Loader>;

  const tabs = dataTabs(overview);
  const requested = SECTIONS[section];
  let current: Section | undefined = requested;
  if (section === '' && !tabs.some((t) => t.key === 'import')) current = tabs[0]?.key;
  const allowed = current !== undefined && tabs.some((t) => t.key === current);

  let body: ReactNode;
  if (tabs.length === 0) {
    body = (
      <Empty title="No data tools for your role">
        Ask the store owner for catalogue, settings, customer or order permissions.
      </Empty>
    );
  } else if (requested === undefined && section !== '') {
    body = <Empty title="This page does not exist" />;
  } else if (!allowed) {
    body = <Empty title="Your role cannot use this part of data tools" />;
  } else if (current === 'jobs' && id) {
    body = <JobDetail id={id} overview={overview} />;
  } else if (current === 'migrate') {
    body = <MigratePanel overview={overview} session={session} />;
  } else if (current === 'export') {
    body = <ExportsPanel overview={overview} />;
  } else if (current === 'history') {
    body = <HistoryPanel overview={overview} />;
  } else if (current === 'jobs') {
    body = <JobsList overview={overview} />;
  } else {
    body = <ImportPanel overview={overview} />;
  }

  return (
    <div>
      {tabs.length > 0 && (
        <nav aria-label="Data tools" className="mb-6 overflow-x-auto border-b border-line">
          <ul className="flex min-w-max gap-1">
            {tabs.map((tab) => {
              const active = tab.key === current;
              return (
                <li key={tab.key}>
                  <Link
                    href={tab.href as Route}
                    aria-current={active ? 'page' : undefined}
                    className={`block border-b-2 px-3 py-2.5 text-sm font-semibold ${active ? 'border-ink text-ink' : 'border-transparent text-muted hover:text-ink'}`}
                  >
                    {tab.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      )}
      {body}
    </div>
  );
}

// --- Import -------------------------------------------------------------------------------------

function ImportPanel({ overview }: { overview: DataOverview }) {
  const [file, setFile] = useState<File | null>(null);
  const [strategy, setStrategy] = useState<ConflictStrategy>('skip');
  const [includeStock, setIncludeStock] = useState(true);
  const [downloadImages, setDownloadImages] = useState(false);
  const [publish, setPublish] = useState(false);
  const [updatePrices, setUpdatePrices] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const fileId = useId();

  async function chooseFile(next: File | null) {
    setFile(next);
    setHeaders([]);
    setMapping({});
    if (!next || next.name.toLowerCase().endsWith('.jsonl')) return;
    try {
      const found = csvHeader(await next.slice(0, 65536).text());
      setHeaders(found);
      setMapping(autoMapping(found, overview.template_fields));
    } catch {
      setError('The first line of this file could not be read as CSV column names.');
    }
  }
  const chosen = STRATEGIES.find((s) => s.value === strategy);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError('');
    if (!file) {
      setError('Choose a CSV or JSON Lines file first.');
      return;
    }
    if (file.size > overview.max_upload_bytes) {
      setError(`The file is larger than ${formatBytes(overview.max_upload_bytes)}.`);
      return;
    }
    const form = new FormData();
    form.append('file', file);
    form.append('strategy', strategy);
    form.append(
      'options',
      JSON.stringify({
        include_stock: includeStock,
        download_images: downloadImages,
        publish,
        update_prices: strategy !== 'skip' && updatePrices,
        ...(headers.length
          ? { mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)) }
          : {}),
      }),
    );
    setBusy(true);
    try {
      const job = transferJob(await api.upload<unknown>(`${BASE}/imports/`, form));
      window.location.assign(jobUrl(job.id));
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Data"
        title="Import products"
        intro="Upload a CSV or JSON Lines file. MateCart first reads and checks every row and shows you a preview. Nothing is added to your store until you approve it."
      />
      <Feedback error={error} />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card title="Upload a file">
          <form className="grid gap-5" onSubmit={(e) => void submit(e)}>
            <div className="grid gap-1.5">
              <label htmlFor={fileId} className="text-[13px] font-semibold">
                File (CSV or JSON Lines)
              </label>
              <input
                id={fileId}
                type="file"
                accept=".csv,.jsonl,text/csv"
                className="block w-full min-w-0 text-sm file:mr-3 file:rounded-base file:border file:border-line-strong file:bg-surface file:px-3 file:py-2 file:text-sm file:font-semibold"
                onChange={(e) => void chooseFile(e.target.files?.[0] ?? null)}
              />
              <p className="text-xs leading-5 text-muted">
                Up to {formatBytes(overview.max_upload_bytes)}. Save spreadsheets as CSV UTF-8.
              </p>
            </div>
            {headers.length > 0 && (
              <ColumnMapping
                headers={headers}
                fields={overview.template_fields}
                mapping={mapping}
                onChange={setMapping}
              />
            )}
            <SelectField
              label="When a product already exists"
              value={strategy}
              options={STRATEGIES.map((s) => ({ value: s.value, label: s.label }))}
              hint={chosen?.hint}
              onChange={(e) => {
                const next = STRATEGIES.find((s) => s.value === e.target.value);
                if (next) setStrategy(next.value);
              }}
            />
            <fieldset className="grid gap-3">
              <legend className="mb-2 text-[13px] font-semibold">Options</legend>
              <Check
                label="Set opening stock for new products"
                hint="Uses the opening_stock column. Stock of existing products never changes."
                checked={includeStock}
                onChange={setIncludeStock}
              />
              <Check
                label="Download product images"
                hint="Images are fetched from the https addresses in the file and checked before they are stored."
                checked={downloadImages}
                onChange={setDownloadImages}
              />
              <Check
                label="Publish valid new products"
                hint="Otherwise new products are saved as drafts for you to review."
                checked={publish}
                onChange={setPublish}
              />
              <Check
                label="Update prices of existing products"
                hint={
                  strategy === 'skip'
                    ? 'Not used while existing products are skipped.'
                    : 'Leave this off to keep your current prices.'
                }
                checked={strategy !== 'skip' && updatePrices}
                disabled={strategy === 'skip'}
                onChange={setUpdatePrices}
              />
            </fieldset>
            <div>
              <Button type="submit" busy={busy}>
                Upload and preview
              </Button>
            </div>
          </form>
        </Card>
        <Card title="Template">
          <div className="grid gap-3 text-sm leading-6">
            <p className="text-muted">
              Start from the template so the columns match. One row per variant; rows with the same
              handle become one product.
            </p>
            <a href={`${BASE}/template/`} className="font-semibold text-brand-deep hover:underline">
              Download the CSV template
            </a>
            <details>
              <summary className="cursor-pointer font-semibold">
                Template columns ({overview.template_fields.length})
              </summary>
              <ul className="mt-2 grid gap-1">
                {overview.template_fields.map((field) => (
                  <li key={field.key} className="break-words">
                    <code className="text-ink">{field.key}</code>{' '}
                    <span className="text-muted">{field.name}</span>
                  </li>
                ))}
              </ul>
            </details>
            <a href="/manage/data/jobs" className="font-semibold text-brand-deep hover:underline">
              See earlier imports
            </a>
          </div>
        </Card>
      </div>
    </div>
  );
}

/** Column names from a CSV header line (quoted cells and a byte order mark allowed). */
export function csvHeader(text: string): string[] {
  const line = text.replace(/^\uFEFF/, '');
  const cells: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      cells.push(cell.trim());
      cell = '';
    } else if (ch === '\n' || ch === '\r') break;
    else cell += ch;
  }
  cells.push(cell.trim());
  return cells.filter(Boolean);
}

function normalName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

/** The same automatic matching the server uses: exact or alias name, each column once. */
export function autoMapping(
  headers: string[],
  fields: { key: string; aliases: string[] }[],
): Record<string, string> {
  const byName = new Map(headers.map((header) => [normalName(header), header]));
  const used = new Set<string>();
  const result: Record<string, string> = {};
  for (const field of fields) {
    for (const alias of [field.key, ...field.aliases]) {
      const header = byName.get(alias);
      if (header && !used.has(header)) {
        result[field.key] = header;
        used.add(header);
        break;
      }
    }
  }
  return result;
}

function ColumnMapping({
  headers,
  fields,
  mapping,
  onChange,
}: {
  headers: string[];
  fields: { key: string; name: string }[];
  mapping: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const used = new Set(Object.values(mapping).filter(Boolean));
  const unused = headers.filter((header) => !used.has(header));
  return (
    <details open className="rounded-base border border-line p-3">
      <summary className="cursor-pointer text-[13px] font-semibold">
        Column mapping ({used.size} of {headers.length} columns used)
      </summary>
      <p className="mt-2 text-xs leading-5 text-muted">
        Matched automatically by column name. Change any field below; columns not chosen are not
        imported.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <SelectField
            key={field.key}
            label={field.name}
            value={mapping[field.key] ?? ''}
            options={[
              { value: '', label: 'Not in this file' },
              ...headers.map((header) => ({ value: header, label: header })),
            ]}
            onChange={(e) => onChange({ ...mapping, [field.key]: e.target.value })}
          />
        ))}
      </div>
      {unused.length > 0 && (
        <p className="mt-3 text-xs leading-5 text-warning-fg">Not imported: {unused.join(', ')}</p>
      )}
    </details>
  );
}

// --- Store migration ----------------------------------------------------------------------------

function MigratePanel({ overview, session }: { overview: DataOverview; session: StaffSession }) {
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [disconnecting, setDisconnecting] = useState<SourceConnection | null>(null);
  const [verifying, setVerifying] = useState('');
  const load = useCallback(
    async () => sourceConnections(await api.get<unknown>(`${BASE}/sources/`)),
    [],
  );
  const { data: sources, setData: setSources, error: loadError, reload } = useLoad(load);
  const onError = useCallback((e: unknown) => setError(errorMessage(e)), []);
  const { run, dialog } = useSensitive(onError);

  function replace(row: SourceConnection) {
    setSources((list) => {
      const rows = list ?? [];
      return rows.some((r) => r.id === row.id)
        ? rows.map((r) => (r.id === row.id ? row : r))
        : [row, ...rows];
    });
  }

  async function verify(row: SourceConnection) {
    setError('');
    setNotice('');
    setVerifying(row.id);
    try {
      const checked = sourceConnection(
        await api.post<unknown>(`${BASE}/sources/${encodeURIComponent(row.id)}/verify/`),
      );
      replace(checked);
      setNotice(
        checked.status === 'connected'
          ? 'Connection checked. MateCart can read this store.'
          : 'Connection checked. See the status below.',
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setVerifying('');
    }
  }

  const connected = (sources ?? []).filter(
    (s) => s.status === 'connected' && (s.credential_set || s.is_simulated),
  );

  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Data"
        title="Move from another store"
        intro="Connect your WooCommerce or Shopify store with a read-only key. MateCart only reads from it and never changes anything there. Your passwords are never needed."
      />
      <Feedback error={error || loadError} notice={notice} />
      <Card title="Connected stores" flush>
        {!sources ? (
          <Loader>Loading connected stores…</Loader>
        ) : sources.length === 0 ? (
          <Empty title="No store connected yet">Connect a store below to start a migration.</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {sources.map((row) => (
              <SourceRow
                key={row.id}
                row={row}
                storeCurrency={overview.store_currency}
                verifying={verifying === row.id}
                onVerify={() => void verify(row)}
                onDisconnect={() => setDisconnecting(row)}
              />
            ))}
          </ul>
        )}
      </Card>
      <ConnectForm
        overview={overview}
        run={run}
        onConnected={(row, message) => {
          replace(row);
          setError('');
          setNotice(message);
        }}
      />
      <StartMigration
        sources={connected}
        session={session}
        overview={overview}
        run={run}
        onError={setError}
      />
      {disconnecting && (
        <Dialog title="Disconnect this store" onClose={() => setDisconnecting(null)}>
          <div className="grid gap-5">
            <p className="text-sm leading-6 text-muted">
              The stored credential is erased and no more data can be read from{' '}
              {disconnecting.name || disconnecting.source_name || disconnecting.store_url}. What was
              already imported stays in your store.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button
                variant="danger"
                type="button"
                onClick={() => {
                  const row = disconnecting;
                  setDisconnecting(null);
                  setError('');
                  setNotice('');
                  void run(
                    'Disconnect this store',
                    'Disconnecting erases the stored credential. Confirm your password.',
                    async () => {
                      await api.delete(`${BASE}/sources/${encodeURIComponent(row.id)}/`);
                      setNotice('Disconnected. The stored credential was erased.');
                      await reload();
                    },
                  );
                }}
              >
                Disconnect
              </Button>
              <Button variant="secondary" type="button" onClick={() => setDisconnecting(null)}>
                Cancel
              </Button>
            </div>
          </div>
        </Dialog>
      )}
      {dialog}
    </div>
  );
}

function SourceRow({
  row,
  storeCurrency,
  verifying,
  onVerify,
  onDisconnect,
}: {
  row: SourceConnection;
  storeCurrency: string;
  verifying: boolean;
  onVerify: () => void;
  onDisconnect: () => void;
}) {
  const status = SOURCE_STATUS[row.status];
  const usable = row.credential_set || row.is_simulated;
  const readable = MIGRATION_SCOPES.filter((scope) => row.capabilities[scope]);
  const counts = Object.entries(row.counts);
  return (
    <li className="grid gap-3 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold break-words">
            {row.name || row.source_name || row.store_url}
          </p>
          <p className="text-xs break-all text-muted">
            {PLATFORM[row.platform]} · {row.store_url}
          </p>
        </div>
        <span className="flex flex-wrap gap-1.5">
          {row.is_simulated && <Badge tone="warning">Simulator (development only)</Badge>}
          {usable ? <Badge tone={status.tone}>{status.label}</Badge> : <Badge>Disconnected</Badge>}
        </span>
      </div>
      {!usable && (
        <p className="text-sm text-muted">
          The credential was erased. This store is kept only as the source of earlier jobs.
        </p>
      )}
      {usable && (
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {row.credential_hint && (
            <div>
              <dt className="text-xs font-semibold text-muted">Credential</dt>
              <dd>Ends in {row.credential_hint}</dd>
            </div>
          )}
          <div>
            <dt className="text-xs font-semibold text-muted">Last checked</dt>
            <dd>{row.last_verified_at ? dateTime(row.last_verified_at) : 'Not checked yet'}</dd>
          </div>
          {row.source_currency && (
            <div>
              <dt className="text-xs font-semibold text-muted">Currency</dt>
              <dd>
                {row.source_currency}
                {storeCurrency && row.source_currency !== storeCurrency && (
                  <span className="block text-xs text-warning-fg">
                    Your store uses {storeCurrency}. Products cannot be migrated until the
                    currencies match; MateCart does not convert prices.
                  </span>
                )}
              </dd>
            </div>
          )}
          {row.last_verified_at && (
            <div>
              <dt className="text-xs font-semibold text-muted">Can read</dt>
              <dd>
                {readable.length > 0
                  ? readable.map((scope) => COUNT_LABEL[scope] ?? scope).join(', ')
                  : 'Nothing yet'}
              </dd>
            </div>
          )}
          {counts.length > 0 && (
            <div className="sm:col-span-2">
              <dt className="text-xs font-semibold text-muted">Records at the source</dt>
              <dd>
                {counts
                  .map(
                    ([key, value]) =>
                      `${COUNT_LABEL[key] ?? key}: ${value === null ? 'unknown' : value}`,
                  )
                  .join(' · ')}
              </dd>
            </div>
          )}
        </dl>
      )}
      {usable && row.last_error_text && row.status !== 'connected' && (
        <p className="text-sm text-danger-fg">{row.last_error_text}</p>
      )}
      {usable && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" type="button" busy={verifying} onClick={onVerify}>
            Verify
          </Button>
          <Button variant="secondary" type="button" onClick={onDisconnect}>
            Disconnect
          </Button>
        </div>
      )}
    </li>
  );
}

type Run = (title: string, description: string, action: () => Promise<void>) => Promise<void>;

function ConnectForm({
  overview,
  run,
  onConnected,
}: {
  overview: DataOverview;
  run: Run;
  onConnected: (row: SourceConnection, message: string) => void;
}) {
  const [platform, setPlatform] = useState<SourcePlatform>('woocommerce');
  const [shopifyMode, setShopifyMode] = useState<'token' | 'client'>('token');
  const [name, setName] = useState('');
  const [storeUrl, setStoreUrl] = useState('');
  const [key, setKey] = useState('');
  const [secret, setSecret] = useState('');
  const [token, setToken] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function create(body: Record<string, unknown>) {
    setError('');
    setFields({});
    setBusy(true);
    try {
      await run(
        'Connect a store',
        'Connecting a store saves its read-only credential. Confirm your password.',
        async () => {
          try {
            let row = sourceConnection(await api.post<unknown>(`${BASE}/sources/`, body));
            setKey('');
            setSecret('');
            setToken('');
            setClientId('');
            setClientSecret('');
            let message = 'Store connected and checked.';
            try {
              row = sourceConnection(
                await api.post<unknown>(`${BASE}/sources/${encodeURIComponent(row.id)}/verify/`),
              );
            } catch (e) {
              message = `Store saved, but it could not be checked yet: ${errorMessage(e)}`;
            }
            onConnected(row, message);
          } catch (e) {
            if (e instanceof ApiError && e.code === 'reauthentication_required') throw e;
            if (e instanceof ApiError) setFields(e.fieldErrors);
            setError(errorMessage(e));
          }
        },
      );
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const body: Record<string, unknown> = {
      platform,
      name: name.trim(),
      store_url: storeUrl.trim(),
    };
    if (platform === 'woocommerce') {
      body.consumer_key = key.trim();
      body.consumer_secret = secret.trim();
    } else if (shopifyMode === 'token') {
      body.access_token = token.trim();
    } else {
      body.client_id = clientId.trim();
      body.client_secret = clientSecret.trim();
    }
    void create(body);
  }

  return (
    <Card title="Connect a store">
      <form className="grid gap-5" onSubmit={submit}>
        {error && <Alert>{error}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField
            label="Platform"
            value={platform}
            options={[
              { value: 'woocommerce', label: 'WooCommerce' },
              { value: 'shopify', label: 'Shopify' },
            ]}
            onChange={(e) => setPlatform(e.target.value === 'shopify' ? 'shopify' : 'woocommerce')}
          />
          <TextField
            label="Name (optional)"
            value={name}
            maxLength={120}
            error={fields.name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        {platform === 'woocommerce' ? (
          <>
            <p className="text-sm leading-6 text-muted">
              In WordPress, open WooCommerce → Settings → Advanced → REST API and add a key with
              Read permission. Paste the consumer key and secret here. MateCart never asks for your
              WordPress password.
            </p>
            <TextField
              label="Store address"
              placeholder="https://shop.example.com"
              inputMode="url"
              autoComplete="off"
              required
              value={storeUrl}
              error={fields.store_url}
              onChange={(e) => setStoreUrl(e.target.value)}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label="Consumer key"
                placeholder="ck_…"
                autoComplete="off"
                spellCheck={false}
                required
                value={key}
                error={fields.consumer_key}
                onChange={(e) => setKey(e.target.value)}
              />
              <TextField
                label="Consumer secret"
                type="password"
                placeholder="cs_…"
                autoComplete="off"
                required
                value={secret}
                error={fields.consumer_secret}
                onChange={(e) => setSecret(e.target.value)}
              />
            </div>
          </>
        ) : (
          <>
            <TextField
              label="Shopify address"
              placeholder="my-shop.myshopify.com"
              autoComplete="off"
              spellCheck={false}
              required
              value={storeUrl}
              error={fields.store_url}
              hint="The store's myshopify.com address, not its custom domain."
              onChange={(e) => setStoreUrl(e.target.value)}
            />
            <SelectField
              label="How to connect"
              value={shopifyMode}
              options={[
                { value: 'token', label: 'Admin API access token of an existing custom app' },
                { value: 'client', label: 'Client ID and secret of your own Dev Dashboard app' },
              ]}
              onChange={(e) => setShopifyMode(e.target.value === 'client' ? 'client' : 'token')}
            />
            <p className="text-sm leading-6 text-muted">
              The app needs the read_products and read_inventory scopes. To migrate customers,
              orders or coupons, also add read_customers, read_orders, read_all_orders and
              read_discounts. MateCart never asks for your Shopify password.
            </p>
            {shopifyMode === 'token' ? (
              <TextField
                label="Admin API access token"
                type="password"
                autoComplete="off"
                required
                value={token}
                error={fields.access_token}
                onChange={(e) => setToken(e.target.value)}
              />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField
                  label="Client ID"
                  autoComplete="off"
                  spellCheck={false}
                  required
                  value={clientId}
                  error={fields.client_id}
                  onChange={(e) => setClientId(e.target.value)}
                />
                <TextField
                  label="Client secret"
                  type="password"
                  autoComplete="off"
                  required
                  value={clientSecret}
                  error={fields.client_secret}
                  onChange={(e) => setClientSecret(e.target.value)}
                />
              </div>
            )}
          </>
        )}
        <div>
          <Button type="submit" busy={busy}>
            Connect and check
          </Button>
        </div>
      </form>
      {overview.simulator_allowed && (
        <div className="mt-6 grid gap-3 border-t border-line pt-5">
          <p className="text-sm font-semibold">Simulator (development only)</p>
          <p className="text-sm leading-6 text-muted">
            Adds a fake {PLATFORM[platform]} store with sample data so the migration can be tried.
            Nothing is read from a real store. Not available on live stores.
          </p>
          <div>
            <Button
              variant="secondary"
              type="button"
              disabled={busy}
              onClick={() => void create({ platform, simulated: true })}
            >
              Add {PLATFORM[platform]} simulator
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function StartMigration({
  sources,
  session,
  overview,
  run,
  onError,
}: {
  sources: SourceConnection[];
  session: StaffSession;
  overview: DataOverview;
  run: Run;
  onError: (message: string) => void;
}) {
  const [chosenId, setChosenId] = useState('');
  const [scopes, setScopes] = useState<MigrationScope[]>([]);
  const [strategy, setStrategy] = useState<ConflictStrategy>('skip');
  const [includeStock, setIncludeStock] = useState(true);
  const [downloadImages, setDownloadImages] = useState(false);
  const [publish, setPublish] = useState(false);
  const [updatePrices, setUpdatePrices] = useState(false);
  const [busy, setBusy] = useState(false);

  const source = sources.find((s) => s.id === chosenId) ?? sources[0];
  const strategyHint = STRATEGIES.find((s) => s.value === strategy)?.hint;

  function reason(scope: MigrationScope): string {
    if (!source) return '';
    if (!can(session, SCOPE[scope].permissions)) return 'Your role cannot migrate this.';
    if (!source.capabilities[scope]) return 'The source credential cannot read this.';
    return '';
  }
  const chosen = scopes.filter((scope) => reason(scope) === '');
  const sensitive = chosen.includes('customers') || chosen.includes('orders');
  const currencyMismatch =
    !!source &&
    chosen.includes('products') &&
    !!source.source_currency &&
    !!overview.store_currency &&
    source.source_currency !== overview.store_currency;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!source) return;
    if (chosen.length === 0) {
      onError('Choose what to migrate.');
      return;
    }
    onError('');
    setBusy(true);
    void run(
      'Start the migration',
      'Reading customer or order data needs a recent sign-in. Confirm your password.',
      async () => {
        const job = transferJob(
          await api.post<unknown>(`${BASE}/migrations/`, {
            connection: source.id,
            scopes: chosen,
            strategy,
            options: {
              include_stock: includeStock,
              download_images: downloadImages,
              publish,
              update_prices: strategy !== 'skip' && updatePrices,
            },
          }),
        );
        window.location.assign(jobUrl(job.id));
      },
    ).finally(() => setBusy(false));
  }

  return (
    <Card title="Start a migration">
      {sources.length === 0 ? (
        <p className="text-sm leading-6 text-muted">
          Connect a store and check it successfully first. Only stores with the Connected status can
          be migrated.
        </p>
      ) : (
        <form className="grid gap-5" onSubmit={submit}>
          <Banner tone="info" title="Nothing is imported until you approve">
            MateCart first reads and validates everything and shows a preview. Customers and orders
            become read-only imported history: no customer accounts are created, no live orders
            appear, and no emails are sent.
          </Banner>
          <SelectField
            label="Source store"
            value={source?.id ?? ''}
            options={sources.map((s) => ({
              value: s.id,
              label: `${s.name || s.source_name || s.store_url} (${PLATFORM[s.platform]}${s.is_simulated ? ', simulator' : ''})`,
            }))}
            onChange={(e) => setChosenId(e.target.value)}
          />
          <fieldset className="grid gap-3">
            <legend className="mb-2 text-[13px] font-semibold">What to migrate</legend>
            {MIGRATION_SCOPES.map((scope) => {
              const why = reason(scope);
              return (
                <Check
                  key={scope}
                  label={SCOPE[scope].label}
                  hint={why || undefined}
                  disabled={why !== ''}
                  checked={why === '' && scopes.includes(scope)}
                  onChange={(on) =>
                    setScopes((list) =>
                      on
                        ? [...list.filter((s) => s !== scope), scope]
                        : list.filter((s) => s !== scope),
                    )
                  }
                />
              );
            })}
          </fieldset>
          {currencyMismatch && source && (
            <Alert>
              The source sells in {source.source_currency} and this store in{' '}
              {overview.store_currency}. MateCart does not convert prices, so products cannot be
              migrated from this store yet.
            </Alert>
          )}
          <SelectField
            label="When a record already exists"
            value={strategy}
            options={STRATEGIES.map((s) => ({ value: s.value, label: s.label }))}
            hint={strategyHint}
            onChange={(e) => {
              const next = STRATEGIES.find((s) => s.value === e.target.value);
              if (next) setStrategy(next.value);
            }}
          />
          {chosen.includes('products') && (
            <fieldset className="grid gap-3">
              <legend className="mb-2 text-[13px] font-semibold">Product options</legend>
              <Check
                label="Copy stock levels for new products"
                checked={includeStock}
                onChange={setIncludeStock}
              />
              <Check
                label="Download product images"
                checked={downloadImages}
                onChange={setDownloadImages}
              />
              <Check
                label="Publish valid new products"
                hint="Otherwise new products are saved as drafts."
                checked={publish}
                onChange={setPublish}
              />
              <Check
                label="Update prices of existing products"
                hint={
                  strategy === 'skip' ? 'Not used while existing products are skipped.' : undefined
                }
                checked={strategy !== 'skip' && updatePrices}
                disabled={strategy === 'skip'}
                onChange={setUpdatePrices}
              />
            </fieldset>
          )}
          {sensitive && (
            <p className="text-xs leading-5 text-muted">
              Customers and orders need a recent sign-in; you may be asked for your password.
            </p>
          )}
          <div>
            <Button type="submit" busy={busy} disabled={chosen.length === 0}>
              Read and preview
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

// --- Job detail ---------------------------------------------------------------------------------

function stage(job: TransferJob): { tone: Tone; title: string; text: string } {
  if (job.kind === 'export') {
    switch (job.status) {
      case 'completed':
      case 'completed_with_errors':
        return {
          tone: 'success',
          title: 'Export ready',
          text: 'Download the file below before it expires.',
        };
      case 'failed':
        return {
          tone: 'danger',
          title: 'Failed',
          text: job.error_text || 'The export could not be generated.',
        };
      case 'cancelled':
        return { tone: 'neutral', title: 'Cancelled', text: 'No file was produced.' };
      default:
        return {
          tone: 'info',
          title: 'Generating the export',
          text: 'This page updates on its own.',
        };
    }
  }
  switch (job.status) {
    case 'analysing':
      return {
        tone: 'info',
        title: 'Reading and validating — nothing has been imported yet',
        text: 'MateCart is reading and checking every record. This page updates on its own.',
      };
    case 'review':
      return {
        tone: 'warning',
        title: 'Preview ready — nothing has been imported yet; review and approve',
        text: 'Check what will be created, updated and skipped below. Your store changes only after you approve.',
      };
    case 'queued':
    case 'running':
      return {
        tone: 'info',
        title: 'Importing',
        text: job.cancel_requested
          ? 'Stopping after the current item.'
          : 'Records are being added one by one. This page updates on its own.',
      };
    case 'completed':
      return { tone: 'success', title: 'Completed', text: 'Every approved record was processed.' };
    case 'completed_with_errors':
      return {
        tone: 'warning',
        title: 'Completed with errors',
        text: 'Some records could not be imported. Download the error report for details.',
      };
    case 'cancelled':
      return job.approved_at
        ? {
            tone: 'neutral',
            title: 'Cancelled — you can resume',
            text: 'Records already imported stay. Resuming continues with the remaining ones.',
          }
        : { tone: 'neutral', title: 'Cancelled', text: 'Nothing was imported.' };
    case 'failed':
      return {
        tone: 'danger',
        title: 'Failed',
        text:
          job.error_text ||
          (job.approved_at
            ? 'The import stopped. You can resume it to continue with the remaining records.'
            : 'Nothing was imported.'),
      };
  }
}

function Counts({ items }: { items: [string, number][] }) {
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {items.map(([name, value]) => (
        <div key={name} className="rounded-base border border-line bg-surface p-3">
          <dt className="text-xs font-semibold text-muted">{name}</dt>
          <dd className="mt-1 text-xl font-bold tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Progress({ done, total }: { done: number; total: number }) {
  const percent = Math.min(100, Math.floor((done / total) * 100));
  return (
    <div className="grid gap-1.5">
      <div
        role="progressbar"
        aria-label="Progress"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="h-2 overflow-hidden rounded-full bg-canvas"
      >
        <div className="h-full bg-ink" style={{ width: `${percent}%` }} />
      </div>
      <p className="text-xs text-muted tabular-nums">
        {done} of {total} processed
      </p>
    </div>
  );
}

type Confirm = 'approve' | 'compensate' | null;

function JobDetail({ id, overview }: { id: string; overview: DataOverview }) {
  const load = useCallback(
    async () => transferJob(await api.get<unknown>(`${BASE}/jobs/${encodeURIComponent(id)}/`)),
    [id],
  );
  const { data: job, setData: setJob, error: loadError, reload } = useLoad(load);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [busy, setBusy] = useState('');
  const [decided, setDecided] = useState(0);
  const onError = useCallback((e: unknown) => setError(errorMessage(e)), []);
  const { run, dialog } = useSensitive(onError);

  usePoll(job !== null && isJobActive(job.status), reload);

  if (!job) return loadError ? <Alert>{loadError}</Alert> : <Loader>Loading the job…</Loader>;

  const path = `${BASE}/jobs/${encodeURIComponent(job.id)}`;
  const current = stage(job);
  const summary = job.summary;
  const importing = job.kind !== 'export';
  const stopped = ['completed', 'completed_with_errors', 'cancelled', 'failed'].includes(
    job.status,
  );
  const canCancel =
    ['analysing', 'review', 'queued', 'running'].includes(job.status) && !job.cancel_requested;
  const canResume =
    importing &&
    job.approved_at !== null &&
    (job.status === 'cancelled' || job.status === 'failed');
  const canReverse =
    importing && job.approved_at !== null && stopped && job.compensated_at === null;
  const undecided = summary?.undecided ?? 0;
  const nothingValid = summary !== null && summary.valid === 0;
  const approveBlock =
    undecided > 0
      ? `Decide every item that needs a decision first (${undecided} left).`
      : nothingValid
        ? 'Nothing valid to import.'
        : '';

  async function action(name: string, url: string, done: string) {
    setError('');
    setNotice('');
    setBusy(name);
    await run(
      name === 'resume' ? 'Resume the import' : 'Confirm it is you',
      'This change needs a recent sign-in. Confirm your password.',
      async () => {
        setJob(transferJob(await api.post<unknown>(url)));
        setNotice(done);
      },
    );
    setBusy('');
  }

  async function decide(decisions: Record<string, 'skip' | 'update'>) {
    setError('');
    try {
      setJob(transferJob(await api.post<unknown>(`${path}/decisions/`, { decisions })));
      setDecided((n) => n + 1);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function approve() {
    setError('');
    setNotice('');
    await run(
      'Approve the import',
      'Approving a store migration or customer and order data needs a recent sign-in. Confirm your password.',
      async () => {
        setJob(transferJob(await api.post<unknown>(`${path}/approve/`)));
        setNotice('Approved. The import has started.');
      },
    );
    setConfirm(null);
  }

  async function reverse() {
    setError('');
    setNotice('');
    await run(
      'Reverse this import',
      'Reversing an import needs a recent sign-in. Confirm your password.',
      async () => {
        const result = compensation(await api.post<unknown>(`${path}/compensate/`));
        setNotice(
          `Reversed. ${result.deleted} removed, ${result.archived} archived, ${result.kept} kept because they were already sold or used.`,
        );
        await reload();
      },
    );
    setConfirm(null);
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow={JOB_KIND[job.kind]}
        title={jobTitle(job, overview)}
        intro={
          <>
            Started by {job.created_by_name || 'a staff member'} on {dateTime(job.created_at)}
            {job.approved_at &&
              `. Approved by ${job.approved_by_name || 'a staff member'} on ${dateTime(job.approved_at)}`}
            .
          </>
        }
        actions={
          <a
            href="/manage/data/jobs"
            className="text-sm font-semibold text-brand-deep hover:underline"
          >
            All jobs
          </a>
        }
      />
      <Feedback error={error} notice={notice} />
      <div className="flex flex-wrap items-center gap-2">
        <JobStatusBadge status={job.status} />
        {job.connection_simulated && <Badge tone="warning">Simulator (development only)</Badge>}
        {job.sensitive && <Badge tone="info">Contains personal data</Badge>}
        {job.compensated_at && <Badge>Reversed {dateTime(job.compensated_at)}</Badge>}
      </div>
      <Banner tone={current.tone} title={current.title}>
        {current.text}
      </Banner>
      {job.total_items > 0 && (job.status === 'running' || job.status === 'queued') && (
        <Progress done={job.processed_items} total={job.total_items} />
      )}
      {importing && summary && (
        <section className="grid gap-3">
          <h2 className="text-base font-bold">Preview</h2>
          <Counts
            items={[
              ['To create', summary.to_create],
              ['To update', summary.to_update],
              ['To skip', summary.to_skip],
              ['Needs decision', summary.conflicts],
              ['Invalid', summary.invalid],
              ['Warnings', summary.with_warnings],
            ]}
          />
        </section>
      )}
      {importing && job.approved_at && (
        <section className="grid gap-3">
          <h2 className="text-base font-bold">Result so far</h2>
          <Counts
            items={[
              ['Created', job.created_count],
              ['Updated', job.updated_count],
              ['Skipped', job.skipped_count],
              ['Failed', job.failed_count],
            ]}
          />
        </section>
      )}
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {job.kind === 'migration' && job.scopes.length > 0 && (
          <div>
            <dt className="text-xs font-semibold text-muted">Migrating</dt>
            <dd>{job.scopes.map((scope) => COUNT_LABEL[scope] ?? scope).join(', ')}</dd>
          </div>
        )}
        {importing && (
          <div>
            <dt className="text-xs font-semibold text-muted">Existing records</dt>
            <dd>{STRATEGIES.find((s) => s.value === job.conflict_strategy)?.label}</dd>
          </div>
        )}
        {job.kind === 'export' && (
          <div>
            <dt className="text-xs font-semibold text-muted">Format</dt>
            <dd>{job.file_format.toUpperCase()}</dd>
          </div>
        )}
        {job.finished_at && (
          <div>
            <dt className="text-xs font-semibold text-muted">Finished</dt>
            <dd>{dateTime(job.finished_at)}</dd>
          </div>
        )}
      </dl>
      {(job.output_file || job.report_file) && (
        <div className="grid gap-2">
          {job.output_file && (
            <FileDownload
              file={job.output_file}
              sensitive={job.sensitive}
              label="Download the export"
            />
          )}
          {job.report_file && (
            <FileDownload
              file={job.report_file}
              sensitive={job.sensitive}
              label="Download the error report"
            />
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {job.status === 'review' && (
          <Button
            type="button"
            disabled={approveBlock !== ''}
            onClick={() => setConfirm('approve')}
          >
            Approve and import
          </Button>
        )}
        {canResume && (
          <Button
            type="button"
            busy={busy === 'resume'}
            onClick={() =>
              void action(
                'resume',
                `${path}/resume/`,
                'Resumed. The remaining records are being imported.',
              )
            }
          >
            Resume
          </Button>
        )}
        {canCancel && (
          <Button
            variant="secondary"
            type="button"
            busy={busy === 'cancel'}
            onClick={() =>
              void action(
                'cancel',
                `${path}/cancel/`,
                job.status === 'running' ? 'Stopping after the current item.' : 'Cancelled.',
              )
            }
          >
            Cancel
          </Button>
        )}
        {canReverse && (
          <Button variant="danger" type="button" onClick={() => setConfirm('compensate')}>
            Reverse import
          </Button>
        )}
      </div>
      {job.status === 'review' && approveBlock && (
        <p className="text-sm text-muted">{approveBlock}</p>
      )}
      {importing && (
        <ItemsTable
          job={job}
          version={`${job.status}:${job.processed_items}:${decided}`}
          onDecide={decide}
        />
      )}
      {confirm === 'approve' && summary && (
        <ConfirmAction
          title="Approve and import"
          confirmLabel="Approve and start"
          onCancel={() => setConfirm(null)}
          onConfirm={approve}
        >
          <p>Your store will change as follows:</p>
          <ul className="mt-2 list-disc pl-5">
            <li>{summary.to_create} new records created</li>
            <li>{summary.to_update} existing records updated</li>
            <li>{summary.to_skip} records skipped</li>
            <li>{summary.invalid} invalid records left out</li>
          </ul>
          {job.scopes.some((s) => s === 'customers' || s === 'orders') && (
            <p className="mt-2">
              Customers and orders are saved as read-only imported history. No accounts, live orders
              or emails are created.
            </p>
          )}
        </ConfirmAction>
      )}
      {confirm === 'compensate' && (
        <ConfirmAction
          title="Reverse this import"
          confirmLabel="Reverse import"
          danger
          onCancel={() => setConfirm(null)}
          onConfirm={reverse}
        >
          <p>
            This removes only what this job created. Products that were already sold are kept, and
            products with stock history are archived instead of removed. Records the job only
            updated or matched are never touched.
          </p>
        </ConfirmAction>
      )}
      {dialog}
    </div>
  );
}

function ConfirmAction({
  title,
  confirmLabel,
  danger = false,
  children,
  onConfirm,
  onCancel,
}: {
  title: string;
  confirmLabel: string;
  danger?: boolean;
  children: ReactNode;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog title={title} onClose={onCancel}>
      <div className="grid gap-5">
        <div className="text-sm leading-6 text-muted">{children}</div>
        <div className="flex flex-wrap gap-3">
          <Button
            type="button"
            variant={danger ? 'danger' : 'primary'}
            busy={busy}
            onClick={() => {
              setBusy(true);
              void onConfirm().finally(() => setBusy(false));
            }}
          >
            {confirmLabel}
          </Button>
          <Button variant="secondary" type="button" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function ItemsTable({
  job,
  version,
  onDecide,
}: {
  job: TransferJob;
  version: string;
  onDecide: (decisions: Record<string, 'skip' | 'update'>) => Promise<void>;
}) {
  const [filter, setFilter] = useState('');
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<Page<ImportItem> | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  useEffect(() => {
    let live = true;
    api
      .get<unknown>(
        `${BASE}/jobs/${encodeURIComponent(job.id)}/items/${query({ action: filter, page })}`,
      )
      .then((value) => {
        if (!live) return;
        setItems(importItemPage(value));
        setError('');
      })
      .catch((e: unknown) => {
        if (live) setError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, [job.id, filter, page, version]);

  const deciding = job.status === 'review';
  const conflicts = (items?.results ?? []).filter((item) => item.action === 'conflict');

  async function decide(key: string, decisions: Record<string, 'skip' | 'update'>) {
    setBusy(key);
    await onDecide(decisions);
    setBusy('');
  }

  function all(decision: 'skip' | 'update') {
    return Object.fromEntries(conflicts.map((item) => [item.id, decision]));
  }

  return (
    <Card title="Items" flush>
      <div
        role="group"
        aria-label="Show items"
        className="flex flex-wrap gap-2 border-b border-line p-4"
      >
        {ITEM_FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={filter === option.value}
            className={`rounded-badge border px-3 py-1.5 text-xs font-semibold ${filter === option.value ? 'border-ink bg-ink text-surface' : 'border-line-strong bg-surface text-ink hover:border-ink'}`}
            onClick={() => {
              setFilter(option.value);
              setPage(1);
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
      {deciding && conflicts.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 text-sm">
          <span className="text-muted">
            For the {conflicts.length} items shown that need a decision:
          </span>
          <Button
            variant="secondary"
            type="button"
            busy={busy === 'all-skip'}
            onClick={() => void decide('all-skip', all('skip'))}
          >
            Skip all
          </Button>
          <Button
            variant="secondary"
            type="button"
            busy={busy === 'all-update'}
            onClick={() => void decide('all-update', all('update'))}
          >
            Update all
          </Button>
        </div>
      )}
      {error && (
        <div className="p-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {!items ? (
        error ? null : (
          <Loader>Loading items…</Loader>
        )
      ) : items.results.length === 0 ? (
        <Empty title={job.status === 'analysing' ? 'Still reading the records' : 'No items here'} />
      ) : (
        <>
          <Table head={['Item', 'Action', 'Status', 'Messages']}>
            {items.results.map((item) => (
              <tr key={item.id}>
                <Cell className="min-w-48">
                  <p className="font-semibold break-words">
                    {item.title || item.key || `Row ${item.position}`}
                  </p>
                  <p className="text-xs break-all text-muted">
                    {[item.object_type, item.key, `#${item.position}`].filter(Boolean).join(' · ')}
                  </p>
                </Cell>
                <Cell>
                  <div className="grid justify-items-start gap-2">
                    <Badge tone={ACTION[item.action].tone}>{ACTION[item.action].label}</Badge>
                    {item.action === 'conflict' && item.decision && (
                      <span className="text-xs text-muted">
                        Decided: {item.decision === 'update' ? 'update' : 'skip'}
                      </span>
                    )}
                    {item.action === 'conflict' && deciding && (
                      <div className="flex gap-1.5">
                        {(['skip', 'update'] as const).map((choice) => (
                          <button
                            key={choice}
                            type="button"
                            aria-pressed={item.decision === choice}
                            disabled={busy !== ''}
                            className={`rounded-badge border px-2.5 py-1 text-xs font-semibold disabled:opacity-50 ${item.decision === choice ? 'border-ink bg-ink text-surface' : 'border-line-strong bg-surface text-ink hover:border-ink'}`}
                            onClick={() => void decide(item.id, { [item.id]: choice })}
                          >
                            {choice === 'skip' ? 'Skip' : 'Update'}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </Cell>
                <Cell>
                  <Badge tone={ITEM_STATUS[item.status].tone}>
                    {ITEM_STATUS[item.status].label}
                  </Badge>
                </Cell>
                <Cell className="min-w-56">
                  {item.messages.length === 0 ? (
                    <span className="text-xs text-muted">—</span>
                  ) : (
                    <ul className="grid gap-1">
                      {item.messages.map((message, index) => (
                        <li
                          key={index}
                          className={`text-xs leading-5 ${message.level === 'error' ? 'text-danger-fg' : message.level === 'warning' ? 'text-warning-fg' : 'text-muted'}`}
                        >
                          {message.text}
                        </li>
                      ))}
                    </ul>
                  )}
                </Cell>
              </tr>
            ))}
          </Table>
          <Pagination
            page={items.page}
            pageSize={items.page_size}
            count={items.count}
            onPage={setPage}
          />
        </>
      )}
    </Card>
  );
}

// --- Jobs ---------------------------------------------------------------------------------------

function JobsList({ overview }: { overview: DataOverview }) {
  const [kind, setKind] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () => transferJobPage(await api.get<unknown>(`${BASE}/jobs/${query({ kind, page })}`)),
    [kind, page],
  );
  const { data, error, reload } = useLoad(load);
  usePoll(
    (data?.results ?? []).some((job) => isJobActive(job.status)),
    reload,
  );
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Data"
        title="All jobs"
        intro="Every import, migration and export you can see, newest first."
      />
      <Feedback error={error} />
      <Card flush>
        <div className="border-b border-line p-4 sm:max-w-xs">
          <SelectField
            label="Show"
            value={kind}
            options={[
              { value: '', label: 'All jobs' },
              { value: 'import', label: 'File imports' },
              { value: 'migration', label: 'Store migrations' },
              { value: 'export', label: 'Exports' },
            ]}
            onChange={(e) => {
              setKind(e.target.value);
              setPage(1);
            }}
          />
        </div>
        {!data ? (
          error ? null : (
            <Loader>Loading jobs…</Loader>
          )
        ) : data.results.length === 0 ? (
          <Empty title="No jobs yet" />
        ) : (
          <>
            <JobTable jobs={data.results} overview={overview} />
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

function JobTable({ jobs, overview }: { jobs: TransferJob[]; overview: DataOverview }) {
  return (
    <Table head={['Job', 'Status', 'Progress', 'Started by', 'Created']}>
      {jobs.map((job) => (
        <tr key={job.id}>
          <Cell className="min-w-48">
            <Link
              href={jobUrl(job.id) as Route}
              className="font-semibold text-brand-deep hover:underline"
            >
              {jobTitle(job, overview)}
            </Link>
            <p className="text-xs text-muted">{JOB_KIND[job.kind]}</p>
          </Cell>
          <Cell>
            <JobStatusBadge status={job.status} />
          </Cell>
          <Cell className="tabular-nums">
            {job.total_items > 0 ? `${job.processed_items} of ${job.total_items}` : '—'}
          </Cell>
          <Cell>{job.created_by_name || '—'}</Cell>
          <Cell className="whitespace-nowrap">{dateTime(job.created_at)}</Cell>
        </tr>
      ))}
    </Table>
  );
}

// --- Exports ------------------------------------------------------------------------------------

function ExportsPanel({ overview }: { overview: DataOverview }) {
  const [formats, setFormats] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      transferJobPage(await api.get<unknown>(`${BASE}/jobs/${query({ kind: 'export', page })}`)),
    [page],
  );
  const { data, error: loadError, reload } = useLoad(load);
  usePoll(
    (data?.results ?? []).some((job) => isJobActive(job.status)),
    reload,
  );
  const onError = useCallback((e: unknown) => setError(errorMessage(e)), []);
  const { run, dialog } = useSensitive(onError);

  async function generate(entity: string, name: string, format: string) {
    setError('');
    setNotice('');
    setBusy(entity);
    await run(
      `Export ${name.toLowerCase()}`,
      'This export contains personal data. Confirm your password; every download is recorded.',
      async () => {
        transferJob(await api.post<unknown>(`${BASE}/exports/`, { entity, format }));
        setNotice(`${name} export started. It appears below when it is ready.`);
        await reload();
      },
    );
    setBusy('');
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Data"
        title="Export your data"
        intro="Download your store's records as files. Files are kept for a limited time and then deleted."
      />
      <Feedback error={error || loadError} notice={notice} />
      <Card title="Available exports" flush>
        <ul className="divide-y divide-line">
          {overview.exports.map((kind) => {
            const format = formats[kind.key] ?? kind.formats[0] ?? 'csv';
            return (
              <li key={kind.key} className="flex flex-wrap items-end justify-between gap-4 p-5">
                <div className="min-w-0">
                  <p className="font-semibold">{kind.name}</p>
                  <p className="text-xs leading-5 text-muted">
                    {!kind.allowed
                      ? 'Your role cannot export this.'
                      : kind.sensitive
                        ? 'Needs a recent sign-in; downloads are recorded.'
                        : `Format: ${kind.formats.map((f) => f.toUpperCase()).join(' or ')}.`}
                  </p>
                </div>
                <div className="flex flex-wrap items-end gap-2">
                  {kind.formats.length > 1 && (
                    <div className="w-36">
                      <SelectField
                        label="Format"
                        value={format}
                        disabled={!kind.allowed}
                        options={kind.formats.map((f) => ({
                          value: f,
                          label: f === 'jsonl' ? 'JSON Lines' : f.toUpperCase(),
                        }))}
                        onChange={(e) =>
                          setFormats((current) => ({ ...current, [kind.key]: e.target.value }))
                        }
                      />
                    </div>
                  )}
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!kind.allowed}
                    busy={busy === kind.key}
                    aria-label={`Generate ${kind.name} export`}
                    onClick={() => void generate(kind.key, kind.name, format)}
                  >
                    Generate
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      </Card>
      <Card title="Recent exports" flush>
        {!data ? (
          loadError ? null : (
            <Loader>Loading exports…</Loader>
          )
        ) : data.results.length === 0 ? (
          <Empty title="No exports yet" />
        ) : (
          <>
            <Table head={['Export', 'Status', 'Requested', 'File']}>
              {data.results.map((job) => (
                <tr key={job.id}>
                  <Cell className="min-w-40">
                    <Link
                      href={jobUrl(job.id) as Route}
                      className="font-semibold text-brand-deep hover:underline"
                    >
                      {jobTitle(job, overview)}
                    </Link>
                    <p className="text-xs text-muted">{job.file_format.toUpperCase()}</p>
                  </Cell>
                  <Cell>
                    <JobStatusBadge status={job.status} />
                  </Cell>
                  <Cell className="whitespace-nowrap">{dateTime(job.created_at)}</Cell>
                  <Cell className="min-w-56">
                    {job.output_file ? (
                      <FileDownload
                        file={job.output_file}
                        sensitive={job.sensitive}
                        label="Download"
                      />
                    ) : (
                      <span className="text-xs text-muted">
                        {isJobActive(job.status) ? 'Being generated' : job.error_text || 'No file'}
                      </span>
                    )}
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
      {dialog}
    </div>
  );
}

// --- Imported history ---------------------------------------------------------------------------

function HistoryPanel({ overview }: { overview: DataOverview }) {
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Data"
        title="Imported history"
        intro="Read-only records from your previous store. They are not MateCart customer accounts or orders, nobody is emailed, and amounts are shown as the source recorded them."
      />
      {overview.can_view_customers && <CustomerHistory />}
      {overview.can_view_orders && <OrderHistory />}
    </div>
  );
}

function SearchBar({
  label,
  placeholder,
  onSearch,
}: {
  label: string;
  placeholder: string;
  onSearch: (q: string) => void;
}) {
  const [draft, setDraft] = useState('');
  return (
    <form
      className="flex flex-wrap items-end gap-2 border-b border-line p-4"
      onSubmit={(e) => {
        e.preventDefault();
        onSearch(draft.trim());
      }}
    >
      <div className="min-w-0 flex-1 sm:max-w-sm">
        <TextField
          label={label}
          type="search"
          placeholder={placeholder}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      </div>
      <Button type="submit" variant="secondary">
        Search
      </Button>
    </form>
  );
}

function CustomerHistory() {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      historicalCustomerPage(
        await api.get<unknown>(`${BASE}/history/customers/${query({ q, page })}`),
      ),
    [q, page],
  );
  const { data, error } = useLoad(load);
  return (
    <Card title="Customers" flush>
      <SearchBar
        label="Search customers"
        placeholder="Name, email or phone"
        onSearch={(value) => {
          setQ(value);
          setPage(1);
        }}
      />
      {error && (
        <div className="p-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {!data ? (
        error ? null : (
          <Loader>Loading customers…</Loader>
        )
      ) : data.results.length === 0 ? (
        <Empty title={q ? 'No matching customers' : 'No imported customers'} />
      ) : (
        <>
          <Table head={['Customer', 'Contact', 'Location', 'Orders at source', 'Customer since']}>
            {data.results.map((row) => (
              <tr key={row.id}>
                <Cell className="min-w-40">
                  <p className="font-semibold">
                    {[row.first_name, row.last_name].filter(Boolean).join(' ') || '—'}
                  </p>
                  <p className="text-xs text-muted">
                    {row.platform ? (PLATFORM[row.platform as SourcePlatform] ?? row.platform) : ''}
                  </p>
                </Cell>
                <Cell className="min-w-48 break-all">
                  {row.email && <p>{row.email}</p>}
                  {row.phone && <p className="text-xs text-muted">{row.phone}</p>}
                  {!row.email && !row.phone && '—'}
                </Cell>
                <Cell>{[row.city, row.country].filter(Boolean).join(', ') || '—'}</Cell>
                <Cell className="tabular-nums">{row.source_orders_count ?? '—'}</Cell>
                <Cell className="whitespace-nowrap">{dateTime(row.source_created_at)}</Cell>
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
  );
}

function OrderHistory() {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      historicalOrderPage(await api.get<unknown>(`${BASE}/history/orders/${query({ q, page })}`)),
    [q, page],
  );
  const { data, error } = useLoad(load);
  return (
    <Card title="Orders" flush>
      <SearchBar
        label="Search orders"
        placeholder="Order number"
        onSearch={(value) => {
          setQ(value);
          setPage(1);
        }}
      />
      {error && (
        <div className="p-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {!data ? (
        error ? null : (
          <Loader>Loading orders…</Loader>
        )
      ) : data.results.length === 0 ? (
        <Empty title={q ? 'No matching orders' : 'No imported orders'} />
      ) : (
        <>
          <Table head={['Order', 'Placed', 'Customer', 'Status at source', 'Total', 'Items']}>
            {data.results.map((row) => (
              <tr key={row.id}>
                <Cell className="whitespace-nowrap">
                  <p className="font-semibold">{row.number || row.external_id}</p>
                  <p className="text-xs text-muted">
                    {PLATFORM[row.platform as SourcePlatform] ?? row.platform}
                  </p>
                </Cell>
                <Cell className="whitespace-nowrap">{dateTime(row.placed_at)}</Cell>
                <Cell>{row.customer_name || '—'}</Cell>
                <Cell className="min-w-40 text-xs leading-5">
                  {[row.source_status, row.source_financial_status, row.source_fulfillment_status]
                    .filter(Boolean)
                    .map((s) => s.replaceAll('_', ' '))
                    .join(' · ') || '—'}
                </Cell>
                <Cell className="whitespace-nowrap tabular-nums">
                  {money(row.grand_total, row.currency)}
                </Cell>
                <Cell className="min-w-48">
                  {row.lines.length === 0 ? (
                    '—'
                  ) : (
                    <details>
                      <summary className="cursor-pointer text-sm">
                        {row.lines.length} {row.lines.length === 1 ? 'line' : 'lines'}
                      </summary>
                      <ul className="mt-1 grid gap-1 text-xs leading-5 text-muted">
                        {row.lines.map((line, index) => (
                          <li key={index}>
                            {line.quantity} × {line.name || line.sku || 'Item'}
                            {line.total ? ` · ${line.total} ${row.currency}` : ''}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
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
  );
}
