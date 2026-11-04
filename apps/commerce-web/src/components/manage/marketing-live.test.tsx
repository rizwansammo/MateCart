import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { LiveVisitors } from './marketing';

const live = {
  window_start: '2026-10-01T12:01:00Z',
  exact_start: '2026-10-01T12:00:30Z',
  now: '2026-10-02T12:00:30Z',
  unique_visitors: 4,
  page_views: 10,
  consented_page_views: 5,
  product_views: 3,
  add_to_cart: 1,
  checkout_started: 1,
  storefront_orders: 1,
  converting_visitors: 1,
  conversion_rate: '25.0',
  consent_coverage: '50.0',
};

describe('rolling 24-hour visitors card', () => {
  it('labels exact figures and per-minute counts separately, with the counting start', () => {
    render(<LiveVisitors live={live} timeZone="Asia/Dhaka" />);

    expect(screen.getByText(/Exact: visitors who allowed analytics/)).toBeInTheDocument();
    expect(screen.getByText(/Exact: orders placed in the last 24 hours/)).toBeInTheDocument();
    expect(screen.getByText(/Per-minute count, all visitors/)).toBeInTheDocument();
    // The counters' window start is shown in store time (12:01 UTC = 18:01 in Dhaka).
    expect(screen.getByText(/first whole minute in the window/).textContent).toMatch(/18:01|6:01/);
    expect(screen.queryByText(/current hour and the 23 before/)).not.toBeInTheDocument();
  });
});
