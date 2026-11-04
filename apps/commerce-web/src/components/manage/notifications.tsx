'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  NotificationCategory,
  NotificationItem,
  NotificationPreference,
} from '@matecart/shared-contracts';
import {
  notificationPage,
  notificationPreferences,
  safeNotificationLink,
  unreadCount,
} from '@matecart/shared-contracts';
import { Button, errorMessage } from '@matecart/shared-ui';
import {
  Badge,
  Card,
  Empty,
  Feedback,
  Filters,
  Loader,
  PageHeader,
  Pagination,
  SelectField,
  api,
  dateTime,
  query,
  useLoad,
} from './kit';

/** Bounded refresh: once a minute, and only while the tab is visible (no WebSockets, D-087). */
export const BELL_POLL_MS = 60_000;
const INBOX = '/api/manage/notifications/inbox/';

export const CATEGORY_LABEL: Record<NotificationCategory, string> = {
  transactional: 'Orders',
  security: 'Security',
  operational: 'Operations',
  marketing: 'Marketing',
};

function ItemLink({ item, children }: { item: NotificationItem; children: React.ReactNode }) {
  const href = safeNotificationLink(item.link, true);
  return href ? (
    <a href={href} className="block hover:underline">
      {children}
    </a>
  ) : (
    <div>{children}</div>
  );
}

