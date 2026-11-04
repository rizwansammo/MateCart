/**
 * Visitor consent and first-party tracking identifiers (Phase 12, D-083). Browser-only helpers;
 * the server records every decision and is the authority for server-side events.
 *
 * - `mc_consent` = `1:<a><m>:<32 hex>` is set by the API when the visitor decides. Missing or
 *   malformed means "no decision": only strictly necessary storage is used.
 * - `mc_vid` (random visitor id) and `mc_src` (last tagged landing) exist only with analytics
 *   consent and are removed when it is withdrawn.
 */

export const CONSENT_COOKIE = 'mc_consent';
export const VISITOR_COOKIE = 'mc_vid';
export const SOURCE_COOKIE = 'mc_src';
export const CONSENT_CHANGED = 'matecart:consent-changed';
export const OPEN_CONSENT = 'matecart:open-consent';

export interface ConsentState {
  analytics: boolean;
  marketing: boolean;
}

const VALUE = /^1:([01])([01]):[0-9a-f]{32}$/;
const VISITOR = /^[0-9a-f]{32}$/;
const PART = /[^a-z0-9._-]/g;

export function parseConsent(value: string | null | undefined): ConsentState | null {
  const match = VALUE.exec(value ?? '');
  if (!match) return null;
  return { analytics: match[1] === '1', marketing: match[2] === '1' };
}

export function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  for (const part of document.cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) {
      try {
        return decodeURIComponent(rest.join('='));
      } catch {
        return null;
      }
    }
  }
  return null;
}

export function readConsent(): ConsentState | null {
  return parseConsent(readCookie(CONSENT_COOKIE));
}

function secure(): string {
  return typeof location !== 'undefined' && location.protocol === 'https:' ? '; Secure' : '';
}

export function writeCookie(name: string, value: string, maxAgeSeconds: number): void {
  document.cookie = `${name}=${encodeURIComponent(value)}; Max-Age=${maxAgeSeconds}; Path=/; SameSite=Lax${secure()}`;
}

export function deleteCookie(name: string): void {
  document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax${secure()}`;
}

/** The random analytics visitor id, created only when analytics consent was given. */
export function visitorId(consent: ConsentState | null): string | null {
  if (!consent?.analytics) return null;
  const existing = readCookie(VISITOR_COOKIE);
  if (existing && VISITOR.test(existing)) return existing;
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const id = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  writeCookie(VISITOR_COOKIE, id, 60 * 60 * 24 * 395);
  return id;
}

/** A UTM value normalised to the server's rule: lowercase, `[a-z0-9._-]`, at most 64. */
export function cleanUtm(value: string | null): string {
  const text = (value ?? '').trim().toLowerCase().replace(PART, '').slice(0, 64);
  return /^[a-z0-9]/.test(text) ? text : '';
}

/** Captures a tagged landing into `mc_src` (analytics consent only). True when it did. */
export function captureSource(
  search: string,
  consent: ConsentState | null,
  now = Date.now(),
): boolean {
  if (!consent?.analytics) return false;
  const params = new URLSearchParams(search);
  const source = cleanUtm(params.get('utm_source'));
  if (!source) return false;
  const medium = cleanUtm(params.get('utm_medium'));
  const campaign = cleanUtm(params.get('utm_campaign'));
  writeCookie(
    SOURCE_COOKIE,
    `1~${source}~${medium}~${campaign}~${Math.floor(now / 1000)}`,
    60 * 60 * 24 * 30,
  );
  return true;
}

/** Withdrawal: remove optional identifiers (ours and Meta's first-party cookies). */
export function clearOptional(consent: ConsentState): void {
  if (!consent.analytics) {
    deleteCookie(VISITOR_COOKIE);
    deleteCookie(SOURCE_COOKIE);
  }
  if (!consent.marketing) {
    deleteCookie('_fbp');
    deleteCookie('_fbc');
  }
}
