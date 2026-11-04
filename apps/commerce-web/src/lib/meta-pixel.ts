/**
 * Meta Pixel (D-084). The script is injected only by `TrackingProvider`, and only when the
 * store configured a Pixel id **and** the visitor granted marketing consent. Every helper here
 * is a no-op until then, so pages can call them unconditionally without any network request.
 * Purchase events carry `purchase.<order id>`, the same event id the server's Conversions API
 * uses, so Meta counts each purchase once.
 */

type Fbq = ((...args: unknown[]) => void) & {
  callMethod?: (...args: unknown[]) => void;
  queue: unknown[][];
  push: Fbq;
  loaded: boolean;
  version: string;
};

declare global {
  interface Window {
    fbq?: Fbq;
    _fbq?: Fbq;
  }
}

const PIXEL_SRC = 'https://connect.facebook.net/en_US/fbevents.js';
const sent = new Set<string>();

export function pixelLoaded(): boolean {
  return typeof window !== 'undefined' && typeof window.fbq === 'function';
}

/** The official loader, written out: a queueing `fbq` stub and the async fbevents.js script. */
export function loadPixel(pixelId: string): void {
  if (typeof window === 'undefined' || window.fbq || !/^[0-9]{5,20}$/.test(pixelId)) return;
  const queue: unknown[][] = [];
  const stub = ((...args: unknown[]) => {
    if (stub.callMethod) stub.callMethod(...args);
    else queue.push(args);
  }) as Fbq;
  stub.queue = queue;
  stub.push = stub;
  stub.loaded = true;
  stub.version = '2.0';
  window.fbq = stub;
  window._fbq = stub;
  const script = document.createElement('script');
  script.async = true;
  script.src = PIXEL_SRC;
  script.dataset.matecart = 'meta-pixel';
  document.head.appendChild(script);
  stub('init', pixelId);
}

/**
 * Track once per ``key`` (React re-renders, Strict Mode and repeated effects never duplicate an
 * event). ``eventId`` is passed to Meta for browser/server deduplication.
 */
export function track(
  key: string,
  event: 'PageView' | 'ViewContent' | 'AddToCart' | 'InitiateCheckout' | 'Purchase',
  data?: Record<string, unknown>,
  eventId?: string,
): boolean {
  if (!pixelLoaded() || sent.has(key)) return false;
  sent.add(key);
  const fbq = window.fbq;
  if (!fbq) return false;
  if (eventId) fbq('track', event, data ?? {}, { eventID: eventId });
  else if (data) fbq('track', event, data);
  else fbq('track', event);
  return true;
}

/** Decimal string → number for Meta's payload only (Meta's wire format is a JSON number). */
export function pixelValue(amount: string | null | undefined): number | undefined {
  if (!amount) return undefined;
  const value = Number(amount);
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : undefined;
}

/** Test helper: forget what was sent. */
export function resetPixelForTests(): void {
  sent.clear();
  if (typeof window !== 'undefined') {
    delete window.fbq;
    delete window._fbq;
  }
}

/**
 * Whether the browser may report this order as a Meta Purchase (D-084): when the merchant
 * reports at placement, any placed order that is neither awaiting online payment nor cancelled;
 * otherwise only an order whose payment is verified. Cash-on-delivery orders are never called
 * paid by the browser.
 */
export function purchaseReportable(
  order: { status: string; payment_status: string },
  timing: 'payment_verified' | 'order_placed',
): boolean {
  if (timing === 'order_placed')
    return order.status !== 'pending_payment' && order.status !== 'cancelled';
  return order.payment_status === 'paid';
}

/** Report a Purchase once per order and browser (a reload never repeats it). */
export function trackPurchase(
  order: {
    id: string;
    status: string;
    payment_status: string;
    currency: string;
    grand_total: string;
    items: { variant: string }[];
  },
  timing: 'payment_verified' | 'order_placed',
): boolean {
  if (!pixelLoaded() || !purchaseReportable(order, timing)) return false;
  const storageKey = `mc_px_purchase_${order.id}`;
  try {
    if (window.localStorage.getItem(storageKey)) return false;
  } catch {
    // Storage blocked: the in-memory guard and Meta's event-id deduplication still apply.
  }
  const sentNow = track(
    `purchase:${order.id}`,
    'Purchase',
    {
      currency: order.currency,
      value: pixelValue(order.grand_total),
      // Variant ids, the same content ids the server's Conversions API event sends.
      content_ids: order.items.map((item) => item.variant),
      content_type: 'product',
      num_items: order.items.length,
    },
    `purchase.${order.id}`,
  );
  if (sentNow) {
    try {
      window.localStorage.setItem(storageKey, '1');
    } catch {
      // ignore
    }
  }
  return sentNow;
}
