import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StaffSession } from '@matecart/shared-contracts';

import { DataWorkspace, JOB_POLL_MS } from './data';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  upload: vi.fn(),
}));

vi.mock('@/lib/auth-client', () => ({ authApi: api }));

const JOB_ID = '00000000-0000-7000-8000-0000000000a1';
const ITEM_ID = '00000000-0000-7000-8000-0000000000b1';

const SESSION: StaffSession = {
  user: {
    id: '00000000-0000-7000-8000-000000000001',
    email: 'owner@example.com',
    full_name: 'Owner',
  },
  role: 'owner',
  role_label: 'Owner',
  permissions: [
    'catalog.manage',
    'store.settings',
    'customers.view',
    'orders.manage',
    'promotions.manage',
  ],
  store_name: 'Shop',
  store_preset: '',
  store_country: 'BD',
  store_currency: 'BDT',
  store_timezone: 'Asia/Dhaka',
};

function overview(overrides: Record<string, unknown> = {}) {
  return {
    can_import: true,
    can_migrate: true,
    can_view_customers: true,
    can_view_orders: true,
    simulator_allowed: false,
    template_fields: [
      { key: 'handle', name: 'Handle' },
      { key: 'sku', name: 'SKU' },
    ],
    exports: [
      {
        key: 'products',
        name: 'Products',
        sensitive: false,
        formats: ['csv', 'jsonl'],
        allowed: true,
      },
      { key: 'customers', name: 'Customers', sensitive: true, formats: ['csv'], allowed: true },
    ],
    store_currency: 'BDT',
    max_upload_bytes: 10 * 1024 * 1024,
    ...overrides,
  };
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: JOB_ID,
    kind: 'import',
    source: 'file',
    status: 'review',
    entity: 'products',
    file_format: 'csv',
    scopes: [],
    conflict_strategy: 'manual',
    options: {},
    summary: {
      total: 4,
      valid: 3,
      invalid: 1,
      to_create: 2,
      to_update: 0,
      to_skip: 0,
      conflicts: 1,
      undecided: 0,
      with_warnings: 0,
    },
    total_items: 4,
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
    created_at: '2026-11-04T05:00:00Z',
    connection_name: '',
    connection_simulated: false,
    ...overrides,
  };
}

const CONFLICT_ITEM = {
  id: ITEM_ID,
  position: 3,
  object_type: 'product',
  external_id: '',
  key: 'TEE-M',
  title: 'Cotton Tee',
  action: 'conflict',
  status: 'pending',
  decision: 'skip',
  messages: [{ level: 'warning', text: 'Image address is not https.' }],
  existing_id: '00000000-0000-7000-8000-0000000000c1',
  local_id: null,
  created_local: false,
  preview: {},
};

function page(results: unknown[]) {
  return { count: results.length, page: 1, page_size: 25, results };
}

