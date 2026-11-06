'use client';

import { type FormEvent, type ReactNode, useEffect, useState } from 'react';
import {
  adminPage,
  alertPage,
  auditPage,
  clientPage,
  deploymentPage,
  monitoringCredentialPage,
  deploymentAutomation,
  monitoringObservationPage,
  operationPage,
  platformCapabilities,
  platformOverview,
  releasePage,
  serverAgentState,
  serverPage,
  storePage,
  type ClientRecord,
  type DeploymentRecord,
  type PlatformCapabilities,
  type ServerRecord,
  type StoreRecord,
} from '@matecart/shared-contracts';
import { Alert, Button, Loading, errorMessage } from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';

const inputClass =
  'w-full rounded-base border border-line-strong bg-surface px-3 py-2 text-sm outline-none focus:border-brand-deep';

function title(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function filteredPath(base: string, query: string, status: string): string {
  const params = new URLSearchParams();
  if (query.trim()) params.set('q', query.trim());
  if (status) params.set('status', status);
  const suffix = params.toString();
  return suffix ? `${base}?${suffix}` : base;
}

function Status({ value }: { value: string }) {
  const tone =
    value === 'healthy' || value === 'active' || value === 'applied' || value === 'resolved'
      ? 'bg-success-bg text-success-fg'
      : value === 'unavailable' || value === 'critical' || value === 'failed'
        ? 'bg-danger-bg text-danger-fg'
        : 'bg-warning-bg text-warning-fg';
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ${tone}`}>
      {title(value)}
    </span>
  );
}

function Heading({
  title: heading,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="text-xs font-bold tracking-widest text-brand-deep uppercase">
          NetaMate operations
        </p>
        <h1 className="mt-2 text-[28px] font-bold tracking-tight">{heading}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{description}</p>
      </div>
      {action}
    </div>
  );
}

function Card({ children }: { children: ReactNode }) {
  return <div className="rounded-base border border-line bg-surface p-5">{children}</div>;
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-base border border-dashed border-line-strong p-8 text-center text-sm text-muted">
      {children}
    </div>
  );
}

function Table({ headers, rows }: { headers: string[]; rows: ReactNode[][] }) {
  if (rows.length === 0) return <Empty>No records match this view yet.</Empty>;
  return (
    <div className="overflow-x-auto rounded-base border border-line bg-surface">
      <table className="w-full min-w-[760px] text-left text-sm">
        <thead className="border-b border-line bg-canvas text-xs tracking-wide text-muted uppercase">
          <tr>
            {headers.map((header) => (
              <th key={header} className="px-4 py-3 font-bold">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((row, index) => (
            <tr key={index} className="hover:bg-canvas/60">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-4 py-3 align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ListFilters({
  query,
  status,
  statuses,
  onQuery,
  onStatus,
}: {
  query: string;
  status: string;
  statuses: string[];
  onQuery: (value: string) => void;
  onStatus: (value: string) => void;
}) {
  return (
    <div className="mb-4 grid gap-3 rounded-base border border-line bg-surface p-4 sm:grid-cols-2">
      <label className="grid gap-1 text-sm font-semibold">
        Search
        <input
          className={inputClass}
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="Name, reference, or identifier"
          type="search"
        />
      </label>
      <label className="grid gap-1 text-sm font-semibold">
        Status
        <select
          className={inputClass}
          value={status}
          onChange={(event) => onStatus(event.target.value)}
        >
          <option value="">All statuses</option>
          {statuses.map((item) => (
            <option key={item} value={item}>
              {title(item)}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function records(value: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const rows = value[key];
  return Array.isArray(rows)
    ? rows.filter((row): row is Record<string, unknown> => typeof row === 'object' && row !== null)
    : [];
}

function RelatedRecords({
  heading,
  items,
  columns,
}: {
  heading: string;
  items: Record<string, unknown>[];
  columns: [string, string][];
}) {
  return (
    <div className="mt-5">
      <h2 className="mb-3 text-lg font-bold">{heading}</h2>
      <Table
        headers={columns.map(([label]) => label)}
        rows={items.map((item) =>
          columns.map(([, key]) => {
            const entry = item[key];
            if (entry === null || entry === undefined || entry === '') return 'Unknown';
            if (Array.isArray(entry)) return entry.join(', ');
            return typeof entry === 'object' ? 'Recorded' : String(entry);
          }),
        )}
      />
    </div>
  );
}

function useData<T>(path: string, decoder: (value: unknown) => T, refresh = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    void api
      .get<unknown>(path)
      .then((value) => {
        if (live) setData(decoder(value));
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason));
      });
    return () => {
      live = false;
    };
  }, [path, decoder, refresh]);
  return { data, error };
}

function CreateClient({ done }: { done: () => void }) {
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.post('/api/console/clients/', {
        display_name: String(form.get('name') ?? ''),
        contact_email: String(form.get('email') ?? ''),
        country: String(form.get('country') ?? '').toUpperCase(),
        time_zone: String(form.get('time_zone') ?? ''),
        status: 'onboarding',
        client_type: 'business',
      });
      done();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <Card>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
        {error && (
          <div className="md:col-span-2">
            <Alert>{error}</Alert>
          </div>
        )}
        <label className="grid gap-1 text-sm font-semibold">
          Client name
          <input className={inputClass} name="name" required maxLength={160} />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Contact email
          <input className={inputClass} name="email" type="email" />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Country code
          <input
            className={inputClass}
            name="country"
            defaultValue="BD"
            minLength={2}
            maxLength={2}
            required
          />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Time zone
          <input className={inputClass} name="time_zone" defaultValue="Asia/Dhaka" required />
        </label>
        <div className="md:col-span-2">
          <Button type="submit">Register client</Button>
        </div>
      </form>
    </Card>
  );
}

function CreateStore({ clients, done }: { clients: ClientRecord[]; done: () => void }) {
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const preset = String(form.get('preset') ?? 'general');
    const themes: Record<string, string> = {
      fashion: 'fashion-editorial',
      grocery: 'grocery-quickshop',
      electronics: 'electronics-spec-first',
      general: 'general-marketplace',
      books: 'books-literary',
    };
    try {
      await api.post('/api/console/stores/', {
        client: String(form.get('client') ?? ''),
        name: String(form.get('name') ?? ''),
        handle: String(form.get('handle') ?? ''),
        preset,
        country: String(form.get('country') ?? 'BD'),
        default_currency: String(form.get('currency') ?? 'BDT'),
        time_zone: String(form.get('time_zone') ?? 'Asia/Dhaka'),
        default_language: 'en',
        supported_languages: ['en', 'bn'],
        desired_theme: themes[preset],
        desired_modules: [
          'catalog',
          'inventory',
          'content_pages',
          'customer_accounts',
          'cart_checkout',
        ],
        status: 'registered',
      });
      done();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <Card>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
        {error && (
          <div className="md:col-span-2">
            <Alert>{error}</Alert>
          </div>
        )}
        <label className="grid gap-1 text-sm font-semibold">
          Client
          <select className={inputClass} name="client" required>
            {clients.map((item) => (
              <option key={item.id} value={item.id}>
                {item.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Store name
          <input className={inputClass} name="name" required />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Store handle
          <input className={inputClass} name="handle" required pattern="[a-z0-9-]+" />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Preset
          <select className={inputClass} name="preset">
            <option value="fashion">Fashion & Apparel</option>
            <option value="grocery">Grocery & Essentials</option>
            <option value="electronics">Electronics & Gadgets</option>
            <option value="general">General / Multi-category</option>
            <option value="books">Bookstore & Booksellers</option>
          </select>
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Country
          <input className={inputClass} name="country" defaultValue="BD" maxLength={2} required />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Currency
          <input className={inputClass} name="currency" defaultValue="BDT" maxLength={3} required />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Time zone
          <input className={inputClass} name="time_zone" defaultValue="Asia/Dhaka" required />
        </label>
        <div className="self-end">
          <Button type="submit">Register store</Button>
        </div>
      </form>
    </Card>
  );
}

function CreateServer({ clients, done }: { clients: ClientRecord[]; done: () => void }) {
  const [error, setError] = useState('');
  const [ownership, setOwnership] = useState('netamate');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.post('/api/console/servers/', {
        name: String(form.get('name') ?? ''),
        ownership,
        owner_client: ownership === 'client' ? String(form.get('owner_client') ?? '') : null,
        provider: String(form.get('provider') ?? ''),
        hostname: String(form.get('hostname') ?? ''),
        country: String(form.get('country') ?? 'BD'),
        status: 'registered',
      });
      done();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <Card>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
        {error && (
          <div className="md:col-span-2">
            <Alert>{error}</Alert>
          </div>
        )}
        <label className="grid gap-1 text-sm font-semibold">
          Server name
          <input className={inputClass} name="name" required />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Ownership
          <select
            className={inputClass}
            name="ownership"
            value={ownership}
            onChange={(event) => setOwnership(event.target.value)}
          >
            <option value="netamate">NetaMate-owned</option>
            <option value="client">Client-owned</option>
          </select>
        </label>
        {ownership === 'client' && (
          <label className="grid gap-1 text-sm font-semibold">
            Owning client
            <select className={inputClass} name="owner_client" required>
              <option value="">Choose a client</option>
              {clients.map((client) => (
                <option key={client.id} value={client.id}>
                  {client.display_name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="grid gap-1 text-sm font-semibold">
          Provider
          <input className={inputClass} name="provider" />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Hostname
          <input className={inputClass} name="hostname" />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Country
          <input className={inputClass} name="country" defaultValue="BD" maxLength={2} />
        </label>
        <div className="md:col-span-2">
          <Button type="submit">Register server</Button>
        </div>
      </form>
    </Card>
  );
}

function CreateDeployment({
  stores,
  servers,
  done,
}: {
  stores: StoreRecord[];
  servers: ServerRecord[];
  done: () => void;
}) {
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.post('/api/console/deployments/', {
        store: String(form.get('store') ?? ''),
        server: String(form.get('server') ?? ''),
        environment: String(form.get('environment') ?? 'production'),
        reference: String(form.get('reference') ?? ''),
        desired_version: String(form.get('version') ?? ''),
        state: 'awaiting_deployment_integration',
      });
      done();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <Card>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
        {error && (
          <div className="md:col-span-2">
            <Alert>{error}</Alert>
          </div>
        )}
        <label className="grid gap-1 text-sm font-semibold">
          Store
          <select className={inputClass} name="store">
            {stores.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Server
          <select className={inputClass} name="server">
            {servers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Environment
          <select className={inputClass} name="environment">
            <option value="development">Development</option>
            <option value="staging">Staging</option>
            <option value="production">Production</option>
          </select>
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Internal reference
          <input className={inputClass} name="reference" required />
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Desired version
          <input className={inputClass} name="version" />
        </label>
        <div className="self-end">
          <Button type="submit">Register deployment</Button>
        </div>
      </form>
    </Card>
  );
}

function CreateOperation({ done }: { done: () => void }) {
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.post('/api/console/operations/', {
        kind: String(form.get('kind') ?? ''),
        title: String(form.get('title') ?? ''),
        description: String(form.get('description') ?? ''),
        status: 'submitted',
      });
      done();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <Card>
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
        {error && (
          <div className="md:col-span-2">
            <Alert>{error}</Alert>
          </div>
        )}
        <label className="grid gap-1 text-sm font-semibold">
          Request type
          <select className={inputClass} name="kind">
            <option value="new_deployment">New deployment</option>
            <option value="version_update">Version update</option>
            <option value="theme_change">Theme change</option>
            <option value="module_change">Module change</option>
            <option value="domain_configuration">Domain configuration</option>
            <option value="backup_investigation">Backup investigation</option>
            <option value="health_investigation">Health investigation</option>
          </select>
        </label>
        <label className="grid gap-1 text-sm font-semibold">
          Title
          <input className={inputClass} name="title" required />
        </label>
        <label className="grid gap-1 text-sm font-semibold md:col-span-2">
          Description
          <textarea className={inputClass} name="description" rows={3} />
        </label>
        <div className="md:col-span-2">
          <Button type="submit">Submit request</Button>
        </div>
      </form>
    </Card>
  );
}

function Overview() {
  const { data, error } = useData('/api/console/overview/', platformOverview);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading>Loading fleet overview…</Loading>;
  const metrics = [
    ['Clients', data.clients],
    ['Stores', data.stores],
    ['Servers', data.servers],
    ['Deployments', data.deployments],
    ['Healthy', data.healthy],
    ['Degraded', data.degraded],
    ['Unavailable', data.unavailable],
    ['Stale', data.stale],
    ['Unknown', data.unknown],
    ['Critical alerts', data.critical_alerts],
    ['Pending operations', data.pending_operations],
  ];
  return (
    <>
      <Heading
        title="Fleet overview"
        description="Factual registration, health, alert, and workflow status across the MateCart fleet."
      />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {metrics.map(([label, value]) => (
          <Card key={label}>
            <p className="text-xs font-bold tracking-wide text-muted uppercase">{label}</p>
            <p className="mt-2 text-3xl font-bold">{value}</p>
          </Card>
        ))}
      </div>
      <h2 className="mt-8 mb-3 text-lg font-bold">Recent activity</h2>
      <Table
        headers={['Action', 'Resource', 'Time']}
        rows={data.recent_activity.map((item) => [
          title(item.action),
          item.resource_type,
          new Date(item.created_at).toLocaleString(),
        ])}
      />
    </>
  );
}

function Clients({ canManage }: { canManage: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const [show, setShow] = useState(false);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const { data, error } = useData(
    filteredPath('/api/console/clients/', query, status),
    clientPage,
    refresh,
  );
  return (
    <>
      <Heading
        title="Clients"
        description="NetaMate client registry, onboarding status, contacts, stores, and assigned infrastructure."
        action={
          canManage ? (
            <Button onClick={() => setShow((value) => !value)}>
              {show ? 'Close form' : 'Add client'}
            </Button>
          ) : undefined
        }
      />
      {show && (
        <div className="mb-5">
          <CreateClient
            done={() => {
              setShow(false);
              setRefresh((value) => value + 1);
            }}
          />
        </div>
      )}
      <ListFilters
        query={query}
        status={status}
        statuses={['prospect', 'onboarding', 'active', 'on_hold', 'archived']}
        onQuery={setQuery}
        onStatus={setStatus}
      />
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading>Loading clients…</Loading>
      ) : (
        <Table
          headers={['Client', 'Status', 'Country', 'Stores', 'Servers', 'Updated']}
          rows={data.results.map((item: ClientRecord) => [
            <a key="client" className="font-bold text-brand-deep" href={`/clients/${item.id}`}>
              {item.display_name}
            </a>,
            <Status key="status" value={item.status} />,
            item.country,
            item.stores_count,
            item.servers_count,
            new Date(item.updated_at).toLocaleDateString(),
          ])}
        />
      )}
    </>
  );
}

function Stores({ canManage }: { canManage: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const [show, setShow] = useState(false);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const { data, error } = useData(
    filteredPath('/api/console/stores/', query, status),
    storePage,
    refresh,
  );
  const clients = useData('/api/console/clients/', clientPage, refresh).data;
  return (
    <>
      <Heading
        title="Stores"
        description="Stable store identities and NetaMate-controlled desired configuration. Pending changes are never presented as applied."
        action={
          canManage && clients?.results.length ? (
            <Button onClick={() => setShow((value) => !value)}>
              {show ? 'Close form' : 'Register store'}
            </Button>
          ) : undefined
        }
      />
      {show && clients && (
        <div className="mb-5">
          <CreateStore
            clients={clients.results}
            done={() => {
              setShow(false);
              setRefresh((value) => value + 1);
            }}
          />
        </div>
      )}
      <ListFilters
        query={query}
        status={status}
        statuses={['registered', 'onboarding', 'active', 'suspended', 'retired']}
        onQuery={setQuery}
        onStatus={setStatus}
      />
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading>Loading stores…</Loading>
      ) : (
        <Table
          headers={['Store', 'Client', 'Preset', 'Status', 'Configuration', 'Revision']}
          rows={data.results.map((item: StoreRecord) => [
            <a key="store" className="font-bold text-brand-deep" href={`/stores/${item.id}`}>
              {item.name}
              <span className="block text-xs font-normal text-muted">{item.id}</span>
            </a>,
            item.client_name,
            title(item.preset),
            <Status key="status" value={item.status} />,
            <Status key="configuration" value={item.configuration_state} />,
            `${item.applied_revision} / ${item.desired_revision}`,
          ])}
        />
      )}
    </>
  );
}

function Servers({ canManage }: { canManage: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const [show, setShow] = useState(false);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const { data, error } = useData(
    filteredPath('/api/console/servers/', query, status),
    serverPage,
    refresh,
  );
  const clients = useData('/api/console/clients/', clientPage, refresh).data;
  return (
    <>
      <Heading
        title="Servers"
        description="NetaMate-owned and client-owned infrastructure inventory. Unknown capacity and health remain visibly unknown."
        action={
          canManage && clients ? (
            <Button onClick={() => setShow((value) => !value)}>
              {show ? 'Close form' : 'Register server'}
            </Button>
          ) : undefined
        }
      />
      {show && clients && (
        <div className="mb-5">
          <CreateServer
            clients={clients.results}
            done={() => {
              setShow(false);
              setRefresh((value) => value + 1);
            }}
          />
        </div>
      )}
      <ListFilters
        query={query}
        status={status}
        statuses={['registered', 'active', 'maintenance', 'retired']}
        onQuery={setQuery}
        onStatus={setStatus}
      />
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading>Loading servers…</Loading>
      ) : (
        <Table
          headers={['Server', 'Owner', 'Provider', 'Hostname', 'Status', 'Deployments']}
          rows={data.results.map((item: ServerRecord) => [
            <a key="server" className="font-bold text-brand-deep" href={`/servers/${item.id}`}>
              {item.name}
            </a>,
            item.owner_name,
            item.provider || 'Unknown',
            item.hostname || 'Unknown',
            <Status key="status" value={item.status} />,
            item.deployments_count,
          ])}
        />
      )}
    </>
  );
}

function Deployments({
  monitoring = false,
  canManage = false,
}: {
  monitoring?: boolean;
  canManage?: boolean;
}) {
  const [refresh, setRefresh] = useState(0);
  const [show, setShow] = useState(false);
  const { data, error } = useData('/api/console/deployments/', deploymentPage, refresh);
  const { data: monitoringData, error: monitoringError } = useData(
    '/api/console/monitoring/?latest=true',
    monitoringObservationPage,
    refresh,
  );
  const stores = useData('/api/console/stores/', storePage, refresh).data;
  const servers = useData('/api/console/servers/', serverPage, refresh).data;
  const canCreate =
    canManage && !monitoring && Boolean(stores?.results.length && servers?.results.length);
  return (
    <>
      <Heading
        title={monitoring ? 'Monitoring' : 'Deployments'}
        description={
          monitoring
            ? 'Fleet health is based only on recent authenticated observations. Missing data is unknown or stale.'
            : 'Store environments, desired releases, observed releases, and accurate pending execution state.'
        }
        action={
          canCreate ? (
            <Button onClick={() => setShow((value) => !value)}>
              {show ? 'Close form' : 'Register deployment'}
            </Button>
          ) : undefined
        }
      />
      {show && stores && servers && (
        <div className="mb-5">
          <CreateDeployment
            stores={stores.results}
            servers={servers.results}
            done={() => {
              setShow(false);
              setRefresh((value) => value + 1);
            }}
          />
        </div>
      )}
      {error && <Alert>{error}</Alert>}
      {monitoring && monitoringError && <Alert>{monitoringError}</Alert>}
      {!data || (monitoring && !monitoringData) ? (
        <Loading>Loading deployments…</Loading>
      ) : monitoring ? (
        <Table
          headers={[
            'Store',
            'Health',
            'Latest report',
            'Freshness',
            'Response',
            'CPU',
            'RAM',
            'Disk',
            'Visitors (24h)',
            'Version',
          ]}
          rows={data.results.map((item: DeploymentRecord) => {
            const report = monitoringData?.results.find(
              (observation) => observation.deployment_id === item.id,
            );
            const metric = (value: string | number | null | undefined, suffix = '') =>
              value === null || value === undefined || value === ''
                ? 'Not reported'
                : `${value}${suffix}`;
            const freshness =
              item.health_state === 'unknown'
                ? 'Not reported'
                : item.health_state === 'stale'
                  ? 'Stale'
                  : 'Current';
            return [
              <a
                key="deployment"
                className="font-bold text-brand-deep"
                href={`/deployments/${item.id}`}
              >
                {item.store_name}
              </a>,
              <Status key="health" value={item.health_state} />,
              report ? new Date(report.observed_at).toLocaleString() : 'Never',
              freshness,
              metric(report?.response_ms, ' ms'),
              metric(report?.cpu_percent, '%'),
              metric(report?.ram_percent, '%'),
              metric(report?.disk_percent, '%'),
              metric(report?.visitor_count_24h),
              item.observed_version || 'Unknown',
            ];
          })}
        />
      ) : (
        <Table
          headers={[
            'Store',
            'Environment',
            'Server',
            'Health',
            'Desired',
            'Observed',
            'Last report',
          ]}
          rows={data.results.map((item: DeploymentRecord) => [
            <a
              key="deployment"
              className="font-bold text-brand-deep"
              href={`/deployments/${item.id}`}
            >
              {item.store_name}
            </a>,
            title(item.environment),
            item.server_name,
            <Status key="health" value={item.health_state} />,
            item.desired_version || 'Not set',
            item.observed_version || 'Unknown',
            item.last_observed_at ? new Date(item.last_observed_at).toLocaleString() : 'Never',
          ])}
        />
      )}
      {!monitoring && <Releases canManage={canManage} />}
    </>
  );
}

function Alerts() {
  const [refresh, setRefresh] = useState(0);
  const { data, error } = useData('/api/console/alerts/', alertPage, refresh);
  async function act(id: string, action: string) {
    try {
      await api.patch(`/api/console/alerts/${id}/`, { action });
      setRefresh((value) => value + 1);
    } catch {
      /* surfaced on refresh */
    }
  }
  return (
    <>
      <Heading
        title="Alerts"
        description="Deduplicated operational alerts with explicit evidence, acknowledgment, silence, and resolution state."
      />
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading>Loading alerts…</Loading>
      ) : (
        <Table
          headers={['Alert', 'Severity', 'Status', 'Opened', 'Actions']}
          rows={data.results.map((item) => [
            item.title,
            <Status key="severity" value={item.severity} />,
            <Status key="status" value={item.status} />,
            new Date(item.created_at).toLocaleString(),
            item.status === 'resolved' ? (
              '—'
            ) : (
              <div key="actions" className="flex gap-2">
                <Button variant="secondary" onClick={() => void act(item.id, 'acknowledge')}>
                  Acknowledge
                </Button>
                <Button variant="secondary" onClick={() => void act(item.id, 'resolve')}>
                  Resolve
                </Button>
              </div>
            ),
          ])}
        />
      )}
    </>
  );
}

function Operations({ canManage }: { canManage: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const [show, setShow] = useState(false);
  const [actionError, setActionError] = useState('');
  const { data, error } = useData('/api/console/operations/', operationPage, refresh);
  async function transition(id: string, status: string) {
    if (!window.confirm(`Move this request to ${title(status)}?`)) return;
    let completedEvidence = '';
    if (status === 'completed') {
      completedEvidence =
        window.prompt('Enter the independently verified completion evidence.') ?? '';
      if (!completedEvidence.trim()) return;
    }
    try {
      await api.patch(`/api/console/operations/${id}/`, {
        status,
        completed_evidence: completedEvidence,
      });
      setActionError('');
      setRefresh((value) => value + 1);
    } catch (reason) {
      setActionError(errorMessage(reason));
    }
  }
  function actions(id: string, status: string): ReactNode {
    if (!canManage || ['completed', 'cancelled', 'rejected'].includes(status)) return '—';
    const choices: Record<string, string[]> = {
      draft: ['submitted', 'cancelled'],
      submitted: ['approved', 'rejected', 'cancelled'],
      approved: ['pending_execution', 'cancelled'],
      pending_execution: ['completed', 'cancelled'],
    };
    return (
      <div className="flex flex-wrap gap-2">
        {(choices[status] ?? []).map((target) => (
          <Button key={target} variant="secondary" onClick={() => void transition(id, target)}>
            {title(target)}
          </Button>
        ))}
      </div>
    );
  }
  return (
    <>
      <Heading
        title="Operations"
        description="Review and follow up deployment, configuration, backup, certificate, and support requests. Approval never means remote execution."
        action={
          canManage ? (
            <Button onClick={() => setShow((value) => !value)}>
              {show ? 'Close form' : 'New request'}
            </Button>
          ) : undefined
        }
      />
      {show && (
        <div className="mb-5">
          <CreateOperation
            done={() => {
              setShow(false);
              setRefresh((value) => value + 1);
            }}
          />
        </div>
      )}
      {actionError && <Alert>{actionError}</Alert>}
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading>Loading operations…</Loading>
      ) : (
        <Table
          headers={['Request', 'Kind', 'Status', 'Requester', 'Created', 'Actions']}
          rows={data.results.map((item) => [
            item.title,
            title(item.kind),
            <Status key="status" value={item.status} />,
            item.requested_by_name || 'Administrator',
            new Date(item.created_at).toLocaleString(),
            actions(item.id, item.status),
          ])}
        />
      )}
    </>
  );
}

function Administrators({ canManage }: { canManage: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const [show, setShow] = useState(false);
  const [actionError, setActionError] = useState('');
  const { data, error } = useData('/api/console/administrators/', adminPage, refresh);
  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.post('/api/console/administrators/', {
        full_name: String(form.get('full_name') ?? ''),
        email: String(form.get('email') ?? ''),
        role: String(form.get('role') ?? ''),
      });
      setActionError('');
      setShow(false);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setActionError(errorMessage(reason));
    }
  }
  async function update(id: string, role: string, isActive: boolean) {
    const action = isActive ? 'activate' : 'suspend';
    if (!window.confirm(`Confirm ${action} access with role ${title(role)}?`)) return;
    try {
      await api.patch(`/api/console/administrators/${id}/`, {
        role,
        is_active: isActive,
      });
      setActionError('');
      setRefresh((value) => value + 1);
    } catch (reason) {
      setActionError(errorMessage(reason));
    }
  }
  return (
    <>
      <Heading
        title="Administrators"
        description="Console-only identities with mandatory MFA and fixed, backend-enforced NetaMate roles. Sensitive changes require recent authentication."
        action={
          canManage ? (
            <Button onClick={() => setShow((value) => !value)}>
              {show ? 'Close form' : 'Invite administrator'}
            </Button>
          ) : undefined
        }
      />
      {show && (
        <div className="mb-5">
          <Card>
            <form onSubmit={invite} className="grid gap-3 md:grid-cols-3">
              <label className="grid gap-1 text-sm font-semibold">
                Full name
                <input className={inputClass} name="full_name" required />
              </label>
              <label className="grid gap-1 text-sm font-semibold">
                Email address
                <input className={inputClass} name="email" type="email" required />
              </label>
              <label className="grid gap-1 text-sm font-semibold">
                Role
                <select className={inputClass} name="role" defaultValue="read_only_auditor">
                  <option value="platform_admin">Platform Admin</option>
                  <option value="operations_manager">Operations Manager</option>
                  <option value="support_operator">Support Operator</option>
                  <option value="read_only_auditor">Read-only Auditor</option>
                </select>
              </label>
              <div className="md:col-span-3">
                <Button type="submit">Send invitation</Button>
              </div>
            </form>
          </Card>
        </div>
      )}
      {actionError && <Alert>{actionError}</Alert>}
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading>Loading administrators…</Loading>
      ) : (
        <Table
          headers={['Administrator', 'Role', 'Access', 'MFA', 'Activated', 'Actions']}
          rows={data.results.map((item) => [
            <span key="administrator">
              <strong>{item.full_name || 'Unnamed administrator'}</strong>
              <span className="block text-xs text-muted">{item.email}</span>
            </span>,
            title(item.role),
            <Status key="access" value={item.is_active ? 'active' : 'suspended'} />,
            item.mfa_enabled ? 'Configured' : 'Pending enrollment',
            item.activated_at
              ? new Date(item.activated_at).toLocaleDateString()
              : 'Invitation pending',
            canManage ? (
              <div key="actions" className="flex flex-wrap gap-2">
                <select
                  className={inputClass}
                  aria-label={`Role for ${item.email}`}
                  value={item.role}
                  onChange={(event) => void update(item.id, event.target.value, item.is_active)}
                >
                  <option value="platform_owner">Platform Owner</option>
                  <option value="platform_admin">Platform Admin</option>
                  <option value="operations_manager">Operations Manager</option>
                  <option value="support_operator">Support Operator</option>
                  <option value="read_only_auditor">Read-only Auditor</option>
                </select>
                <Button
                  variant="secondary"
                  onClick={() => void update(item.id, item.role, !item.is_active)}
                >
                  {item.is_active ? 'Suspend' : 'Activate'}
                </Button>
              </div>
            ) : (
              '—'
            ),
          ])}
        />
      )}
    </>
  );
}

function AuditLog() {
  const { data, error } = useData('/api/console/audit/', auditPage);
  return (
    <>
      <Heading
        title="Audit log"
        description="Append-only operational history with actor, request, resource, and safe structured changes."
      />
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading>Loading audit events…</Loading>
      ) : (
        <Table
          headers={['Time', 'Actor', 'Action', 'Resource', 'Request ID']}
          rows={data.results.map((item) => [
            new Date(item.created_at).toLocaleString(),
            item.actor,
            title(item.action),
            item.resource_type,
            item.request_id || '—',
          ])}
        />
      )}
    </>
  );
}

function StoreConfiguration({
  value,
  canManage,
  saved,
}: {
  value: Record<string, unknown>;
  canManage: boolean;
  saved: () => void;
}) {
  const { data: capabilities, error: capabilityError } = useData(
    '/api/console/capabilities/',
    platformCapabilities,
  );
  const [preset, setPreset] = useState(String(value.preset ?? 'general'));
  const [theme, setTheme] = useState(String(value.desired_theme ?? ''));
  const [modules, setModules] = useState<string[]>(
    Array.isArray(value.desired_modules) ? value.desired_modules.map(String) : [],
  );
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  function choosePreset(next: string, source: PlatformCapabilities) {
    setPreset(next);
    const selected = source.presets.find((item) => item.id === next);
    if (selected) setTheme(selected.default_theme);
    setModules((current) =>
      current.filter((key) => {
        const specification = source.modules.find((item) => item.key === key);
        return Boolean(specification?.available && specification.presets.includes(next));
      }),
    );
  }

  function toggleModule(key: string, checked: boolean) {
    setModules((current) =>
      checked ? [...new Set([...current, key])] : current.filter((item) => item !== key),
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.patch(`/api/console/stores/${String(value.id)}/`, {
        preset,
        desired_theme: theme,
        desired_modules: modules,
      });
      setError('');
      saved();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  const desiredModules = Array.isArray(value.desired_modules)
    ? value.desired_modules.map(String)
    : [];
  const appliedModules = Array.isArray(value.applied_modules)
    ? value.applied_modules.map(String)
    : [];
  return (
    <div className="mb-5">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-bold">Store configuration</h2>
            <p className="mt-1 text-sm text-muted">
              Desired revision {String(value.desired_revision ?? 'Unknown')} · Applied revision{' '}
              {String(value.applied_revision ?? 'Unknown')}
            </p>
          </div>
          <Status value={String(value.configuration_state ?? 'pending_application')} />
        </div>
        <dl className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <dt className="text-xs font-bold tracking-wide text-muted uppercase">Desired</dt>
            <dd className="mt-1 text-sm">
              {title(String(value.preset ?? 'unknown'))} ·{' '}
              {String(value.desired_theme ?? 'Unknown')}
              <span className="mt-1 block text-muted">
                {desiredModules.length ? desiredModules.map(title).join(', ') : 'No modules'}
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-xs font-bold tracking-wide text-muted uppercase">
              Reported applied
            </dt>
            <dd className="mt-1 text-sm">
              {String(value.applied_theme || 'Not reported')}
              <span className="mt-1 block text-muted">
                {appliedModules.length ? appliedModules.map(title).join(', ') : 'Not reported'}
              </span>
            </dd>
          </div>
        </dl>
        {capabilityError && <Alert>{capabilityError}</Alert>}
        {canManage && capabilities && (
          <form onSubmit={submit} className="mt-5 grid gap-4 border-t border-line pt-5">
            {error && <Alert>{error}</Alert>}
            <div className="grid gap-3 md:grid-cols-2">
              <label className="grid gap-1 text-sm font-semibold">
                Preset
                <select
                  className={inputClass}
                  value={preset}
                  onChange={(event) => choosePreset(event.target.value, capabilities)}
                >
                  {capabilities.presets.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-sm font-semibold">
                Theme
                <select
                  className={inputClass}
                  value={theme}
                  onChange={(event) => setTheme(event.target.value)}
                >
                  {capabilities.themes
                    .filter((item) => item.preset === preset)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name} ({item.id})
                      </option>
                    ))}
                </select>
              </label>
            </div>
            <fieldset>
              <legend className="text-sm font-bold">Desired modules</legend>
              <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {capabilities.modules
                  .filter((item) => item.available && item.presets.includes(preset))
                  .map((item) => (
                    <label
                      key={item.key}
                      className="flex gap-2 rounded-base border border-line p-3"
                    >
                      <input
                        type="checkbox"
                        checked={item.core || modules.includes(item.key)}
                        disabled={item.core}
                        onChange={(event) => toggleModule(item.key, event.target.checked)}
                      />
                      <span className="text-sm">
                        <strong>{item.name}</strong>
                        {item.requires.length > 0 && (
                          <span className="block text-xs text-muted">
                            Requires {item.requires.map(title).join(', ')}
                          </span>
                        )}
                      </span>
                    </label>
                  ))}
              </div>
            </fieldset>
            <div>
              <Button type="submit" busy={busy}>
                Save desired configuration
              </Button>
            </div>
          </form>
        )}
      </Card>
    </div>
  );
}

function DeploymentCredentials({ deploymentId }: { deploymentId: string }) {
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [token, setToken] = useState('');
  const { data, error: loadError } = useData(
    `/api/console/deployments/${deploymentId}/monitoring-credentials/`,
    monitoringCredentialPage,
    refresh,
  );
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const response = await api.post<unknown>(
        `/api/console/deployments/${deploymentId}/monitoring-credentials/`,
        { label: String(form.get('label') ?? '') },
      );
      if (!response || typeof response !== 'object' || !('token' in response))
        throw new Error('The server returned an invalid credential.');
      const raw = (response as Record<string, unknown>).token;
      if (typeof raw !== 'string') throw new Error('The server returned an invalid credential.');
      setToken(raw);
      setError('');
      event.currentTarget.reset();
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  async function revoke(id: string) {
    if (!window.confirm('Revoke this monitoring credential? Existing agents using it will stop.'))
      return;
    try {
      await api.patch(`/api/console/deployments/${deploymentId}/monitoring-credentials/${id}/`, {
        action: 'revoke',
      });
      setError('');
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <div className="mt-5">
      <h2 className="mb-3 text-lg font-bold">Monitoring credentials</h2>
      <Card>
        {(error || loadError) && <Alert>{error || loadError}</Alert>}
        {token && (
          <Alert>
            Copy this credential now. It will not be shown again.
            <code className="mt-2 block break-all">{token}</code>
          </Alert>
        )}
        <form onSubmit={create} className="mb-4 flex flex-wrap items-end gap-3">
          <label className="grid min-w-64 flex-1 gap-1 text-sm font-semibold">
            Credential label
            <input className={inputClass} name="label" defaultValue="Deployment telemetry" />
          </label>
          <Button type="submit">Issue credential</Button>
        </form>
        {!data ? (
          <Loading>Loading credentials…</Loading>
        ) : (
          <Table
            headers={['Label', 'Identifier', 'Status', 'Created', 'Last used', 'Action']}
            rows={data.results.map((item) => [
              item.label,
              `••••${item.last_four}`,
              <Status key="status" value={item.status} />,
              new Date(item.created_at).toLocaleString(),
              item.last_used_at ? new Date(item.last_used_at).toLocaleString() : 'Never',
              item.revoked_at ? (
                '—'
              ) : (
                <Button key="revoke" variant="danger" onClick={() => void revoke(item.id)}>
                  Revoke
                </Button>
              ),
            ])}
          />
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Phase 17 deployment automation

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—';
}

function ServerAgent({ serverId, canManage }: { serverId: string; canManage: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [token, setToken] = useState('');
  const { data, error: loadError } = useData(
    `/api/console/servers/${serverId}/agent/`,
    serverAgentState,
    refresh,
  );
  async function issue() {
    try {
      const response = await api.post<unknown>(`/api/console/servers/${serverId}/agent/`, {});
      const raw =
        response && typeof response === 'object'
          ? (response as Record<string, unknown>).token
          : null;
      if (typeof raw !== 'string') throw new Error('The server returned an invalid token.');
      setToken(raw);
      setError('');
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  async function revoke() {
    if (!window.confirm('Revoke this Deployment Agent? Queued and running commands will fail.'))
      return;
    try {
      await api.patch(`/api/console/servers/${serverId}/agent/`, { action: 'revoke' });
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  const agent = data?.agent ?? null;
  return (
    <div className="mt-5">
      <h2 className="mb-3 text-lg font-bold">Deployment Agent</h2>
      <Card>
        {(error || loadError) && <Alert>{error || loadError}</Alert>}
        {token && (
          <Alert>
            Single-use enrollment token (expires in 1 hour, shown once). On the Server, pipe it to
            <code className="mx-1">python3 -m matecart_agent enroll</code>
            through standard input; never put it on a command line.
            <code className="mt-2 block break-all">{token}</code>
          </Alert>
        )}
        {!data ? (
          <Loading>Loading Agent…</Loading>
        ) : agent ? (
          <dl className="grid gap-4 text-sm sm:grid-cols-2 xl:grid-cols-4">
            <div>
              <dt className="font-bold text-muted">Connection</dt>
              <dd>
                <Status value={agent.connection} />
              </dd>
            </div>
            <div>
              <dt className="font-bold text-muted">Last heartbeat</dt>
              <dd>{when(agent.last_seen_at)}</dd>
            </div>
            <div>
              <dt className="font-bold text-muted">Agent version</dt>
              <dd>{agent.agent_version || 'Not reported'}</dd>
            </div>
            <div>
              <dt className="font-bold text-muted">Credential expires</dt>
              <dd>{when(agent.credential_expires_at)}</dd>
            </div>
          </dl>
        ) : (
          <Empty>No Agent is enrolled. Commands cannot be delivered to this Server.</Empty>
        )}
        {canManage && (
          <div className="mt-4 flex flex-wrap gap-3">
            {!agent && <Button onClick={() => void issue()}>Issue enrollment token</Button>}
            {agent && (
              <Button variant="danger" onClick={() => void revoke()}>
                Revoke Agent
              </Button>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

const COMMAND_KINDS: [string, string][] = [
  ['deploy_release', 'Install release'],
  ['update_release', 'Update release'],
  ['rollback_release', 'Roll back to previous release'],
  ['apply_configuration', 'Apply store configuration'],
  ['configure_domains', 'Configure verified domains'],
  ['run_backup', 'Run backup now'],
];

function DeploymentAutomationPanel({
  deploymentId,
  canManage,
  domains,
  refreshed,
}: {
  deploymentId: string;
  canManage: boolean;
  domains: Record<string, unknown>[];
  refreshed: () => void;
}) {
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const { data, error: loadError } = useData(
    `/api/console/deployments/${deploymentId}/automation/`,
    deploymentAutomation,
    refresh,
  );
  const releases = useData('/api/console/releases/', releasePage, refresh).data;
  async function request(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const kind = String(form.get('kind') ?? '');
    const version = String(form.get('version') ?? '');
    try {
      await api.post('/api/console/operations/', {
        kind,
        title: `${title(kind)}${version ? ` ${version}` : ''}`,
        status: 'submitted',
        deployment: deploymentId,
        parameters: version ? { version } : {},
      });
      setNotice(
        'Request submitted. A different authorized administrator must approve it on Operations; ' +
          'approval does not run anything until it is moved to Pending Execution.',
      );
      setError('');
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.put(`/api/console/deployments/${deploymentId}/backup-policy/`, {
        enabled: form.get('enabled') === 'on',
        schedule_hour_utc: Number(form.get('hour') ?? 2),
        schedule_minute: Number(form.get('minute') ?? 30),
        retention_days: Number(form.get('retention') ?? 14),
        include_media: form.get('media') === 'on',
        destination_kind: 'local',
      });
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  async function verify(domainId: string) {
    try {
      const result = await api.post<unknown>(`/api/console/domains/${domainId}/verify/`, {});
      const row = result && typeof result === 'object' ? (result as Record<string, unknown>) : {};
      setNotice(
        `${String(row.verification_detail ?? '')} Create TXT ${String(row.txt_record_name ?? '')} = ${String(row.txt_record_value ?? '')}`,
      );
      refreshed();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  const policy = data?.backup_policy ?? null;
  return (
    <div className="mt-5 grid gap-5">
      <h2 className="text-lg font-bold">Deployment automation</h2>
      {(error || loadError) && <Alert>{error || loadError}</Alert>}
      {notice && <Alert>{notice}</Alert>}
      {!data ? (
        <Loading>Loading deployment automation…</Loading>
      ) : (
        <>
          <Card>
            <h3 className="mb-3 font-bold">
              Readiness: <Status value={data.readiness.ready ? 'ready' : 'not_ready'} />
            </h3>
            <ul className="grid gap-1 text-sm sm:grid-cols-2">
              {Object.entries(data.readiness.checks).map(([key, ok]) => (
                <li key={key}>
                  {ok ? '✓' : '✗'} {title(key)}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-sm text-muted">
              Agent: {data.agent ? title(data.agent.connection) : 'not enrolled'} · Rollback{' '}
              {data.rollback_eligible ? 'available to the last verified release' : 'not available'}
            </p>
          </Card>
          <Card>
            <h3 className="mb-3 font-bold">Commands</h3>
            {data.commands.length === 0 ? (
              <Empty>No deployment commands yet.</Empty>
            ) : (
              <Table
                headers={['Command', 'Status', 'Target', 'Latest step', 'Started', 'Verified']}
                rows={data.commands.map((item) => {
                  const last = item.progress[item.progress.length - 1];
                  return [
                    title(item.kind),
                    <Status key="status" value={item.status} />,
                    item.target_version || '—',
                    item.failure_category
                      ? `${title(item.failure_category)}: ${item.failure_detail}`
                      : last
                        ? `${last.stage || last.event}${last.message ? ` — ${last.message}` : ''}`
                        : 'Waiting for the Agent',
                    when(item.started_at),
                    when(item.verified_at),
                  ];
                })}
              />
            )}
          </Card>
          {canManage && (
            <Card>
              <h3 className="mb-3 font-bold">Request a deployment operation</h3>
              <form onSubmit={request} className="flex flex-wrap items-end gap-3">
                <label className="grid min-w-56 gap-1 text-sm font-semibold">
                  Operation
                  <select className={inputClass} name="kind" defaultValue="update_release">
                    {COMMAND_KINDS.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="grid min-w-48 gap-1 text-sm font-semibold">
                  Release (install, update, rollback)
                  <select className={inputClass} name="version" defaultValue="">
                    <option value="">Not applicable</option>
                    {releases?.results
                      .filter((item) => item.status === 'available')
                      .map((item) => (
                        <option key={item.id} value={item.version}>
                          {item.version}
                        </option>
                      ))}
                  </select>
                </label>
                <Button type="submit">Submit for approval</Button>
              </form>
            </Card>
          )}
          {canManage && (
            <Card>
              <h3 className="mb-3 font-bold">Backup policy (local Server storage)</h3>
              <form onSubmit={savePolicy} className="flex flex-wrap items-end gap-3 text-sm">
                <label className="flex items-center gap-2 font-semibold">
                  <input type="checkbox" name="enabled" defaultChecked={policy?.enabled ?? true} />
                  Enabled
                </label>
                <label className="grid gap-1 font-semibold">
                  Hour (UTC)
                  <input
                    className={inputClass}
                    name="hour"
                    type="number"
                    min={0}
                    max={23}
                    defaultValue={policy?.schedule_hour_utc ?? 2}
                  />
                </label>
                <label className="grid gap-1 font-semibold">
                  Minute
                  <input
                    className={inputClass}
                    name="minute"
                    type="number"
                    min={0}
                    max={59}
                    defaultValue={policy?.schedule_minute ?? 30}
                  />
                </label>
                <label className="grid gap-1 font-semibold">
                  Retention (days)
                  <input
                    className={inputClass}
                    name="retention"
                    type="number"
                    min={1}
                    max={365}
                    defaultValue={policy?.retention_days ?? 14}
                  />
                </label>
                <label className="flex items-center gap-2 font-semibold">
                  <input
                    type="checkbox"
                    name="media"
                    defaultChecked={policy?.include_media ?? true}
                  />
                  Include media
                </label>
                <Button type="submit">Save policy</Button>
              </form>
            </Card>
          )}
          {canManage && domains.length > 0 && (
            <Card>
              <h3 className="mb-3 font-bold">Domain verification</h3>
              <Table
                headers={['Domain', 'Ownership', 'DNS', 'TLS', 'Renewal', 'Action']}
                rows={domains.map((item) => [
                  String(item.domain ?? ''),
                  <Status key="v" value={String(item.verification_state ?? 'pending')} />,
                  String(item.dns_state ?? 'unknown'),
                  String(item.tls_state ?? 'unknown'),
                  String(item.renewal_state ?? 'unknown'),
                  <Button key="verify" onClick={() => void verify(String(item.id))}>
                    Check DNS
                  </Button>,
                ])}
              />
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Releases({ canManage }: { canManage: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const { data, error: loadError } = useData('/api/console/releases/', releasePage, refresh);
  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const manifest: unknown = JSON.parse(String(form.get('manifest') ?? ''));
      await api.post('/api/console/releases/', {
        manifest,
        notes: String(form.get('notes') ?? ''),
      });
      event.currentTarget.reset();
      setError('');
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(
        reason instanceof SyntaxError ? 'Paste the release.json content.' : errorMessage(reason),
      );
    }
  }
  return (
    <div className="mt-6">
      <h2 className="mb-3 text-lg font-bold">Releases</h2>
      <Card>
        {(error || loadError) && <Alert>{error || loadError}</Alert>}
        {!data ? (
          <Loading>Loading releases…</Loading>
        ) : data.results.length === 0 ? (
          <Empty>No release is registered. Publishing to GHCR needs separate owner approval.</Empty>
        ) : (
          <Table
            headers={['Version', 'Status', 'Commit', 'Registered', 'Commerce API image']}
            rows={data.results.map((item) => [
              item.version,
              <Status key="status" value={item.status} />,
              item.git_commit.slice(0, 12),
              when(item.created_at),
              <code key="image" className="text-xs break-all">
                {item.images['commerce-api'] ?? '—'}
              </code>,
            ])}
          />
        )}
        {canManage && (
          <form onSubmit={register} className="mt-4 grid gap-3">
            <label className="grid gap-1 text-sm font-semibold">
              Release manifest (release.json from the release workflow)
              <textarea className={`${inputClass} min-h-32 font-mono`} name="manifest" required />
            </label>
            <input className={inputClass} name="notes" placeholder="Notes (optional)" />
            <Button type="submit">Register release</Button>
          </form>
        )}
      </Card>
    </div>
  );
}

function Detail({
  kind,
  id,
  canManage,
  canMonitor,
  canDeploy,
}: {
  kind: 'clients' | 'stores' | 'servers' | 'deployments';
  id: string;
  canManage: boolean;
  canMonitor: boolean;
  canDeploy: boolean;
}) {
  const [value, setValue] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    void api
      .get<unknown>(`/api/console/${kind}/${id}/`)
      .then((row) => {
        if (typeof row === 'object' && row) setValue(row as Record<string, unknown>);
        else setError('The server returned an invalid record.');
      })
      .catch((reason: unknown) => setError(errorMessage(reason)));
  }, [kind, id, refresh]);
  if (error) return <Alert>{error}</Alert>;
  if (!value) return <Loading>Loading record…</Loading>;
  const heading = String(value.display_name ?? value.name ?? value.store_name ?? 'Record');
  const safeEntries = Object.entries(value).filter(
    ([key, entry]) =>
      ![
        'notes',
        'address',
        'contacts',
        'stores',
        'servers',
        'deployments',
        'alerts',
        'observations',
        'revisions',
      ].includes(key) &&
      !Array.isArray(entry) &&
      (typeof entry !== 'object' || entry === null),
  );
  const contacts = Array.isArray(value.contacts)
    ? (value.contacts as Record<string, unknown>[])
    : [];
  const stores = records(value, 'stores');
  const servers = records(value, 'servers');
  const deployments = records(value, 'deployments');
  const domains = records(value, 'domains');
  const backups = records(value, 'backups');
  const alerts = records(value, 'alerts');
  const observations = records(value, 'observations');
  const revisions = records(value, 'revisions');
  async function updateClient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.patch(`/api/console/clients/${id}/`, {
        display_name: String(form.get('display_name') ?? ''),
        status: String(form.get('status') ?? ''),
      });
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  async function addContact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api.post(`/api/console/clients/${id}/contacts/`, {
        name: String(form.get('name') ?? ''),
        role: String(form.get('role') ?? ''),
        email: String(form.get('email') ?? ''),
        phone: String(form.get('phone') ?? ''),
      });
      event.currentTarget.reset();
      setRefresh((item) => item + 1);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }
  return (
    <>
      <Heading
        title={heading}
        description={`${title(kind.slice(0, -1))} operational record and current registered state.`}
      />
      {kind === 'clients' && canManage && (
        <div className="mb-5 grid gap-4 xl:grid-cols-2">
          <Card>
            <h2 className="mb-3 font-bold">Edit client</h2>
            <form onSubmit={updateClient} className="grid gap-3">
              <input
                className={inputClass}
                name="display_name"
                defaultValue={String(value.display_name ?? '')}
                required
              />
              <select
                className={inputClass}
                name="status"
                defaultValue={String(value.status ?? 'onboarding')}
              >
                <option value="prospect">Prospect</option>
                <option value="onboarding">Onboarding</option>
                <option value="active">Active</option>
                <option value="on_hold">On hold</option>
                <option value="archived">Archived</option>
              </select>
              <Button type="submit">Save client</Button>
            </form>
          </Card>
          <Card>
            <h2 className="mb-3 font-bold">Add contact</h2>
            <form onSubmit={addContact} className="grid gap-3">
              <input className={inputClass} name="name" placeholder="Contact name" required />
              <input className={inputClass} name="role" placeholder="Role" />
              <input className={inputClass} name="email" type="email" placeholder="Email" />
              <input className={inputClass} name="phone" placeholder="Phone" />
              <Button type="submit">Add contact</Button>
            </form>
          </Card>
        </div>
      )}
      {kind === 'stores' && (
        <StoreConfiguration
          key={String(value.desired_revision ?? 'configuration')}
          value={value}
          canManage={canManage}
          saved={() => setRefresh((item) => item + 1)}
        />
      )}
      <Card>
        <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
          {safeEntries.map(([key, entry]) => (
            <div key={key}>
              <dt className="text-xs font-bold tracking-wide text-muted uppercase">{title(key)}</dt>
              <dd className="mt-1 text-sm break-words">
                {entry === null || entry === '' ? 'Unknown' : String(entry)}
              </dd>
            </div>
          ))}
        </dl>
      </Card>
      {kind === 'clients' && (
        <>
          <RelatedRecords
            heading="Contacts"
            items={contacts}
            columns={[
              ['Name', 'name'],
              ['Role', 'role'],
              ['Email', 'email'],
              ['Phone', 'phone'],
            ]}
          />
          <RelatedRecords
            heading="Stores"
            items={stores}
            columns={[
              ['Store', 'name'],
              ['Preset', 'preset'],
              ['Status', 'status'],
              ['Store ID', 'id'],
            ]}
          />
          <RelatedRecords
            heading="Assigned servers"
            items={servers}
            columns={[
              ['Server', 'name'],
              ['Ownership', 'ownership'],
              ['Provider', 'provider'],
              ['Status', 'status'],
            ]}
          />
        </>
      )}
      {['stores', 'servers'].includes(kind) && (
        <RelatedRecords
          heading="Deployments"
          items={deployments}
          columns={[
            ['Store', 'store_name'],
            ['Environment', 'environment'],
            ['Server', 'server_name'],
            ['State', 'state'],
            ['Observed version', 'observed_version'],
          ]}
        />
      )}
      {['stores', 'deployments'].includes(kind) && (
        <RelatedRecords
          heading="Domains and certificates"
          items={domains}
          columns={[
            ['Domain', 'domain'],
            ['DNS', 'dns_state'],
            ['TLS', 'tls_state'],
            ['Certificate expires', 'certificate_expires_at'],
          ]}
        />
      )}
      {kind === 'deployments' && (
        <>
          <RelatedRecords
            heading="Health observations"
            items={observations}
            columns={[
              ['Observed', 'observed_at'],
              ['Store', 'store_status'],
              ['API', 'api_status'],
              ['Response ms', 'response_ms'],
              ['Source', 'source'],
            ]}
          />
          <RelatedRecords
            heading="Backup observations"
            items={backups}
            columns={[
              ['Observed', 'observed_at'],
              ['Status', 'status'],
              ['Type', 'backup_type'],
              ['Source', 'source'],
              ['Recovery tested', 'recovery_tested_at'],
            ]}
          />
          {canMonitor && <DeploymentCredentials deploymentId={id} />}
          <DeploymentAutomationPanel
            deploymentId={id}
            canManage={canDeploy}
            domains={domains}
            refreshed={() => setRefresh((item) => item + 1)}
          />
        </>
      )}
      {kind === 'servers' && <ServerAgent serverId={id} canManage={canManage} />}
      {kind === 'stores' && (
        <RelatedRecords
          heading="Configuration revisions"
          items={revisions}
          columns={[
            ['Revision', 'revision'],
            ['Status', 'status'],
            ['Theme', 'theme'],
            ['Created', 'created_at'],
            ['Applied', 'applied_at'],
          ]}
        />
      )}
      {['stores', 'servers', 'deployments'].includes(kind) && (
        <RelatedRecords
          heading="Alerts"
          items={alerts}
          columns={[
            ['Alert', 'title'],
            ['Severity', 'severity'],
            ['Status', 'status'],
            ['Opened', 'created_at'],
          ]}
        />
      )}
    </>
  );
}

export function PlatformWorkspace({ path, permissions }: { path: string; permissions: string[] }) {
  const [root, id] = path.split('/');
  if (id && ['clients', 'stores', 'servers', 'deployments'].includes(root ?? ''))
    return (
      <Detail
        kind={root as 'clients' | 'stores' | 'servers' | 'deployments'}
        id={id}
        canManage={permissions.includes(
          `${root === 'stores' ? 'store_configuration' : root}.manage`,
        )}
        canMonitor={permissions.includes('monitoring.manage')}
        canDeploy={
          permissions.includes('deployments.manage') && permissions.includes('operations.manage')
        }
      />
    );
  if (path === 'clients') return <Clients canManage={permissions.includes('clients.manage')} />;
  if (path === 'stores') return <Stores canManage={permissions.includes('stores.manage')} />;
  if (path === 'servers') return <Servers canManage={permissions.includes('servers.manage')} />;
  if (path === 'deployments')
    return <Deployments canManage={permissions.includes('deployments.manage')} />;
  if (path === 'monitoring') return <Deployments monitoring />;
  if (path === 'alerts') return <Alerts />;
  if (path === 'operations')
    return <Operations canManage={permissions.includes('operations.manage')} />;
  if (path === 'administrators')
    return <Administrators canManage={permissions.includes('administrators.manage')} />;
  if (path === 'audit') return <AuditLog />;
  return <Overview />;
}
