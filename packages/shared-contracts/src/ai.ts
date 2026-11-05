/**
 * Runtime-validated AI assistant responses (Phase 15), mirroring the committed OpenAPI schema.
 *
 * Settings carry only whether a provider key is stored and its last characters (`key_hint`);
 * decoders build fresh objects field by field, so an unexpected secret is never kept. AI output
 * is decoded per feature and is always plain text: the dashboard renders it as text, never HTML.
 */
import {
  array,
  boolean,
  number,
  object,
  string,
  strings,
  type Decoder,
  type JsonObject,
} from './auth';
import { isoDateTime, nullable, type Page } from './catalog';
import { managePage } from './manage';

function integer(value: unknown): number {
  const n = number(value);
  if (!Number.isInteger(n)) throw new Error('Invalid response');
  return n;
}
function oneOf<T extends string>(values: readonly T[]): Decoder<T> {
  return (value) => {
    const text = string(value);
    if (!(values as readonly string[]).includes(text)) throw new Error('Invalid response');
    return text as T;
  };
}
const optionalStrings = (value: unknown): string[] => (value === undefined ? [] : strings(value));

export const AI_FEATURES = [
  'product_content',
  'image_analysis',
  'support_reply',
  'support_summary',
  'marketing_copy',
  'business_insights',
] as const;
export type AIFeature = (typeof AI_FEATURES)[number];
export const AI_STATUSES = [
  'not_configured',
  'unverified',
  'connected',
  'invalid_credentials',
  'rate_limited',
  'unavailable',
  'unsupported_model',
  'disabled',
] as const;
export type AIStatus = (typeof AI_STATUSES)[number];
export const AI_REQUEST_STATUSES = [
  'pending',
  'processing',
  'completed',
  'failed',
  'cancelled',
  'expired',
] as const;
export type AIRequestStatus = (typeof AI_REQUEST_STATUSES)[number];
export const AI_REVIEW_STATUSES = ['none', 'pending', 'applied', 'used', 'rejected'] as const;
export type AIReviewStatus = (typeof AI_REVIEW_STATUSES)[number];
export const AI_PROVIDERS = ['groq', 'test'] as const;
export type AIProvider = (typeof AI_PROVIDERS)[number];
export const PRODUCT_CONTENT_FIELDS = [
  'title',
  'summary',
  'description',
  'highlights',
  'seo_title',
  'seo_description',
  'tags',
  'image_alt_text',
] as const;
export type ProductContentField = (typeof PRODUCT_CONTENT_FIELDS)[number];
export const MARKETING_KINDS = [
  'campaign_title',
  'campaign_description',
  'homepage_headline',
  'email_subject',
  'email_body',
  'sms',
  'social_caption',
  'product_promo',
] as const;
export type MarketingKind = (typeof MARKETING_KINDS)[number];
export const SUPPORT_TOPICS = [
  'order_status',
  'delivery',
  'return_exchange',
  'product_question',
  'payment',
  'complaint',
  'other',
] as const;
export type SupportTopic = (typeof SUPPORT_TOPICS)[number];

/** Only these states can still change; the dashboard polls them. */
export function isAIRequestRunning(status: AIRequestStatus): boolean {
  return status === 'pending' || status === 'processing';
}

// --- Results --------------------------------------------------------------------------------------

