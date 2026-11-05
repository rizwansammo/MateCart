import { describe, expect, it } from 'vitest';

import {
  aiOverview,
  aiRequest,
  aiSettings,
  isAIRequestRunning,
  productRecommendations,
} from './ai';
import {
  compensation,
  dataOverview,
  importItem,
  isJobActive,
  sourceConnection,
  transferJob,
} from './transfers';

const request = (overrides: Record<string, unknown> = {}) => ({
  id: 'r1',
  feature: 'product_content',
  status: 'completed',
  review_status: 'pending',
  provider: 'test',
  model: 'matecart-test-text',
  is_test: true,
  subject_type: 'product',
  subject_id: 'p1',
  options: { fields: ['title'] },
  context_sources: ['product'],
  result: {
    title: '[Test] Tee',
    summary: '',
    description: '',
    highlights: [],
    seo_title: '',
    seo_description: '',
    tags: [],
    image_alt_text: '',
    missing_information: ['Material'],
  },
  result_available: true,
  error_code: '',
  error_text: '',
  input_tokens: 10,
  output_tokens: 5,
  total_tokens: 15,
  latency_ms: 20,
  applied_fields: [],
  requested_by_name: 'Owner',
  created_at: '2026-11-05T10:00:00Z',
  started_at: null,
  completed_at: '2026-11-05T10:00:01Z',
  reviewed_at: null,
  ...overrides,
});

const feature = {
  key: 'product_content',
  name: 'Product content',
  permission: 'catalog.manage',
  enabled: true,
  available: true,
  problem: '',
  problem_text: '',
  needs_vision: false,
  uses_customer_data: false,
};

describe('AI contracts', () => {
  it('decodes a request with a feature-specific result', () => {
    const decoded = aiRequest(request());
    expect(decoded.result?.feature).toBe('product_content');
    if (decoded.result?.feature === 'product_content') {
      expect(decoded.result.data.missing_information).toEqual(['Material']);
      expect(decoded.result.data.warnings).toEqual([]);
    }
    expect(aiRequest(request({ result: null })).result).toBeNull();
  });

  it('decodes insights metrics and support facts', () => {
    const insights = aiRequest(
      request({
        feature: 'business_insights',
        result: {
          headline: 'Sales grew',
          observations: [],
          trends: [],
          suggestions: [],
          caveats: [],
          metrics: { period: { days: 30 } },
        },
      }),
    );
    expect(
      insights.result?.feature === 'business_insights' && insights.result.data.metrics,
    ).toEqual({
      period: { days: 30 },
    });
    const reply = aiRequest(
      request({
        feature: 'support_reply',
        result: {
          reply: 'Hi',
          language: 'bn',
          topic: 'delivery',
          next_action: '',
          needs_verification: [],
          verified_facts: ['Channel: WhatsApp'],
        },
      }),
    );
    expect(reply.result?.feature === 'support_reply' && reply.result.data.verified_facts).toEqual([
      'Channel: WhatsApp',
    ]);
  });

  it('rejects unknown features, statuses and malformed results', () => {
    expect(() => aiRequest(request({ feature: 'image_generation' }))).toThrow();
    expect(() => aiRequest(request({ status: 'done' }))).toThrow();
    expect(() => aiRequest(request({ result: { title: 5 } }))).toThrow();
  });

  it('never keeps a provider key in settings', () => {
    const settings = aiSettings({
      module_enabled: true,
      provider: 'groq',
      status: 'connected',
      key_set: true,
      key_hint: 'ab12',
      api_key: 'gsk_secret',
      text_model: 'openai/gpt-oss-20b',
      vision_model: '',
      available_models: [
        {
          id: 'openai/gpt-oss-20b',
          capabilities: ['text', 'json_schema'],
          context_window: 131072,
          max_output_tokens: 65536,
          preview: false,
        },
      ],
      models_checked_at: null,
      last_verified_at: null,
      last_error: '',
      last_error_text: '',
      enabled_features: ['product_content'],
      monthly_request_limit: 500,
      monthly_token_limit: 1000000,
      max_output_tokens: 1500,
      user_hourly_limit: 40,
      customer_data_confirmed: false,
      customer_data_confirmed_at: null,
      test_provider_allowed: false,
      is_test: false,
      features: [feature],
    });
    expect(JSON.stringify(settings)).not.toContain('gsk_secret');
    expect(settings.key_hint).toBe('ab12');
  });

  it('decodes the overview with optional usage', () => {
    const overview = aiOverview({
      module_enabled: false,
      provider: 'groq',
      status: 'disabled',
      text_model: '',
      vision_model: '',
      is_test: false,
      features: [feature],
      can_manage_settings: false,
      usage: null,
      recent: [request()],
      provider_data_notice: 'Notice',
    });
    expect(overview.usage).toBeNull();
    expect(overview.recent).toHaveLength(1);
  });

  it('knows which requests are still running', () => {
    expect(isAIRequestRunning('pending')).toBe(true);
    expect(isAIRequestRunning('processing')).toBe(true);
    expect(isAIRequestRunning('failed')).toBe(false);
  });

  it('decodes merchant recommendations', () => {
    const entry = {
      id: 'p',
      name: 'Tee',
      slug: 'tee',
      status: 'active',
      reasons: ['same_category'],
    };
    expect(
      productRecommendations({ pinned: [], hidden: [], automatic: [entry], storefront: [entry] })
        .automatic[0]?.reasons,
    ).toEqual(['same_category']);
  });
});

