'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  Channel,
  ConversationDetail,
  ConversationSummary,
  InboxEvent,
  InboxProduct,
  InboxTeamMember,
  Note,
  StaffMessage,
  StaffSession,
  Thread,
  TimelineEvent,
  WhatsAppTemplate,
} from '@matecart/shared-contracts';
import {
  conversationDetail,
  conversationPage,
  conversationSummary,
  inboxEvent,
  inboxProducts,
  inboxTeam,
  note as decodeNote,
  safeProductLink,
  staffMessage,
  thread as decodeThread,
  whatsAppTemplates,
} from '@matecart/shared-contracts';
import { Alert, ApiError, Button, Dialog, TextField, errorMessage } from '@matecart/shared-ui';
import { InboxAssistant } from './ai-assist';
import { AssistedOrderForm } from './assisted-order';
import { InboxAnalyticsView } from './inbox-analytics';
import {
  Badge,
  CASE_KIND,
  CASE_STATUS,
  Loader,
  ORDER_STATUS,
  PAYMENT_STATUS,
  SOURCE,
  SelectField,
  api,
  dateTime,
  label,
  money,
  newKey,
  query,
} from './kit';

/**
 * Unified Inbox (Phase 14). Website chat, Messenger, WhatsApp and Instagram in one place.
 *
 * The list and threads come from the API; the WebSocket only says what changed, and after any
 * reconnection the page reconciles with the server (no messages are kept only in the browser).
 * While the socket is down, the list refreshes on a bounded timer instead.
 */

export const CHANNEL_LABEL: Record<Channel, string> = {
  website: 'Website chat',
  messenger: 'Messenger',
  whatsapp: 'WhatsApp',
  instagram: 'Instagram',
};
const CHANNEL_MARK: Record<Channel, string> = {
  website: 'WEB',
  messenger: 'MSG',
  whatsapp: 'WA',
  instagram: 'IG',
};
const STATUS: Record<string, string> = {
  open: 'Open',
  pending: 'Pending',
  resolved: 'Resolved',
  closed: 'Closed',
};
const DELIVERY: Record<string, string> = {
  queued: 'Queued',
  sending: 'Sending',
  accepted: 'Sent',
  delivered: 'Delivered',
  read: 'Read',
  failed: 'Not sent',
  unknown: 'Outcome unknown',
};
const ERRORS: Record<string, string> = {
  connection_unavailable: 'The channel is not connected.',
  meta_190: 'The store’s access was revoked.',
  meta_131047: 'The 24-hour window had ended.',
  meta_131026: 'WhatsApp could not deliver to this number.',
  worker_lost: 'Sending was interrupted.',
  timeout: 'The provider did not answer in time.',
  network: 'The provider could not be reached.',
  // Refused when it was due to be sent: no longer allowed then, so it was never sent (D-099).
  conversation_closed: 'Not sent: the conversation was closed first.',
  channel_disabled: 'Not sent: the channel was switched off first.',
  connection_revoked: 'Not sent: the store’s access was revoked.',
  connection_suspended: 'Not sent: the provider suspended this account.',
  permission_missing: 'Not sent: a messaging permission is missing.',
  window_closed: 'Not sent: the reply window ended first.',
  template_required: 'Not sent: the 24-hour window ended first. Use a template.',
  opted_out: 'Not sent: the customer opted out first.',
  opt_in_missing: 'Not sent: the customer’s agreement is no longer recorded.',
  template_unavailable: 'Not sent: the template is no longer approved.',
  template_parameters_invalid: 'Not sent: the template’s values no longer match it.',
};
const POLL_MS = 30_000;
const HEARTBEAT_MS = 25_000;

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? '')
      .join('') || '?'
  );
}

function ago(iso: string | null): string {
  if (!iso) return '';
  const minutes = Math.floor((Date.now() - Date.parse(iso)) / 60_000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}h`;
  return dateTime(iso);
}

// --- WebSocket ------------------------------------------------------------------------------------

function useInboxSocket(onEvent: (event: InboxEvent) => void, onReconnect: () => void) {
  const [connected, setConnected] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const handler = useRef(onEvent);
  const reconnect = useRef(onReconnect);
  const watching = useRef<string | null>(null);
  useEffect(() => {
    handler.current = onEvent;
    reconnect.current = onReconnect;
  });

  useEffect(() => {
    let stopped = false;
    let backoff = 1000;
    let retry: number | null = null;
    let beat: number | null = null;
    let first = true;
    function open() {
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${scheme}://${window.location.host}/api/manage/ws/inbox/`);
      socket.current = ws;
      ws.onopen = () => {
        backoff = 1000;
        setConnected(true);
        if (watching.current) {
          ws.send(JSON.stringify({ type: 'watch', conversation: watching.current }));
        }
        if (!first) reconnect.current();
        first = false;
        beat = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
        }, HEARTBEAT_MS);
      };
      ws.onmessage = (message: MessageEvent<string>) => {
        try {
          const event = inboxEvent(JSON.parse(message.data));
          if (event) handler.current(event);
        } catch {
          // Malformed frames are ignored; the API stays authoritative.
        }
      };
      ws.onclose = (event: CloseEvent) => {
        if (beat) window.clearInterval(beat);
        // A socket replaced earlier (React remounts, reconnects) must not clear the live one.
        if (socket.current !== ws) return;
        socket.current = null;
        setConnected(false);
        if (stopped) return;
        if (event.code === 4401) {
          window.location.replace('/manage/sign-in');
          return;
        }
        retry = window.setTimeout(open, backoff + Math.random() * 500);
        backoff = Math.min(backoff * 2, 30_000);
      };
    }
    open();
    return () => {
      stopped = true;
      if (retry) window.clearTimeout(retry);
      if (beat) window.clearInterval(beat);
      socket.current?.close();
    };
  }, []);

  const send = useCallback((frame: Record<string, unknown>) => {
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
  }, []);
  const watch = useCallback(
    (id: string | null) => {
      watching.current = id;
      send(id ? { type: 'watch', conversation: id } : { type: 'unwatch' });
    },
    [send],
  );
  return { connected, send, watch };
}

// --- Workspace ------------------------------------------------------------------------------------

