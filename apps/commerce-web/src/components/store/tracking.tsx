'use client';

import { consentDecision } from '@matecart/shared-contracts';
import { usePathname } from 'next/navigation';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { authApi } from '@/lib/auth-client';
import {
  CONSENT_CHANGED,
  OPEN_CONSENT,
  captureSource,
  clearOptional,
  readConsent,
  visitorId,
  type ConsentState,
} from '@/lib/consent';
import { messages } from '@/lib/i18n';
import { loadPixel, pixelLoaded, pixelValue, track } from '@/lib/meta-pixel';

import { FOCUS } from './ui';

/**
 * Consent banner, first-party view events and the optional Meta Pixel (D-083, D-084).
 *
 * - Nothing optional runs before a decision: no visitor id, no campaign cookie, no Pixel.
 * - Page views are reported for every visitor without an identifier when the store's analytics
 *   is on; with analytics consent they carry the random `mc_vid` (set by this file) so unique
 *   visitors can be counted.
 * - The Pixel loads only when the store configured a Pixel id **and** marketing was accepted.
 * - Withdrawing consent deletes the optional cookies and reloads the page, so an already loaded
 *   Meta script is gone and nothing optional runs afterwards.
 * Every network call here is best effort: a failure never affects shopping.
 */

export type ViewEvent = 'product_view' | 'category_view' | 'collection_view';

const sentViews = new Set<string>();
let lastPageKey = '';

export async function sendEvent(
  name: 'page_view' | ViewEvent,
  objectId?: string,
  landing = false,
): Promise<void> {
  try {
    await authApi.post('/api/analytics/events/', {
      name,
      ...(objectId ? { object_id: objectId } : {}),
      ...(landing ? { landing: true } : {}),
    });
  } catch {
    // Analytics never interrupts the visitor.
  }
}

/** Test helper. */
export function resetTrackingForTests(): void {
  sentViews.clear();
  lastPageKey = '';
}

interface TrackingConfig {
  analytics: boolean;
  meta_pixel_id: string | null;
}

export function TrackingProvider({ config, lang }: { config: TrackingConfig; lang: string }) {
  const pathname = usePathname();
  const [consent, setConsent] = useState<ConsentState | null>(null);
  const [decided, setDecided] = useState(true);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Cookies exist only in the browser: read them after hydration, outside the effect body.
    void Promise.resolve().then(() => {
      const current = readConsent();
      setConsent(current);
      setDecided(current !== null);
      setReady(true);
    });
    const changed = () => {
      const next = readConsent();
      setConsent(next);
      setDecided(next !== null);
    };
    window.addEventListener(CONSENT_CHANGED, changed);
    return () => window.removeEventListener(CONSENT_CHANGED, changed);
  }, []);

  useEffect(() => {
    if (ready && consent?.marketing && config.meta_pixel_id) loadPixel(config.meta_pixel_id);
  }, [ready, consent, config.meta_pixel_id]);

  useEffect(() => {
    if (!ready) return;
    const key = `${pathname}${window.location.search}`;
    // Strict Mode and re-renders run this twice for one navigation; only the first counts.
    if (key === lastPageKey) return;
    lastPageKey = key;
    if (config.analytics) {
      visitorId(consent);
      const landing = captureSource(window.location.search, consent);
      void sendEvent('page_view', undefined, landing);
    }
    track(`page:${key}`, 'PageView');
  }, [ready, pathname, consent, config.analytics]);

  return <ConsentBanner lang={lang} show={ready && !decided} />;
}

/** Product, category and collection pages: one view event per page and object. */
export function TrackView({
  event,
  objectId,
  analytics,
  content,
}: {
  event: ViewEvent;
  objectId: string;
  analytics: boolean;
  /** For Meta ViewContent on product pages (genuine displayed price). */
  content?: { id: string; currency: string; amount: string };
}) {
  useEffect(() => {
    const key = `${event}:${objectId}:${window.location.pathname}`;
    if (sentViews.has(key)) return;
    sentViews.add(key);
    if (analytics) void sendEvent(event, objectId);
    if (content) {
      const send = () =>
        track(`view:${content.id}`, 'ViewContent', {
          content_ids: [content.id],
          content_type: 'product',
          currency: content.currency,
          value: pixelValue(content.amount),
        });
      // The Pixel may load just after this page's first effect.
      if (!send() && !pixelLoaded()) window.setTimeout(send, 1500);
    }
  }, [event, objectId, analytics, content]);
  return null;
}

/** Footer link that reopens the preferences. */
export function CookiePreferencesButton({
  label,
  className,
}: {
  label: string;
  className: string;
}) {
  return (
    <button
      type="button"
      className={className}
      onClick={() => window.dispatchEvent(new CustomEvent(OPEN_CONSENT))}
    >
      {label}
    </button>
  );
}

