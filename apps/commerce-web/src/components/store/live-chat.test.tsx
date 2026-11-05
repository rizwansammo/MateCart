import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveChat } from './live-chat';

/**
 * Website chat across conversations (Phase 14.1, D-100): message sequences restart in every
 * conversation, so the widget must never mix threads, skip messages of a new conversation or
 * send receipts for the wrong one, over WebSocket or the HTTP fallback.
 */

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  upload: vi.fn(),
}));
vi.mock('@/lib/auth-client', () => ({ authApi: api }));
vi.mock('next/navigation', () => ({ usePathname: () => '/' }));

type Frame = Record<string, unknown>;

/** A socket the test opens, feeds and drops. */
class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  sent: Frame[] = [];
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(JSON.parse(data) as Frame);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    act(() => this.onopen?.());
  }
  push(frame: Frame) {
    act(() => this.onmessage?.({ data: JSON.stringify(frame) }));
  }
  drop() {
    this.readyState = 3;
    act(() => this.onclose?.({ code: 1006 }));
  }
  frames(type: string): Frame[] {
    return this.sent.filter((f) => f.type === type);
  }
}

const OLD = '01a1a838-0000-7000-8000-0000000000c1';
const NEW = '01a1a838-0000-7000-8000-0000000000c2';

function msg(
  conversation: string,
  seq: number,
  direction: 'inbound' | 'outbound',
  body: string,
  client_key = '',
) {
  return {
    id: `${conversation.slice(-2)}-${seq}`,
    conversation,
    seq,
    direction,
    kind: 'text',
    body,
    created_at: new Date().toISOString(),
    client_key,
    sender_name: direction === 'outbound' ? 'Hasan' : '',
    product: null,
    attachment: null,
  };
}

function state(conversation: string | null, status: string | null, messages: unknown[]) {
  return {
    config: {
      enabled: true,
      welcome: 'Hi! Ask us anything.',
      button_label: 'Chat with us',
      away_message: 'We are away right now.',
      languages: ['en'],
      position: 'right',
      online: true,
      within_hours: true,
    },
    conversation_id: conversation,
    conversation_status: status,
    messages,
    signed_in: false,
    guest_chat_linkable: false,
  };
}

const OLD_THREAD = [
  msg(OLD, 1, 'inbound', 'Is the kurti in stock?'),
  msg(OLD, 2, 'outbound', 'Yes, size M.'),
];

function live(): FakeSocket {
  const socket = FakeSocket.instances.at(-1);
  if (!socket) throw new Error('no socket');
  return socket;
}