export interface ProductContentResult {
  title: string;
  summary: string;
  description: string;
  highlights: string[];
  seo_title: string;
  seo_description: string;
  tags: string[];
  image_alt_text: string;
  missing_information: string[];
  warnings: string[];
}
export interface ImageAnalysisResult {
  description: string;
  alt_text: string;
  visible_objects: string[];
  colors: string[];
  category_suggestion: string;
  attribute_suggestions: { name: string; value: string }[];
  quality_issues: string[];
  mismatch_notes: string[];
  cannot_determine: string[];
  warnings: string[];
}
export interface SupportReplyResult {
  reply: string;
  language: string;
  topic: SupportTopic;
  next_action: string;
  needs_verification: string[];
  verified_facts: string[];
  warnings: string[];
}
export interface SupportSummaryResult {
  summary: string;
  customer_claims: string[];
  interpretation: string[];
  topic: SupportTopic;
  next_action: string;
  verified_facts: string[];
  warnings: string[];
}
export interface MarketingResult {
  variants: { headline: string; body: string }[];
  notes: string[];
  warnings: string[];
}
export interface InsightsResult {
  headline: string;
  observations: string[];
  trends: string[];
  suggestions: string[];
  caveats: string[];
  /** The aggregated figures the explanation was based on (calculated by the server). */
  metrics: JsonObject;
  warnings: string[];
}
export type AIResult =
  | { feature: 'product_content'; data: ProductContentResult }
  | { feature: 'image_analysis'; data: ImageAnalysisResult }
  | { feature: 'support_reply'; data: SupportReplyResult }
  | { feature: 'support_summary'; data: SupportSummaryResult }
  | { feature: 'marketing_copy'; data: MarketingResult }
  | { feature: 'business_insights'; data: InsightsResult };

function aiResult(feature: AIFeature, value: unknown): AIResult {
  const o = object(value);
  const warnings = optionalStrings(o.warnings);
  switch (feature) {
    case 'product_content':
      return {
        feature,
        data: {
          title: string(o.title),
          summary: string(o.summary),
          description: string(o.description),
          highlights: strings(o.highlights),
          seo_title: string(o.seo_title),
          seo_description: string(o.seo_description),
          tags: strings(o.tags),
          image_alt_text: string(o.image_alt_text),
          missing_information: strings(o.missing_information),
          warnings,
        },
      };
    case 'image_analysis':
      return {
        feature,
        data: {
          description: string(o.description),
          alt_text: string(o.alt_text),
          visible_objects: strings(o.visible_objects),
          colors: strings(o.colors),
          category_suggestion: string(o.category_suggestion),
          attribute_suggestions: array(o.attribute_suggestions, (entry) => {
            const a = object(entry);
            return { name: string(a.name), value: string(a.value) };
          }),
          quality_issues: strings(o.quality_issues),
          mismatch_notes: strings(o.mismatch_notes),
          cannot_determine: strings(o.cannot_determine),
          warnings,
        },
      };
    case 'support_reply':
      return {
        feature,
        data: {
          reply: string(o.reply),
          language: string(o.language),
          topic: oneOf(SUPPORT_TOPICS)(o.topic),
          next_action: string(o.next_action),
          needs_verification: strings(o.needs_verification),
          verified_facts: optionalStrings(o.verified_facts),
          warnings,
        },
      };
    case 'support_summary':
      return {
        feature,
        data: {
          summary: string(o.summary),
          customer_claims: strings(o.customer_claims),
          interpretation: strings(o.interpretation),
          topic: oneOf(SUPPORT_TOPICS)(o.topic),
          next_action: string(o.next_action),
          verified_facts: optionalStrings(o.verified_facts),
          warnings,
        },
      };
    case 'marketing_copy':
      return {
        feature,
        data: {
          variants: array(o.variants, (entry) => {
            const v = object(entry);
            return { headline: string(v.headline), body: string(v.body) };
          }),
          notes: strings(o.notes),
          warnings,
        },
      };
    case 'business_insights':
      return {
        feature,
        data: {
          headline: string(o.headline),
          observations: strings(o.observations),
          trends: strings(o.trends),
          suggestions: strings(o.suggestions),
          caveats: strings(o.caveats),
          metrics: o.metrics === undefined ? {} : object(o.metrics),
          warnings,
        },
      };
  }
}

// --- Requests -------------------------------------------------------------------------------------

