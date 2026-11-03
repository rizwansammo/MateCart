import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { DashboardShell } from './auth-pages';

describe('DashboardShell', () => {
  it('renders only the given groups and marks the open page', () => {
    render(
      <DashboardShell
        realm="staff"
        name="Rahim Fashion"
        subtitle="Nadia · Manager"
        current="/manage/orders/0b0f6d3e-1f6c-4a8e-9c3d-2a1b0c9d8e7f"
        groups={[
          { links: [{ href: '/manage', label: 'Overview' }] },
          { heading: 'Sell', links: [{ href: '/manage/orders', label: 'Orders' }] },
        ]}
        onLogout={vi.fn()}
      >
        <p>Content</p>
      </DashboardShell>,
    );
    expect(screen.getByRole('link', { name: 'Orders' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current');
    expect(screen.getByText('Sell')).toBeInTheDocument();
    expect(screen.queryByText('Team & access')).not.toBeInTheDocument();
  });

  it('keeps the default navigation and toggles the phone menu', async () => {
    render(
      <DashboardShell realm="console" name="NetaMate" subtitle="Admin" onLogout={vi.fn()}>
        <p>Content</p>
      </DashboardShell>,
    );
    expect(screen.getByRole('link', { name: 'Overview' })).toBeInTheDocument();
    const menu = screen.getByRole('button', { name: 'Menu' });
    expect(menu).toHaveAttribute('aria-expanded', 'false');
    await userEvent.setup().click(menu);
    expect(screen.getByRole('button', { name: 'Close' })).toHaveAttribute('aria-expanded', 'true');
  });
});
