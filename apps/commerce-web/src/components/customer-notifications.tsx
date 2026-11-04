'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  notificationPage,
  notificationPreferences,
  safeNotificationLink,
  type NotificationItem,
  type NotificationPreference,
} from '@matecart/shared-contracts';
import { Alert, Button, errorMessage } from '@matecart/shared-ui';

import { authApi as api } from '@/lib/auth-client';
import { LANGUAGE_COOKIE, formatDate, messages, type Messages } from '@/lib/i18n';

/** The customer's language, read the same way the storefront server does (first-party cookie). */
export function clientMessages(): { t: Messages; lang: string } {
  const value =
    typeof document === 'undefined'
      ? ''
      : (document.cookie
          .split('; ')
          .find((part) => part.startsWith(`${LANGUAGE_COOKIE}=`))
          ?.split('=')[1] ?? '');
  const lang = value === 'bn' ? 'bn' : 'en';
  return { t: messages(lang), lang };
}

/**
 * The signed-in customer's own notifications (order, payment, delivery, request and security
 * updates). The API only ever returns this customer's rows; links stay on this store.
 */
export function CustomerNotifications() {
  const { t, lang } = clientMessages();
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    try {
      setItems(
        notificationPage(await api.get<unknown>('/api/account/notifications/?page_size=20'))
          .results,
      );
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);
  async function markAll() {
    try {
      await api.post('/api/account/notifications/read/', { all: true });
      await load();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  const unread = items?.some((item) => !item.read_at) ?? false;
  return (
    <section
      id="notifications"
      className="mb-6 rounded-base border border-line bg-surface p-6"
      aria-labelledby="customer-notifications"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="customer-notifications" className="text-lg font-bold">
          {t.notifications}
        </h2>
        {unread && (
          <Button variant="secondary" onClick={() => void markAll()}>
            {t.markAllRead}
          </Button>
        )}
      </div>
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      {items && items.length === 0 && (
        <p className="mt-3 text-sm text-muted">{t.noNotifications}</p>
      )}
      {items && items.length > 0 && (
        <ul className="mt-3 divide-y divide-line">
          {items.map((item) => {
            const href = safeNotificationLink(item.link, false);
            const content = (
              <>
                <p className="flex flex-wrap items-center gap-2 font-semibold">
                  {!item.read_at && (
                    <span className="rounded-badge bg-info-bg px-1.5 text-[11px] text-info-fg">
                      {t.newBadge}
                    </span>
                  )}
                  {item.title}
                </p>
                <p className="mt-0.5 text-muted">{item.body}</p>
                <p className="mt-1 text-xs text-muted">{formatDate(item.created_at, lang)}</p>
              </>
            );
            return (
              <li key={item.id} className="py-3 text-sm">
                {href ? (
                  <a href={href} className="block hover:underline">
                    {content}
                  </a>
                ) : (
                  content
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Per-channel choices. Essential order and security email is shown as always on. */
export function CommunicationPreferences() {
  const { t } = clientMessages();
  const [rows, setRows] = useState<NotificationPreference[] | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    void api
      .get<unknown>('/api/account/notifications/preferences/')
      .then((value) => setRows(notificationPreferences(value)))
      .catch((e: unknown) => setError(errorMessage(e)));
  }, []);
  async function toggle(row: NotificationPreference) {
    setError('');
    setNotice('');
    try {
      setRows(
        notificationPreferences(
          await api.put<unknown>('/api/account/notifications/preferences/', {
            topic: row.topic,
            channel: row.channel,
            enabled: !row.enabled,
          }),
        ),
      );
      setNotice(t.preferenceSaved);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <section
      id="communication"
      className="mb-6 rounded-base border border-line bg-surface p-6"
      aria-labelledby="communication-preferences"
    >
      <h2 id="communication-preferences" className="text-lg font-bold">
        {t.communicationPreferences}
      </h2>
      <p className="mt-2 text-sm leading-6 text-muted">{t.preferencesIntro}</p>
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
      {notice && (
        <div className="mt-3">
          <Alert tone="success">{notice}</Alert>
        </div>
      )}
      {rows && (
        <ul className="mt-3 grid gap-2">
          {rows.map((row) => (
            <li key={`${row.topic}-${row.channel}`}>
              <label className="flex items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={row.enabled}
                  disabled={row.locked}
                  onChange={() => void toggle(row)}
                />
                <span>
                  {row.title} · {row.channel === 'sms' ? t.channelSms : t.channelEmail}
                  {row.locked && (
                    <span className="ml-2 text-xs text-muted">({t.alwaysOnEssential})</span>
                  )}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** One explicit click, so link scanners that open the URL never unsubscribe anyone. */
/** The page's own words, as plain strings: message objects hold functions and cannot be passed
 * from a server component to this client component. */
export interface UnsubscribeCopy {
  unsubscribeMissing: string;
  unsubscribeDone: string;
  unsubscribeBody: string;
  unsubscribeFailed: string;
  unsubscribeButton: string;
}

export function UnsubscribeForm({ token, t }: { token: string; t: UnsubscribeCopy }) {
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'failed'>('idle');
  if (!token) return <p className="text-sm text-muted">{t.unsubscribeMissing}</p>;
  if (state === 'done') return <Alert tone="success">{t.unsubscribeDone}</Alert>;
  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        setState('busy');
        void api
          .post('/api/notifications/unsubscribe/', { token })
          .then(() => setState('done'))
          .catch(() => setState('failed'));
      }}
    >
      <p className="text-sm leading-6 text-muted">{t.unsubscribeBody}</p>
      {state === 'failed' && <Alert>{t.unsubscribeFailed}</Alert>}
      <div>
        <Button type="submit" busy={state === 'busy'}>
          {t.unsubscribeButton}
        </Button>
      </div>
    </form>
  );
}