export function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [error, setError] = useState('');
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      setUnread(unreadCount(await api.get<unknown>(`${INBOX}unread/`)));
    } catch {
      // The bell is a convenience; the dashboard keeps working without it.
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(refresh);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, BELL_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        button.current?.focus();
      }
    };
    const onClick = (e: MouseEvent) => {
      if (
        panel.current &&
        !panel.current.contains(e.target as Node) &&
        !button.current?.contains(e.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    panel.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [open]);

  async function openPanel() {
    setOpen(true);
    setError('');
    try {
      const page = notificationPage(await api.get<unknown>(`${INBOX}${query({ page_size: 8 })}`));
      setItems(page.results);
      const ids = page.results.filter((row) => !row.read_at).map((row) => row.id);
      if (ids.length) {
        setUnread(unreadCount(await api.post<unknown>(`${INBOX}read/`, { ids })));
      }
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function markAll() {
    try {
      setUnread(unreadCount(await api.post<unknown>(`${INBOX}read/`, { all: true })));
      setItems((rows) => rows?.map((row) => ({ ...row, read_at: row.read_at ?? 'now' })) ?? null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const label = unread ? `Notifications, ${unread} unread` : 'Notifications, nothing unread';
  return (
    <div className="relative">
      <button
        ref={button}
        type="button"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : void openPanel())}
        className="relative inline-flex h-10 w-10 items-center justify-center rounded-base border border-line-strong bg-surface text-ink hover:border-ink focus:ring-3 focus:ring-brand-tint focus:outline-none"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-5 w-5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
        >
          <path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9Z" />
          <path d="M10 20a2 2 0 0 0 4 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute -top-1.5 -right-1.5 min-w-5 rounded-full bg-danger-fg px-1.5 text-center text-[11px] leading-5 font-bold text-surface">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          ref={panel}
          role="dialog"
          aria-label="Recent notifications"
          tabIndex={-1}
          className="absolute right-0 z-30 mt-2 w-[min(92vw,380px)] rounded-base border border-line bg-surface shadow-lg outline-none"
        >
          <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
            <p className="text-sm font-bold">Notifications</p>
            <button
              type="button"
              className="text-xs font-semibold text-brand-deep hover:underline"
              onClick={() => void markAll()}
            >
              Mark all as read
            </button>
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
            {error && <p className="px-4 py-3 text-sm text-danger-fg">{error}</p>}
            {!items && !error && <p className="px-4 py-3 text-sm text-muted">Loading…</p>}
            {items && items.length === 0 && (
              <p className="px-4 py-6 text-center text-sm text-muted">Nothing new yet.</p>
            )}
            {items?.map((item) => (
              <div key={item.id} className="border-b border-line px-4 py-3 text-sm last:border-0">
                <ItemLink item={item}>
                  <p className="font-semibold">{item.title}</p>
                  <p className="mt-0.5 text-xs leading-5 text-muted">{item.body}</p>
                  <p className="mt-1 text-[11px] text-muted">{dateTime(item.created_at)}</p>
                </ItemLink>
              </div>
            ))}
          </div>
          <a
            href="/manage/notifications"
            className="block border-t border-line px-4 py-2.5 text-center text-sm font-semibold text-brand-deep hover:underline"
          >
            See all notifications
          </a>
        </div>
      )}
    </div>
  );
}

export function NotificationCenter() {
  const [page, setPage] = useState(1);
  const [unreadOnly, setUnreadOnly] = useState('');
  const [category, setCategory] = useState('');
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');
  const load = useCallback(
    async () =>
      notificationPage(
        await api.get<unknown>(
          `${INBOX}${query({ page, unread: unreadOnly || null, category: category || null })}`,
        ),
      ),
    [page, unreadOnly, category],
  );
  const { data, error, loading, reload } = useLoad(load);

  async function mark(body: { ids?: string[]; all?: boolean }) {
    setActionError('');
    try {
      await api.post(`${INBOX}read/`, body);
      setNotice(body.all ? 'Everything is marked as read.' : 'Marked as read.');
      await reload();
    } catch (e) {
      setActionError(errorMessage(e));
    }
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Workspace"
        title="Notifications"
        intro="Alerts for your role: new orders, risk reviews, deliveries, customer requests and stock. You only see notifications for work your role can open."
        actions={<Button onClick={() => void mark({ all: true })}>Mark all as read</Button>}
      />
      <Feedback error={actionError || error} notice={notice} />
      <Card flush>
        <Filters>
          <SelectField
            label="Show"
            value={unreadOnly}
            onChange={(e) => {
              setPage(1);
              setUnreadOnly(e.target.value);
            }}
            options={[
              { value: '', label: 'All notifications' },
              { value: 'true', label: 'Unread only' },
            ]}
          />
          <SelectField
            label="Type"
            value={category}
            onChange={(e) => {
              setPage(1);
              setCategory(e.target.value);
            }}
            options={[
              { value: '', label: 'All types' },
              ...Object.entries(CATEGORY_LABEL).map(([value, label]) => ({ value, label })),
            ]}
          />
        </Filters>
        {loading && !data && <Loader>Loading notifications…</Loader>}
        {data && data.results.length === 0 && (
          <Empty title="No notifications">New alerts for your role will appear here.</Empty>
        )}
        {data && data.results.length > 0 && (
          <ul className="divide-y divide-line">
            {data.results.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-start justify-between gap-3 px-5 py-4"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {!item.read_at && <Badge tone="info">New</Badge>}
                    <Badge>{CATEGORY_LABEL[item.category]}</Badge>
                    <span className="text-xs text-muted">{dateTime(item.created_at)}</span>
                  </div>
                  <div className="mt-1.5 text-sm">
                    <ItemLink item={item}>
                      <p className="font-semibold">{item.title}</p>
                      <p className="mt-0.5 leading-6 text-muted">{item.body}</p>
                    </ItemLink>
                  </div>
                </div>
                {!item.read_at && (
                  <Button variant="secondary" onClick={() => void mark({ ids: [item.id] })}>
                    Mark as read
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {data && (
          <Pagination
            page={data.page}
            pageSize={data.page_size}
            count={data.count}
            onPage={setPage}
          />
        )}
      </Card>
      <StaffAlertPreferences />
    </div>
  );
}

export function StaffAlertPreferences() {
  const load = useCallback(
    async () =>
      notificationPreferences(await api.get<unknown>('/api/manage/notifications/preferences/')),
    [],
  );
  const { data, setData, error } = useLoad(load);
  const [saveError, setSaveError] = useState('');
  async function toggle(row: NotificationPreference) {
    setSaveError('');
    try {
      setData(
        notificationPreferences(
          await api.put<unknown>('/api/manage/notifications/preferences/', {
            topic: row.topic,
            channel: row.channel,
            enabled: !row.enabled,
          }),
        ),
      );
    } catch (e) {
      setSaveError(errorMessage(e));
    }
  }
  return (
    <Card title="My email alerts">
      <p className="mb-4 text-sm leading-6 text-muted">
        In-app notifications are always on. Choose which alerts you also want by email. You only
        receive alerts for work your role allows.
      </p>
      <Feedback error={saveError || error} />
      {data ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {data.map((row) => (
            <label key={row.topic} className="flex items-center gap-3 text-sm">
              <input
                type="checkbox"
                className="h-4 w-4"
                checked={row.enabled}
                onChange={() => void toggle(row)}
              />
              {row.title}
            </label>
          ))}
        </div>
      ) : (
        <Loader />
      )}
    </Card>
  );
}
