import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '@matecart/shared-ui';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { isManagePath, navigation, requiredPermission } from '@/lib/manage-routes';
import { aiTabs } from './ai';
import { InboxAssistant, ProductAssistant } from './ai-assist';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  upload: vi.fn(),
}));

vi.mock('@/lib/auth-client', () => ({ authApi: api }));

const PRODUCT = {
  id: '00000000-0000-7000-8000-000000000010',
  name: 'Plain Tee',
  summary: '',
  description: '',
  seo_title: '',
  seo_description: '',
  tags: [],
  translations: {},
  primaryAltText: null,
};

function request(overrides: Record<string, unknown> = {}) {
  return {
    id: '00000000-0000-7000-8000-000000000020',
    feature: 'product_content',
    status: 'completed',
    review_status: 'pending',
    provider: 'test',
    model: 'matecart-test-text',
    is_test: true,
    subject_type: 'product',
    subject_id: PRODUCT.id,
    options: { fields: ['title', 'summary'], language: 'en', tone: 'friendly' },
    context_sources: ['product', 'store'],
    result: {
      title: '[Test] Plain Tee',
      summary: '[Test] A tee.',
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
    latency_ms: 3,
    applied_fields: [],
    requested_by_name: 'Owner',
    created_at: '2026-11-05T10:00:00Z',
    started_at: '2026-11-05T10:00:00Z',
    completed_at: '2026-11-05T10:00:01Z',
    reviewed_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});

describe('AI routes and navigation', () => {
  it('opens AI tabs by permission', () => {
    expect(isManagePath('ai')).toBe(true);
    expect(requiredPermission('ai/settings')).toBe('store.settings');
    expect(requiredPermission('ai/products')).toBe('catalog.manage');
    expect(requiredPermission('data/jobs/00000000-0000-7000-8000-000000000001')).toBeNull();
    const editor = aiTabs(['catalog.manage', 'content.manage']).map((t) => t.label);
    expect(editor).toEqual(['Overview', 'Product content', 'Image analysis']);
    const links = navigation(['messages.manage']).flatMap((group) =>
      group.links.map((l) => l.href),
    );
    expect(links).toContain('/manage/ai');
    expect(links).not.toContain('/manage/data');
  });
});

describe('ProductAssistant', () => {
  it('generates a labelled draft and applies only the selected, reviewed fields', async () => {
    const user = userEvent.setup();
    const onApplied = vi.fn();
    api.post.mockImplementation((path: string) => {
      if (path.endsWith('/requests/')) return Promise.resolve(request());
      if (path.endsWith('/apply/'))
        return Promise.resolve(request({ review_status: 'applied', applied_fields: ['summary'] }));
      return Promise.reject(new Error(`unexpected ${path}`));
    });
    render(<ProductAssistant product={PRODUCT} canBangla onApplied={onApplied} />);
    await user.click(screen.getByRole('button', { name: 'Generate draft' }));
    expect(await screen.findByText('Test output, not written by AI')).toBeInTheDocument();
    expect(screen.getByText(/Material/)).toBeInTheDocument();
    const [created] = api.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(created).toBe('/api/manage/ai/requests/');
    // The second "Product name" checkbox belongs to the draft (the first chooses what to write).
    await user.click(screen.getAllByRole('checkbox', { name: 'Product name' })[1]!);
    const summary = screen.getByLabelText('Draft summary');
    await user.clear(summary);
    await user.type(summary, 'Soft cotton tee.');
    await user.click(screen.getByRole('button', { name: 'Apply selected to product' }));
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    const apply = api.post.mock.calls.find(([path]) => String(path).endsWith('/apply/'));
    expect(apply?.[1]).toEqual({ values: { summary: 'Soft cotton tee.' }, overwrite: false });
  });

  it('asks before replacing existing content', async () => {
    const user = userEvent.setup();
    let applies = 0;
    api.post.mockImplementation((path: string, body: { overwrite?: boolean }) => {
      if (path.endsWith('/requests/')) return Promise.resolve(request());
      applies += 1;
      if (!body.overwrite)
        return Promise.reject(new ApiError(409, 'ai_overwrite_needs_confirmation', 'Confirm'));
      return Promise.resolve(request({ review_status: 'applied' }));
    });
    render(
      <ProductAssistant product={{ ...PRODUCT, summary: 'Old' }} canBangla onApplied={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: 'Generate draft' }));
    await screen.findByText('Test output, not written by AI');
    await user.click(screen.getByRole('button', { name: 'Apply selected to product' }));
    expect(await screen.findByText('Replace existing content?')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Replace' }));
    await waitFor(() => expect(applies).toBe(2));
  });

  it('shows a failure honestly', async () => {
    const user = userEvent.setup();
    api.post.mockResolvedValue(
      request({ status: 'failed', result: null, error_text: 'GroqCloud refused the API key.' }),
    );
    render(<ProductAssistant product={PRODUCT} canBangla onApplied={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Generate draft' }));
    expect(await screen.findByText('GroqCloud refused the API key.')).toBeInTheDocument();
  });
});

describe('InboxAssistant', () => {
  it('fills the reply box but never sends a message', async () => {
    const user = userEvent.setup();
    const onUseReply = vi.fn();
    const reply = request({
      feature: 'support_reply',
      subject_type: 'conversation',
      result: {
        reply: 'Your order is being packed.',
        language: 'en',
        topic: 'order_status',
        next_action: 'Check the courier booking.',
        needs_verification: ['Delivery date'],
        verified_facts: ['Channel: WhatsApp'],
      },
    });
    api.post.mockImplementation((path: string) => {
      if (path.endsWith('/requests/')) return Promise.resolve(reply);
      if (path.endsWith('/review/')) return Promise.resolve({ ...reply, review_status: 'used' });
      return Promise.reject(new Error(`unexpected ${path}`));
    });
    render(<InboxAssistant conversationId="c1" onUseReply={onUseReply} />);
    await user.click(screen.getByRole('button', { name: 'AI assist' }));
    await user.click(screen.getByRole('button', { name: 'Suggest reply' }));
    expect(await screen.findByText('Your order is being packed.')).toBeInTheDocument();
    expect(screen.getByText(/Delivery date/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Use in reply box' }));
    expect(onUseReply).toHaveBeenCalledWith('Your order is being packed.');
    const paths = api.post.mock.calls.map(([path]) => String(path));
    expect(paths.some((path) => path.includes('/inbox/'))).toBe(false);
  });
});
