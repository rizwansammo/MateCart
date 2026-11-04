'use client';

import type { PublicPromotion } from '@matecart/shared-contracts';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { messages } from '@/lib/i18n';

/** Remaining whole d/h/m/s until ``target`` (never negative). */
export function remaining(target: number, now: number) {
  const total = Math.max(0, Math.floor((target - now) / 1000));
  return {
    total,
    d: Math.floor(total / 86400),
    h: Math.floor((total % 86400) / 3600),
    m: Math.floor((total % 3600) / 60),
    s: total % 60,
  };
}

/**
 * A countdown to a time the server set (a promotion's end or a flash sale's start). The server
 * clock is authoritative: ``serverTime`` (from the same response) corrects the browser's clock.
 * Nothing renders until mounted, so server and browser HTML always match. When the time is
 * reached the page refreshes and the server shows the price that now applies.
 */
export function Countdown({
  target,
  serverTime,
  lang,
  mode,
}: {
  target: string;
  serverTime?: string;
  lang: string;
  mode: 'ends' | 'starts';
}) {
  const t = messages(lang);
  const router = useRouter();
  const skew = useRef(0);
  const [now, setNow] = useState<number | null>(null);
  const refreshed = useRef(false);
  const end = Date.parse(target);

  useEffect(() => {
    const server = serverTime ? Date.parse(serverTime) : Number.NaN;
    skew.current = Number.isNaN(server) ? 0 : server - Date.now();
    const tick = () => setNow(Date.now() + skew.current);
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [serverTime]);

  useEffect(() => {
    if (now !== null && now >= end && !refreshed.current) {
      refreshed.current = true;
      router.refresh();
    }
  }, [now, end, router]);

  if (now === null || Number.isNaN(end)) return null;
  const left = remaining(end, now);
  if (left.total === 0) {
    return (
      <span role="status" className="text-[13px] text-store-muted">
        {mode === 'ends' ? t.offerEnded : ''}
      </span>
    );
  }
  const text = t.countdown(left.d, left.h, left.m, left.s);
  return (
    <span className="text-[13px] font-semibold tabular-nums" aria-live="off">
      {mode === 'ends' ? t.endsIn(text) : t.startsIn(text)}
    </span>
  );
}

/** The promotion's badge: the merchant's text for campaigns, a fixed label for flash sales. */
export function promotionBadge(promotion: PublicPromotion, lang: string): string {
  if (promotion.kind === 'flash_sale') return messages(lang).flashSale;
  return promotion.badge;
}

export function PromotionBadge({
  promotion,
  lang,
  className = '',
}: {
  promotion: PublicPromotion;
  lang: string;
  className?: string;
}) {
  const tone =
    promotion.kind === 'featured' ? 'bg-info-bg text-info-fg' : 'bg-danger-bg text-danger-fg';
  return (
    <span
      className={`inline-flex items-center rounded-badge px-2 py-0.5 text-[11.5px] font-bold tracking-wide uppercase ${tone} ${className}`}
    >
      {promotionBadge(promotion, lang)}
    </span>
  );
}
