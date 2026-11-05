'use client';

/**
 * /manage/ai — the AI workspace (Phase 15): overview, product content, image analysis, marketing
 * copy, business insights, usage and settings. Support suggestions live in the unified inbox.
 * Every tab and action is limited to what the role may do; the API enforces the same rules.
 */
import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import type { Route } from 'next';
import Link from 'next/link';
import {
  MARKETING_KINDS,
  aiOverview,
  aiRequestPage,
  aiSettings,
  aiUsage,
  campaignPage,
  managePage,
  managedProduct,
  managedProductSummary,
  type AIFeature,
  type AIFeatureState,
  type AIModel,
  type AISettings,
  type AIUsage,
  type InsightsResult,
  type MarketingKind,
  type StaffSession,
} from '@matecart/shared-contracts';
import {
  Alert,
  ApiError,
  Button,
  ConfirmDialog,
  PasswordField,
  TextField,
  errorMessage,
} from '@matecart/shared-ui';
import { TextArea } from '../appearance-app';
import {
  AI_BASE,
  FEATURE_LABEL,
  ImageAnalysisView,
  ProductAssistant,
  productTextFields,
  REQUEST_STATUS,
  REVIEW_STATUS,
  RequestState,
  TestOutputBadge,
  Warnings,
  useAIRequest,
} from './ai-assist';
import {
  Badge,
  Card,
  Cell,
  Detail,
  Empty,
  Feedback,
  Loader,
  PageHeader,
  ReauthDialog,
  SelectField,
  Stat,
  Table,
  api,
  dateTime,
  query,
  useLoad,
} from './kit';