async function openChat() {
  const user = userEvent.setup();
  render(<LiveChat lang="en" />);
  await user.click(await screen.findByRole('button', { name: /^Chat with us/ }));
  return { user, dialog: screen.getByRole('dialog', { name: 'Chat with us' }) };
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  FakeSocket.instances = [];
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('website chat across conversations', () => {
  it('switches to the new conversation after closing, without mixing or skipping', async () => {
    api.get.mockResolvedValue(state(OLD, 'closed', OLD_THREAD));
    const { user, dialog } = await openChat();
    expect(await within(dialog).findByText('Yes, size M.')).toBeInTheDocument();
    expect(within(dialog).getByText(/conversation was closed/i)).toBeInTheDocument();
    const first = live();
    first.open();
    expect(first.frames('sync')).toEqual([{ type: 'sync', conversation: OLD, after_seq: 2 }]);
    first.push({ type: 'sync', conversation: OLD, status: 'closed', messages: [], online: true });

    await user.type(within(dialog).getByLabelText('Write a message'), 'A new question{Enter}');
    const [sent] = first.frames('message');
    expect(sent?.body).toBe('A new question');
    // The server started a new conversation: its seq 1 is not merged into the closed thread.
    first.push({
      type: 'ack',
      client_key: sent?.client_key,
      message: msg(NEW, 1, 'inbound', 'A new question', String(sent?.client_key)),
    });
    expect(first.frames('sync').at(-1)).toEqual({ type: 'sync', conversation: OLD, after_seq: 2 });
    first.push({
      type: 'sync',
      conversation: NEW,
      status: 'open',
      messages: [msg(NEW, 1, 'inbound', 'A new question', String(sent?.client_key))],
      online: true,
    });
    await waitFor(() => expect(within(dialog).queryByText('Yes, size M.')).toBeNull());
    expect(within(dialog).queryByText('Is the kurti in stock?')).toBeNull();
    expect(within(dialog).queryByText(/conversation was closed/i)).toBeNull();
    expect(within(dialog).getAllByText('A new question')).toHaveLength(1); // no duplicate

    // A reply in the new conversation: receipts name it, with its own sequence.
    first.push({ type: 'message', message: msg(NEW, 2, 'outbound', 'Welcome back!') });
    expect(await within(dialog).findByText('Welcome back!')).toBeInTheDocument();
    expect(first.frames('delivered').at(-1)).toEqual({
      type: 'delivered',
      conversation: NEW,
      seq: 2,
    });
    await waitFor(() =>
      expect(first.frames('read').at(-1)).toEqual({ type: 'read', conversation: NEW, seq: 2 }),
    );

    // Reconnecting resumes the new conversation from its own last sequence.
    first.drop();
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 4000 });
    const second = live();
    second.open();
    expect(second.frames('sync')).toEqual([{ type: 'sync', conversation: NEW, after_seq: 2 }]);
  });

  it('ignores late frames from the previous conversation', async () => {
    api.get.mockResolvedValue(
      state(NEW, 'open', [msg(NEW, 1, 'inbound', 'A new question', 'chat-key-new-1')]),
    );
    const { dialog } = await openChat();
    expect(await within(dialog).findByText('A new question')).toBeInTheDocument();
    const socket = live();
    socket.open();
    socket.push({ type: 'sync', conversation: NEW, status: 'open', messages: [], online: true });
    const syncs = socket.frames('sync').length;
    // A delayed reply and status change from the closed conversation arrive late.
    socket.push({ type: 'message', message: msg(OLD, 3, 'outbound', 'Late old reply') });
    socket.push({ type: 'conversation', conversation: OLD, status: 'closed' });
    expect(within(dialog).queryByText('Late old reply')).toBeNull();
    expect(within(dialog).queryByText(/conversation was closed/i)).toBeNull();
    // The widget asks which conversation is current instead of guessing.
    expect(socket.frames('sync').length).toBeGreaterThan(syncs);
    expect(socket.frames('sync').at(-1)).toEqual({ type: 'sync', conversation: NEW, after_seq: 1 });
    // Status changes of the shown conversation still apply.
    socket.push({ type: 'conversation', conversation: NEW, status: 'closed' });
    expect(await within(dialog).findByText(/conversation was closed/i)).toBeInTheDocument();
  });

  it('keeps the HTTP fallback consistent when a new conversation starts', async () => {
    let posted = '';
    api.get
      .mockResolvedValueOnce(state(OLD, 'closed', OLD_THREAD))
      .mockImplementation(async () =>
        state(NEW, 'open', [msg(NEW, 1, 'inbound', 'Sent over HTTP', posted)]),
      );
    api.post.mockImplementation(async (_path: string, body: { client_key: string }) => {
      posted = body.client_key;
      return msg(NEW, 1, 'inbound', 'Sent over HTTP', body.client_key);
    });
    const { user, dialog } = await openChat();
    expect(await within(dialog).findByText('Yes, size M.')).toBeInTheDocument();
    // The socket never opens: the message goes over HTTP.
    await user.type(within(dialog).getByLabelText('Write a message'), 'Sent over HTTP{Enter}');
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    // The answer belongs to another conversation, so the widget reloads the current one.
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(within(dialog).queryByText('Yes, size M.')).toBeNull());
    expect(within(dialog).getAllByText('Sent over HTTP')).toHaveLength(1);
    expect(within(dialog).queryByText(/conversation was closed/i)).toBeNull();
  });

  it('counts each store reply once as unread', async () => {
    api.get.mockResolvedValue(
      state(NEW, 'open', [msg(NEW, 1, 'inbound', 'A new question', 'chat-key-new-1')]),
    );
    render(<LiveChat lang="en" />);
    await screen.findByRole('button', { name: 'Chat with us' });
    const socket = live();
    socket.open();
    const reply = { type: 'message', message: msg(NEW, 2, 'outbound', 'Welcome back!') };
    socket.push(reply);
    socket.push(reply); // the same event again (e.g. sync after a reconnect)
    socket.push({
      type: 'sync',
      conversation: NEW,
      status: 'open',
      messages: [reply.message],
      online: true,
    });
    expect(screen.getByRole('button', { name: 'Chat with us, 1 unread' })).toBeInTheDocument();
  });
});
