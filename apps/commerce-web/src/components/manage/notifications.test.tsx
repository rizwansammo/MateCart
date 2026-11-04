import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { UnsubscribeForm } from '@/components/customer-notifications';
import { messages } from '@/lib/i18n';
import { isManagePath, navigation, requiredPermission } from '@/lib/manage-routes';
import { MateMailCard, SmtpCard, TemplateEditor } from './notification-settings';
import { BELL_POLL_MS, NotificationBell, NotificationCenter } from './notifications';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  upload: vi.fn(),
}));

vi.mock('@/lib/auth-client', () => ({ authApi: api }));

const ITEM = {
  id: '00000000-0000-7000-8000-000000000001',
  kind: 'staff.order.new',
  category: 'operational',
  title: 'New order',
  body: 'Order ORD-000001 was placed.',
  link: '/manage/orders/00000000-0000-7000-8000-000000000002',
  created_at: '2026-11-04T05:00:00Z',
  read_at: null,
};

function page(results: unknown[]) {
  return { count: results.length, page: 1, page_size: 25, results };
}

const CONNECTION = {
  provider: 'smtp' as const,
  is_enabled: false,
  status: 'unverified' as const,
  display_name: '',
  smtp_host: 'smtp.example.com',
  smtp_port: 587,
  smtp_security: 'starttls' as const,
  smtp_username: 'shop@example.com',
  smtp_password_set: true,
  from_email: 'shop@example.com',
  from_name: 'Shop',
  reply_to: '',
  sender_verified: false,
  sender_code_pending: false,
  imap_host: '',
  imap_port: null,
  imap_security: 'tls' as const,
  imap_username: '',
  imap_password_set: false,
  imap_status: 'not_configured' as const,
  imap_verified_at: null,
  imap_error: '',
  matemail_tenant_id: '',
  matemail_authorization_url: '',
  matemail_request_expires_at: null,
  matemail_mailbox: '',
  matemail_scopes: [],
  matemail_token_set: false,
  last_verified_at: null,
  last_error: '',
};

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('notification bell', () => {
  it('shows the unread count and polls once a minute only while visible', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.get.mockResolvedValue({ unread: 3 });
    const { unmount } = render(<NotificationBell />);
    expect(await screen.findByRole('button', { name: 'Notifications, 3 unread' })).toBeVisible();
    expect(api.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(BELL_POLL_MS);
    });
    expect(api.get).toHaveBeenCalledTimes(2);

    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BELL_POLL_MS * 3);
    });
    expect(api.get).toHaveBeenCalledTimes(2);
    visibility.mockRestore();

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BELL_POLL_MS * 2);
    });
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('marks shown items read, closes with Escape and only links within the dashboard', async () => {
    const user = userEvent.setup();
    api.get.mockImplementation((path: string) =>
      Promise.resolve(
        path.includes('unread')
          ? { unread: 2 }
          : page([
              ITEM,
              {
                ...ITEM,
                id: '00000000-0000-7000-8000-000000000003',
                title: 'Phish',
                link: 'https://evil.test/manage',
              },
            ]),
      ),
    );
    api.post.mockResolvedValue({ unread: 0 });
    render(<NotificationBell />);
    await user.click(await screen.findByRole('button', { name: 'Notifications, 2 unread' }));
    expect(await screen.findByText('New order')).toBeVisible();
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/manage/notifications/inbox/read/', {
        ids: [ITEM.id, '00000000-0000-7000-8000-000000000003'],
      }),
    );
    expect(screen.getByText('New order').closest('a')).toHaveAttribute('href', ITEM.link);
    expect(screen.getByText('Phish').closest('a')).toBeNull();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Recent notifications' })).toBeNull();
  });
});

describe('notification center', () => {
  it('marks everything as read', async () => {
    const user = userEvent.setup();
    api.get.mockImplementation((path: string) =>
      Promise.resolve(path.includes('preferences') ? [] : page([ITEM])),
    );
    api.post.mockResolvedValue({ unread: 0 });
    render(<NotificationCenter />);
    expect(await screen.findByText('New order')).toBeVisible();
    await user.click(screen.getAllByRole('button', { name: 'Mark all as read' })[0]!);
    expect(api.post).toHaveBeenCalledWith('/api/manage/notifications/inbox/read/', { all: true });
    expect(await screen.findByText('Everything is marked as read.')).toBeVisible();
  });
});

describe('email settings', () => {
  it('never shows a saved password, only that one is saved', () => {
    render(
      <SmtpCard
        connection={CONNECTION}
        staffEmail="owner@example.test"
        onSave={vi.fn()}
        onAction={vi.fn()}
      />,
    );
    const field = screen.getByLabelText('Replace password');
    expect(field).toHaveValue('');
    expect(screen.getAllByText(/Password saved/).length).toBeGreaterThan(0);
  });

  it('shows MateMail as unavailable when the store has no MateMail instance', () => {
    render(
      <MateMailCard
        available={false}
        connection={{ ...CONNECTION, provider: 'matemail', status: 'not_configured' }}
        onStart={vi.fn()}
        onDisconnect={vi.fn()}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText('Unavailable')).toBeVisible();
    expect(screen.queryByLabelText('Integration Secret')).toBeNull();
  });

  it('renders template previews in a sandboxed frame', async () => {
    const user = userEvent.setup();
    api.post.mockResolvedValue({
      subject: 'Order received',
      text: 'Hi',
      html: '<p>Hi</p><script>alert(1)</script>',
      sms: '',
      sms_segments: 0,
      in_app_title: 'Thanks',
      in_app_body: 'Hi',
    });
    render(
      <TemplateEditor
        template={{
          key: 'order.placed',
          title: 'Order placed',
          category: 'transactional',
          audience: 'customer',
          variables: ['order_number'],
          supports_sms: false,
          locales: [
            {
              locale: 'en',
              customized: false,
              is_enabled: true,
              version: 0,
              subject: '',
              heading: '',
              body: '',
              sms: '',
              in_app_title: '',
              in_app_body: '',
              default_subject: 'Order {order_number} received',
              default_heading: '',
              default_body: '',
              default_sms: '',
            },
          ],
        }}
        locale="en"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByText('{order_number}')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    const frame = await screen.findByTitle('Email preview');
    expect(frame).toHaveAttribute('sandbox', '');
  });
});

describe('unsubscribe', () => {
  it('needs an explicit click, so opening the link does nothing', async () => {
    const user = userEvent.setup();
    api.post.mockResolvedValue({ unsubscribed: true });
    render(<UnsubscribeForm token="signed-token" t={messages('en')} />);
    expect(api.post).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    expect(api.post).toHaveBeenCalledWith('/api/notifications/unsubscribe/', {
      token: 'signed-token',
    });
    expect(await screen.findByText(/will not receive offers/)).toBeVisible();
  });
});

describe('notification routes', () => {
  it('gives every staff member an inbox and keeps settings to store settings access', () => {
    expect(isManagePath('notifications')).toBe(true);
    expect(requiredPermission('notifications')).toBeNull();
    for (const tab of ['', '/email', '/sms', '/templates', '/deliveries']) {
      expect(requiredPermission(`settings/notifications${tab}`)).toBe('store.settings');
    }
    expect(isManagePath('settings/notifications/secrets')).toBe(false);
    const labels = (permissions: string[]) =>
      navigation(permissions).flatMap((group) => group.links.map((link) => link.label));
    expect(labels(['catalog.manage', 'content.manage'])).toContain('Notifications');
  });
});