function routes(current: { job: Record<string, unknown>; items?: unknown[]; overview?: unknown }) {
  api.get.mockImplementation((path: string) => {
    if (path === '/api/manage/data/') return Promise.resolve(current.overview ?? overview());
    if (path.startsWith(`/api/manage/data/jobs/${JOB_ID}/items/`))
      return Promise.resolve(page(current.items ?? [CONFLICT_ITEM]));
    if (path === `/api/manage/data/jobs/${JOB_ID}/`) return Promise.resolve(current.job);
    if (path.startsWith('/api/manage/data/jobs/')) return Promise.resolve(page([]));
    return Promise.resolve(page([]));
  });
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('data workspace', () => {
  it('shows the import form with strategies, options and the template', async () => {
    routes({ job: job() });
    render(<DataWorkspace path="data" session={SESSION} />);
    const strategy = await screen.findByLabelText('When a product already exists');
    const options = within(strategy)
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(options).toEqual([
      'Create new, skip existing',
      'Create new, update existing',
      'Decide each existing one',
    ]);
    expect(screen.getByRole('link', { name: 'Download the CSV template' })).toHaveAttribute(
      'href',
      '/api/manage/data/template/',
    );
    expect(screen.getByText('Template columns (2)')).toBeVisible();
    expect(screen.getByLabelText('Set opening stock for new products')).toBeChecked();
    // Prices of existing products cannot change while existing products are skipped.
    expect(screen.getByLabelText('Update prices of existing products')).toBeDisabled();
    expect(screen.getByRole('link', { name: 'Import' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Imported history' })).toBeVisible();
  });

  it('hides the history tab when the role may not see customers or orders', async () => {
    routes({
      job: job(),
      overview: overview({ can_view_customers: false, can_view_orders: false }),
    });
    render(<DataWorkspace path="data" session={SESSION} />);
    expect(await screen.findByRole('link', { name: 'Import' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'Store migration' })).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Imported history' })).toBeNull();
  });

  it('refuses a section the role cannot use', async () => {
    routes({
      job: job(),
      overview: overview({ can_view_customers: false, can_view_orders: false }),
    });
    render(<DataWorkspace path="data/history" session={SESSION} />);
    expect(await screen.findByText('Your role cannot use this part of data tools')).toBeVisible();
    expect(api.get).not.toHaveBeenCalledWith(expect.stringContaining('/history/'));
  });

  it('shows a review preview and approves only after confirmation', async () => {
    const user = userEvent.setup();
    routes({ job: job() });
    api.post.mockResolvedValue(
      job({ status: 'queued', approved_at: '2026-11-04T05:10:00Z', approved_by_name: 'Owner' }),
    );
    render(<DataWorkspace path={`data/jobs/${JOB_ID}`} session={SESSION} />);
    expect(
      await screen.findByText('Preview ready — nothing has been imported yet; review and approve'),
    ).toBeVisible();
    expect(await screen.findByText('Cotton Tee')).toBeVisible();
    expect(screen.getByText('Image address is not https.')).toHaveClass('text-warning-fg');

    await user.click(screen.getByRole('button', { name: 'Approve and import' }));
    const dialog = screen.getByRole('dialog', { name: 'Approve and import' });
    expect(within(dialog).getByText('2 new records created')).toBeVisible();
    expect(api.post).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Approve and start' }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(`/api/manage/data/jobs/${JOB_ID}/approve/`),
    );
    expect(await screen.findByText('Approved. The import has started.')).toBeVisible();
    expect(screen.getByText('Importing')).toBeVisible();
  });

  it('blocks approval while items still need a decision', async () => {
    routes({
      job: job({
        summary: { ...job().summary, undecided: 1 },
      }),
      items: [{ ...CONFLICT_ITEM, decision: '' }],
    });
    render(<DataWorkspace path={`data/jobs/${JOB_ID}`} session={SESSION} />);
    expect(await screen.findByRole('button', { name: 'Approve and import' })).toBeDisabled();
    expect(
      screen.getByText('Decide every item that needs a decision first (1 left).'),
    ).toBeVisible();
  });

  it('posts a decision for an item that needs one', async () => {
    const user = userEvent.setup();
    routes({ job: job() });
    api.post.mockResolvedValue(job({ summary: { ...job().summary, to_update: 1 } }));
    render(<DataWorkspace path={`data/jobs/${JOB_ID}`} session={SESSION} />);
    const row = (await screen.findByText('Cotton Tee')).closest('tr');
    expect(row).not.toBeNull();
    const skip = within(row as HTMLElement).getByRole('button', { name: 'Skip' });
    expect(skip).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(row as HTMLElement).getByRole('button', { name: 'Update' }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(`/api/manage/data/jobs/${JOB_ID}/decisions/`, {
        decisions: { [ITEM_ID]: 'update' },
      }),
    );
  });

  it('filters items by what will happen to them', async () => {
    const user = userEvent.setup();
    routes({ job: job() });
    render(<DataWorkspace path={`data/jobs/${JOB_ID}`} session={SESSION} />);
    await screen.findByText('Cotton Tee');
    await user.click(screen.getByRole('button', { name: 'Needs decision' }));
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        `/api/manage/data/jobs/${JOB_ID}/items/?action=conflict&page=1`,
      ),
    );
  });

  it('polls a running job every two seconds and stops once it has finished', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const current = {
      job: job({
        status: 'running',
        approved_at: '2026-11-04T05:10:00Z',
        processed_items: 1,
      }) as Record<string, unknown>,
    };
    routes(current);
    const { unmount } = render(<DataWorkspace path={`data/jobs/${JOB_ID}`} session={SESSION} />);
    expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '1');
    expect(screen.getByText('1 of 4 processed')).toBeVisible();
    const jobCalls = () =>
      api.get.mock.calls.filter(([path]) => path === `/api/manage/data/jobs/${JOB_ID}/`).length;
    expect(jobCalls()).toBe(1);

    current.job = job({
      status: 'completed',
      approved_at: '2026-11-04T05:10:00Z',
      processed_items: 4,
      finished_at: '2026-11-04T05:12:00Z',
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(JOB_POLL_MS);
    });
    expect(await screen.findByText('Every approved record was processed.')).toBeVisible();
    expect(jobCalls()).toBe(2);
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.getByRole('button', { name: 'Reverse import' })).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 3);
    });
    expect(jobCalls()).toBe(2);
    unmount();
  });
});

describe('import column mapping helpers', () => {
  it('reads quoted CSV headers and matches columns like the server', async () => {
    const { autoMapping, csvHeader } = await import('./data');
    const headers = csvHeader('﻿Title,"Variant SKU",Price,"Fabric, main",Extra\r\nx,y,z\n');
    expect(headers).toEqual(['Title', 'Variant SKU', 'Price', 'Fabric, main', 'Extra']);
    const mapping = autoMapping(headers, [
      { key: 'name', aliases: ['name', 'title'] },
      { key: 'sku', aliases: ['sku', 'variant_sku'] },
      { key: 'price', aliases: ['price'] },
    ]);
    expect(mapping).toEqual({ name: 'Title', sku: 'Variant SKU', price: 'Price' });
  });
});