const job = (overrides: Record<string, unknown> = {}) => ({
  id: 'j1',
  kind: 'import',
  source: 'csv',
  status: 'review',
  entity: 'catalog',
  file_format: 'csv',
  scopes: ['products'],
  conflict_strategy: 'skip',
  options: {},
  summary: {
    total: 3,
    valid: 1,
    invalid: 2,
    to_create: 1,
    to_update: 0,
    to_skip: 0,
    conflicts: 0,
    undecided: 0,
    with_warnings: 0,
    by_type: {},
  },
  total_items: 3,
  processed_items: 0,
  created_count: 0,
  updated_count: 0,
  skipped_count: 0,
  failed_count: 0,
  error_code: '',
  error_text: '',
  sensitive: false,
  cancel_requested: false,
  input_file: null,
  output_file: null,
  report_file: null,
  created_by_name: 'Owner',
  approved_by_name: '',
  approved_at: null,
  started_at: null,
  finished_at: null,
  compensated_at: null,
  created_at: '2026-11-05T10:00:00Z',
  connection_name: '',
  connection_simulated: false,
  ...overrides,
});

describe('data transfer contracts', () => {
  it('decodes jobs with and without a preview summary', () => {
    expect(transferJob(job()).summary?.to_create).toBe(1);
    expect(transferJob(job({ kind: 'export', summary: {} })).summary).toBeNull();
    expect(() => transferJob(job({ status: 'done' }))).toThrow();
  });

  it('decodes staged items and their messages', () => {
    const item = importItem({
      id: 'i1',
      position: 0,
      object_type: 'product',
      external_id: 'tee',
      key: 'TEE-S',
      title: 'Tee',
      action: 'invalid',
      status: 'pending',
      decision: '',
      messages: [{ level: 'error', text: 'Price is not a number.' }],
      existing_id: null,
      local_id: null,
      created_local: false,
      preview: { name: 'Tee' },
    });
    expect(item.messages[0]?.level).toBe('error');
    expect(isJobActive('running')).toBe(true);
    expect(isJobActive('review')).toBe(false);
  });

  it('never keeps a source credential', () => {
    const source = sourceConnection({
      id: 's1',
      platform: 'woocommerce',
      name: 'Shop',
      store_url: 'https://shop.example.com',
      credential_kind: 'consumer_key',
      credential_hint: 'abcd',
      credential_set: true,
      consumer_secret: 'cs_secret',
      status: 'connected',
      last_verified_at: null,
      last_error: '',
      last_error_text: '',
      source_name: 'Shop',
      source_currency: 'BDT',
      capabilities: { products: true, orders: false },
      counts: { products: 10, orders: null },
      is_simulated: false,
      created_at: '2026-11-05T10:00:00Z',
    });
    expect(JSON.stringify(source)).not.toContain('cs_secret');
    expect(source.counts.orders).toBeNull();
  });

  it('decodes the overview and compensation', () => {
    const overview = dataOverview({
      can_import: true,
      can_migrate: false,
      can_view_customers: false,
      can_view_orders: false,
      simulator_allowed: true,
      template_fields: [{ key: 'sku', name: 'SKU' }],
      exports: [
        { key: 'products', name: 'Products', sensitive: false, formats: ['csv'], allowed: true },
      ],
      store_currency: 'BDT',
      max_upload_bytes: 1024,
    });
    expect(overview.exports[0]?.allowed).toBe(true);
    expect(compensation({ deleted: 1, archived: 2, kept: 0 }).archived).toBe(2);
  });
});