export interface AIRequest {
  id: string;
  feature: AIFeature;
  status: AIRequestStatus;
  review_status: AIReviewStatus;
  provider: AIProvider;
  model: string;
  is_test: boolean;
  subject_type: string;
  subject_id: string | null;
  options: JsonObject;
  context_sources: string[];
  result: AIResult | null;
  result_available: boolean;
  error_code: string;
  error_text: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number | null;
  applied_fields: string[];
  requested_by_name: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  reviewed_at: string | null;
}
export function aiRequest(value: unknown): AIRequest {
  const o = object(value);
  const feature = oneOf(AI_FEATURES)(o.feature);
  const count = nullable(integer);
  return {
    id: string(o.id),
    feature,
    status: oneOf(AI_REQUEST_STATUSES)(o.status),
    review_status: oneOf(AI_REVIEW_STATUSES)(o.review_status),
    provider: oneOf(AI_PROVIDERS)(o.provider),
    model: string(o.model),
    is_test: boolean(o.is_test),
    subject_type: string(o.subject_type),
    subject_id: nullable(string)(o.subject_id),
    options: object(o.options),
    context_sources: strings(o.context_sources),
    result: o.result === null || o.result === undefined ? null : aiResult(feature, o.result),
    result_available: boolean(o.result_available),
    error_code: string(o.error_code),
    error_text: string(o.error_text),
    input_tokens: count(o.input_tokens),
    output_tokens: count(o.output_tokens),
    total_tokens: count(o.total_tokens),
    latency_ms: count(o.latency_ms),
    applied_fields: strings(o.applied_fields),
    requested_by_name: string(o.requested_by_name),
    created_at: isoDateTime(o.created_at),
    started_at: nullable(isoDateTime)(o.started_at),
    completed_at: nullable(isoDateTime)(o.completed_at),
    reviewed_at: nullable(isoDateTime)(o.reviewed_at),
  };
}
export const aiRequestPage: Decoder<Page<AIRequest>> = managePage(aiRequest);

// --- Settings, usage and overview -----------------------------------------------------------------

export interface AIFeatureState {
  key: AIFeature;
  name: string;
  permission: string;
  enabled: boolean;
  available: boolean;
  problem: string;
  problem_text: string;
  needs_vision: boolean;
  uses_customer_data: boolean;
}
function featureState(value: unknown): AIFeatureState {
  const o = object(value);
  return {
    key: oneOf(AI_FEATURES)(o.key),
    name: string(o.name),
    permission: string(o.permission),
    enabled: boolean(o.enabled),
    available: boolean(o.available),
    problem: string(o.problem),
    problem_text: string(o.problem_text),
    needs_vision: boolean(o.needs_vision),
    uses_customer_data: boolean(o.uses_customer_data),
  };
}

export interface AIModel {
  id: string;
  capabilities: string[];
  context_window: number | null;
  max_output_tokens: number | null;
  preview: boolean;
}
function aiModel(value: unknown): AIModel {
  const o = object(value);
  return {
    id: string(o.id),
    capabilities: strings(o.capabilities),
    context_window: nullable(integer)(o.context_window),
    max_output_tokens: nullable(integer)(o.max_output_tokens),
    preview: boolean(o.preview),
  };
}

export interface AISettings {
  module_enabled: boolean;
  provider: AIProvider;
  status: AIStatus;
  key_set: boolean;
  key_hint: string;
  text_model: string;
  vision_model: string;
  available_models: AIModel[];
  models_checked_at: string | null;
  last_verified_at: string | null;
  last_error: string;
  last_error_text: string;
  enabled_features: AIFeature[];
  monthly_request_limit: number;
  monthly_token_limit: number;
  max_output_tokens: number;
  user_hourly_limit: number;
  customer_data_confirmed: boolean;
  customer_data_confirmed_at: string | null;
  test_provider_allowed: boolean;
  is_test: boolean;
  features: AIFeatureState[];
}
export function aiSettings(value: unknown): AISettings {
  const o = object(value);
  return {
    module_enabled: boolean(o.module_enabled),
    provider: oneOf(AI_PROVIDERS)(o.provider),
    status: oneOf(AI_STATUSES)(o.status),
    key_set: boolean(o.key_set),
    key_hint: string(o.key_hint),
    text_model: string(o.text_model),
    vision_model: string(o.vision_model),
    available_models: array(o.available_models, aiModel),
    models_checked_at: nullable(isoDateTime)(o.models_checked_at),
    last_verified_at: nullable(isoDateTime)(o.last_verified_at),
    last_error: string(o.last_error),
    last_error_text: string(o.last_error_text),
    enabled_features: array(o.enabled_features, oneOf(AI_FEATURES)),
    monthly_request_limit: integer(o.monthly_request_limit),
    monthly_token_limit: integer(o.monthly_token_limit),
    max_output_tokens: integer(o.max_output_tokens),
    user_hourly_limit: integer(o.user_hourly_limit),
    customer_data_confirmed: boolean(o.customer_data_confirmed),
    customer_data_confirmed_at: nullable(isoDateTime)(o.customer_data_confirmed_at),
    test_provider_allowed: boolean(o.test_provider_allowed),
    is_test: boolean(o.is_test),
    features: array(o.features, featureState),
  };
}

