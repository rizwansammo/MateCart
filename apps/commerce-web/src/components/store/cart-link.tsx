'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { CART_UPDATED_EVENT, getCart } from '@/lib/checkout-client';

import { BagIcon, FOCUS } from './ui';

export function CartLink({ label, compact = false }: { label: string; compact?: boolean }) {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let active = true;
    void getCart()
      .then((value) => active && setCount(value.item_count))
      .catch(() => undefined);
    const update = (event: Event) => {
      const detail = (event as CustomEvent<{ item_count?: unknown }>).detail;
      if (typeof detail?.item_count === 'number') setCount(detail.item_count);
    };
    window.addEventListener(CART_UPDATED_EVENT, update);
    return () => {
      active = false;
      window.removeEventListener(CART_UPDATED_EVENT, update);
    };
  }, []);

  return (
    <Link
      href="/cart"
      aria-label={compact ? `${label}: ${count}` : undefined}
      className={`relative flex h-10 items-center gap-2 rounded-base px-2.5 text-[13.5px] font-semibold ${FOCUS}`}
    >
      <BagIcon />
      {!compact && <span>{label}</span>}
      {count > 0 && (
        <span className="flex min-w-5 items-center justify-center rounded-full bg-store-action px-1 text-[11px] leading-5 text-store-on-action">
          {count > 99 ? '99+' : count}
        </span>
      )}
    </Link>
  );
}