export function InboxWorkspace({ path, session }: { path: string; session: StaffSession }) {
  const [, second = '', third = ''] = path.split('/');
  if (second === 'analytics') return <InboxAnalyticsView session={session} />;
  if (second && third === 'order') return <ConversationOrder id={second} session={session} />;
  return <Inbox initial={second || null} session={session} />;
}

function ConversationOrder({ id, session }: { id: string; session: StaffSession }) {
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api
      .get<unknown>(`/api/manage/inbox/conversations/${id}/`)
      .then((value) => setDetail(conversationDetail(value)))
      .catch((e: unknown) => setError(errorMessage(e)));
  }, [id]);
  if (error) return <Alert>{error}</Alert>;
  if (!detail) return <Loader />;
  const channel = detail.conversation.channel;
  const source = {
    website: 'live_chat',
    messenger: 'facebook',
    whatsapp: 'whatsapp',
    instagram: 'instagram',
  }[channel];
  return (
    <AssistedOrderForm
      session={session}
      conversation={{
        id,
        channelLabel: CHANNEL_LABEL[channel],
        sourceLabel: label(SOURCE, source),
        name: detail.participant.display_name,
        phone: detail.participant.phone,
      }}
    />
  );
}

type Filters = { view: string; status: string; channel: string; q: string };