export interface AIUsage {
  month_start: string;
  month_end: string;
  requests: number;
  request_limit: number;
  tokens_input: number;
  tokens_output: number;
  tokens_total: number;
  token_limit: number;
  requests_without_usage: number;
  failed: number;
  test_requests: number;
  by_status: Record<string, number>;
  by_feature: {
    feature: string;
    requests: number;
    completed: number;
    failed: number;
    tokens: number;
  }[];
  by_day: { date: string; requests: number; tokens: number }[];
}
export function aiUsage(value: unknown): AIUsage {
  const o = object(value);
  const statuses = object(o.by_status);
  return {
    month_start: isoDateTime(o.month_start),
    month_end: isoDateTime(o.month_end),
    requests: integer(o.requests),
    request_limit: integer(o.request_limit),
    tokens_input: integer(o.tokens_input),
    tokens_output: integer(o.tokens_output),
    tokens_total: integer(o.tokens_total),
    token_limit: integer(o.token_limit),
    requests_without_usage: integer(o.requests_without_usage),
    failed: integer(o.failed),
    test_requests: integer(o.test_requests),
    by_status: Object.fromEntries(Object.entries(statuses).map(([k, v]) => [k, integer(v)])),
    by_feature: array(o.by_feature, (entry) => {
      const f = object(entry);
      return {
        feature: string(f.feature),
        requests: integer(f.requests),
        completed: integer(f.completed),
        failed: integer(f.failed),
        tokens: integer(f.tokens),
      };
    }),
    by_day: array(o.by_day, (entry) => {
      const d = object(entry);
      return { date: string(d.date), requests: integer(d.requests), tokens: integer(d.tokens) };
    }),
  };
}

export interface AIOverview {
  module_enabled: boolean;
  provider: AIProvider;
  status: AIStatus;
  text_model: string;
  vision_model: string;
  is_test: boolean;
  features: AIFeatureState[];
  can_manage_settings: boolean;
  usage: AIUsage | null;
  recent: AIRequest[];
  provider_data_notice: string;
}
export function aiOverview(value: unknown): AIOverview {
  const o = object(value);
  return {
    module_enabled: boolean(o.module_enabled),
    provider: oneOf(AI_PROVIDERS)(o.provider),
    status: oneOf(AI_STATUSES)(o.status),
    text_model: string(o.text_model),
    vision_model: string(o.vision_model),
    is_test: boolean(o.is_test),
    features: array(o.features, featureState),
    can_manage_settings: boolean(o.can_manage_settings),
    usage: nullable(aiUsage)(o.usage),
    recent: array(o.recent, aiRequest),
    provider_data_notice: string(o.provider_data_notice),
  };
}

// --- Related products (merchant view) -------------------------------------------------------------

export interface RecommendationEntry {
  id: string;
  name: string;
  slug: string;
  status: string;
  reasons: string[];
}
export interface ProductRecommendations {
  pinned: RecommendationEntry[];
  hidden: RecommendationEntry[];
  automatic: RecommendationEntry[];
  storefront: RecommendationEntry[];
}
function recommendationEntry(value: unknown): RecommendationEntry {
  const o = object(value);
  return {
    id: string(o.id),
    name: string(o.name),
    slug: string(o.slug),
    status: string(o.status),
    reasons: strings(o.reasons),
  };
}
export function productRecommendations(value: unknown): ProductRecommendations {
  const o = object(value);
  return {
    pinned: array(o.pinned, recommendationEntry),
    hidden: array(o.hidden, recommendationEntry),
    automatic: array(o.automatic, recommendationEntry),
    storefront: array(o.storefront, recommendationEntry),
  };
}
