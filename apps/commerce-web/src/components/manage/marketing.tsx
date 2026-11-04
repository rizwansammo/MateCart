'use client';

import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import type { Route } from 'next';
import Link from 'next/link';
import {
  marketingOverview,
  seoSettings,
  trackingSettings,
  type LiveSummary,
  type SeoItem,
  type StaffSession,
  type TrackingSettings,
} from '@matecart/shared-contracts';
import { Alert, Button, TextField, errorMessage } from '@matecart/shared-ui';
import { marketingTabs } from '@/lib/manage-routes';
import {
  Badge,
  Card,
  Cell,
  Empty,
  Feedback,
  Loader,
  PageHeader,
  SelectField,
  Stat,
  Table,
  api,
  useLoad,
} from './kit';
import {
  CampaignEditor,
  CampaignList,
  CouponManager,
  FlashSaleEditor,
  FlashSaleList,
} from './marketing-promotions';
import { AnalyticsReportView, LoyaltyManager } from './marketing-insights';

/** /manage/marketing/* — one workspace, tabs filtered by the role's permissions (D-079). */
export function MarketingWorkspace({ path, session }: { path: string; session: StaffSession }) {
  const [, , section = '', id = ''] = ['', ...path.split('/')];
  const timeZone = session.store_timezone || 'UTC';
  let body: ReactNode;
  switch (section) {
    case 'campaigns':
      body = id ? (
        <CampaignEditor id={id} timeZone={timeZone} />
      ) : (
        <CampaignList timeZone={timeZone} />
      );
      break;
    case 'flash-sales':
      body = id ? (
        <FlashSaleEditor id={id} timeZone={timeZone} />
      ) : (
        <FlashSaleList timeZone={timeZone} />
      );
      break;
    case 'coupons':
      body = <CouponManager timeZone={timeZone} />;
      break;
    case 'loyalty':
      body = <LoyaltyManager session={session} />;
      break;
    case 'seo':
      body = <SeoManager />;
      break;
    case 'analytics':
      body = <AnalyticsReportView timeZone={timeZone} />;
      break;
    case 'tracking':
      body = <TrackingManager />;
      break;
    default:
      body = <MarketingOverview session={session} />;
  }
  return (
    <div>
      <MarketingTabs path={path} permissions={session.permissions} />
      {body}
    </div>
  );
}