const STATUS_TEXT: Record<
  string,
  { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' | 'info' }
> = {
  not_configured: { label: 'Not configured', tone: 'neutral' },
  unverified: { label: 'Configured, not verified', tone: 'warning' },
  connected: { label: 'Connected', tone: 'success' },
  invalid_credentials: { label: 'Invalid credentials', tone: 'danger' },
  rate_limited: { label: 'Rate limited', tone: 'warning' },
  unavailable: { label: 'Provider unavailable', tone: 'warning' },
  unsupported_model: { label: 'Model unavailable', tone: 'danger' },
  disabled: { label: 'Switched off', tone: 'neutral' },
};
const PROVIDER_LABEL: Record<string, string> = { groq: 'GroqCloud', test: 'Test provider (no AI)' };
const KIND_LABEL: Record<MarketingKind, string> = {
  campaign_title: 'Campaign name',
  campaign_description: 'Campaign description',
  homepage_headline: 'Homepage headline',
  email_subject: 'Email subject lines',
  email_body: 'Promotional email',
  sms: 'SMS message',
  social_caption: 'Social media caption',
  product_promo: 'Product promotion text',
};

export function aiTabs(permissions: readonly string[]): { href: string; label: string }[] {
  const can = (p: string) => permissions.includes(p);
  return [
    { href: '/manage/ai', label: 'Overview', show: true },
    { href: '/manage/ai/products', label: 'Product content', show: can('catalog.manage') },
    { href: '/manage/ai/images', label: 'Image analysis', show: can('catalog.manage') },
    { href: '/manage/ai/marketing', label: 'Marketing', show: can('promotions.manage') },
    { href: '/manage/ai/insights', label: 'Business insights', show: can('reports.view') },
    { href: '/manage/ai/usage', label: 'Usage', show: can('store.settings') },
    { href: '/manage/ai/settings', label: 'Settings', show: can('store.settings') },
  ]
    .filter((tab) => tab.show)
    .map(({ href, label }) => ({ href, label }));
}

export function AIWorkspace({ path, session }: { path: string; session: StaffSession }) {
  const [, section = ''] = path.split('/');
  const can = (p: string) => session.permissions.includes(p);
  let body: ReactNode;
  switch (section) {
    case 'products':
      body = can('catalog.manage') ? <ProductTab /> : <NotAllowed />;
      break;
    case 'images':
      body = can('catalog.manage') ? <ImageTab /> : <NotAllowed />;
      break;
    case 'marketing':
      body = can('promotions.manage') ? <MarketingTab /> : <NotAllowed />;
      break;
    case 'insights':
      body = can('reports.view') ? <InsightsTab /> : <NotAllowed />;
      break;
    case 'usage':
      body = can('store.settings') ? <UsageTab /> : <NotAllowed />;
      break;
    case 'settings':
      body = can('store.settings') ? <SettingsTab /> : <NotAllowed />;
      break;
    default:
      body = <OverviewTab session={session} />;
  }
  const current = `/manage/${path}`;
  return (
    <div>
      <PageHeader
        eyebrow="AI assistant"
        title="AI assistant"
        intro="Drafts for products, photos, support replies, marketing and insights. You review everything; nothing is saved, published or sent until you choose to."
      />
      <nav aria-label="AI assistant" className="mb-6 overflow-x-auto border-b border-line">
        <ul className="flex min-w-max gap-1">
          {aiTabs(session.permissions).map((tab) => {
            const active =
              tab.href === '/manage/ai' ? current === tab.href : current.startsWith(tab.href);
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
      {body}
    </div>
  );
}

function NotAllowed() {
  return (
    <Card>
      <Empty title="Not available for your role">Ask the store owner if you need this.</Empty>
    </Card>
  );
}

function FeatureGate({ feature, children }: { feature: AIFeature; children: ReactNode }) {
  const load = useCallback(async () => aiOverview(await api.get<unknown>(`${AI_BASE}/`)), []);
  const { data, error, loading } = useLoad(load);
  if (loading && !data) return <Loader />;
  if (error) return <Alert>{error}</Alert>;
  const state = data?.features.find((f) => f.key === feature);
  return (
    <div className="grid gap-4">
      {state && !state.available && (
        <Alert tone="info">
          {state.problem_text || 'This feature is not available yet.'}{' '}
          {data?.can_manage_settings && (
            <Link href={'/manage/ai/settings' as Route} className="font-semibold underline">
              Open AI settings
            </Link>
          )}
        </Alert>
      )}
      {data?.is_test && (
        <Alert tone="info">
          The test provider is selected: outputs are fixed test texts labelled “[Test]”, not AI.
        </Alert>
      )}
      {children}
    </div>
  );
}

// --- Overview -------------------------------------------------------------------------------------

function OverviewTab({ session }: { session: StaffSession }) {
  const load = useCallback(async () => aiOverview(await api.get<unknown>(`${AI_BASE}/`)), []);
  const { data, error, loading } = useLoad(load);
  if (loading && !data) return <Loader />;
  if (error || !data) return <Alert>{error || 'Could not load the AI assistant.'}</Alert>;
  const status = STATUS_TEXT[data.status] ?? { label: data.status, tone: 'neutral' as const };
  return (
    <div className="grid gap-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Status" value={<Badge tone={status.tone}>{status.label}</Badge>} />
        <Stat
          label="Provider"
          value={<span className="text-base">{PROVIDER_LABEL[data.provider]}</span>}
        />
        <Stat
          label="Text model"
          value={<span className="text-base break-all">{data.text_model || 'None chosen'}</span>}
        />
        <Stat
          label="Image model"
          value={<span className="text-base break-all">{data.vision_model || 'None chosen'}</span>}
        />
      </div>
      {!data.module_enabled && (
        <Alert tone="info">
          The AI assistant is switched off. A settings manager can switch it on in{' '}
          <Link href={'/manage/appearance' as Route} className="font-semibold underline">
            store features
          </Link>
          .
        </Alert>
      )}
      <Card title="Your features" flush>
        <Table head={['Feature', 'State']}>
          {data.features.map((feature) => (
            <FeatureRow key={feature.key} feature={feature} session={session} />
          ))}
        </Table>
        {data.features.length === 0 && (
          <Empty title="No AI features for your role">Your role does not use AI features.</Empty>
        )}
      </Card>
      {data.usage && <UsageSummary usage={data.usage} />}
      <Card title="Your recent requests" flush>
        {data.recent.length ? (
          <Table head={['Feature', 'Status', 'Review', 'When']}>
            {data.recent.map((row) => (
              <tr key={row.id}>
                <Cell>
                  {FEATURE_LABEL[row.feature]} {row.is_test && <Badge tone="warning">Test</Badge>}
                </Cell>
                <Cell>
                  <Badge
                    tone={
                      row.status === 'failed'
                        ? 'danger'
                        : row.status === 'completed'
                          ? 'success'
                          : 'neutral'
                    }
                  >
                    {REQUEST_STATUS[row.status]}
                  </Badge>
                  {row.error_text && (
                    <span className="mt-1 block text-xs text-muted">{row.error_text}</span>
                  )}
                </Cell>
                <Cell>{REVIEW_STATUS[row.review_status] || '—'}</Cell>
                <Cell>{dateTime(row.created_at)}</Cell>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty title="No requests yet" />
        )}
      </Card>
      <Card title="How AI works in MateCart">
        <ul className="grid gap-2 text-sm leading-6 text-muted">
          <li>
            • You approve everything. Drafts are labelled; nothing is published, sent or changed
            until you press the button.
          </li>
          <li>
            • Drafts use your own store records. Prices, stock, payments and order states always
            come from MateCart, never from AI.
          </li>
          <li>• Store data and customer messages are treated as information, not instructions.</li>
          <li>• {data.provider_data_notice}</li>
        </ul>
      </Card>
    </div>
  );
}

function FeatureRow({ feature, session }: { feature: AIFeatureState; session: StaffSession }) {
  const links: Partial<Record<AIFeature, string>> = {
    product_content: '/manage/ai/products',
    image_analysis: '/manage/ai/images',
    marketing_copy: '/manage/ai/marketing',
    business_insights: '/manage/ai/insights',
    support_reply: '/manage/inbox',
    support_summary: '/manage/inbox',
  };
  const href = links[feature.key];
  const permitted = session.permissions.includes(feature.permission);
  return (
    <tr>
      <Cell>
        {href && permitted ? (
          <Link href={href as Route} className="font-semibold hover:underline">
            {feature.name}
          </Link>
        ) : (
          feature.name
        )}
        {feature.uses_customer_data && (
          <span className="mt-1 block text-xs text-muted">
            Uses short, masked conversation excerpts
          </span>
        )}
      </Cell>
      <Cell>
        {feature.available ? (
          <Badge tone="success">Ready</Badge>
        ) : (
          <span className="grid gap-1">
            <Badge tone="neutral">Unavailable</Badge>
            <span className="text-xs text-muted">{feature.problem_text}</span>
          </span>
        )}
      </Cell>
    </tr>
  );
}

// --- Product content and images -------------------------------------------------------------------

function ProductPicker({
  onPick,
  label = 'Product',
}: {
  onPick: (id: string) => void;
  label?: string;
}) {
  const [text, setText] = useState('');
  const [search, setSearch] = useState('');
  const load = useCallback(
    async () =>
      managePage(managedProductSummary)(
        await api.get<unknown>(
          `/api/manage/catalog/products/${query({ q: search, page_size: 20, ordering: '-updated_at' })}`,
        ),
      ),
    [search],
  );
  const { data, error, loading } = useLoad(load);
  return (
    <div className="grid gap-3">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(text.trim());
        }}
      >
        <div className="min-w-[14rem] flex-1">
          <TextField
            label={`Find a ${label.toLowerCase()}`}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <Button variant="secondary" type="submit">
          Search
        </Button>
      </form>
      {error && <Alert>{error}</Alert>}
      {loading && !data ? (
        <Loader />
      ) : data && data.results.length ? (
        <ul className="grid gap-1">
          {data.results.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                onClick={() => onPick(row.id)}
                className="w-full rounded-base border border-line px-3 py-2 text-left text-sm hover:border-ink"
              >
                <span className="font-semibold">{row.name}</span>{' '}
                <span className="text-xs text-muted">{row.status}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <Empty title="No products found" />
      )}
    </div>
  );
}

function useProduct(id: string) {
  const load = useCallback(
    async () =>
      id ? managedProduct(await api.get<unknown>(`/api/manage/catalog/products/${id}/`)) : null,
    [id],
  );
  return useLoad(load);
}

function ProductTab() {
  const [id, setId] = useState('');
  const { data: product, error, loading, reload } = useProduct(id);
  return (
    <FeatureGate feature="product_content">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <Card title="Choose a product">
          <ProductPicker onPick={setId} />
        </Card>
        <Card
          title={product ? product.name : 'Draft'}
          actions={
            product && (
              <Link
                href={`/manage/products/${product.id}` as Route}
                className="text-sm font-semibold underline"
              >
                Open product
              </Link>
            )
          }
        >
          {!id ? (
            <Empty title="Pick a product">
              Drafts use the product’s real details and attributes.
            </Empty>
          ) : loading && !product ? (
            <Loader />
          ) : error || !product ? (
            <Alert>{error || 'Product not found.'}</Alert>
          ) : (
            <ProductAssistant
              key={product.id}
              product={productTextFields(product)}
              canBangla
              onApplied={reload}
            />
          )}
        </Card>
      </div>
    </FeatureGate>
  );
}

function ImageTab() {
  const [id, setId] = useState('');
  const [asset, setAsset] = useState('');
  const { data: product, error, loading, reload } = useProduct(id);
  const ai = useAIRequest();
  const media = product?.media.find((m) => m.asset === asset);
  const result = ai.request?.result?.feature === 'image_analysis' ? ai.request.result.data : null;
  return (
    <FeatureGate feature="image_analysis">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <Card title="Choose a product photo">
          <ProductPicker
            onPick={(value) => {
              setId(value);
              setAsset('');
              ai.reset();
            }}
          />
          {product && (
            <div className="mt-4 grid grid-cols-3 gap-2">
              {product.media.length === 0 && (
                <p className="col-span-3 text-sm text-muted">This product has no photos.</p>
              )}
              {product.media.map((m) => (
                <button
                  key={m.asset}
                  type="button"
                  aria-pressed={asset === m.asset}
                  onClick={() => {
                    setAsset(m.asset);
                    ai.reset();
                  }}
                  className={`overflow-hidden rounded-base border-2 ${asset === m.asset ? 'border-ink' : 'border-line'}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- private, authorised media URL */}
                  <img
                    src={m.url}
                    alt={m.alt_text || 'Product photo'}
                    className="aspect-square w-full object-cover"
                  />
                </button>
              ))}
            </div>
          )}
          {error && <Alert>{error}</Alert>}
          {loading && id && !product && <Loader />}
        </Card>
        <Card title="Analysis">
          {!media || !product ? (
            <Empty title="Pick a photo">
              The photo is checked by an image-capable model. MateCart never sends a public link.
            </Empty>
          ) : (
            <div className="grid gap-4">
              <Button
                type="button"
                busy={ai.busy || ai.running}
                onClick={() =>
                  void ai.generate('image_analysis', media.asset, { product: product.id })
                }
              >
                {ai.request ? 'Analyse again' : 'Analyse photo'}
              </Button>
              {ai.error && <Alert>{ai.error}</Alert>}
              <RequestState request={ai.request} onCancel={() => void ai.cancel()} />
              {result && ai.request && (
                <ImageAnalysisView
                  key={ai.request.id}
                  result={result}
                  request={ai.request}
                  currentAlt={media.alt_text}
                  onApplyAlt={async (text, overwrite) => {
                    await ai.apply({ alt_text: text }, overwrite);
                    await reload();
                  }}
                />
              )}
            </div>
          )}
        </Card>
      </div>
    </FeatureGate>
  );
}

// --- Marketing ------------------------------------------------------------------------------------

function MarketingTab() {
  const ai = useAIRequest();
  const [kind, setKind] = useState<MarketingKind>('social_caption');
  const [language, setLanguage] = useState('en');
  const [tone, setTone] = useState('friendly');
  const [campaign, setCampaign] = useState('');
  const [products, setProducts] = useState<{ id: string; name: string }[]>([]);
  const [brief, setBrief] = useState('');
  const [notice, setNotice] = useState('');
  const [applying, setApplying] = useState<{ value: string; overwrite: boolean } | null>(null);
  const loadCampaigns = useCallback(
    async () =>
      campaignPage(await api.get<unknown>('/api/manage/marketing/campaigns/?page_size=50')),
    [],
  );
  const campaigns = useLoad(loadCampaigns);
  const result = ai.request?.result?.feature === 'marketing_copy' ? ai.request.result.data : null;
  const applyField =
    kind === 'campaign_title' ? 'name' : kind === 'campaign_description' ? 'description' : '';
  const requestKind = typeof ai.request?.options.kind === 'string' ? ai.request.options.kind : kind;
  const requestCampaign =
    typeof ai.request?.options.campaign === 'string' ? ai.request.options.campaign : '';
  const canApply =
    (requestKind === 'campaign_title' || requestKind === 'campaign_description') &&
    Boolean(requestCampaign);

  async function apply(value: string, overwrite: boolean) {
    const field = requestKind === 'campaign_title' ? 'name' : 'description';
    try {
      await ai.apply({ [field]: value }, overwrite);
      setApplying(null);
      setNotice('Saved to the campaign. Its status and discount did not change.');
    } catch (e) {
      if (e instanceof ApiError && e.code === 'ai_overwrite_needs_confirmation')
        setApplying({ value, overwrite: true });
      else ai.setError(errorMessage(e));
    }
  }

  return (
    <FeatureGate feature="marketing_copy">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Card title="What to write">
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              setNotice('');
              void ai.generate('marketing_copy', null, {
                kind,
                language,
                tone,
                campaign: campaign || null,
                products: products.map((p) => p.id),
                brief,
              });
            }}
          >
            <SelectField
              label="Type"
              value={kind}
              onChange={(e) => setKind(e.target.value as MarketingKind)}
              options={MARKETING_KINDS.map((value) => ({ value, label: KIND_LABEL[value] }))}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectField
                label="Language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                options={[
                  { value: 'en', label: 'English' },
                  { value: 'bn', label: 'Bangla' },
                ]}
              />
              <SelectField
                label="Tone"
                value={tone}
                onChange={(e) => setTone(e.target.value)}
                options={[
                  { value: 'friendly', label: 'Friendly' },
                  { value: 'premium', label: 'Premium' },
                  { value: 'simple', label: 'Simple' },
                  { value: 'formal', label: 'Formal' },
                ]}
              />
            </div>
            <SelectField
              label="Campaign (its real discount and dates are used)"
              value={campaign}
              onChange={(e) => setCampaign(e.target.value)}
              options={[
                { value: '', label: 'No campaign' },
                ...(campaigns.data?.results ?? []).map((row) => ({
                  value: row.id,
                  label: row.name,
                })),
              ]}
            />
            {campaigns.error && <Alert>{campaigns.error}</Alert>}
            <div className="grid gap-2">
              <p className="text-[13px] font-semibold">Products ({products.length} of 5)</p>
              {products.map((p) => (
                <span
                  key={p.id}
                  className="flex items-center justify-between gap-2 rounded-base border border-line px-3 py-1.5 text-sm"
                >
                  {p.name}
                  <button
                    type="button"
                    className="text-xs underline"
                    onClick={() => setProducts(products.filter((x) => x.id !== p.id))}
                  >
                    Remove
                  </button>
                </span>
              ))}
              {products.length < 5 && (
                <ProductPickerInline
                  onPick={(row) => {
                    if (!products.some((p) => p.id === row.id)) setProducts([...products, row]);
                  }}
                />
              )}
            </div>
            <TextArea
              label="Brief (optional)"
              hint="For example the occasion or audience. Do not add discounts here; only real campaign discounts are used."
              value={brief}
              maxLength={500}
              rows={3}
              onChange={(e) => setBrief(e.target.value)}
            />
            <Button
              type="submit"
              busy={ai.busy || ai.running}
              disabled={!campaign && !products.length}
            >
              {ai.request ? 'Write again' : 'Write drafts'}
            </Button>
          </form>
        </Card>
        <Card title="Drafts">
          {ai.error && <Alert>{ai.error}</Alert>}
          {notice && <Alert tone="success">{notice}</Alert>}
          <RequestState request={ai.request} onCancel={() => void ai.cancel()} />
          {!ai.request && (
            <Empty title="No drafts yet">Choose a campaign or products and write drafts.</Empty>
          )}
          {result && ai.request && (
            <div className="grid gap-4">
              <div className="flex flex-wrap gap-2">
                <Badge tone="info">AI drafts</Badge>
                <TestOutputBadge request={ai.request} />
              </div>
              <Warnings items={result.warnings} />
              {result.variants.map((variant, index) => {
                const text = [variant.headline, variant.body].filter(Boolean).join('\n\n');
                const value =
                  requestKind === 'campaign_title'
                    ? variant.headline
                    : variant.body || variant.headline;
                return (
                  <div key={index} className="grid gap-2 rounded-base border border-line p-4">
                    {variant.headline && (
                      <p className="font-semibold break-words">{variant.headline}</p>
                    )}
                    {variant.body && (
                      <p className="text-sm leading-6 break-words whitespace-pre-wrap">
                        {variant.body}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="secondary"
                        type="button"
                        onClick={() =>
                          void navigator.clipboard?.writeText(text).then(() => setNotice('Copied.'))
                        }
                      >
                        Copy
                      </Button>
                      {canApply && ai.request?.review_status === 'pending' && (
                        <Button type="button" onClick={() => void apply(value, false)}>
                          Use as campaign{' '}
                          {applyField ||
                            (requestKind === 'campaign_title' ? 'name' : 'description')}
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
              {result.notes.length > 0 && (
                <ul className="text-sm text-muted">
                  {result.notes.map((note) => (
                    <li key={note}>• {note}</li>
                  ))}
                </ul>
              )}
              <p className="text-xs leading-5 text-muted">
                Nothing is sent or activated. Emails, SMS and campaigns keep their own consent,
                review and launch steps.
              </p>
            </div>
          )}
        </Card>
      </div>
      {applying && (
        <ConfirmDialog
          title="Replace campaign text?"
          confirmLabel="Replace"
          onCancel={() => setApplying(null)}
          onConfirm={() => apply(applying.value, true)}
        >
          The campaign already has this text. Replacing it does not change its discount, dates or
          status.
        </ConfirmDialog>
      )}
    </FeatureGate>
  );
}

function ProductPickerInline({ onPick }: { onPick: (row: { id: string; name: string }) => void }) {
  const [text, setText] = useState('');
  const [rows, setRows] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState('');
  async function search() {
    setError('');
    try {
      const page = managePage(managedProductSummary)(
        await api.get<unknown>(
          `/api/manage/catalog/products/${query({ q: text.trim(), page_size: 8 })}`,
        ),
      );
      setRows(page.results.map((row) => ({ id: row.id, name: row.name })));
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <div className="grid gap-2">
      <div className="flex gap-2">
        <input
          aria-label="Search products"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="h-11 min-w-0 flex-1 rounded-base border border-line-strong bg-surface px-3 text-sm"
          placeholder="Search products"
        />
        <Button variant="secondary" type="button" onClick={() => void search()}>
          Find
        </Button>
      </div>
      {error && <Alert>{error}</Alert>}
      {rows.map((row) => (
        <button
          key={row.id}
          type="button"
          onClick={() => onPick(row)}
          className="rounded-base border border-line px-3 py-1.5 text-left text-sm hover:border-ink"
        >
          + {row.name}
        </button>
      ))}
    </div>
  );
}

// --- Insights -------------------------------------------------------------------------------------

function InsightsTab() {
  const ai = useAIRequest();
  const [days, setDays] = useState('30');
  const [focus, setFocus] = useState('overview');
  const result =
    ai.request?.result?.feature === 'business_insights' ? ai.request.result.data : null;
  return (
    <FeatureGate feature="business_insights">
      <Card title="Explain my numbers">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void ai.generate('business_insights', null, { days: Number(days), focus });
          }}
        >
          <div className="min-w-[10rem]">
            <SelectField
              label="Period"
              value={days}
              onChange={(e) => setDays(e.target.value)}
              options={[
                { value: '7', label: 'Last 7 days' },
                { value: '30', label: 'Last 30 days' },
                { value: '90', label: 'Last 90 days' },
              ]}
            />
          </div>
          <div className="min-w-[10rem]">
            <SelectField
              label="Focus"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              options={[
                { value: 'overview', label: 'Everything I can see' },
                { value: 'sales', label: 'Sales' },
                { value: 'products', label: 'Products' },
                { value: 'inventory', label: 'Inventory' },
                { value: 'marketing', label: 'Marketing' },
                { value: 'support', label: 'Support' },
              ]}
            />
          </div>
          <Button type="submit" busy={ai.busy || ai.running}>
            Explain
          </Button>
        </form>
        <p className="mt-3 text-xs leading-5 text-muted">
          MateCart calculates every figure from your records; AI only explains them. Sections your
          role cannot see are never shared. Cost prices and margins are never sent.
        </p>
      </Card>
      <div className="mt-6 grid gap-6">
        {ai.error && <Alert>{ai.error}</Alert>}
        <RequestState request={ai.request} onCancel={() => void ai.cancel()} />
        {result && ai.request && <InsightsView result={result} isTest={ai.request.is_test} />}
      </div>
    </FeatureGate>
  );
}

function InsightsView({ result, isTest }: { result: InsightsResult; isTest: boolean }) {
  const period = result.metrics.period as Record<string, unknown> | undefined;
  return (
    <>
      <Card
        title={result.headline || 'Explanation'}
        actions={
          <div className="flex gap-2">
            <Badge tone="info">AI explanation</Badge>
            {isTest && <Badge tone="warning">Test output</Badge>}
          </div>
        }
      >
        {period && (
          <p className="mb-3 text-xs text-muted">
            Period {String(period.start)} to {String(period.end)} (store time).
          </p>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <List title="Observations" items={result.observations} />
          <List title="Trends in past data (not predictions)" items={result.trends} />
          <List title="Ideas to consider" items={result.suggestions} />
          <List title="Limits of the data" items={result.caveats} />
        </div>
        <Warnings items={result.warnings} />
      </Card>
      <Card title="Figures the explanation is based on">
        <MetricsView value={result.metrics} />
      </Card>
    </>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">{title}</p>
      <ul className="grid gap-1 text-sm leading-6">
        {items.map((item) => (
          <li key={item}>• {item}</li>
        ))}
      </ul>
    </div>
  );
}

function scalar(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Renders server-calculated aggregates as text tables (values are shown, never interpreted). */
function MetricsView({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (Array.isArray(value)) {
    const rows = value.filter(
      (row): row is Record<string, unknown> => typeof row === 'object' && row !== null,
    );
    if (!rows.length) return <span className="text-sm text-muted">None</span>;
    const head = Object.keys(rows[0] ?? {});
    return (
      <Table head={head.map((key) => key.replaceAll('_', ' '))}>
        {rows.slice(0, 15).map((row, index) => (
          <tr key={index}>
            {head.map((key) => (
              <Cell key={key}>{scalar(row[key])}</Cell>
            ))}
          </tr>
        ))}
      </Table>
    );
  }
  if (typeof value === 'object' && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <div className={`grid gap-3 ${depth ? '' : ''}`}>
        {entries.map(([key, item]) =>
          typeof item === 'object' && item !== null && depth < 2 ? (
            <details key={key} open={depth === 0}>
              <summary className="cursor-pointer text-sm font-semibold">
                {key.replaceAll('_', ' ')}
              </summary>
              <div className="mt-2 pl-3">
                <MetricsView value={item} depth={depth + 1} />
              </div>
            </details>
          ) : (
            <Detail key={key} label={key.replaceAll('_', ' ')}>
              {scalar(item)}
            </Detail>
          ),
        )}
      </div>
    );
  }
  return <span>{scalar(value)}</span>;
}

// --- Usage ----------------------------------------------------------------------------------------

function UsageSummary({ usage }: { usage: AIUsage }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Stat
        label="Requests this month"
        value={usage.requests}
        hint={`of ${usage.request_limit} allowed`}
      />
      <Stat
        label="Tokens this month"
        value={usage.tokens_total.toLocaleString()}
        hint={`of ${usage.token_limit.toLocaleString()} allowed`}
      />
      <Stat label="Failed" value={usage.failed} />
      <Stat label="Test requests" value={usage.test_requests} hint="Not sent to any AI" />
    </div>
  );
}

function UsageTab() {
  const load = useCallback(async () => aiUsage(await api.get<unknown>(`${AI_BASE}/usage/`)), []);
  const { data, error, loading } = useLoad(load);
  const loadRequests = useCallback(
    async () =>
      aiRequestPage(await api.get<unknown>(`${AI_BASE}/requests/?scope=all&page_size=25`)),
    [],
  );
  const requests = useLoad(loadRequests);
  if (loading && !data) return <Loader />;
  if (error || !data) return <Alert>{error || 'Could not load usage.'}</Alert>;
  return (
    <div className="grid gap-6">
      <UsageSummary usage={data} />
      <Alert tone="info">
        Token counts are those reported by the provider. Your provider bills you directly; MateCart
        does not estimate costs.{' '}
        {data.requests_without_usage > 0 &&
          `${data.requests_without_usage} requests had no usage reported.`}
      </Alert>
      <Card title="By feature" flush>
        <Table head={['Feature', 'Requests', 'Completed', 'Failed', 'Tokens']}>
          {data.by_feature.map((row) => (
            <tr key={row.feature}>
              <Cell>{FEATURE_LABEL[row.feature as AIFeature] ?? row.feature}</Cell>
              <Cell>{row.requests}</Cell>
              <Cell>{row.completed}</Cell>
              <Cell>{row.failed}</Cell>
              <Cell>{row.tokens.toLocaleString()}</Cell>
            </tr>
          ))}
        </Table>
        {!data.by_feature.length && <Empty title="No requests this month" />}
      </Card>
      <Card title="All recent requests" flush>
        {requests.error && <Alert>{requests.error}</Alert>}
        <Table head={['Feature', 'By', 'Status', 'Model', 'Tokens', 'When']}>
          {(requests.data?.results ?? []).map((row) => (
            <tr key={row.id}>
              <Cell>{FEATURE_LABEL[row.feature]}</Cell>
              <Cell>{row.requested_by_name}</Cell>
              <Cell>{REQUEST_STATUS[row.status]}</Cell>
              <Cell className="break-all">{row.model || '—'}</Cell>
              <Cell>{row.total_tokens ?? '—'}</Cell>
              <Cell>{dateTime(row.created_at)}</Cell>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

// --- Settings -------------------------------------------------------------------------------------

function SettingsTab() {
  const load = useCallback(
    async () => aiSettings(await api.get<unknown>(`${AI_BASE}/settings/`)),
    [],
  );
  const { data, setData, error, loading } = useLoad(load);
  const [message, setMessage] = useState({ error: '', notice: '' });
  const [pending, setPending] = useState<{ title: string; run: () => Promise<void> } | null>(null);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState('');

  async function run(label: string, action: () => Promise<AISettings>, done: string, title = '') {
    setMessage({ error: '', notice: '' });
    setBusy(label);
    try {
      setData(await action());
      setMessage({ error: '', notice: done });
    } catch (e) {
      if (e instanceof ApiError && e.code === 'reauthentication_required' && title) {
        setPending({
          title,
          run: async () => {
            setData(await action());
            setMessage({ error: '', notice: done });
            setPending(null);
          },
        });
      } else {
        setMessage({ error: errorMessage(e), notice: '' });
      }
    } finally {
      setBusy('');
    }
  }

  if (loading && !data) return <Loader />;
  if (error || !data) return <Alert>{error || 'Could not load AI settings.'}</Alert>;
  const status = STATUS_TEXT[data.status] ?? { label: data.status, tone: 'neutral' as const };
  const textModels = data.available_models.filter((m) => m.capabilities.includes('text'));
  const visionModels = data.available_models.filter((m) => m.capabilities.includes('vision'));
  const save = (body: Record<string, unknown>, done: string) =>
    run(
      'save',
      async () => aiSettings(await api.patch<unknown>(`${AI_BASE}/settings/`, body)),
      done,
      'Confirm AI settings',
    );

  return (
    <div className="grid gap-6">
      <Feedback error={message.error} notice={message.notice} />
      <Card title="Provider" actions={<Badge tone={status.tone}>{status.label}</Badge>}>
        <div className="grid gap-4">
          {!data.module_enabled && (
            <Alert tone="info">
              The AI assistant module is off; no request runs until it is switched on in{' '}
              <Link href={'/manage/appearance' as Route} className="underline">
                store features
              </Link>
              .
            </Alert>
          )}
          {data.last_error_text && <Alert>{data.last_error_text}</Alert>}
          <SelectField
            label="Provider"
            value={data.provider}
            onChange={(e) =>
              void save({ provider: e.target.value }, 'Provider changed. Verify the connection.')
            }
            options={[
              { value: 'groq', label: 'GroqCloud' },
              ...(data.test_provider_allowed || data.provider === 'test'
                ? [{ value: 'test', label: 'Test provider (development only, no AI)' }]
                : []),
            ]}
          />
          {data.provider === 'groq' && (
            <form
              className="grid gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                const value = key;
                void run(
                  'key',
                  async () =>
                    aiSettings(
                      await api.put<unknown>(`${AI_BASE}/settings/key/`, { api_key: value }),
                    ),
                  'Key saved. It is stored encrypted; verify the connection next.',
                  'Confirm the API key change',
                ).then(() => setKey(''));
              }}
            >
              <PasswordField
                label={
                  data.key_set
                    ? `GroqCloud API key (saved, ends with ${data.key_hint})`
                    : 'GroqCloud API key'
                }
                hint="Create a key at console.groq.com. It is encrypted and never shown again."
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" busy={busy === 'key'} disabled={!key.trim()}>
                  {data.key_set ? 'Replace key' : 'Save key'}
                </Button>
                {data.key_set && (
                  <Button
                    variant="secondary"
                    type="button"
                    onClick={() =>
                      void run(
                        'remove',
                        async () =>
                          aiSettings(await api.delete<unknown>(`${AI_BASE}/settings/key/`)),
                        'Key removed. AI requests stop until a new key is verified.',
                        'Remove the API key',
                      )
                    }
                  >
                    Remove key
                  </Button>
                )}
              </div>
            </form>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              type="button"
              busy={busy === 'verify'}
              onClick={() =>
                void run(
                  'verify',
                  async () => aiSettings(await api.post<unknown>(`${AI_BASE}/settings/verify/`)),
                  'Connection checked.',
                )
              }
            >
              Test connection
            </Button>
            <span className="text-xs text-muted">
              {data.last_verified_at
                ? `Last verified ${dateTime(data.last_verified_at)}`
                : 'Never verified'}
            </span>
          </div>
        </div>
      </Card>
      <Card title="Models">
        {data.available_models.length === 0 ? (
          <Empty title="No models listed yet">
            Test the connection to list the models your key can use.
          </Empty>
        ) : (
          <div className="grid gap-4">
            <ModelSelect
              label="Text and structured drafts"
              value={data.text_model}
              models={textModels}
              onChange={(value) => void save({ text_model: value }, 'Text model saved.')}
            />
            <ModelSelect
              label="Image analysis"
              value={data.vision_model}
              models={visionModels}
              empty="No listed model can read images. Image analysis stays unavailable."
              onChange={(value) => void save({ vision_model: value }, 'Image model saved.')}
            />
            <p className="text-xs leading-5 text-muted">
              Models marked preview may be withdrawn by the provider at short notice. MateCart never
              switches models for you; if a chosen model disappears, the status says so.
            </p>
          </div>
        )}
      </Card>
      <Card title="Features">
        <div className="grid gap-3">
          {data.features.map((feature) => (
            <label key={feature.key} className="flex items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={data.enabled_features.includes(feature.key)}
                onChange={(e) =>
                  void save(
                    {
                      enabled_features: e.target.checked
                        ? [...data.enabled_features, feature.key]
                        : data.enabled_features.filter((f) => f !== feature.key),
                    },
                    'Features saved.',
                  )
                }
              />
              <span>
                <span className="font-semibold">{feature.name}</span>
                {!feature.available && feature.problem !== 'ai_feature_off' && (
                  <span className="block text-xs text-muted">{feature.problem_text}</span>
                )}
              </span>
            </label>
          ))}
        </div>
      </Card>
      <Card title="Customer messages">
        <div className="grid gap-3 text-sm leading-6">
          <p>
            Support suggestions send short excerpts of the conversation (e-mail addresses and phone
            numbers masked, internal notes never) and, for staff who manage orders, the linked order
            status to the provider.
          </p>
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              className="mt-1"
              checked={data.customer_data_confirmed}
              onChange={(e) => {
                const confirmed = e.target.checked;
                void run(
                  'consent',
                  async () =>
                    aiSettings(
                      await api.post<unknown>(`${AI_BASE}/settings/customer-data/`, { confirmed }),
                    ),
                  confirmed
                    ? 'Support suggestions may now use conversation excerpts.'
                    : 'Support suggestions are stopped.',
                  'Confirm customer data use',
                );
              }}
            />
            <span>
              Our privacy notice tells customers that messages may be processed by an AI service to
              help staff reply.
            </span>
          </label>
        </div>
      </Card>
      <LimitsCard
        settings={data}
        onSave={(body) => void save(body, 'Limits saved.')}
        busy={busy === 'save'}
      />
      {pending && (
        <ReauthDialog
          title={pending.title}
          description="Confirm with your password to change AI provider settings."
          onConfirm={pending.run}
          onClose={() => setPending(null)}
        />
      )}
    </div>
  );
}

function ModelSelect({
  label,
  value,
  models,
  empty,
  onChange,
}: {
  label: string;
  value: string;
  models: AIModel[];
  empty?: string;
  onChange: (value: string) => void;
}) {
  if (!models.length && empty) return <Alert tone="info">{empty}</Alert>;
  const known = models.some((m) => m.id === value);
  return (
    <SelectField
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      options={[
        { value: '', label: 'Not chosen' },
        ...(value && !known ? [{ value, label: `${value} (no longer available)` }] : []),
        ...models.map((m) => ({
          value: m.id,
          label: `${m.id}${m.preview ? ' (preview)' : ''}${m.capabilities.includes('json_schema') ? ', strict structured output' : ', JSON mode'}`,
        })),
      ]}
    />
  );
}

function LimitsCard({
  settings,
  onSave,
  busy,
}: {
  settings: AISettings;
  onSave: (body: Record<string, number>) => void;
  busy: boolean;
}) {
  const [values, setValues] = useState({
    monthly_request_limit: String(settings.monthly_request_limit),
    monthly_token_limit: String(settings.monthly_token_limit),
    max_output_tokens: String(settings.max_output_tokens),
    user_hourly_limit: String(settings.user_hourly_limit),
  });
  const fields: { key: keyof typeof values; label: string; hint: string }[] = [
    {
      key: 'monthly_request_limit',
      label: 'Requests per month',
      hint: 'Requests stop when reached.',
    },
    { key: 'monthly_token_limit', label: 'Tokens per month', hint: 'As reported by the provider.' },
    { key: 'max_output_tokens', label: 'Longest answer (tokens)', hint: 'Between 64 and 8192.' },
    {
      key: 'user_hourly_limit',
      label: 'Requests per person per hour',
      hint: 'Between 1 and 1000.',
    },
  ];
  return (
    <Card title="Limits">
      <form
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(Object.fromEntries(Object.entries(values).map(([k, v]) => [k, Number(v)])));
        }}
      >
        {fields.map((field) => (
          <TextField
            key={field.key}
            label={field.label}
            hint={field.hint}
            inputMode="numeric"
            value={values[field.key]}
            onChange={(e) =>
              setValues({ ...values, [field.key]: e.target.value.replace(/\D/g, '') })
            }
          />
        ))}
        <div className="sm:col-span-2">
          <Button type="submit" busy={busy}>
            Save limits
          </Button>
        </div>
      </form>
    </Card>
  );
}
