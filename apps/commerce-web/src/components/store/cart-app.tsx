'use client';

import type { Cart } from '@matecart/shared-contracts';
import { errorMessage } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { createCheckout, getCart, removeCartItem, updateCartItem } from '@/lib/checkout-client';
import { formatMoney } from '@/lib/money';

import { FOCUS } from './ui';

const COPY = {
  en: {
    title: 'Your cart',
    empty: 'Your cart is empty.',
    continue: 'Continue shopping',
    quantity: 'Quantity',
    remove: 'Remove',
    subtotal: 'Subtotal',
    checkout: 'Continue to checkout',
    loading: 'Loading your cart…',
    unavailable: 'This item needs attention before checkout.',
  },
  bn: {
    title: 'আপনার কার্ট',
    empty: 'আপনার কার্ট খালি।',
    continue: 'কেনাকাটা চালিয়ে যান',
    quantity: 'পরিমাণ',
    remove: 'সরান',
    subtotal: 'সাবটোটাল',
    checkout: 'চেকআউটে যান',
    loading: 'আপনার কার্ট লোড হচ্ছে…',
    unavailable: 'চেকআউটের আগে এই পণ্যটি দেখুন।',
  },
} as const;

export function CartApp({ lang }: { lang: string }) {
  const copy = lang === 'bn' ? COPY.bn : COPY.en;
  const router = useRouter();
  const [cart, setCart] = useState<Cart | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  useEffect(() => {
    let live = true;
    getCart()
      .then((value) => {
        if (live) setCart(value);
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason));
      });
    return () => {
      live = false;
    };
  }, []);

  async function change(item: string, quantity: string) {
    setBusy(item);
    setError('');
    try {
      setCart(await updateCartItem(item, quantity));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy('');
    }
  }

  async function remove(item: string) {
    setBusy(item);
    setError('');
    try {
      setCart(await removeCartItem(item));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy('');
    }
  }

  async function checkout() {
    setBusy('checkout');
    setError('');
    try {
      const session = await createCheckout();
      router.push(`/checkout/${session.id}` as Route);
    } catch (reason) {
      setError(errorMessage(reason));
      setBusy('');
    }
  }

  if (!cart && !error) return <p className="text-store-muted">{copy.loading}</p>;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start">
      <section aria-labelledby="cart-title" className="min-w-0">
        <h1 id="cart-title" className="mt-0 store-display text-3xl sm:text-4xl">
          {copy.title}
        </h1>
        {error && (
          <p role="alert" className="rounded-base bg-danger-bg p-3 text-sm text-danger-fg">
            {error}
          </p>
        )}
        {cart?.lines.length === 0 ? (
          <div className="rounded-base border border-store-line bg-store-surface p-8 text-center">
            <p className="mt-0 text-store-muted">{copy.empty}</p>
            <Link
              href={'/shop' as Route}
              className={`inline-flex rounded-base bg-store-action px-5 py-3 font-semibold text-store-on-action ${FOCUS}`}
            >
              {copy.continue}
            </Link>
          </div>
        ) : (
          <ul className="m-0 flex list-none flex-col divide-y divide-store-line rounded-base border border-store-line bg-store-surface p-0">
            {cart?.lines.map((line) => (
              <li key={line.id} className="grid gap-4 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="min-w-0">
                  <Link
                    href={`/products/${line.product_slug}` as Route}
                    className={`font-semibold hover:underline ${FOCUS}`}
                  >
                    {line.product_name}
                  </Link>
                  <p className="my-1 text-sm text-store-muted">
                    {[line.variant_title, line.sku].filter(Boolean).join(' · ')}
                  </p>
                  {line.issue && <p className="my-1 text-sm text-danger-fg">{copy.unavailable}</p>}
                  {line.unit_price && line.currency && (
                    <p className="m-0 text-sm tabular-nums">
                      {formatMoney(line.unit_price, line.currency)}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-end gap-2 sm:justify-end">
                  <label className="flex flex-col gap-1 text-xs font-semibold text-store-muted">
                    {copy.quantity}
                    <input
                      aria-label={`${copy.quantity}: ${line.product_name}`}
                      type="number"
                      min="0.001"
                      max="999.999"
                      step="0.001"
                      defaultValue={line.quantity}
                      disabled={busy === line.id}
                      onBlur={(event) => {
                        if (event.target.value !== line.quantity) {
                          void change(line.id, event.target.value);
                        }
                      }}
                      className={`h-10 w-24 rounded-base border border-store-line bg-store-canvas px-3 text-store-ink ${FOCUS}`}
                    />
                  </label>
                  <button
                    type="button"
                    disabled={busy === line.id}
                    onClick={() => void remove(line.id)}
                    className={`h-10 rounded-base border border-store-line px-3 text-sm font-semibold hover:border-store-ink disabled:opacity-50 ${FOCUS}`}
                  >
                    {copy.remove}
                  </button>
                  {line.subtotal && line.currency && (
                    <strong className="ml-auto min-w-24 pb-2 text-right tabular-nums sm:ml-2">
                      {formatMoney(line.subtotal, line.currency)}
                    </strong>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      {cart && cart.lines.length > 0 && (
        <aside className="rounded-base border border-store-line bg-store-surface p-5 lg:sticky lg:top-36">
          <div className="flex items-center justify-between gap-4 border-b border-store-line pb-4">
            <span className="font-semibold">{copy.subtotal}</span>
            <strong className="text-xl tabular-nums">
              {formatMoney(cart.subtotal, cart.currency)}
            </strong>
          </div>
          <button
            type="button"
            disabled={!cart.valid || busy === 'checkout'}
            onClick={() => void checkout()}
            className={`mt-5 h-12 w-full rounded-base bg-store-action px-4 font-semibold text-store-on-action disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS}`}
          >
            {copy.checkout}
          </button>
        </aside>
      )}
    </div>
  );
}