function MarketingTabs({ path, permissions }: { path: string; permissions: readonly string[] }) {
  const tabs = marketingTabs(permissions);
  const current = `/manage/${path}`;
  return (
    <nav aria-label="Marketing" className="mb-6 overflow-x-auto border-b border-line">
      <ul className="flex min-w-max gap-1">
        {tabs.map((tab) => {
          const active =
            tab.href === '/manage/marketing'
              ? current === tab.href
              : current === tab.href || current.startsWith(`${tab.href}/`);
          return (
            <li key={tab.href}>
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
  );
}

// --- Overview -------------------------------------------------------------------------------------

function count(value: number | null): string {
  return value === null ? '—' : String(value);
}

export function MarketingOverview({ session }: { session: StaffSession }) {
  const load = useCallback(
    async () => marketingOverview(await api.get<unknown>('/api/manage/marketing/')),
    [],
  );
  const { data, error, loading } = useLoad(load);
  const can = (permission: string) => session.permissions.includes(permission);
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Marketing"
        title="Marketing overview"
        intro="Promotions, rewards, search and visitor tracking for your store. Every figure comes from your store's own records."
      />
      <Feedback error={error} />
      {loading && !data && <Loader>Loading marketing…</Loader>}
      {data && (
        <>
          {can('promotions.manage') && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat
                label="Live campaigns"
                value={count(data.live_campaigns)}
                hint={`${count(data.scheduled_campaigns)} scheduled`}
                href="/manage/marketing/campaigns"
              />
              <Stat
                label="Live flash sales"
                value={count(data.live_flash_sales)}
                hint={
                  data.flash_sales_module
                    ? `${count(data.scheduled_flash_sales)} scheduled`
                    : 'Flash sales module is off'
                }
                href="/manage/marketing/flash-sales"
              />
              <Stat
                label="Active coupons"
                value={count(data.active_coupons)}
                href="/manage/marketing/coupons"
              />
              <Stat
                label="Loyalty members"
                value={count(data.loyalty_members)}
                hint={
                  !data.loyalty_module
                    ? 'Loyalty module is off'
                    : data.loyalty_enabled
                      ? 'Program on'
                      : 'Program off'
                }
                href="/manage/marketing/loyalty"
              />
            </div>
          )}
          {data.live && <LiveVisitors live={data.live} />}
          {data.tracking && (
            <Card title="Tracking">
              <dl className="grid gap-3 text-sm sm:grid-cols-3">
                <div>
                  <dt className="text-xs font-semibold text-muted">First-party analytics</dt>
                  <dd className="mt-1">{data.analytics_enabled ? 'On' : 'Off'}</dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold text-muted">Meta Pixel (browser)</dt>
                  <dd className="mt-1">
                    {data.tracking.browser_pixel === 'active'
                      ? 'On for visitors who accept marketing'
                      : 'Off'}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold text-muted">Conversions API (server)</dt>
                  <dd className="mt-1">{SERVER_STATE[data.tracking.server_events]}</dd>
                </div>
              </dl>
            </Card>
          )}
          {data.seo_indexing_preference !== null && (
            <Card title="Search engines">
              <p className="text-sm text-muted">
                Your preference:{' '}
                <strong className="text-ink">
                  {data.seo_indexing_preference ? 'allow indexing' : 'keep the store out of search'}
                </strong>
                . Indexing also needs the store’s public address, set up by NetaMate for live
                stores.{' '}
                <Link href="/manage/marketing/seo" className="font-semibold underline">
                  SEO settings
                </Link>
              </p>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

const SERVER_STATE: Record<TrackingSettings['status']['server_events'], string> = {
  active: 'Sending purchases',
  missing_credentials: 'Waiting for server credentials',
  off: 'Off',
};

function percent(value: string | null): string {
  return value === null ? '—' : `${value}%`;
}

/** The rolling 24-hour figures with their exact definitions (D-083). */
export function LiveVisitors({ live }: { live: LiveSummary }) {
  return (
    <Card title="Visitors (last 24 hours)">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Unique visitors"
          value={live.unique_visitors}
          hint="Visitors who allowed analytics, seen in exactly the last 24 hours"
        />
        <Stat
          label="Page views"
          value={live.page_views}
          hint={`All visitors · ${percent(live.consent_coverage)} with analytics consent`}
        />
        <Stat label="Product views" value={live.product_views} />
        <Stat label="Added to cart" value={live.add_to_cart} />
        <Stat label="Checkouts started" value={live.checkout_started} />
        <Stat
          label="Online store orders"
          value={live.storefront_orders}
          hint="Placed orders from the order records"
        />
        <Stat
          label="Visitor conversion"
          value={percent(live.conversion_rate)}
          hint={`${live.converting_visitors} of ${live.unique_visitors} consented visitors ordered`}
        />
      </div>
      <p className="mt-4 text-xs leading-5 text-muted">
        Unique visitors are not people online right now. Page, product, cart and checkout counts
        cover every visitor without identifying anyone, for the current hour and the 23 before it.
        Visitors who decline analytics are counted in page views but never as unique visitors, so
        the visitor figure is a lower bound. Sales and payments are reported from orders, not from
        browser events.
      </p>
    </Card>
  );
}

// --- SEO ------------------------------------------------------------------------------------------

const SEO_KIND: Record<SeoItem['kind'], string> = {
  category: 'Category',
  collection: 'Collection',
  page: 'Page',
};

function SeoManager() {
  const load = useCallback(
    async () => seoSettings(await api.get<unknown>('/api/manage/marketing/seo/')),
    [],
  );
  const { data, setData, error, loading } = useLoad(load);
  const [form, setForm] = useState<{ title: string; description: string } | null>(null);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const values =
    form ?? (data ? { title: data.home_title, description: data.home_description } : null);

  async function save(body: Record<string, unknown>, message: string) {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      setData(seoSettings(await api.patch<unknown>('/api/manage/marketing/seo/', body)));
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
        title="Search and sharing"
        intro="What search engines and social networks show for your store. Plain text only; leave a field empty to use your product, category or page text."
      />
      <Feedback error={error || failure} notice={notice} />
      {loading && !data && <Loader>Loading SEO settings…</Loader>}
      {data && values && (
        <>
          <Card title="Store defaults">
            <form
              className="grid gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void save(
                  { home_title: values.title, home_description: values.description },
                  'Search defaults saved.',
                );
              }}
            >
              <TextField
                label="Homepage title"
                maxLength={70}
                value={values.title}
                hint={`${values.title.length}/70 · empty uses the store name`}
                onChange={(e) => setForm({ ...values, title: e.target.value })}
              />
              <TextArea
                label="Homepage description"
                maxLength={320}
                value={values.description}
                hint={`${values.description.length}/320 · empty uses the store description`}
                onChange={(description) => setForm({ ...values, description })}
              />
              <div>
                <Button type="submit" busy={busy}>
                  Save defaults
                </Button>
              </div>
            </form>
          </Card>
          <Card title="Search engine indexing">
            <div className="grid gap-3 text-sm leading-6">
              <p>
                Current preference:{' '}
                <Badge tone={data.allow_indexing ? 'success' : 'neutral'}>
                  {data.allow_indexing ? 'Allow indexing' : 'Keep out of search'}
                </Badge>
              </p>
              <p className="text-muted">
                Search engines can list the store only when all three hold: this preference allows
                it, the store has its verified public HTTPS address, and NetaMate has switched
                indexing on for the deployment. Development and preview stores always stay out of
                search. Accounts, carts, checkout, orders and this dashboard are never listed.
              </p>
              {data.can_change_indexing ? (
                <div>
                  <Button
                    variant="secondary"
                    busy={busy}
                    onClick={() =>
                      void save(
                        { allow_indexing: !data.allow_indexing },
                        data.allow_indexing
                          ? 'The store will ask search engines not to index it.'
                          : 'Indexing is allowed where the deployment permits it.',
                      )
                    }
                  >
                    {data.allow_indexing ? 'Keep the store out of search' : 'Allow indexing'}
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-muted">
                  Only staff who can change store settings can change this preference.
                </p>
              )}
            </div>
          </Card>
          <Card title="Products">
            <p className="text-sm leading-6 text-muted">
              {data.products_missing_seo} of {data.active_products} published products use their own
              name and summary for search (no custom title or description). Edit a product’s search
              title and description in its{' '}
              <Link href="/manage/products" className="font-semibold text-ink underline">
                product editor
              </Link>
              .
            </p>
          </Card>
          <Card title="Categories, collections and pages" flush>
            {data.items.length === 0 ? (
              <Empty title="Nothing to describe yet">
                Categories, collections and pages appear here once you create them.
              </Empty>
            ) : (
              <ul className="divide-y divide-line">
                {data.items.map((item) => (
                  <SeoRow key={`${item.kind}-${item.id}`} item={item} onSaved={setNotice} />
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function TextArea({
  label,
  value,
  onChange,
  hint,
  maxLength,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  maxLength?: number;
}) {
  const id = `ta-${label.replaceAll(' ', '-').toLowerCase()}`;
  return (
    <div className="grid gap-1.5">
      <label className="text-[13px] font-semibold" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        value={value}
        maxLength={maxLength}
        rows={3}
        onChange={(e) => onChange(e.target.value)}
        className="w-full min-w-0 rounded-base border border-line-strong bg-surface px-3 py-2 text-[15px] outline-none focus:border-brand-deep focus:ring-3 focus:ring-brand-tint"
      />
      {hint && <p className="text-xs leading-5 text-muted">{hint}</p>}
    </div>
  );
}

function SeoRow({ item, onSaved }: { item: SeoItem; onSaved: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(item.seo_title);
  const [description, setDescription] = useState(item.seo_description);
  const [saved, setSaved] = useState(item);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <li className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">
            {item.name} <span className="font-normal text-muted">/{item.slug}</span>
          </p>
          <p className="mt-1 flex flex-wrap gap-2 text-xs text-muted">
            <Badge>{SEO_KIND[item.kind]}</Badge>
            {!item.public && <Badge tone="warning">Not public</Badge>}
            {saved.seo_title || saved.seo_description ? 'Custom search text' : 'Uses its own text'}
          </p>
        </div>
        <Button variant="secondary" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Close' : 'Edit search text'}
        </Button>
      </div>
      {open && (
        <form
          className="mt-4 grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            void (async () => {
              try {
                await api.patch(`/api/manage/marketing/seo/${item.kind}/${item.id}/`, {
                  seo_title: title,
                  seo_description: description,
                });
                setSaved({ ...saved, seo_title: title, seo_description: description });
                setOpen(false);
                onSaved(`Search text for “${item.name}” saved.`);
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            })();
          }}
        >
          <TextField
            label="Search title"
            maxLength={70}
            value={title}
            hint={`${title.length}/70`}
            onChange={(e) => setTitle(e.target.value)}
          />
          <TextArea
            label={`Search description (${item.slug})`}
            maxLength={320}
            value={description}
            hint={`${description.length}/320`}
            onChange={setDescription}
          />
          {error && <Alert>{error}</Alert>}
          <div>
            <Button type="submit" busy={busy}>
              Save
            </Button>
          </div>
        </form>
      )}
    </li>
  );
}

// --- Tracking and consent -------------------------------------------------------------------------

function Toggle({
  label,
  checked,
  onChange,
  hint,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-start gap-3 text-sm">
      <input
        type="checkbox"
        className="mt-1 h-4 w-4"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        <span className="font-semibold">{label}</span>
        {hint && <span className="mt-0.5 block text-xs leading-5 text-muted">{hint}</span>}
      </span>
    </label>
  );
}

type TrackingForm = Omit<
  TrackingSettings,
  'status' | 'consent_records_30d' | 'consent_marketing_30d' | 'consent_analytics_30d'
>;

function TrackingManager() {
  const load = useCallback(
    async () => trackingSettings(await api.get<unknown>('/api/manage/marketing/tracking/')),
    [],
  );
  const { data, setData, error, loading } = useLoad(load);
  const [form, setForm] = useState<TrackingForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const values: TrackingForm | null = form ?? data;
  const update = (patch: Partial<TrackingForm>) => values && setForm({ ...values, ...patch });

  async function save() {
    if (!values) return;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const body: TrackingForm = {
        analytics_enabled: values.analytics_enabled,
        meta_enabled: values.meta_enabled,
        meta_pixel_id: values.meta_pixel_id.trim(),
        meta_capi_enabled: values.meta_capi_enabled,
        meta_send_hashed_contact: values.meta_send_hashed_contact,
        meta_purchase_timing: values.meta_purchase_timing,
        meta_test_event_code: values.meta_test_event_code.trim(),
      };
      setData(trackingSettings(await api.patch<unknown>('/api/manage/marketing/tracking/', body)));
      setForm(null);
      setNotice('Tracking settings saved.');
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
        title="Tracking and consent"
        intro="Visitors choose what they allow in the cookie banner. Optional tracking runs only for visitors who agreed; sign-in, cart and checkout always work without it."
      />
      <Feedback error={error || failure} notice={notice} />
      {loading && !data && <Loader>Loading tracking settings…</Loader>}
      {data && values && (
        <form
          className="grid gap-6"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <Card title="Store analytics">
            <Toggle
              label="Count visits in MateCart analytics"
              checked={values.analytics_enabled}
              onChange={(analytics_enabled) => update({ analytics_enabled })}
              hint="Page views are counted for everyone without identifying them. Unique visitors and campaign sources are recorded only for visitors who allow analytics. Data stays in your store."
            />
          </Card>
          <Card title="Meta Pixel and Conversions API">
            <div className="grid gap-4">
              <Toggle
                label="Use Meta tracking"
                checked={values.meta_enabled}
                onChange={(meta_enabled) =>
                  update({
                    meta_enabled,
                    meta_capi_enabled: meta_enabled ? values.meta_capi_enabled : false,
                  })
                }
                hint="The Pixel loads only for visitors who allow marketing in the cookie banner."
              />
              <TextField
                label="Pixel ID"
                inputMode="numeric"
                maxLength={20}
                value={values.meta_pixel_id}
                hint="5–20 digits, from Meta Events Manager."
                onChange={(e) => update({ meta_pixel_id: e.target.value })}
              />
              <Toggle
                label="Also send purchases from the server (Conversions API)"
                checked={values.meta_capi_enabled}
                disabled={!values.meta_enabled}
                onChange={(meta_capi_enabled) => update({ meta_capi_enabled })}
                hint={
                  data.status.token_configured && data.status.public_url_configured
                    ? 'Server events use the same event id as the Pixel, so Meta counts each purchase once.'
                    : 'Not available yet: this deployment has no Conversions API token or public HTTPS address. NetaMate configures them for live stores; the token is never shown here.'
                }
              />
              <SelectField
                label="When to report a purchase"
                value={values.meta_purchase_timing}
                onChange={(e) =>
                  update({
                    meta_purchase_timing:
                      e.target.value === 'order_placed' ? 'order_placed' : 'payment_verified',
                  })
                }
                options={[
                  {
                    value: 'payment_verified',
                    label: 'When payment is verified or cash is collected (recommended)',
                  },
                  { value: 'order_placed', label: 'When the order is placed' },
                ]}
                hint="“Order placed” reports cash-on-delivery orders before any money arrives; they are sent as unpaid, never as paid."
              />
              <Toggle
                label="Send hashed email and phone for matching"
                checked={values.meta_send_hashed_contact}
                disabled={!values.meta_capi_enabled}
                onChange={(meta_send_hashed_contact) => update({ meta_send_hashed_contact })}
                hint="Only for visitors who allowed marketing. Contact details are hashed (SHA-256) when sent and never stored for Meta. Make sure your privacy policy says so before turning this on."
              />
              <TextField
                label="Test event code (optional)"
                maxLength={40}
                value={values.meta_test_event_code}
                hint="Shows server events in Meta's Test Events tab while you check the setup."
                onChange={(e) => update({ meta_test_event_code: e.target.value })}
              />
              <dl className="grid gap-3 rounded-base bg-canvas p-4 text-sm sm:grid-cols-3">
                <div>
                  <dt className="text-xs font-semibold text-muted">Browser Pixel</dt>
                  <dd className="mt-1">{data.status.browser_pixel === 'active' ? 'On' : 'Off'}</dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold text-muted">Server events</dt>
                  <dd className="mt-1">{SERVER_STATE[data.status.server_events]}</dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold text-muted">Sent · waiting · failed</dt>
                  <dd className="mt-1 tabular-nums">
                    {data.status.sent_events} · {data.status.pending_events} ·{' '}
                    {data.status.failed_events}
                  </dd>
                </div>
              </dl>
            </div>
          </Card>
          <Card title="Consent decisions (last 30 days)">
            {data.consent_records_30d === 0 ? (
              <p className="text-sm text-muted">
                No visitor has made a choice in the last 30 days.
              </p>
            ) : (
              <Table head={['Decisions', 'Allowed analytics', 'Allowed marketing']}>
                <tr>
                  <Cell>{data.consent_records_30d}</Cell>
                  <Cell>{data.consent_analytics_30d}</Cell>
                  <Cell>{data.consent_marketing_30d}</Cell>
                </tr>
              </Table>
            )}
            <p className="mt-3 text-xs leading-5 text-muted">
              Each decision is kept for two years as proof, without IP address or browser details. A
              banner alone does not make a store compliant everywhere; check your local privacy
              rules and policy text.
            </p>
          </Card>
          <div>
            <Button type="submit" busy={busy} disabled={!form}>
              Save tracking settings
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