function Inbox({ initial, session }: { initial: string | null; session: StaffSession }) {
  const [filters, setFilters] = useState<Filters>({
    view: 'all',
    status: 'active',
    channel: '',
    q: '',
  });
  const [search, setSearch] = useState('');
  const [rows, setRows] = useState<ConversationSummary[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [listError, setListError] = useState('');
  const [selected, setSelected] = useState<string | null>(initial);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [threadData, setThreadData] = useState<Thread | null>(null);
  const [typing, setTyping] = useState<string>('');
  const [team, setTeam] = useState<InboxTeamMember[]>([]);
  const [paneError, setPaneError] = useState('');
  const selectedRef = useRef(selected);
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  useEffect(() => {
    const timer = window.setTimeout(() => setFilters((f) => ({ ...f, q: search.trim() })), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const loadList = useCallback(
    async (more = false) => {
      try {
        const page = conversationPage(
          await api.get<unknown>(
            `/api/manage/inbox/conversations/${query({
              view: filters.view,
              status: filters.status,
              channel: filters.channel,
              q: filters.q,
              cursor: more ? cursor : '',
              limit: 30,
            })}`,
          ),
        );
        setRows((current) => (more && current ? [...current, ...page.results] : page.results));
        setCursor(page.next_cursor);
        setUnreadTotal(page.unread_conversations);
        setListError('');
      } catch (e) {
        setListError(errorMessage(e));
      }
    },
    [filters, cursor],
  );

  // Reload when the filters change (not when the cursor does).
  const filterKey = JSON.stringify(filters);
  useEffect(() => {
    void Promise.resolve().then(() => {
      setRows(null);
      return loadList(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- cursor changes must not reload
  }, [filterKey]);

  useEffect(() => {
    void api
      .get<unknown>('/api/manage/inbox/team/')
      .then((value) => setTeam(inboxTeam(value)))
      .catch(() => setTeam([]));
  }, []);

  const loadConversation = useCallback(async (id: string) => {
    try {
      const [d, t] = await Promise.all([
        api.get<unknown>(`/api/manage/inbox/conversations/${id}/`),
        api.get<unknown>(`/api/manage/inbox/conversations/${id}/thread/`),
      ]);
      if (selectedRef.current !== id) return;
      setDetail(conversationDetail(d));
      setThreadData(decodeThread(t));
      setPaneError('');
      await api.post(`/api/manage/inbox/conversations/${id}/read/`);
      setRows(
        (current) => current?.map((row) => (row.id === id ? { ...row, unread: 0 } : row)) ?? null,
      );
    } catch (e) {
      setPaneError(errorMessage(e));
    }
  }, []);

  const onEvent = useCallback(
    (event: InboxEvent) => {
      const current = selectedRef.current;
      switch (event.type) {
        case 'conversation': {
          const incoming = event.conversation;
          setRows((list) => {
            if (!list) return list;
            const previous = list.find((row) => row.id === incoming.id);
            const added = incoming.inbound_count - (previous?.inbound_count ?? 0);
            const unread =
              incoming.id === current
                ? 0
                : Math.max((previous?.unread ?? 0) + Math.max(added, 0), 0);
            const merged = { ...incoming, unread };
            const rest = list.filter((row) => row.id !== incoming.id);
            return [merged, ...rest].sort((a, b) =>
              (b.last_message_at ?? b.created_at).localeCompare(a.last_message_at ?? a.created_at),
            );
          });
          if (incoming.id === current) {
            setDetail((d) => (d ? { ...d, conversation: { ...incoming, unread: 0 } } : d));
          }
          break;
        }
        case 'message':
          if (current && event.conversation === current) {
            setThreadData((t) =>
              t && !t.messages.some((m) => m.id === event.message.id)
                ? { ...t, messages: [...t.messages, event.message].sort((a, b) => a.seq - b.seq) }
                : t,
            );
            if (event.message.direction === 'inbound') {
              setTyping('');
              void api
                .post(`/api/manage/inbox/conversations/${current}/read/`)
                .catch(() => undefined);
            }
          }
          break;
        case 'note':
          if (event.conversation !== current) break;
          setThreadData((t) =>
            t && !t.notes.some((n) => n.id === event.note.id)
              ? { ...t, notes: [...t.notes, event.note] }
              : t,
          );
          break;
        case 'delivery':
          setThreadData((t) =>
            t
              ? {
                  ...t,
                  messages: t.messages.map((m) =>
                    m.id === event.message
                      ? { ...m, delivery_status: event.status, error_code: event.error_code }
                      : m,
                  ),
                }
              : t,
          );
          break;
        case 'attachment':
          if (current) void loadConversation(current);
          break;
        case 'visitor_read':
          setDetail((d) =>
            d && d.conversation.id === event.conversation
              ? { ...d, visitor_read_seq: event.seq }
              : d,
          );
          setThreadData((t) =>
            t
              ? {
                  ...t,
                  messages: t.messages.map((m) =>
                    m.direction === 'outbound' &&
                    m.seq <= event.seq &&
                    ['accepted', 'delivered'].includes(m.delivery_status)
                      ? { ...m, delivery_status: 'read' }
                      : m,
                  ),
                }
              : t,
          );
          break;
        case 'typing':
          if (event.conversation === current) {
            setTyping(
              event.on
                ? event.who === 'visitor'
                  ? 'Customer is typing…'
                  : `${event.name || 'A teammate'} is typing…`
                : '',
            );
          }
          break;
        default:
          break;
      }
    },
    [loadConversation],
  );

  const reconcile = useCallback(() => {
    void loadList(false);
    if (selectedRef.current) void loadConversation(selectedRef.current);
  }, [loadList, loadConversation]);

  const live = useInboxSocket(onEvent, reconcile);

  // Bounded fallback while the socket is down: refresh the list every 30 s, visible tabs only.
  useEffect(() => {
    if (live.connected) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadList(false);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [live.connected, loadList]);

  const { watch } = live;
  useEffect(() => {
    watch(selected);
    void Promise.resolve().then(() => {
      setTyping('');
      setDetail(null);
      setThreadData(null);
      return selected ? loadConversation(selected) : undefined;
    });
  }, [selected, watch, loadConversation]);

  useEffect(() => {
    const onPop = () => {
      const match = /^\/manage\/inbox\/([0-9a-f-]{36})$/.exec(window.location.pathname);
      setSelected(match?.[1] ?? null);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  function choose(id: string | null) {
    setSelected(id);
    window.history.pushState(null, '', id ? `/manage/inbox/${id}` : '/manage/inbox');
  }

  const set = (name: keyof Filters) => (value: string) =>
    setFilters((f) => ({ ...f, [name]: value }));
  const can = (permission: string) => session.permissions.includes(permission);

  return (
    <div className="-m-4 flex h-[calc(100dvh-4rem)] min-h-[560px] overflow-hidden border-line bg-surface sm:-m-6 lg:m-0 lg:rounded-base lg:border">
      <aside
        className={`${selected ? 'hidden lg:flex' : 'flex'} w-full min-w-0 flex-col border-r border-line lg:w-[320px] lg:flex-none`}
        aria-label="Conversations"
      >
        <div className="grid gap-2.5 border-b border-line p-4">
          <div className="flex items-center gap-2">
            <h1 className="flex-1 text-lg font-bold">Inbox</h1>
            <span className="text-xs text-muted">{unreadTotal} unread</span>
            {can('messages.manage') && (
              <a
                href="/manage/inbox/analytics"
                className="text-xs font-semibold text-brand-deep hover:underline"
              >
                Insights
              </a>
            )}
          </div>
          <label className="sr-only" htmlFor="inbox-search">
            Search conversations
          </label>
          <input
            id="inbox-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name, phone or order number"
            className="h-9 rounded-base border border-line bg-canvas px-3 text-sm outline-none focus:border-brand-deep"
          />
          <div className="flex gap-1 overflow-x-auto" role="tablist" aria-label="Views">
            {(
              [
                ['all', 'All'],
                ['unassigned', 'Unassigned'],
                ['mine', 'Mine'],
                ['unread', 'Unread'],
              ] as const
            ).map(([value, text]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={filters.view === value}
                onClick={() => set('view')(value)}
                className={`h-7 flex-none rounded-base border px-2.5 text-xs font-semibold ${filters.view === value ? 'border-ink bg-ink text-surface' : 'border-line bg-surface'}`}
              >
                {text}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <select
              aria-label="Status"
              value={filters.status}
              onChange={(e) => set('status')(e.target.value)}
              className="h-8 rounded-base border border-line bg-surface px-2 text-xs"
            >
              <option value="active">Active</option>
              <option value="open">Open</option>
              <option value="pending">Pending</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
            <select
              aria-label="Channel"
              value={filters.channel}
              onChange={(e) => set('channel')(e.target.value)}
              className="h-8 rounded-base border border-line bg-surface px-2 text-xs"
            >
              <option value="">All channels</option>
              {(Object.keys(CHANNEL_LABEL) as Channel[]).map((c) => (
                <option key={c} value={c}>
                  {CHANNEL_LABEL[c]}
                </option>
              ))}
            </select>
          </div>
          {!live.connected && (
            <p role="status" className="text-xs text-muted">
              Live updates are reconnecting. The list refreshes every 30 seconds meanwhile.
            </p>
          )}
        </div>
        <div className="flex-1 overflow-y-auto">
          {listError && (
            <div className="p-4">
              <Alert>{listError}</Alert>
            </div>
          )}
          {!rows ? (
            <Loader />
          ) : !rows.length ? (
            <div className="p-6 text-center text-sm text-muted">
              <p className="font-semibold text-ink">No conversations here</p>
              <p className="mt-1">
                New website chats and connected channel messages appear here as they arrive.
              </p>
            </div>
          ) : (
            <ul>
              {rows.map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => choose(row.id)}
                    aria-current={row.id === selected ? 'true' : undefined}
                    className={`flex w-full gap-2.5 border-b border-l-[3px] border-b-line px-4 py-3 text-left ${row.id === selected ? 'border-l-brand bg-brand-tint/40' : 'border-l-transparent hover:bg-canvas'}`}
                  >
                    <span className="relative flex h-9 w-9 flex-none items-center justify-center rounded-base bg-canvas text-xs font-bold">
                      {initials(row.display_name)}
                      <span className="absolute -right-1.5 -bottom-1.5 rounded-badge border border-surface bg-ink px-1 text-[9px] leading-4 font-bold text-surface">
                        {CHANNEL_MARK[row.channel]}
                      </span>
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex items-center gap-2">
                        <span
                          className={`flex-1 truncate text-sm ${row.unread ? 'font-bold' : 'font-semibold'}`}
                        >
                          {row.display_name}
                        </span>
                        <span className="text-[11px] text-muted">{ago(row.last_message_at)}</span>
                      </span>
                      <span className="flex items-center gap-2">
                        <span
                          className={`flex-1 truncate text-xs ${row.unread ? 'text-ink' : 'text-muted'}`}
                        >
                          {row.last_direction === 'outbound' ? 'You: ' : ''}
                          {row.last_preview}
                        </span>
                        {row.unread ? (
                          <span className="min-w-5 rounded-badge bg-brand px-1 text-center text-[11px] leading-5 font-bold text-ink">
                            {row.unread}
                            <span className="sr-only"> unread</span>
                          </span>
                        ) : null}
                      </span>
                      <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                        <span>{STATUS[row.status]}</span>
                        <span aria-hidden="true">·</span>
                        <span>{row.assignee ? row.assignee.name : 'Unassigned'}</span>
                        {row.is_simulated && <Badge tone="warning">Simulator</Badge>}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {cursor && (
            <div className="p-3">
              <Button
                variant="secondary"
                type="button"
                className="w-full"
                onClick={() => void loadList(true)}
              >
                Load more
              </Button>
            </div>
          )}
        </div>
      </aside>
      {selected ? (
        <ConversationPane
          key={selected}
          id={selected}
          detail={detail}
          thread={threadData}
          error={paneError}
          typing={typing}
          team={team}
          session={session}
          onBack={() => choose(null)}
          onTyping={(on) => live.send({ type: 'typing', on })}
          onChanged={() => void loadConversation(selected)}
          onMessage={(message) =>
            setThreadData((t) =>
              t && !t.messages.some((m) => m.id === message.id)
                ? { ...t, messages: [...t.messages, message].sort((a, b) => a.seq - b.seq) }
                : t,
            )
          }
          onNote={(added) =>
            setThreadData((t) =>
              t && !t.notes.some((n) => n.id === added.id)
                ? { ...t, notes: [...t.notes, added] }
                : t,
            )
          }
        />
      ) : (
        <div className="hidden flex-1 items-center justify-center bg-canvas p-8 text-center text-sm text-muted lg:flex">
          Choose a conversation to read and reply.
        </div>
      )}
    </div>
  );
}

// --- Conversation ---------------------------------------------------------------------------------

type Item =
  | { kind: 'message'; at: string; seq: number; message: StaffMessage }
  | { kind: 'note'; at: string; seq: number; note: Note }
  | { kind: 'event'; at: string; seq: number; event: TimelineEvent };

function eventText(event: TimelineEvent): string {
  const who = event.actor?.name ?? 'The system';
  switch (event.kind) {
    case 'assigned':
      return `${who} assigned this to ${event.assignee?.name ?? 'a teammate'}`;
    case 'unassigned':
      return `${who} removed the assignment`;
    case 'status':
      return event.actor
        ? `${who} changed the status to ${label(STATUS, event.to_status).toLowerCase()}`
        : `Status changed to ${label(STATUS, event.to_status).toLowerCase()}`;
    case 'order_linked':
      return `${who} linked order ${event.order?.number ?? ''}`;
    case 'opt_in':
      return `${who} recorded agreement to WhatsApp updates`;
    case 'opt_out':
      return `${who} recorded that the customer does not want to be contacted`;
    case 'customer_linked':
      return 'The visitor linked this chat to their customer account';
    default:
      return event.kind;
  }
}

function ConversationPane({
  id,
  detail,
  thread,
  error,
  typing,
  team,
  session,
  onBack,
  onTyping,
  onChanged,
  onMessage,
  onNote,
}: {
  id: string;
  detail: ConversationDetail | null;
  thread: Thread | null;
  error: string;
  typing: string;
  team: InboxTeamMember[];
  session: StaffSession;
  onBack: () => void;
  onTyping: (on: boolean) => void;
  onChanged: () => void;
  onMessage: (message: StaffMessage) => void;
  onNote: (note: Note) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const items = useMemo<Item[]>(() => {
    if (!thread) return [];
    const list: Item[] = [
      ...thread.messages.map((m) => ({
        kind: 'message' as const,
        at: m.created_at,
        seq: m.seq,
        message: m,
      })),
      ...thread.notes.map((n) => ({ kind: 'note' as const, at: n.created_at, seq: 0, note: n })),
      ...thread.events.map((e) => ({ kind: 'event' as const, at: e.created_at, seq: 0, event: e })),
    ];
    return list.sort((a, b) => a.at.localeCompare(b.at) || a.seq - b.seq);
  }, [thread]);
  useEffect(() => {
    const box = scroller.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [items.length]);

  if (error) {
    return (
      <section className="flex-1 p-6">
        <Alert>{error}</Alert>
      </section>
    );
  }
  if (!detail || !thread) {
    return (
      <section className="flex-1">
        <Loader />
      </section>
    );
  }
  const conversation = detail.conversation;
  return (
    <>
      <section className="flex min-w-0 flex-1 flex-col bg-canvas" aria-label="Conversation">
        <header className="flex h-14 flex-none items-center gap-2 border-b border-line bg-surface px-3 sm:px-4">
          <button
            type="button"
            onClick={onBack}
            className="flex h-9 w-9 items-center justify-center rounded-base text-lg lg:hidden"
            aria-label="Back to conversations"
          >
            ←
          </button>
          <div className="min-w-0 flex-1 leading-tight">
            <p className="truncate text-sm font-bold">{conversation.display_name}</p>
            <p className="truncate text-xs text-muted">
              {CHANNEL_LABEL[conversation.channel]}
              {conversation.connection_name ? ` · ${conversation.connection_name}` : ''}
              {detail.participant.phone ? ` · ${detail.participant.phone}` : ''}
            </p>
          </div>
          <Badge
            tone={
              conversation.status === 'open'
                ? 'info'
                : conversation.status === 'resolved'
                  ? 'success'
                  : 'neutral'
            }
          >
            {STATUS[conversation.status]}
          </Badge>
        </header>
        {conversation.is_simulated && (
          <p className="border-b border-line bg-warning-bg px-4 py-2 text-xs text-warning-fg">
            Development simulator: nothing in this conversation reaches Meta or a real customer.
          </p>
        )}
        <div
          ref={scroller}
          className="flex flex-1 flex-col gap-2 overflow-y-auto px-3 py-4 sm:px-5"
        >
          {thread.has_more && (
            <p className="text-center text-xs text-muted">
              Older messages are kept; scroll position shows the latest 50.
            </p>
          )}
          {items.map((item) =>
            item.kind === 'message' ? (
              <MessageBubble
                key={item.message.id}
                message={item.message}
                channel={conversation.channel}
                onResent={onMessage}
              />
            ) : item.kind === 'note' ? (
              <div
                key={item.note.id}
                className="max-w-[85%] self-end rounded-base border border-warning-fg/30 bg-warning-bg px-3 py-2 text-sm"
              >
                <p className="text-[11px] font-bold tracking-wide text-warning-fg uppercase">
                  Internal note · only your team sees this
                </p>
                <p className="mt-1 break-words whitespace-pre-wrap">{item.note.body}</p>
                <p className="mt-1 text-[11px] text-muted">
                  {item.note.author.name} · {dateTime(item.note.created_at)}
                </p>
              </div>
            ) : (
              <p
                key={item.event.id}
                className="self-center rounded-base bg-surface px-2.5 py-1 text-center text-[11px] text-muted"
              >
                {eventText(item.event)} · {dateTime(item.event.created_at)}
              </p>
            ),
          )}
          {typing && <p className="text-xs text-muted italic">{typing}</p>}
        </div>
        <Composer
          detail={detail}
          onTyping={onTyping}
          onMessage={onMessage}
          onNote={onNote}
          onChanged={onChanged}
        />
      </section>
      <DetailsPanel id={id} detail={detail} team={team} session={session} onChanged={onChanged} />
    </>
  );
}

function MessageBubble({
  message,
  channel,
  onResent,
}: {
  message: StaffMessage;
  channel: Channel;
  onResent: (message: StaffMessage) => void;
}) {
  const outbound = message.direction === 'outbound';
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function resend() {
    if (
      message.delivery_status === 'unknown' &&
      !window.confirm(
        'The provider may already have delivered this message. Sending again can show it twice. Send again?',
      )
    )
      return;
    setBusy(true);
    try {
      onResent(
        staffMessage(await api.post<unknown>(`/api/manage/inbox/messages/${message.id}/resend/`)),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const file = message.attachment;
  return (
    <div
      className={`flex max-w-[85%] flex-col gap-1 ${outbound ? 'items-end self-end' : 'items-start self-start'}`}
    >
      <div
        className={`rounded-base px-3 py-2 text-sm leading-6 ${outbound ? 'bg-ink text-surface' : 'border border-line bg-surface'}`}
      >
        {message.kind === 'product' && message.product ? (
          <div>
            <p className="font-semibold">{message.product.name}</p>
            <p className="text-xs opacity-80">
              {[message.product.variant_title, message.product.sku].filter(Boolean).join(' · ')} ·{' '}
              {money(message.product.amount, message.product.currency)}
              {!message.product.in_stock && ' · Out of stock'}
            </p>
            {safeProductLink(message.product.url) && (
              <a
                href={safeProductLink(message.product.url)}
                className="text-xs underline"
                target="_blank"
                rel="noopener noreferrer"
              >
                {message.product.url}
              </a>
            )}
          </div>
        ) : file ? (
          file.url ? (
            file.content_type.startsWith('image/') ? (
              <a href={file.url} target="_blank" rel="noopener noreferrer">
                {/* eslint-disable-next-line @next/next/no-img-element -- private, checked API file */}
                <img
                  src={file.url}
                  alt="Attachment from the conversation"
                  className="max-h-56 rounded-base object-contain"
                />
              </a>
            ) : (
              <a href={file.url} className="underline">
                {file.filename || 'Document'}
              </a>
            )
          ) : (
            <span className="italic opacity-80">
              {file.status === 'pending'
                ? 'Downloading the attachment…'
                : file.status === 'unsupported'
                  ? 'This file type is not supported and was not stored.'
                  : 'The attachment could not be downloaded from the provider.'}
            </span>
          )
        ) : message.kind === 'unsupported' ? (
          <span className="italic opacity-80">
            The customer sent content this channel cannot show here (for example a sticker or
            location).
          </span>
        ) : null}
        {message.body && message.kind !== 'product' && (
          <p className="break-words whitespace-pre-wrap">{message.body}</p>
        )}
        {message.kind === 'template' && (
          <p className="mt-1 text-[11px] opacity-70">Template: {message.template_name}</p>
        )}
      </div>
      <p className="text-[11px] text-muted">
        {dateTime(message.created_at)}
        {outbound && message.sender ? ` · ${message.sender.name}` : ''}
        {!outbound ? ` · ${CHANNEL_LABEL[channel]}` : ''}
        {outbound && ` · ${label(DELIVERY, message.delivery_status)}`}
        {outbound && message.reply_tag ? ' · Human agent reply' : ''}
      </p>
      {outbound &&
        (message.delivery_status === 'failed' || message.delivery_status === 'unknown') && (
          <div className="flex items-center gap-2 text-[11px]">
            <span className="text-danger-fg">
              {ERRORS[message.error_code] ?? 'The provider did not confirm this message.'}
            </span>
            {message.kind !== 'template' && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void resend()}
                className="font-semibold underline"
              >
                Send again
              </button>
            )}
          </div>
        )}
      {error && <p className="text-[11px] text-danger-fg">{error}</p>}
    </div>
  );
}

function Composer({
  detail,
  onTyping,
  onMessage,
  onNote,
  onChanged,
}: {
  detail: ConversationDetail;
  onTyping: (on: boolean) => void;
  onMessage: (message: StaffMessage) => void;
  onNote: (note: Note) => void;
  onChanged: () => void;
}) {
  const conversation = detail.conversation;
  const policy = detail.policy;
  const [mode, setMode] = useState<'reply' | 'note'>('reply');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [picker, setPicker] = useState<'product' | 'template' | null>(null);
  const key = useRef(newKey('reply'));
  const lastTyping = useRef(0);
  const file = useRef<HTMLInputElement>(null);
  const base = `/api/manage/inbox/conversations/${conversation.id}`;

  async function post(body: Record<string, unknown>) {
    setBusy(true);
    setError('');
    try {
      onMessage(
        staffMessage(
          await api.post<unknown>(`${base}/messages/`, { client_key: key.current, ...body }),
        ),
      );
      key.current = newKey('reply');
      setText('');
      setPicker(null);
      onTyping(false);
    } catch (e) {
      setError(errorMessage(e));
      if (e instanceof ApiError && e.status === 409) onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    const body = text.trim();
    if (!body) return;
    if (mode === 'note') {
      setBusy(true);
      setError('');
      try {
        onNote(decodeNote(await api.post<unknown>(`${base}/notes/`, { body })));
        setText('');
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setBusy(false);
      }
      return;
    }
    await post({ body });
  }

  async function upload(chosen: File) {
    const form = new FormData();
    form.append('file', chosen);
    form.append('client_key', newKey('file'));
    setBusy(true);
    setError('');
    try {
      onMessage(staffMessage(await api.upload<unknown>(`${base}/attachments/`, form)));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const replying = mode === 'reply';
  const blocked = replying && !policy.can_reply;
  return (
    <div className="flex-none border-t border-line bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center gap-1" role="tablist" aria-label="Composer">
        {(
          [
            ['reply', 'Reply to customer'],
            ['note', 'Internal note'],
          ] as const
        ).map(([value, text]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={mode === value}
            onClick={() => setMode(value)}
            className={`h-7 rounded-base px-2.5 text-xs font-semibold ${mode === value ? (value === 'note' ? 'bg-warning-bg text-warning-fg' : 'bg-ink text-surface') : 'text-muted hover:bg-canvas'}`}
          >
            {text}
          </button>
        ))}
        {policy.window_ends_at && replying && (
          <span className="ml-auto text-[11px] text-muted">
            Replies allowed until {dateTime(policy.window_ends_at)}
            {policy.tag ? ' (human agent reply)' : ''}
          </span>
        )}
      </div>
      {blocked && (
        <div className="mb-2 rounded-base border border-line bg-canvas p-2.5 text-xs leading-5">
          <p>{policy.message}</p>
          {conversation.channel === 'whatsapp' && policy.reason === 'template_required' && (
            <p className="mt-1 text-muted">
              {policy.templates_allowed
                ? 'You can send an approved template below.'
                : policy.template_reason}
            </p>
          )}
        </div>
      )}
      {replying && (
        <div className="mb-2">
          <InboxAssistant
            conversationId={conversation.id}
            onUseReply={(draft) => {
              setMode('reply');
              setText(draft);
            }}
          />
        </div>
      )}
      {error && (
        <div className="mb-2">
          <Alert>{error}</Alert>
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="flex items-end gap-2"
      >
        <div
          className={`flex flex-1 flex-col rounded-base border ${mode === 'note' ? 'border-warning-fg/40 bg-warning-bg' : 'border-line-strong'}`}
        >
          <label htmlFor="composer" className="sr-only">
            {replying ? 'Reply to the customer' : 'Internal note for your team'}
          </label>
          <textarea
            id="composer"
            rows={2}
            maxLength={replying ? 4000 : 2000}
            disabled={blocked || busy}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              if (replying && Date.now() - lastTyping.current > 3000) {
                lastTyping.current = Date.now();
                onTyping(true);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder={
              blocked
                ? 'Free text replies are not possible right now'
                : replying
                  ? 'Write a reply (Enter to send, Shift+Enter for a new line)'
                  : 'Only your team will see this note'
            }
            className="resize-none bg-transparent px-3 py-2 text-sm outline-none disabled:cursor-not-allowed"
          />
          {replying && (
            <div className="flex flex-wrap gap-1 px-2 pb-2">
              <button
                type="button"
                disabled={!policy.can_reply}
                onClick={() => setPicker('product')}
                className="h-7 rounded-badge border border-line bg-surface px-2 text-xs font-semibold disabled:opacity-50"
              >
                Share product
              </button>
              {conversation.channel === 'whatsapp' && (
                <button
                  type="button"
                  disabled={!policy.templates_allowed}
                  title={policy.templates_allowed ? undefined : policy.template_reason}
                  onClick={() => setPicker('template')}
                  className="h-7 rounded-badge border border-line bg-surface px-2 text-xs font-semibold disabled:opacity-50"
                >
                  Approved template
                </button>
              )}
              {conversation.channel === 'website' && (
                <>
                  <input
                    ref={file}
                    type="file"
                    accept="image/jpeg,image/png,image/webp,application/pdf"
                    className="hidden"
                    onChange={(e) => {
                      const chosen = e.target.files?.[0];
                      if (chosen) void upload(chosen);
                      e.target.value = '';
                    }}
                  />
                  <button
                    type="button"
                    disabled={!policy.can_reply}
                    onClick={() => file.current?.click()}
                    className="h-7 rounded-badge border border-line bg-surface px-2 text-xs font-semibold disabled:opacity-50"
                  >
                    Attach file
                  </button>
                </>
              )}
            </div>
          )}
        </div>
        <Button type="submit" busy={busy} disabled={blocked || !text.trim()}>
          {replying ? 'Send' : 'Add note'}
        </Button>
      </form>
      {picker === 'product' && (
        <ProductDialog
          onClose={() => setPicker(null)}
          busy={busy}
          onShare={(variant) => void post({ product_variant: variant })}
        />
      )}
      {picker === 'template' && (
        <TemplateDialog
          onClose={() => setPicker(null)}
          busy={busy}
          onSend={(template, parameters) =>
            void post({ template, template_parameters: parameters })
          }
        />
      )}
    </div>
  );
}

function ProductDialog({
  onClose,
  onShare,
  busy,
}: {
  onClose: () => void;
  onShare: (variant: string) => void;
  busy: boolean;
}) {
  const [text, setText] = useState('');
  const [rows, setRows] = useState<InboxProduct[] | null>(null);
  const [links, setLinks] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      api
        .get<unknown>(`/api/manage/inbox/products/${query({ q: text })}`)
        .then((value) => {
          if (!live) return;
          const data = inboxProducts(value);
          setRows(data.results);
          setLinks(data.links_available);
        })
        .catch((e: unknown) => live && setError(errorMessage(e)));
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [text]);
  return (
    <Dialog title="Share a product" onClose={onClose}>
      <div className="grid gap-3">
        <TextField
          label="Find a product"
          placeholder="Name, SKU or barcode"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {!links && (
          <p className="text-xs leading-5 text-muted">
            Product links need the store&apos;s public https address. Messenger, WhatsApp and
            Instagram customers will receive the name and price without a link until it is set.
          </p>
        )}
        {error && <Alert>{error}</Alert>}
        {rows && !rows.length && (
          <p className="text-sm text-muted">No published product matches.</p>
        )}
        {rows && rows.length > 0 && (
          <ul className="max-h-80 divide-y divide-line overflow-y-auto rounded-base border border-line">
            {rows.map((row) => (
              <li
                key={row.variant_id}
                className="flex items-center justify-between gap-3 p-3 text-sm"
              >
                <span className="min-w-0">
                  <span className="block truncate font-semibold">{row.name}</span>
                  <span className="text-xs text-muted">
                    {[row.variant_title, row.sku].filter(Boolean).join(' · ')} ·{' '}
                    {row.amount ? money(row.amount, row.currency) : 'No price'} ·{' '}
                    {row.available_quantity === null
                      ? row.in_stock
                        ? 'Available'
                        : 'Out of stock'
                      : `${row.available_quantity.replace(/\.?0+$/, '')} in stock`}
                  </span>
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy || !row.amount}
                  onClick={() => onShare(row.variant_id)}
                >
                  Share
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  );
}

function TemplateDialog({
  onClose,
  onSend,
  busy,
}: {
  onClose: () => void;
  onSend: (template: string, parameters: string[]) => void;
  busy: boolean;
}) {
  const [rows, setRows] = useState<WhatsAppTemplate[] | null>(null);
  const [chosen, setChosen] = useState('');
  const [values, setValues] = useState<string[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    api
      .get<unknown>('/api/manage/inbox/templates/')
      .then((value) => setRows(whatsAppTemplates(value).filter((row) => row.sendable)))
      .catch((e: unknown) => setError(errorMessage(e)));
  }, []);
  const template = rows?.find((row) => row.id === chosen);
  let preview = template?.body_text ?? '';
  values.forEach((value, index) => {
    preview = preview.replaceAll(`{{${index + 1}}}`, value || `{{${index + 1}}}`);
  });
  return (
    <Dialog title="Send an approved WhatsApp template" onClose={onClose}>
      <div className="grid gap-3">
        <p className="text-xs leading-5 text-muted">
          Only templates Meta approved in the utility category are listed. Templates are created and
          approved in Meta Business tools, then synchronised in Social channels settings.
        </p>
        {error && <Alert>{error}</Alert>}
        {rows && !rows.length && (
          <p className="text-sm text-muted">No approved utility templates are available.</p>
        )}
        {rows && rows.length > 0 && (
          <SelectField
            label="Template"
            value={chosen}
            onChange={(e) => {
              setChosen(e.target.value);
              const next = rows.find((row) => row.id === e.target.value);
              setValues(Array.from({ length: next?.parameter_count ?? 0 }, () => ''));
            }}
            options={[
              { value: '', label: 'Choose a template' },
              ...rows.map((row) => ({ value: row.id, label: `${row.name} (${row.language})` })),
            ]}
          />
        )}
        {values.map((value, index) => (
          <TextField
            key={index}
            label={`Value {{${index + 1}}}`}
            maxLength={200}
            value={value}
            onChange={(e) =>
              setValues((current) => current.map((v, at) => (at === index ? e.target.value : v)))
            }
          />
        ))}
        {template && (
          <p className="rounded-base bg-canvas p-3 text-sm whitespace-pre-wrap">{preview}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            busy={busy}
            disabled={!template || values.some((v) => !v.trim())}
            onClick={() => template && onSend(template.id, values)}
          >
            Send template
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// --- Details --------------------------------------------------------------------------------------

function DetailsPanel({
  id,
  detail,
  team,
  session,
  onChanged,
}: {
  id: string;
  detail: ConversationDetail;
  team: InboxTeamMember[];
  session: StaffSession;
  onChanged: () => void;
}) {
  const conversation = detail.conversation;
  const participant = detail.participant;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [orderNumber, setOrderNumber] = useState('');
  const [optIn, setOptIn] = useState(false);
  const base = `/api/manage/inbox/conversations/${id}`;
  const can = (permission: string) => session.permissions.includes(permission);

  async function act(path: string, body?: Record<string, unknown>) {
    setBusy(true);
    setError('');
    try {
      await api.post(`${base}/${path}/`, body);
      onChanged();
    } catch (e) {
      setError(errorMessage(e));
      if (e instanceof ApiError && e.status === 409) onChanged();
    } finally {
      setBusy(false);
    }
  }
  const status = (value: string) => act('status', { status: value, version: conversation.version });
  const assign = (assignee: string | null) =>
    act('assign', { assignee, version: conversation.version });

  async function linkOrder() {
    setBusy(true);
    setError('');
    try {
      const found = await api.get<{ results: { id: string; number: string }[] }>(
        `/api/manage/orders/${query({ q: orderNumber.trim(), page_size: 1 })}`,
      );
      const order = found.results.find(
        (row) => row.number.toLowerCase() === orderNumber.trim().toLowerCase(),
      );
      if (!order) throw new Error('missing');
      await api.post(`${base}/orders/link/`, { order: order.id });
      setOrderNumber('');
      onChanged();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? errorMessage(e)
          : 'No order with that number was found in this store.',
      );
    } finally {
      setBusy(false);
    }
  }

  const actions: [string, string][] =
    conversation.status === 'open'
      ? [
          ['pending', 'Mark pending'],
          ['resolved', 'Resolve'],
          ['closed', 'Close'],
        ]
      : conversation.status === 'pending'
        ? [
            ['open', 'Reopen'],
            ['resolved', 'Resolve'],
            ['closed', 'Close'],
          ]
        : conversation.status === 'resolved'
          ? [
              ['open', 'Reopen'],
              ['closed', 'Close'],
            ]
          : [['open', 'Reopen']];

  return (
    <aside
      className="hidden w-[300px] flex-none overflow-y-auto border-l border-line p-4 xl:block"
      aria-label="Conversation details"
    >
      <div className="grid gap-5 text-sm">
        <div className="flex flex-col items-center gap-1 border-b border-line pb-4 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-base bg-ink text-base font-bold text-surface">
            {initials(conversation.display_name)}
          </span>
          <p className="font-bold">{conversation.display_name}</p>
          <p className="text-xs text-muted">{CHANNEL_LABEL[conversation.channel]}</p>
          {participant.customer_id ? (
            <a
              href={`/manage/customers/${participant.customer_id}`}
              className="text-xs font-semibold text-brand-deep hover:underline"
            >
              Verified customer account →
            </a>
          ) : (
            <p className="text-xs text-muted">
              {conversation.customer_linked
                ? 'Linked to a customer account'
                : 'Not linked to a customer account'}
            </p>
          )}
        </div>
        {error && <Alert>{error}</Alert>}
        <section className="grid gap-2">
          <h3 className="text-[11px] font-bold tracking-widest text-muted uppercase">Status</h3>
          <div className="flex flex-wrap gap-1.5">
            {actions.map(([value, text]) => (
              <Button
                key={value}
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => void status(value)}
                className="min-h-9 px-3 text-xs"
              >
                {text}
              </Button>
            ))}
          </div>
        </section>
        <section className="grid gap-2">
          <h3 className="text-[11px] font-bold tracking-widest text-muted uppercase">
            Assigned to
          </h3>
          <select
            aria-label="Assigned to"
            disabled={busy}
            value={conversation.assignee?.id ?? ''}
            onChange={(e) => void assign(e.target.value || null)}
            className="h-9 rounded-base border border-line-strong bg-surface px-2 text-sm"
          >
            <option value="">Unassigned</option>
            {team.map((member) => (
              <option key={member.id} value={member.id}>
                {member.name}
                {member.id === session.user.id ? ' (you)' : ''}
              </option>
            ))}
          </select>
          {conversation.assignee?.id !== session.user.id && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void assign(session.user.id)}
              className="justify-self-start text-xs font-semibold text-brand-deep hover:underline"
            >
              Take ownership
            </button>
          )}
        </section>
        {conversation.channel === 'whatsapp' && (
          <section className="grid gap-2">
            <h3 className="text-[11px] font-bold tracking-widest text-muted uppercase">
              WhatsApp updates
            </h3>
            {participant.opted_out_at ? (
              <p className="text-xs leading-5 text-muted">
                The customer asked not to be contacted. Templates are blocked.
              </p>
            ) : participant.template_opt_in_at ? (
              <p className="text-xs leading-5 text-muted">
                Agreement recorded {dateTime(participant.template_opt_in_at)}.
              </p>
            ) : (
              <>
                <label className="flex gap-2 text-xs leading-5">
                  <input
                    type="checkbox"
                    checked={optIn}
                    onChange={(e) => setOptIn(e.target.checked)}
                    className="mt-0.5"
                  />
                  The customer agreed in this conversation to receive WhatsApp updates from the
                  store.
                </label>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!optIn || busy}
                  onClick={() => void act('opt-in', { confirmed: true })}
                  className="min-h-9 text-xs"
                >
                  Record agreement
                </Button>
              </>
            )}
            {!participant.opted_out_at && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void act('opt-out')}
                className="justify-self-start text-xs text-danger-fg hover:underline"
              >
                Customer asked not to be contacted
              </button>
            )}
          </section>
        )}
        {detail.can_create_orders && (
          <section className="grid gap-2">
            <h3 className="text-[11px] font-bold tracking-widest text-muted uppercase">Orders</h3>
            {detail.orders.length ? (
              <ul className="grid gap-2">
                {detail.orders.map((order) => (
                  <li key={order.id} className="rounded-base border border-line p-2.5">
                    <a
                      href={`/manage/orders/${order.id}`}
                      className="font-semibold hover:underline"
                    >
                      {order.number}
                    </a>
                    <p className="text-xs text-muted">
                      {label(ORDER_STATUS, order.status)} ·{' '}
                      {label(PAYMENT_STATUS, order.payment_status)} ·{' '}
                      {money(order.grand_total, order.currency)}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted">No orders linked yet.</p>
            )}
            {conversation.status !== 'closed' && (
              <a
                href={`/manage/inbox/${id}/order`}
                className="inline-flex min-h-9 items-center justify-center rounded-base border border-ink bg-ink px-3 text-xs font-semibold text-surface"
              >
                Create order
              </a>
            )}
            <form
              className="flex gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                void linkOrder();
              }}
            >
              <label htmlFor="link-order" className="sr-only">
                Order number to link
              </label>
              <input
                id="link-order"
                value={orderNumber}
                onChange={(e) => setOrderNumber(e.target.value)}
                placeholder="Order number"
                className="h-9 min-w-0 flex-1 rounded-base border border-line-strong px-2 text-xs"
              />
              <Button
                type="submit"
                variant="secondary"
                disabled={!orderNumber.trim() || busy}
                className="min-h-9 px-3 text-xs"
              >
                Link
              </Button>
            </form>
          </section>
        )}
        {detail.cases.length > 0 && (
          <section className="grid gap-2">
            <h3 className="text-[11px] font-bold tracking-widest text-muted uppercase">
              Customer requests
            </h3>
            <ul className="grid gap-1.5">
              {detail.cases.map((item) => (
                <li key={item.id} className="text-xs">
                  <a href="/manage/aftersales" className="font-semibold hover:underline">
                    {label(CASE_KIND, item.kind)}
                  </a>{' '}
                  · {label(CASE_STATUS, item.status)} · {dateTime(item.created_at)}
                </li>
              ))}
            </ul>
          </section>
        )}
        {detail.recent.length > 0 && (
          <section className="grid gap-2">
            <h3 className="text-[11px] font-bold tracking-widest text-muted uppercase">
              Earlier conversations
            </h3>
            <ul className="grid gap-1.5">
              {detail.recent.map((row) => (
                <li key={row.id} className="text-xs">
                  <a href={`/manage/inbox/${row.id}`} className="hover:underline">
                    {dateTime(row.created_at)} · {STATUS[row.status]}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}
        {!can('orders.manage') && (
          <p className="text-xs leading-5 text-muted">
            Orders for this conversation need order access.
          </p>
        )}
      </div>
    </aside>
  );
}

/** Parses a live conversation update (exported for tests). */
export function decodeConversationEvent(value: unknown): ConversationSummary {
  return conversationSummary(value);
}
