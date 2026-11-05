import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveChat } from '@/components/store/live-chat';
import { isManagePath, navigation, requiredPermission } from '@/lib/manage-routes';
import { InboxWorkspace } from './inbox';

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  upload: vi.fn(),
}));
const nav = vi.hoisted(() => ({ pathname: '/' }));

vi.mock('@/lib/auth-client', () => ({ authApi: api }));
vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname }));

/** A socket that never connects: the components must keep working over HTTP. */
class SilentSocket {
  static instances: SilentSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  sent: string[] = [];
  constructor(public url: string) {
    SilentSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
}

const UUID = '01a1a838-0000-7000-8000-0000000000aa';
const SESSION = {
  user: { id: 'staff-1', full_name: 'Owner One', email: 'o@example.test' },
  role: 'owner',
  role_label: 'Owner',
  permissions: ['messages.manage', 'orders.manage'],
  store_name: 'Kantha',
  store_preset: 'fashion',
  store_currency: 'BDT',
  store_country: 'BD',
  store_timezone: 'Asia/Dhaka',
} as unknown as Parameters<typeof InboxWorkspace>[0]['session'];

function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: UUID,
    channel: 'whatsapp',
    status: 'open',
    version: 1,
    display_name: 'Sadia Rahman',
    connection_name: 'Kantha & Co',
    is_simulated: false,
    assignee: null,
    last_message_at: '2026-11-05T09:00:00Z',
    last_preview: 'Is size M available?',
    last_direction: 'inbound',
    inbound_count: 1,
    unread: 1,
    customer_linked: false,
    created_at: '2026-11-05T09:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  SilentSocket.instances = [];
  vi.stubGlobal('WebSocket', SilentSocket);
  nav.pathname = '/';
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('inbox routes', () => {
  it('needs messages access, and order access to create orders', () => {
    expect(requiredPermission('inbox')).toBe('messages.manage');
    expect(requiredPermission('inbox/analytics')).toBe('messages.manage');
    expect(requiredPermission(`inbox/${UUID}`)).toBe('messages.manage');
    expect(requiredPermission(`inbox/${UUID}/order`)).toBe('orders.manage');
    expect(requiredPermission('settings/social')).toBe('store.settings');
    expect(isManagePath('inbox/not-a-uuid')).toBe(false);
    const labels = (permissions: string[]) =>
      navigation(permissions).flatMap((group) => group.links.map((link) => link.label));
    expect(labels(['messages.manage'])).toContain('Inbox');
    expect(labels(['catalog.manage'])).not.toContain('Inbox');
  });
});

describe('website chat widget', () => {
  const config = {
    config: {
      enabled: true,
      welcome: 'Hi! Ask us anything.',
      button_label: 'Chat with us',
      away_message: 'We are away right now.',
      languages: ['en', 'bn'],
      position: 'right',
      online: false,
      within_hours: true,
    },
    conversation_status: null,
    messages: [],
    signed_in: false,
    guest_chat_linkable: false,
  };

  it('is not shown on checkout pages', () => {
    nav.pathname = '/checkout/abc';
    const { container } = render(<LiveChat lang="en" />);
    expect(container).toBeEmptyDOMElement();
    expect(api.get).not.toHaveBeenCalled();
  });

  it('shows the honest away state and sends the first message over HTTP once', async () => {
    api.get.mockResolvedValue(config);
    api.post.mockImplementation(
      async (_path: string, body: { client_key: string; body: string }) => ({
        id: '01a1a838-0000-7000-8000-000000000001',
        seq: 1,
        direction: 'inbound',
        kind: 'text',
        body: body.body,
        created_at: new Date().toISOString(),
        client_key: body.client_key,
        sender_name: '',
        product: null,
        attachment: null,
      }),
    );
    const user = userEvent.setup();
    render(<LiveChat lang="en" />);
    await user.click(await screen.findByRole('button', { name: 'Chat with us' }));
    const dialog = screen.getByRole('dialog', { name: 'Chat with us' });
    expect(within(dialog).getByText('We are away right now.')).toBeInTheDocument();
    expect(within(dialog).getByText('Away')).toBeInTheDocument();
    // No socket before the visitor has a conversation.
    expect(SilentSocket.instances).toHaveLength(0);
    await user.type(within(dialog).getByLabelText('Write a message'), '<b>bold?</b>{Enter}');
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [path, body] = api.post.mock.calls[0] as [string, { client_key: string; body: string }];
    expect(path).toBe('/api/chat/messages/');
    expect(body.client_key).toMatch(/^chat-/);
    // Markup is shown as text, never interpreted.
    expect(await within(dialog).findByText('<b>bold?</b>')).toBeInTheDocument();
    expect(dialog.querySelector('b')).toBeNull();
    // After the first message the widget connects for live replies.
    expect(SilentSocket.instances[0]?.url).toMatch(/\/api\/chat\/ws\/$/);
  });
});

describe('unified inbox', () => {
  function respond(policy: Record<string, unknown>) {
    api.get.mockImplementation(async (path: string) => {
      if (
        path.startsWith('/api/manage/inbox/conversations/?') ||
        path === '/api/manage/inbox/conversations/'
      )
        return { results: [summary()], next_cursor: null, unread_conversations: 1 };
      if (path === '/api/manage/inbox/team/')
        return [{ id: 'staff-1', name: 'Owner One', role: 'owner' }];
      if (path.endsWith('/thread/'))
        return {
          messages: [
            {
              id: 'm-1',
              seq: 1,
              direction: 'inbound',
              kind: 'text',
              body: 'Is size M available?',
              created_at: '2026-11-05T09:00:00Z',
              client_key: '',
              sender_name: '',
              product: null,
              attachment: null,
              delivery_status: 'received',
              status_changed_at: null,
              error_code: '',
              provider_timestamp: null,
              sender: null,
              template_name: '',
              template_language: '',
              reply_tag: '',
              pricing_category: '',
              attempts: 0,
            },
          ],
          notes: [
            {
              id: 'n-1',
              body: 'Repeat customer',
              created_at: '2026-11-05T09:01:00Z',
              author: { id: 'staff-1', name: 'Owner One' },
            },
          ],
          events: [],
          has_more: false,
        };
      if (path === `/api/manage/inbox/conversations/${UUID}/`)
        return {
          conversation: summary({ unread: 0 }),
          participant: {
            id: 'p-1',
            display_name: 'Sadia Rahman',
            channel: 'whatsapp',
            phone: '+8801711000001',
            customer_id: null,
            customer_linked_at: null,
            template_opt_in_at: null,
            opted_out_at: null,
          },
          policy: {
            can_reply: true,
            reason: '',
            message: '',
            tag: '',
            window_ends_at: '2026-11-06T09:00:00Z',
            templates_allowed: false,
            template_reason: 'Record the customer’s agreement first.',
            ...policy,
          },
          orders: [],
          cases: [],
          recent: [],
          can_create_orders: true,
          visitor_read_seq: 0,
        };
      throw new Error(`Unexpected GET ${path}`);
    });
    api.post.mockResolvedValue(undefined);
  }

  it('lists conversations, opens a thread and keeps internal notes visibly separate', async () => {
    respond({});
    render(<InboxWorkspace path={`inbox/${UUID}`} session={SESSION} />);
    expect(await screen.findByText('Repeat customer')).toBeInTheDocument();
    expect(screen.getByText(/Internal note · only your team sees this/)).toBeInTheDocument();
    expect(screen.getAllByText('Is size M available?').length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(`/api/manage/inbox/conversations/${UUID}/read/`),
    );
    expect(screen.getByRole('link', { name: 'Create order' })).toHaveAttribute(
      'href',
      `/manage/inbox/${UUID}/order`,
    );
    expect(SilentSocket.instances[0]?.url).toMatch(/\/api\/manage\/ws\/inbox\/$/);
  });

  it('explains a closed provider window instead of offering a normal send', async () => {
    respond({
      can_reply: false,
      reason: 'template_required',
      message: 'The 24-hour WhatsApp service window has ended. Send an approved template instead.',
      window_ends_at: null,
    });
    render(<InboxWorkspace path={`inbox/${UUID}`} session={SESSION} />);
    expect(await screen.findByText(/service window has ended/)).toBeInTheDocument();
    expect(screen.getByLabelText('Reply to the customer')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Approved template' })).toBeDisabled();
  });
});