export function ConsentBanner({ lang, show }: { lang: string; show: boolean }) {
  const t = messages(lang);
  const [reopened, setReopened] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const open = reopened || (show && !dismissed);
  const [managing, setManaging] = useState(false);
  const [analytics, setAnalytics] = useState(false);
  const [marketing, setMarketing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const titleId = useId();
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const reopen = () => {
      const current = readConsent();
      setAnalytics(current?.analytics ?? false);
      setMarketing(current?.marketing ?? false);
      setManaging(true);
      setReopened(true);
    };
    window.addEventListener(OPEN_CONSENT, reopen);
    return () => window.removeEventListener(OPEN_CONSENT, reopen);
  }, []);
  useEffect(() => {
    if (open) dialog.current?.focus();
  }, [open, managing]);

  const decide = useCallback(
    async (choice: ConsentState, origin: 'banner' | 'preferences') => {
      setBusy(true);
      setError('');
      const before = readConsent();
      try {
        consentDecision(
          await authApi.post<unknown>('/api/consent/', {
            analytics: choice.analytics,
            marketing: choice.marketing,
            origin,
          }),
        );
        clearOptional(choice);
        setDismissed(true);
        setReopened(false);
        setManaging(false);
        const withdrawn =
          (before?.analytics && !choice.analytics) || (before?.marketing && !choice.marketing);
        if (withdrawn) {
          // Unload anything optional that already ran on this page.
          window.location.reload();
          return;
        }
        window.dispatchEvent(new CustomEvent(CONSENT_CHANGED));
      } catch {
        setError(t.consentFailed);
      } finally {
        setBusy(false);
      }
    },
    [t.consentFailed],
  );

  if (!open) return null;
  const button = `min-h-11 flex-1 rounded-base border px-4 text-sm font-semibold disabled:opacity-50 ${FOCUS}`;
  return (
    <div
      ref={dialog}
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      tabIndex={-1}
      className="fixed inset-x-3 bottom-3 z-50 mx-auto max-w-2xl rounded-base border border-store-line bg-store-surface p-5 text-store-ink shadow-overlay outline-none sm:inset-x-6"
    >
      <h2 id={titleId} className="m-0 text-lg font-bold">
        {t.consentTitle}
      </h2>
      <p className="mt-2 mb-4 text-sm leading-6 text-store-muted">{t.consentBody}</p>
      {managing && (
        <fieldset className="m-0 mb-4 grid gap-3 border-0 p-0">
          <div className="flex items-start justify-between gap-3 rounded-base bg-store-canvas p-3">
            <span>
              <span className="block text-sm font-semibold">{t.consentNecessary}</span>
              <span className="block text-xs leading-5 text-store-muted">
                {t.consentNecessaryBody}
              </span>
            </span>
            <span className="text-xs font-semibold whitespace-nowrap">{t.alwaysOn}</span>
          </div>
          {(
            [
              ['analytics', t.consentAnalytics, t.consentAnalyticsBody, analytics, setAnalytics],
              ['marketing', t.consentMarketing, t.consentMarketingBody, marketing, setMarketing],
            ] as const
          ).map(([key, label, body, checked, set]) => (
            <label
              key={key}
              className="flex items-start justify-between gap-3 rounded-base bg-store-canvas p-3"
            >
              <span>
                <span className="block text-sm font-semibold">{label}</span>
                <span className="block text-xs leading-5 text-store-muted">{body}</span>
              </span>
              <input
                type="checkbox"
                name={key}
                checked={checked}
                onChange={(event) => set(event.target.checked)}
                className="mt-1 h-5 w-5 shrink-0"
              />
            </label>
          ))}
        </fieldset>
      )}
      {error && (
        <p role="alert" className="mt-0 mb-3 text-sm text-danger-fg">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          className={`${button} border-store-ink bg-store-surface`}
          onClick={() =>
            void decide({ analytics: false, marketing: false }, managing ? 'preferences' : 'banner')
          }
        >
          {t.rejectOptional}
        </button>
        <button
          type="button"
          disabled={busy}
          className={`${button} border-store-ink bg-store-surface`}
          onClick={() =>
            void decide({ analytics: true, marketing: true }, managing ? 'preferences' : 'banner')
          }
        >
          {t.acceptAll}
        </button>
        {managing ? (
          <button
            type="button"
            disabled={busy}
            className={`${button} border-store-ink bg-store-surface`}
            onClick={() => void decide({ analytics, marketing }, 'preferences')}
          >
            {t.savePreferences}
          </button>
        ) : (
          <button
            type="button"
            disabled={busy}
            className={`${button} border-store-line bg-store-surface`}
            onClick={() => setManaging(true)}
          >
            {t.managePreferences}
          </button>
        )}
      </div>
    </div>
  );
}
