import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resetPixelForTests } from '@/lib/meta-pixel';
import { messages } from '@/lib/i18n';
import { summary } from '@/test/fixtures';

import { ProductCard } from './product-card';
import { TrackingProvider, resetTrackingForTests } from './tracking';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  upload: vi.fn(),
}));
const route = vi.hoisted(() => ({ path: '/' }));

vi.mock('@/lib/auth-client', () => ({ authApi: api }));
vi.mock('next/navigation', () => ({
  usePathname: () => route.path,
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
}));

const ID = 'a'.repeat(32);

function setConsent(value: string | null) {
  document.cookie = value ? `mc_consent=${value}; Path=/` : 'mc_consent=; Max-Age=0; Path=/';
}

function metaScripts() {
  return document.querySelectorAll('script[data-matecart="meta-pixel"]');
}

beforeEach(() => {
  resetTrackingForTests();
  resetPixelForTests();
  for (const name of ['mc_consent', 'mc_vid', 'mc_src'])
    document.cookie = `${name}=; Max-Age=0; Path=/`;
  document.querySelectorAll('script').forEach((node) => node.remove());
  api.post.mockResolvedValue(undefined);
  route.path = '/';
});
afterEach(() => {
  vi.clearAllMocks();
});

describe('consent banner', () => {
  it('asks before anything optional runs and offers equal choices', async () => {
    render(
      <TrackingProvider config={{ analytics: true, meta_pixel_id: '1234567890' }} lang="en" />,
    );
    expect(await screen.findByRole('dialog', { name: 'Your privacy choices' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Accept all' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Reject optional' })).toBeVisible();
    // No decision: no Pixel, no visitor id; the page view carries no identifier.
    expect(metaScripts()).toHaveLength(0);
    expect(document.cookie).not.toContain('mc_vid');
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/analytics/events/', { name: 'page_view' }),
    );
  });

  it('starts preferences with optional categories switched off and saves the choice', async () => {
    const user = userEvent.setup();
    api.post.mockImplementation((path: string) =>
      Promise.resolve(
        path === '/api/consent/'
          ? { analytics: true, marketing: false, policy_version: '1' }
          : undefined,
      ),
    );
    render(<TrackingProvider config={{ analytics: true, meta_pixel_id: null }} lang="en" />);
    await user.click(await screen.findByRole('button', { name: 'Manage preferences' }));
    const analytics = screen.getByRole('checkbox', { name: /Analytics/ });
    const marketing = screen.getByRole('checkbox', { name: /Marketing/ });
    expect(analytics).not.toBeChecked();
    expect(marketing).not.toBeChecked();
    await user.click(analytics);
    await user.click(screen.getByRole('button', { name: 'Save preferences' }));
    expect(api.post).toHaveBeenCalledWith('/api/consent/', {
      analytics: true,
      marketing: false,
      origin: 'preferences',
    });
  });

  it('posts a banner decision when accepting everything', async () => {
    const user = userEvent.setup();
    api.post.mockResolvedValue({ analytics: true, marketing: true, policy_version: '1' });
    render(<TrackingProvider config={{ analytics: false, meta_pixel_id: null }} lang="bn" />);
    await user.click(await screen.findByRole('button', { name: messages('bn').acceptAll }));
    expect(api.post).toHaveBeenCalledWith('/api/consent/', {
      analytics: true,
      marketing: true,
      origin: 'banner',
    });
  });
});

describe('tracking provider', () => {
  it('never loads Meta without marketing consent', async () => {
    setConsent(`1:10:${ID}`);
    render(
      <TrackingProvider config={{ analytics: true, meta_pixel_id: '1234567890' }} lang="en" />,
    );
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(metaScripts()).toHaveLength(0);
    expect(window.fbq).toBeUndefined();
    // Analytics consent: a random visitor id exists and no banner is shown.
    expect(document.cookie).toMatch(/mc_vid=[0-9a-f]{32}/);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('never loads Meta when the store has no Pixel id', async () => {
    setConsent(`1:11:${ID}`);
    render(<TrackingProvider config={{ analytics: false, meta_pixel_id: null }} lang="en" />);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(metaScripts()).toHaveLength(0);
    expect(api.post).not.toHaveBeenCalled();
  });

  it('loads the Pixel once with marketing consent and counts each page once', async () => {
    setConsent(`1:11:${ID}`);
    const { rerender } = render(
      <TrackingProvider config={{ analytics: true, meta_pixel_id: '1234567890' }} lang="en" />,
    );
    await waitFor(() => expect(metaScripts()).toHaveLength(1));
    rerender(
      <TrackingProvider config={{ analytics: true, meta_pixel_id: '1234567890' }} lang="en" />,
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(metaScripts()).toHaveLength(1);
    route.path = '/products/shirt';
    rerender(
      <TrackingProvider config={{ analytics: true, meta_pixel_id: '1234567890' }} lang="en" />,
    );
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  });
});

describe('promotional prices', () => {
  it('shows the genuine sale price, the struck regular price and the badge', () => {
    render(
      <ProductCard
        product={summary({
          price: {
            currency: 'BDT',
            min_amount: '200.0000',
            max_amount: '200.0000',
            regular_min_amount: '250.0000',
          },
          promotion: {
            kind: 'flash_sale',
            name: 'Eid flash',
            badge: 'Flash sale',
            ends_at: '2026-11-10T18:00:00Z',
          },
        })}
        variant="marketplace"
        lang="en"
        t={messages('en')}
      />,
    );
    expect(screen.getByText('৳200')).toBeVisible();
    expect(screen.getByText('৳250').closest('s')).not.toBeNull();
    expect(screen.getByText('Flash sale')).toBeVisible();
  });

  it('shows no badge or struck price without a promotion', () => {
    render(<ProductCard product={summary()} variant="marketplace" lang="en" t={messages('en')} />);
    expect(screen.queryByText('Flash sale')).toBeNull();
    expect(document.querySelector('s')).toBeNull();
  });
});
