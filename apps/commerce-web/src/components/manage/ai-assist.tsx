'use client';

/**
 * AI assistance building blocks (Phase 15) shared by the AI workspace, the product editor and the
 * unified inbox. Every AI output is a draft shown as plain text: nothing is saved, published or
 * sent until a staff member chooses to, and saving goes through the same APIs as manual edits.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  PRODUCT_CONTENT_FIELDS,
  aiRequest,
  isAIRequestRunning,
  type AIFeature,
  type AIRequest,
  type ImageAnalysisResult,
  type ManagedProduct,
  type ProductContentField,
  type ProductContentResult,
  type SupportReplyResult,
  type SupportSummaryResult,
} from '@matecart/shared-contracts';
import { Alert, ApiError, Button, ConfirmDialog, errorMessage } from '@matecart/shared-ui';
import { TextArea } from '../appearance-app';
import { Badge, SelectField, api, newKey } from './kit';

export const AI_BASE = '/api/manage/ai';
const POLL_MS = 1500;

export const FEATURE_LABEL: Record<AIFeature, string> = {
  product_content: 'Product content',
  image_analysis: 'Image analysis',
  support_reply: 'Reply suggestion',
  support_summary: 'Conversation summary',
  marketing_copy: 'Marketing copy',
  business_insights: 'Business insights',
};
export const REQUEST_STATUS: Record<string, string> = {
  pending: 'Waiting',
  processing: 'Writing',
  completed: 'Ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
  expired: 'Expired',
};
export const REVIEW_STATUS: Record<string, string> = {
  none: '',
  pending: 'Not reviewed',
  applied: 'Applied',
  used: 'Used',
  rejected: 'Rejected',
};
export const TOPIC_LABEL: Record<string, string> = {
  order_status: 'Order status',
  delivery: 'Delivery',
  return_exchange: 'Return or exchange',
  product_question: 'Product question',
  payment: 'Payment',
  complaint: 'Complaint',
  other: 'Other',
};
const FIELD_LABEL: Record<ProductContentField, string> = {
  title: 'Product name',
  summary: 'Summary',
  description: 'Description',
  highlights: 'Key selling points',
  seo_title: 'SEO title',
  seo_description: 'SEO description',
  tags: 'Tags',
  image_alt_text: 'Main photo alt text',
};
const FIELD_LIMIT: Partial<Record<ProductContentField, number>> = {
  title: 200,
  summary: 500,
  description: 20000,
  seo_title: 70,
  seo_description: 320,
  image_alt_text: 300,
};

/** Creates an AI request and follows it until it finishes (polling while it runs). */
export function useAIRequest() {
  const [request, setRequest] = useState<AIRequest | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const running = request !== null && isAIRequestRunning(request.status);
  const id = request?.id;
  useEffect(() => {
    if (!running || !id) return undefined;
    const timer = window.setTimeout(() => {
      api
        .get<unknown>(`${AI_BASE}/requests/${id}/`)
        .then((value) => {
          if (live.current) setRequest(aiRequest(value));
        })
        .catch((e: unknown) => {
          if (live.current) setError(errorMessage(e));
        });
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [running, id, request]);

  const generate = useCallback(
    async (feature: AIFeature, subjectId: string | null, options: Record<string, unknown>) => {
      setBusy(true);
      setError('');
      try {
        const created = aiRequest(
          await api.post<unknown>(`${AI_BASE}/requests/`, {
            feature,
            subject_id: subjectId,
            options,
            client_key: newKey('ai')
              .replace(/[^A-Za-z0-9_-]/g, '')
              .slice(0, 64),
          }),
        );
        if (live.current) setRequest(created);
      } catch (e) {
        if (live.current) setError(errorMessage(e));
      } finally {
        if (live.current) setBusy(false);
      }
    },
    [],
  );

  const act = useCallback(
    async (path: string, body?: unknown) => {
      if (!id) return null;
      const updated = aiRequest(
        await api.post<unknown>(`${AI_BASE}/requests/${id}/${path}/`, body),
      );
      if (live.current) setRequest(updated);
      return updated;
    },
    [id],
  );

  return {
    request,
    setRequest,
    error,
    setError,
    busy,
    running,
    generate,
    cancel: () => act('cancel'),
    review: (decision: 'used' | 'rejected') => act('review', { decision }),
    apply: (values: Record<string, unknown>, overwrite: boolean) =>
      act('apply', { values, overwrite }),
    reset: () => {
      setRequest(null);
      setError('');
    },
  };
}

export function TestOutputBadge({ request }: { request: AIRequest | null }) {
  if (!request?.is_test) return null;
  return <Badge tone="warning">Test output, not written by AI</Badge>;
}

/** The request's state in words: writing, failed with a reason, cancelled. */
export function RequestState({
  request,
  onCancel,
  onRetry,
}: {
  request: AIRequest | null;
  onCancel?: () => void;
  onRetry?: () => void;
}) {
  if (!request) return null;
  if (isAIRequestRunning(request.status)) {
    return (
      <div role="status" className="flex flex-wrap items-center gap-3 text-sm text-muted">
        <span
          aria-hidden="true"
          className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent"
        />
        {request.status === 'pending' ? 'Waiting to start…' : 'Writing a draft…'}
        {onCancel && (
          <Button variant="secondary" type="button" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    );
  }
  if (request.status === 'completed') return null;
  return (
    <Alert>
      <span>
        {request.status === 'failed'
          ? request.error_text || 'The AI request failed.'
          : request.status === 'expired'
            ? request.error_text || 'The request expired before it finished.'
            : 'The request was cancelled.'}
      </span>
      {onRetry && (
        <span className="mt-2 block">
          <Button variant="secondary" type="button" onClick={onRetry}>
            Try again
          </Button>
        </span>
      )}
    </Alert>
  );
}

function SourceNote({ request }: { request: AIRequest }) {
  if (!request.context_sources.length) return null;
  const names: Record<string, string> = {
    product: 'product record',
    store: 'store details',
    image: 'the photo',
    conversation_excerpt: 'recent messages (contact details masked)',
    order_status: 'linked order status',
    policy_pages: 'your published policy pages',
    products: 'chosen products',
    prices: 'current prices',
    campaign: 'the campaign',
    sales: 'sales report',
    inventory: 'inventory report',
    marketing: 'marketing analytics',
    support: 'inbox analytics',
  };
  return (
    <p className="text-xs leading-5 text-muted">
      Based on {request.context_sources.map((source) => names[source] ?? source).join(', ')}.
      {request.total_tokens !== null && ` ${request.total_tokens} tokens.`}
    </p>
  );
}

function Bullets({ title, items, tone }: { title: string; items: string[]; tone?: string }) {
  if (!items.length) return null;
  return (
    <div className="grid gap-1">
      <p className={`text-xs font-semibold tracking-wide uppercase ${tone ?? 'text-muted'}`}>
        {title}
      </p>
      <ul className="grid gap-1 text-sm leading-6">
        {items.map((item) => (
          <li key={item} className="break-words">
            • {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Warnings({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="rounded-base border border-warning-fg/30 bg-warning-bg p-3 text-sm leading-6 text-warning-fg">
      <p className="font-semibold">Check before using</p>
      <ul>
        {items.map((item) => (
          <li key={item}>• {item}</li>
        ))}
      </ul>
    </div>
  );
}

// --- Product content ------------------------------------------------------------------------------

export interface ProductTextFields {
  id: string;
  name: string;
  summary: string;
  description: string;
  seo_title: string;
  seo_description: string;
  tags: string[];
  translations: Record<string, Record<string, string>>;
  primaryAltText: string | null;
}

function currentValue(product: ProductTextFields, field: ProductContentField, language: string) {
  if (language === 'bn') {
    const bn = product.translations.bn ?? {};
    if (field === 'title') return bn.name ?? '';
    if (field === 'summary') return bn.summary ?? '';
    if (field === 'description') return bn.description ?? '';
    return '';
  }
  switch (field) {
    case 'title':
      return product.name;
    case 'tags':
      return product.tags.join(', ');
    case 'image_alt_text':
      return product.primaryAltText ?? '';
    case 'highlights':
      return '';
    default:
      return product[field];
  }
}

function draftText(result: ProductContentResult, field: ProductContentField): string {
  if (field === 'highlights') return result.highlights.join('\n');
  if (field === 'tags') return result.tags.join(', ');
  return result[field];
}

/** Applicable fields per language (Bangla drafts go to the existing translation fields). */
function applicable(field: ProductContentField, language: string, hasPhoto: boolean) {
  if (field === 'highlights') return false;
  if (field === 'image_alt_text') return language === 'en' && hasPhoto;
  if (language === 'bn') return field === 'title' || field === 'summary' || field === 'description';
  return true;
}

/**
 * Generate, compare and selectively apply product text. Applying calls the AI apply endpoint,
 * which saves through the catalogue service (same validation and audit as a manual edit).
 */
export function ProductAssistant({
  product,
  canBangla,
  onApplied,
}: {
  product: ProductTextFields;
  canBangla: boolean;
  onApplied: () => void | Promise<void>;
}) {
  const ai = useAIRequest();
  const [fields, setFields] = useState<ProductContentField[]>([
    'title',
    'summary',
    'description',
    'seo_title',
    'seo_description',
  ]);
  const [language, setLanguage] = useState('en');
  const [tone, setTone] = useState('friendly');
  const [drafts, setDrafts] = useState<Partial<Record<ProductContentField, string>>>({});
  const [chosen, setChosen] = useState<ProductContentField[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState('');
  const result = ai.request?.result?.feature === 'product_content' ? ai.request.result.data : null;
  const hasPhoto = product.primaryAltText !== null;
  const requestLanguage =
    typeof ai.request?.options.language === 'string' ? ai.request.options.language : language;

  const loadedId = useRef<string | null>(null);
  useEffect(() => {
    if (!result || !ai.request || loadedId.current === ai.request.id) return;
    loadedId.current = ai.request.id;
    const next: Partial<Record<ProductContentField, string>> = {};
    for (const field of PRODUCT_CONTENT_FIELDS) {
      const text = draftText(result, field);
      if (text) next[field] = text;
    }
    void Promise.resolve().then(() => {
      setDrafts(next);
      setChosen(
        (Object.keys(next) as ProductContentField[]).filter((field) =>
          applicable(field, requestLanguage, hasPhoto),
        ),
      );
    });
  }, [result, ai.request, requestLanguage, hasPhoto]);

  function values(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const field of chosen) {
      const text = drafts[field] ?? '';
      out[field] =
        field === 'tags'
          ? text
              .split(',')
              .map((tag) => tag.trim())
              .filter(Boolean)
          : text;
    }
    return out;
  }

  async function apply(overwrite: boolean) {
    setNotice('');
    try {
      await ai.apply(values(), overwrite);
      setConfirming(false);
      setNotice('Saved to the product. It stays in its current publication state.');
      await onApplied();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'ai_overwrite_needs_confirmation') {
        setConfirming(true);
      } else {
        ai.setError(errorMessage(e));
      }
    }
  }

  const reviewed = ai.request?.review_status && ai.request.review_status !== 'pending';
  return (
    <div className="grid gap-5">
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-[13px] font-semibold">What to write</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {PRODUCT_CONTENT_FIELDS.map((field) => (
            <label key={field} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={fields.includes(field)}
                disabled={!applicable(field, language, hasPhoto) && field !== 'highlights'}
                onChange={(e) =>
                  setFields(
                    e.target.checked ? [...fields, field] : fields.filter((f) => f !== field),
                  )
                }
              />
              {FIELD_LABEL[field]}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          label="Language"
          value={language}
          onChange={(e) => {
            setLanguage(e.target.value);
            setFields(
              fields.filter((f) => applicable(f, e.target.value, hasPhoto) || f === 'highlights'),
            );
          }}
          options={[
            { value: 'en', label: 'English' },
            ...(canBangla ? [{ value: 'bn', label: 'Bangla (translation fields)' }] : []),
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
          ]}
        />
      </div>
      <div className="flex flex-wrap gap-3">
        <Button
          type="button"
          busy={ai.busy || ai.running}
          disabled={!fields.length}
          onClick={() => {
            loadedId.current = null;
            setNotice('');
            void ai.generate('product_content', product.id, { fields, language, tone });
          }}
        >
          {ai.request ? 'Regenerate' : 'Generate draft'}
        </Button>
      </div>
      {ai.error && <Alert>{ai.error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      <RequestState
        request={ai.request}
        onCancel={() => void ai.cancel().catch((e: unknown) => ai.setError(errorMessage(e)))}
        onRetry={() => void ai.generate('product_content', product.id, { fields, language, tone })}
      />
      {result && ai.request && (
        <div className="grid gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="info">AI draft</Badge>
            <TestOutputBadge request={ai.request} />
            {reviewed && <Badge>{REVIEW_STATUS[ai.request.review_status]}</Badge>}
          </div>
          <SourceNote request={ai.request} />
          <Warnings items={result.warnings} />
          {result.missing_information.length > 0 && (
            <Alert tone="info">
              Facts the draft could not use because your product does not record them:{' '}
              {result.missing_information.join('; ')}. Add them to the product for a better draft.
            </Alert>
          )}
          {(Object.keys(drafts) as ProductContentField[]).map((field) => {
            const canApply = applicable(field, requestLanguage, hasPhoto);
            const current = currentValue(product, field, requestLanguage);
            return (
              <div key={field} className="grid gap-2 rounded-base border border-line p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <label className="flex items-center gap-2 text-sm font-semibold">
                    {canApply && !reviewed && (
                      <input
                        type="checkbox"
                        checked={chosen.includes(field)}
                        onChange={(e) =>
                          setChosen(
                            e.target.checked
                              ? [...chosen, field]
                              : chosen.filter((f) => f !== field),
                          )
                        }
                      />
                    )}
                    {FIELD_LABEL[field]}
                  </label>
                  {!canApply && (
                    <span className="text-xs text-muted">
                      {field === 'highlights'
                        ? 'Copy into the description if you want them on the page'
                        : 'Not applied automatically'}
                    </span>
                  )}
                </div>
                {current && (
                  <p className="text-xs leading-5 break-words text-muted">
                    <span className="font-semibold">Now:</span> {current}
                  </p>
                )}
                <TextArea
                  label={`Draft ${FIELD_LABEL[field].toLowerCase()}`}
                  value={drafts[field] ?? ''}
                  maxLength={FIELD_LIMIT[field]}
                  rows={field === 'description' || field === 'highlights' ? 6 : 2}
                  disabled={Boolean(reviewed)}
                  onChange={(e) => setDrafts({ ...drafts, [field]: e.target.value })}
                />
              </div>
            );
          })}
          {!reviewed && (
            <div className="flex flex-wrap gap-3">
              <Button type="button" disabled={!chosen.length} onClick={() => void apply(false)}>
                Apply selected to product
              </Button>
              <Button
                variant="secondary"
                type="button"
                onClick={() =>
                  void ai.review('rejected').catch((e: unknown) => ai.setError(errorMessage(e)))
                }
              >
                Discard draft
              </Button>
            </div>
          )}
        </div>
      )}
      {confirming && (
        <ConfirmDialog
          title="Replace existing content?"
          confirmLabel="Replace"
          onCancel={() => setConfirming(false)}
          onConfirm={() => apply(true)}
        >
          Some selected fields already have text. Applying replaces it with your reviewed draft. The
          change is recorded in the product history; price, stock and publication state do not
          change.
        </ConfirmDialog>
      )}
    </div>
  );
}

// --- Image analysis -------------------------------------------------------------------------------

export function ImageAnalysisView({
  result,
  request,
  currentAlt,
  onApplyAlt,
}: {
  result: ImageAnalysisResult;
  request: AIRequest;
  currentAlt: string;
  onApplyAlt: (text: string, overwrite: boolean) => Promise<void>;
}) {
  const [alt, setAlt] = useState(result.alt_text);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');
  const reviewed = request.review_status !== 'pending';
  async function apply(overwrite: boolean) {
    setError('');
    try {
      await onApplyAlt(alt, overwrite);
      setConfirming(false);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'ai_overwrite_needs_confirmation')
        setConfirming(true);
      else setError(errorMessage(e));
    }
  }
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-2">
        <Badge tone="info">AI suggestions</Badge>
        <TestOutputBadge request={request} />
      </div>
      <SourceNote request={request} />
      <p className="text-sm leading-6">{result.description}</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Bullets title="Visible objects" items={result.visible_objects} />
        <Bullets title="Colours" items={result.colors} />
        <Bullets
          title="Suggested attributes (check before adding)"
          items={result.attribute_suggestions.map((a) => `${a.name}: ${a.value}`)}
        />
        {result.category_suggestion && (
          <Bullets title="Possible category" items={[result.category_suggestion]} />
        )}
        <Bullets title="Photo quality" items={result.quality_issues} tone="text-warning-fg" />
        <Bullets title="Possible mismatch" items={result.mismatch_notes} tone="text-warning-fg" />
        <Bullets title="Cannot be told from a photo" items={result.cannot_determine} />
      </div>
      <Warnings items={result.warnings} />
      <TextArea
        label="Alt text"
        hint={currentAlt ? `Now: ${currentAlt}` : 'This photo has no alt text yet.'}
        value={alt}
        maxLength={300}
        rows={2}
        disabled={reviewed}
        onChange={(e) => setAlt(e.target.value)}
      />
      {error && <Alert>{error}</Alert>}
      {!reviewed ? (
        <div className="flex flex-wrap gap-3">
          <Button type="button" disabled={!alt.trim()} onClick={() => void apply(false)}>
            Save alt text to photo
          </Button>
        </div>
      ) : (
        <Badge>{REVIEW_STATUS[request.review_status]}</Badge>
      )}
      {confirming && (
        <ConfirmDialog
          title="Replace the alt text?"
          confirmLabel="Replace"
          onCancel={() => setConfirming(false)}
          onConfirm={() => apply(true)}
        >
          This photo already has alt text. Saving replaces it with your reviewed text.
        </ConfirmDialog>
      )}
    </div>
  );
}

// --- Unified inbox --------------------------------------------------------------------------------

/**
 * Reply suggestions and summaries inside an open conversation. The draft only fills the reply box;
 * staff edit it and send it themselves through the normal reply (window and policy checks apply).
 */
export function InboxAssistant({
  conversationId,
  onUseReply,
}: {
  conversationId: string;
  onUseReply: (text: string) => void;
}) {
  const reply = useAIRequest();
  const summary = useAIRequest();
  const [tone, setTone] = useState('friendly');
  const [language, setLanguage] = useState('auto');
  const [open, setOpen] = useState(false);
  const replyResult =
    reply.request?.result?.feature === 'support_reply' ? reply.request.result.data : null;
  const summaryResult =
    summary.request?.result?.feature === 'support_summary' ? summary.request.result.data : null;
  const previous = useRef(conversationId);
  useEffect(() => {
    if (previous.current === conversationId) return;
    previous.current = conversationId;
    reply.reset();
    summary.reset();
  }, [conversationId, reply, summary]);

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" type="button" onClick={() => setOpen(true)}>
          AI assist
        </Button>
      </div>
    );
  }
  return (
    <section aria-label="AI assistance" className="grid gap-3 rounded-base border border-line p-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[8rem]">
          <SelectField
            label="Tone"
            value={tone}
            onChange={(e) => setTone(e.target.value)}
            options={[
              { value: 'friendly', label: 'Friendly' },
              { value: 'formal', label: 'Formal' },
              { value: 'short', label: 'Short' },
            ]}
          />
        </div>
        <div className="min-w-[8rem]">
          <SelectField
            label="Language"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            options={[
              { value: 'auto', label: 'Same as customer' },
              { value: 'en', label: 'English' },
              { value: 'bn', label: 'Bangla' },
            ]}
          />
        </div>
        <Button
          type="button"
          busy={reply.busy || reply.running}
          onClick={() => void reply.generate('support_reply', conversationId, { tone, language })}
        >
          Suggest reply
        </Button>
        <Button
          variant="secondary"
          type="button"
          busy={summary.busy || summary.running}
          onClick={() => void summary.generate('support_summary', conversationId, {})}
        >
          Summarise
        </Button>
        <Button variant="secondary" type="button" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>
      {(reply.error || summary.error) && <Alert>{reply.error || summary.error}</Alert>}
      <RequestState request={reply.request} />
      <RequestState request={summary.request} />
      {replyResult && reply.request && (
        <ReplyDraft
          result={replyResult}
          request={reply.request}
          onUse={async () => {
            onUseReply(replyResult.reply);
            await reply.review('used').catch(() => undefined);
          }}
          onDismiss={() => void reply.review('rejected').catch(() => undefined)}
        />
      )}
      {summaryResult && summary.request && (
        <SummaryView result={summaryResult} request={summary.request} />
      )}
    </section>
  );
}

function ReplyDraft({
  result,
  request,
  onUse,
  onDismiss,
}: {
  result: SupportReplyResult;
  request: AIRequest;
  onUse: () => Promise<void>;
  onDismiss: () => void;
}) {
  const reviewed = request.review_status !== 'pending';
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">AI draft reply</Badge>
        <Badge>{TOPIC_LABEL[result.topic] ?? result.topic}</Badge>
        <TestOutputBadge request={request} />
      </div>
      <p className="rounded-base bg-canvas p-3 text-sm leading-6 break-words whitespace-pre-wrap">
        {result.reply}
      </p>
      <Bullets
        title="Verify before sending"
        items={result.needs_verification}
        tone="text-warning-fg"
      />
      {result.next_action && <Bullets title="Suggested next step" items={[result.next_action]} />}
      <SourceNote request={request} />
      {!reviewed && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" onClick={() => void onUse()}>
            Use in reply box
          </Button>
          <Button variant="secondary" type="button" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
      )}
      <p className="text-xs text-muted">
        The draft is not sent. You review, edit and send it yourself.
      </p>
    </div>
  );
}

export function SummaryView({
  result,
  request,
}: {
  result: SupportSummaryResult;
  request: AIRequest;
}): ReactNode {
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">AI summary</Badge>
        <Badge>{TOPIC_LABEL[result.topic] ?? result.topic}</Badge>
        <TestOutputBadge request={request} />
      </div>
      <p className="text-sm leading-6">{result.summary}</p>
      <Bullets title="Verified by MateCart" items={result.verified_facts} tone="text-success-fg" />
      <Bullets title="What the customer says" items={result.customer_claims} />
      <Bullets title="AI interpretation" items={result.interpretation} tone="text-warning-fg" />
      {result.next_action && <Bullets title="Suggested next step" items={[result.next_action]} />}
    </div>
  );
}

/** The text fields the product assistant compares and applies. */
export function productTextFields(product: ManagedProduct): ProductTextFields {
  const primary = product.media.find((m) => m.is_primary) ?? product.media[0];
  return {
    id: product.id,
    name: product.name,
    summary: product.summary,
    description: product.description,
    seo_title: product.seo_title,
    seo_description: product.seo_description,
    tags: product.tags,
    translations: product.translations,
    primaryAltText: primary ? primary.alt_text : null,
  };
}
