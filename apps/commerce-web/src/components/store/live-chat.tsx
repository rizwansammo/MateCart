'use client';

import { usePathname } from 'next/navigation';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ChatState, ConversationStatus, VisitorMessage } from '@matecart/shared-contracts';
import {
  chatState,
  safeProductLink,
  visitorEvent,
  visitorMessage,
} from '@matecart/shared-contracts';
import { ApiError } from '@matecart/shared-ui';

import { authApi as api } from '@/lib/auth-client';
import { messages as dictionary } from '@/lib/i18n';
import { formatMoney } from '@/lib/money';

/**
 * MateCart's own website chat (Phase 14, D-093). No third-party widget or script.
 *
 * - The server is the record: a message counts as sent only after the server acknowledges it,
 *   which it does after committing it. Until then it shows as "sending"; on failure it can be
 *   retried with the same client key, so a retry never creates a duplicate.
 * - The chat credential is an HttpOnly cookie the page cannot read; nothing is stored in
 *   localStorage.
 * - After every (re)connection the widget asks for everything after the last message it has,
 *   so nothing is missed while the connection was down.
 * - The thread shows one conversation at a time. Message sequences restart in every
 *   conversation, so the widget keeps the id of the conversation its messages and `lastSeq`
 *   belong to and names it in every sync and receipt. A message or status from another
 *   conversation (a new one after closing, or one staff reopened) is never merged: the widget
 *   asks the server which conversation is current and shows that one instead (D-100).
 * - "Online" is shown only when the store says a team member has the inbox open now.
 */

const HEARTBEAT_MS = 25_000;
const MAX_BACKOFF_MS = 30_000;
const TYPING_IDLE_MS = 4_000;

type Pending = { client_key: string; body: string; state: 'sending' | 'failed' };
type Link = 'idle' | 'connecting' | 'open' | 'reconnecting';

function key(): string {
  return `chat-${crypto.randomUUID()}`;
}

function socketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.host}/api/chat/ws/`;
}

function merge(current: VisitorMessage[], incoming: VisitorMessage[]): VisitorMessage[] {
  const byId = new Map(current.map((m) => [m.id, m]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
}

/** Not shown on checkout pages, so it never covers payment or order controls on small screens. */
export function LiveChat({ lang }: { lang: string }) {
  const pathname = usePathname();
  if (pathname.startsWith('/checkout')) return null;
  return <ChatWidget lang={lang} />;
}

function ChatWidget({ lang }: { lang: string }) {
  const t = dictionary(lang).chat;
  const [state, setState] = useState<ChatState | null>(null);
  const [list, setList] = useState<VisitorMessage[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [status, setStatus] = useState<ConversationStatus | null>(null);
  const [online, setOnline] = useState(false);
  const [open, setOpen] = useState(false);
  const [link, setLink] = useState<Link>('idle');
  const [draft, setDraft] = useState('');
  const [unread, setUnread] = useState(0);
  const [agentTyping, setAgentTyping] = useState(false);
  const [notice, setNotice] = useState('');
  const [linkedNotice, setLinkedNotice] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const backoff = useRef(1000);
  const retryTimer = useRef<number | null>(null);
  const heartbeat = useRef<number | null>(null);
  const typingTimer = useRef<number | null>(null);
  const lastTyping = useRef(0);
  const openRef = useRef(open);
  const lastSeq = useRef(0);
  // The conversation the shown messages and `lastSeq` belong to ('' before the first one).
  const thread = useRef('');
  const seen = useRef(new Set<string>());
  const refreshes = useRef(0);
  const wanted = useRef(false);
  // The reconnect timer calls the latest `connect` through this ref (it refers to itself).
  const reconnect = useRef<() => void>(() => undefined);
  const resync = useRef<() => void>(() => undefined);
  const [now, setNow] = useState(0);
  const panelId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => {
    openRef.current = open;
  }, [open]);

  /**
   * Adds messages of the shown conversation. Returns false when some belong to another
   * conversation: those are not shown here, and the server is asked for the current one.
   */
  const accept = useCallback((incoming: VisitorMessage[]): boolean => {
    if (!thread.current) thread.current = incoming.find((m) => m.conversation)?.conversation ?? '';
    const own = incoming.filter((m) => !m.conversation || m.conversation === thread.current);
    const foreign = own.length < incoming.length;
    if (own.length) {
      lastSeq.current = Math.max(lastSeq.current, ...own.map((m) => m.seq));
      setList((current) => merge(current, own));
      setPending((current) =>
        current.filter((p) => !own.some((m) => m.client_key && m.client_key === p.client_key)),
      );
      const fresh = own.filter((m) => !seen.current.has(m.id));
      for (const m of fresh) seen.current.add(m.id);
      const fromStore = fresh.filter((m) => m.direction === 'outbound').length;
      if (fromStore && !openRef.current) setUnread((n) => n + fromStore);
    }
    if (foreign) resync.current();
    return !foreign;
  }, []);

  /** The server's answer to "which conversation is current": shows that one, and only it. */
  const adopt = useCallback(
    (conversation: string | null, incoming: VisitorMessage[]) => {
      const id = conversation ?? '';
      if (id !== thread.current) {
        thread.current = id;
        lastSeq.current = 0;
        seen.current = new Set();
        setList([]);
        setAgentTyping(false);
      }
      accept(incoming.filter((m) => !m.conversation || m.conversation === id));
    },
    [accept],
  );

  const send = useCallback((frame: Record<string, unknown>) => {
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(frame));
      return true;
    }
    return false;
  }, []);

  const connect = useCallback(() => {
    if (socket.current && socket.current.readyState <= WebSocket.OPEN) return;
    wanted.current = true;
    setLink((current) => (current === 'idle' ? 'connecting' : 'reconnecting'));
    const ws = new WebSocket(socketUrl());
    socket.current = ws;
    ws.onopen = () => {
      backoff.current = 1000;
      setLink('open');
      ws.send(
        JSON.stringify({
          type: 'sync',
          conversation: thread.current || null,
          after_seq: lastSeq.current,
        }),
      );
      if (heartbeat.current) window.clearInterval(heartbeat.current);
      heartbeat.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
      }, HEARTBEAT_MS);
    };
    ws.onmessage = (event: MessageEvent<string>) => {
      // A socket replaced earlier must not change what the live one shows.
      if (socket.current !== ws) return;
      let data: unknown;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      const frame = visitorEvent(data);
      if (!frame) return;
      switch (frame.type) {
        case 'ready':
          setOnline(frame.online);
          break;
        case 'sync':
          setOnline(frame.online);
          setStatus(frame.status);
          adopt(frame.conversation, frame.messages);
          break;
        case 'ack':
          accept([frame.message]);
          break;
        case 'nack':
          setPending((current) =>
            current.map((p) => (p.client_key === frame.client_key ? { ...p, state: 'failed' } : p)),
          );
          if (frame.code === 'rate_limited') setNotice(t.rateLimited);
          break;
        case 'message':
          accept([frame.message]);
          if (frame.message.direction === 'outbound') {
            setAgentTyping(false);
            ws.send(
              JSON.stringify({
                type: 'delivered',
                conversation: frame.message.conversation || null,
                seq: frame.message.seq,
              }),
            );
          }
          break;
        case 'conversation':
          if (frame.conversation && thread.current && frame.conversation !== thread.current) {
            resync.current();
          } else {
            setStatus(frame.status);
          }
          break;
        case 'typing':
          if (frame.who === 'agent') setAgentTyping(frame.on);
          break;
        default:
          break;
      }
    };
    ws.onclose = (event: CloseEvent) => {
      // A socket replaced earlier (React remounts, reconnects) must not touch the live one.
      if (socket.current !== ws) return;
      if (heartbeat.current) window.clearInterval(heartbeat.current);
      socket.current = null;
      setAgentTyping(false);
      // Refusals (no credential, chat switched off) are not retried in a loop.
      if (!wanted.current || event.code === 4401 || event.code === 4403) {
        setLink('idle');
        return;
      }
      setLink('reconnecting');
      const delay = backoff.current;
      backoff.current = Math.min(backoff.current * 2, MAX_BACKOFF_MS);
      retryTimer.current = window.setTimeout(
        () => reconnect.current(),
        delay + Math.random() * 500,
      );
    };
  }, [accept, adopt, t.rateLimited]);

  useEffect(() => {
    reconnect.current = connect;
  }, [connect]);

  const configUrl = `/api/chat/config/?locale=${lang === 'bn' ? 'bn' : 'en'}`;

  // Asks which conversation is current: over the socket when it is open, otherwise over HTTP.
  // Only the newest HTTP answer is used, so a slow earlier one cannot bring back an old thread.
  useEffect(() => {
    resync.current = () => {
      if (send({ type: 'sync', conversation: thread.current || null, after_seq: lastSeq.current }))
        return;
      const request = ++refreshes.current;
      api
        .get<unknown>(configUrl)
        .then((value) => {
          if (request !== refreshes.current) return;
          const data = chatState(value);
          setStatus(data.conversation_status);
          adopt(data.conversation_id, data.messages);
        })
        .catch(() => {
          // The next (re)connection synchronises again.
        });
    };
  }, [adopt, configUrl, send]);

  // Initial state: configuration and this visitor's own history (if any).
  useEffect(() => {
    let live = true;
    const request = ++refreshes.current;
    api
      .get<unknown>(configUrl)
      .then((value) => {
        if (!live || request !== refreshes.current) return;
        const data = chatState(value);
        setState(data);
        setOnline(data.config.online);
        setStatus(data.conversation_status);
        adopt(data.conversation_id, data.messages);
        if (data.messages.length) connect();
      })
      .catch(() => {
        // Chat is optional: the store keeps working without it.
      });
    return () => {
      live = false;
    };
  }, [configUrl, adopt, connect]);

  useEffect(
    () => () => {
      wanted.current = false;
      if (retryTimer.current) window.clearTimeout(retryTimer.current);
      if (heartbeat.current) window.clearInterval(heartbeat.current);
      socket.current?.close();
    },
    [],
  );

  // Relative timestamps refresh once a minute while the panel is open.
  useEffect(() => {
    if (!open) return;
    const update = () => setNow(Date.now());
    void Promise.resolve().then(update);
    const timer = window.setInterval(update, 60_000);
    return () => window.clearInterval(timer);
  }, [open, list.length]);

  // Reading: while open, tell the store what the visitor has seen; keep the newest in view.
  const newest = list.at(-1)?.seq ?? 0;
  useEffect(() => {
    if (!open) return;
    const box = listRef.current;
    if (box) box.scrollTop = box.scrollHeight;
    if (newest) send({ type: 'read', conversation: thread.current || null, seq: newest });
  }, [open, newest, pending.length, send]);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        launcher.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const deliver = useCallback(
    async (client_key: string, body: string) => {
      if (send({ type: 'message', client_key, body })) return;
      try {
        const message = visitorMessage(
          await api.post<unknown>('/api/chat/messages/', { client_key, body }),
        );
        if (accept([message])) setStatus('open');
        connect();
      } catch (error) {
        setPending((current) =>
          current.map((p) => (p.client_key === client_key ? { ...p, state: 'failed' } : p)),
        );
        if (error instanceof ApiError && error.status === 429) setNotice(t.rateLimited);
        else if (error instanceof ApiError && error.status === 403) setNotice(t.unavailable);
      }
    },
    [accept, connect, send, t.rateLimited, t.unavailable],
  );

  function submit() {
    const body = draft.trim();
    if (!body) return;
    const client_key = key();
    setDraft('');
    setNotice('');
    setPending((current) => [...current, { client_key, body, state: 'sending' }]);
    send({ type: 'typing', on: false });
    void deliver(client_key, body);
  }

  function retry(item: Pending) {
    setPending((current) =>
      current.map((p) => (p.client_key === item.client_key ? { ...p, state: 'sending' } : p)),
    );
    void deliver(item.client_key, item.body);
  }

  function typing(value: string) {
    setDraft(value);
    const now = Date.now();
    if (value && now - lastTyping.current > 3000) {
      lastTyping.current = now;
      send({ type: 'typing', on: true });
    }
    if (typingTimer.current) window.clearTimeout(typingTimer.current);
    typingTimer.current = window.setTimeout(
      () => send({ type: 'typing', on: false }),
      TYPING_IDLE_MS,
    );
  }

  async function upload(chosen: File) {
    const form = new FormData();
    form.append('file', chosen);
    form.append('client_key', key());
    setNotice('');
    try {
      if (accept([visitorMessage(await api.upload<unknown>('/api/chat/attachments/', form))]))
        setStatus('open');
      connect();
    } catch {
      setNotice(t.fileTooLarge);
    }
  }

  async function linkAccount() {
    try {
      await api.post('/api/chat/link-account/');
      setLinkedNotice(true);
      setState((current) => (current ? { ...current, guest_chat_linkable: false } : current));
    } catch {
      setNotice(t.unavailable);
    }
  }

  const welcome = state?.config.welcome ?? '';
  const left = state?.config.position === 'left';
  const label = state?.config.button_label || t.title;
  const timeFormat = useMemo(
    () =>
      new Intl.DateTimeFormat(lang === 'bn' ? 'bn-BD-u-nu-latn' : 'en-GB', {
        hour: 'numeric',
        minute: '2-digit',
      }),
    [lang],
  );
  function when(iso: string): string {
    if (!now) return timeFormat.format(new Date(iso));
    const minutes = Math.floor((now - Date.parse(iso)) / 60_000);
    if (minutes < 1) return t.now;
    if (minutes < 60) return t.minutesAgo(minutes);
    return timeFormat.format(new Date(iso));
  }

  if (!state) return null;
  const side = left ? 'left-4' : 'right-4';
  return (
    <>
      <button
        ref={launcher}
        type="button"
        data-live-chat="launcher"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={open ? t.minimize : `${label}${unread ? `, ${t.unread(unread)}` : ''}`}
        onClick={() => {
          setOpen((value) => !value);
          setUnread(0);
        }}
        className={`fixed bottom-4 ${side} z-40 flex h-12 min-w-12 items-center justify-center gap-2 rounded-base bg-store-action px-3 text-sm font-semibold text-store-on-action shadow-overlay hover:bg-store-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-store-focus print:hidden`}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M4 5h16v11H8l-4 4V5z"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </svg>
        <span className="hidden sm:inline">{open ? t.minimize : label}</span>
        {unread > 0 && !open && (
          <span className="min-w-5 rounded-badge bg-store-accent px-1 text-xs leading-5 text-store-on-accent">
            {unread}
          </span>
        )}
      </button>
      {open && (
        <section
          id={panelId}
          role="dialog"
          aria-modal="false"
          aria-label={t.title}
          className={`fixed inset-0 z-40 flex flex-col bg-store-surface text-store-ink sm:inset-auto sm:bottom-20 ${left ? 'sm:left-4' : 'sm:right-4'} sm:h-[min(560px,calc(100dvh-7rem))] sm:w-[360px] sm:rounded-base sm:border sm:border-store-line sm:shadow-overlay print:hidden`}
        >
          <header className="flex items-center gap-3 border-b border-store-line px-4 py-3">
            <div className="min-w-0 flex-1">
              <h2 className="m-0 truncate text-base font-bold">{t.title}</h2>
              <p className="m-0 flex items-center gap-1.5 text-xs text-store-muted">
                <span
                  aria-hidden="true"
                  className={`inline-block h-2 w-2 rounded-full ${online ? 'bg-store-action' : 'border border-store-muted'}`}
                />
                {online ? t.online : t.away}
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                launcher.current?.focus();
              }}
              aria-label={t.close}
              className="flex h-10 w-10 items-center justify-center rounded-base text-lg hover:bg-store-canvas focus-visible:outline-2 focus-visible:outline-store-focus"
            >
              ×
            </button>
          </header>
          {link === 'reconnecting' && (
            <p role="status" className="m-0 bg-store-canvas px-4 py-2 text-xs text-store-muted">
              {t.reconnecting}
            </p>
          )}
          <div
            ref={listRef}
            className="flex flex-1 flex-col gap-2 overflow-y-auto px-4 py-4"
            aria-live="polite"
            aria-relevant="additions"
          >
            <p className="m-0 max-w-[85%] self-start rounded-base border border-store-line bg-store-canvas px-3 py-2 text-sm leading-6 whitespace-pre-wrap">
              {online ? welcome : state.config.away_message}
            </p>
            {state.guest_chat_linkable && (
              <div className="rounded-base border border-store-line p-3 text-sm">
                <p className="m-0">{t.linkTitle}</p>
                <button
                  type="button"
                  onClick={() => void linkAccount()}
                  className="mt-2 text-sm font-semibold underline"
                >
                  {t.linkAction}
                </button>
              </div>
            )}
            {linkedNotice && <p className="m-0 text-xs text-store-muted">{t.linked}</p>}
            {list.map((message) => (
              <Bubble
                key={message.id}
                message={message}
                mine={message.direction === 'inbound'}
                stamp={when(message.created_at)}
                t={t}
                lang={lang}
              />
            ))}
            {pending.map((item) => (
              <div key={item.client_key} className="flex max-w-[85%] flex-col items-end self-end">
                <p className="m-0 rounded-base bg-store-primary px-3 py-2 text-sm leading-6 whitespace-pre-wrap text-store-on-primary opacity-70">
                  {item.body}
                </p>
                {item.state === 'failed' ? (
                  <button
                    type="button"
                    onClick={() => retry(item)}
                    className="mt-1 text-xs font-semibold text-store-accent underline"
                  >
                    {t.failed}
                  </button>
                ) : (
                  <span className="mt-1 text-xs text-store-muted">{t.sending}</span>
                )}
              </div>
            ))}
            {agentTyping && <p className="m-0 text-xs text-store-muted italic">{t.typing}</p>}
            {status === 'closed' && (
              <p className="m-0 text-center text-xs text-store-muted">{t.closedNotice}</p>
            )}
          </div>
          {notice && (
            <p role="alert" className="m-0 px-4 pb-2 text-xs text-store-accent">
              {notice}
            </p>
          )}
          <form
            className="flex items-end gap-2 border-t border-store-line p-3"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <input
              ref={file}
              type="file"
              accept="image/jpeg,image/png,image/webp,application/pdf"
              className="hidden"
              onChange={(event) => {
                const chosen = event.target.files?.[0];
                if (chosen) void upload(chosen);
                event.target.value = '';
              }}
            />
            <button
              type="button"
              aria-label={t.attach}
              onClick={() => file.current?.click()}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-base border border-store-line hover:bg-store-canvas focus-visible:outline-2 focus-visible:outline-store-focus"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M21 12l-8.5 8.5a5 5 0 01-7-7L14 5a3.5 3.5 0 015 5l-8.5 8.5a2 2 0 01-3-3L15 8"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                />
              </svg>
            </button>
            <label className="sr-only" htmlFor={`${panelId}-input`}>
              {t.placeholder}
            </label>
            <textarea
              id={`${panelId}-input`}
              ref={input}
              rows={1}
              maxLength={4000}
              value={draft}
              placeholder={t.placeholder}
              onChange={(event) => typing(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              className="max-h-32 min-h-11 flex-1 resize-none rounded-base border border-store-line bg-store-surface px-3 py-2.5 text-sm leading-5 text-store-ink focus-visible:outline-2 focus-visible:outline-store-focus"
            />
            <button
              type="submit"
              disabled={!draft.trim()}
              className="h-11 shrink-0 rounded-base bg-store-action px-4 text-sm font-semibold text-store-on-action hover:bg-store-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-store-focus disabled:opacity-50"
            >
              {t.send}
            </button>
          </form>
        </section>
      )}
    </>
  );
}

function Bubble({
  message,
  mine,
  stamp,
  t,
  lang,
}: {
  message: VisitorMessage;
  mine: boolean;
  stamp: string;
  t: ReturnType<typeof dictionary>['chat'];
  lang: string;
}) {
  const product = message.product;
  const file = message.attachment;
  return (
    <div
      className={`flex max-w-[85%] flex-col ${mine ? 'items-end self-end' : 'items-start self-start'}`}
    >
      {product ? (
        <div className="w-60 rounded-base border border-store-line bg-store-surface text-sm">
          {product.image_url && (
            // eslint-disable-next-line @next/next/no-img-element -- API-served media; no image optimiser
            <img
              src={product.image_url}
              alt=""
              className="h-32 w-full rounded-t-base object-cover"
              loading="lazy"
            />
          )}
          <div className="p-3">
            <p className="m-0 font-semibold">{product.name}</p>
            {product.variant_title && (
              <p className="m-0 text-xs text-store-muted">{product.variant_title}</p>
            )}
            <p className="m-0 mt-1 font-semibold tabular-nums">
              {formatMoney(product.amount, product.currency)}
            </p>
            {!product.in_stock && <p className="m-0 text-xs text-store-accent">{t.outOfStock}</p>}
            {safeProductLink(product.url) && (
              <a
                href={safeProductLink(product.url)}
                className="mt-2 inline-block text-sm font-semibold underline"
              >
                {t.viewProduct}
              </a>
            )}
          </div>
        </div>
      ) : file && file.url ? (
        file.content_type.startsWith('image/') ? (
          <a href={file.url} target="_blank" rel="noopener noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element -- private, checked API file */}
            <img
              src={file.url}
              alt={t.attachment}
              className="max-h-48 rounded-base border border-store-line object-contain"
              loading="lazy"
            />
          </a>
        ) : (
          <a
            href={file.url}
            className={`rounded-base px-3 py-2 text-sm underline ${mine ? 'bg-store-primary text-store-on-primary' : 'border border-store-line'}`}
          >
            {file.filename || t.attachment}
          </a>
        )
      ) : (
        <p
          lang={lang}
          className={`m-0 rounded-base px-3 py-2 text-sm leading-6 break-words whitespace-pre-wrap ${
            mine
              ? 'bg-store-primary text-store-on-primary'
              : 'border border-store-line bg-store-canvas'
          }`}
        >
          {message.body}
        </p>
      )}
      <span className="mt-1 text-xs text-store-muted">
        {mine ? `${t.you} · ` : message.sender_name ? `${message.sender_name} · ` : `${t.team} · `}
        {stamp}
      </span>
    </div>
  );
}
