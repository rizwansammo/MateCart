'use client';

import type { Order, OrderPaymentStatus, OrderStatus } from '@matecart/shared-contracts';
import { errorMessage } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { cancelOrder, getOrder, startPayment } from '@/lib/checkout-client';
import { formatMoney } from '@/lib/money';

import { FOCUS } from './ui';

const COPY = {
  en: {
    loading: 'Loading your order…',
    placed: 'Thank you, your order is placed',
    awaiting: 'Complete your payment',
    cancelledTitle: 'Order cancelled',
    number: 'Order number',
    date: 'Placed',
    status: 'Order status',
    payment: 'Payment',
    items: 'Items',
    subtotal: 'Items',
    discount: 'Discount',
    shipping: 'Delivery',
    tax: 'Tax',
    total: 'Total',
    paid: 'Paid',
    refunded: 'Refunded',
    delivery: 'Delivery',
    slot: 'Delivery time',
    invoice: 'View invoice',
    cancel: 'Cancel order',
    confirmCancel: 'Cancel this order? Reserved items will be released.',
    pay: 'Pay now',
    refresh: 'Check payment status',
    payBy: 'Pay before',
    statuses: {
      pending_payment: 'Awaiting payment',
      placed: 'Placed — awaiting store confirmation',
      confirmed: 'Confirmed',
      processing: 'Being prepared',
      cancelled: 'Cancelled',
    } satisfies Record<OrderStatus, string>,
    cod: 'Cash on delivery — pay when your order arrives.',
    online: 'Online payment',
    payments: {
      unpaid: 'Not paid yet',
      paid: 'Paid',
      partially_refunded: 'Partly refunded',
      refunded: 'Refunded',
      failed: 'Payment not completed',
    } satisfies Record<OrderPaymentStatus, string>,
  },
  bn: {
    loading: 'আপনার অর্ডার লোড হচ্ছে…',
    placed: 'ধন্যবাদ, আপনার অর্ডার গ্রহণ করা হয়েছে',
    awaiting: 'আপনার পেমেন্ট সম্পন্ন করুন',
    cancelledTitle: 'অর্ডার বাতিল হয়েছে',
    number: 'অর্ডার নম্বর',
    date: 'অর্ডারের সময়',
    status: 'অর্ডারের অবস্থা',
    payment: 'পেমেন্ট',
    items: 'পণ্য',
    subtotal: 'পণ্য',
    discount: 'ছাড়',
    shipping: 'ডেলিভারি',
    tax: 'কর',
    total: 'মোট',
    paid: 'পরিশোধিত',
    refunded: 'ফেরত',
    delivery: 'ডেলিভারি',
    slot: 'ডেলিভারির সময়',
    invoice: 'ইনভয়েস দেখুন',
    cancel: 'অর্ডার বাতিল করুন',
    confirmCancel: 'অর্ডারটি বাতিল করবেন? সংরক্ষিত পণ্য ছেড়ে দেওয়া হবে।',
    pay: 'এখনই পরিশোধ করুন',
    refresh: 'পেমেন্টের অবস্থা দেখুন',
    payBy: 'পরিশোধের শেষ সময়',
    statuses: {
      pending_payment: 'পেমেন্টের অপেক্ষায়',
      placed: 'গ্রহণ করা হয়েছে — স্টোরের নিশ্চিতকরণের অপেক্ষায়',
      confirmed: 'নিশ্চিত',
      processing: 'প্রস্তুত হচ্ছে',
      cancelled: 'বাতিল',
    } satisfies Record<OrderStatus, string>,
    cod: 'ক্যাশ অন ডেলিভারি — অর্ডার হাতে পেয়ে পরিশোধ করুন।',
    online: 'অনলাইন পেমেন্ট',
    payments: {
      unpaid: 'এখনো পরিশোধ হয়নি',
      paid: 'পরিশোধিত',
      partially_refunded: 'আংশিক ফেরত',
      refunded: 'ফেরত দেওয়া হয়েছে',
      failed: 'পেমেন্ট সম্পন্ন হয়নি',
    } satisfies Record<OrderPaymentStatus, string>,
  },
} as const;

export function OrderApp({ id, lang }: { id: string; lang: string }) {
  const copy = lang === 'bn' ? COPY.bn : COPY.en;
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dates = new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeStyle: 'short' });

  useEffect(() => {
    let live = true;
    getOrder(id)
      .then((value) => {
        if (live) setOrder(value);
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason));
      });
    return () => {
      live = false;
    };
  }, [id]);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!order && !error) return <p className="text-store-muted">{copy.loading}</p>;
  if (!order)
    return (
      <p role="alert" className="text-danger-fg">
        {error}
      </p>
    );

  const title =
    order.status === 'cancelled'
      ? copy.cancelledTitle
      : order.status === 'pending_payment'
        ? copy.awaiting
        : copy.placed;
  const totals: Array<[string, string]> = [
    [copy.subtotal, order.item_subtotal],
    [copy.discount, `-${order.discount_total}`],
    [copy.shipping, order.shipping_total],
    [copy.tax, order.tax_total],
  ];
  const address = [
    order.recipient_name,
    order.address_line_1,
    order.address_line_2,
    [order.city, order.region, order.postal_code].filter(Boolean).join(', '),
    order.country,
  ].filter(Boolean);

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
      <div className="flex min-w-0 flex-col gap-6">
        <div>
          <h1 className="m-0 store-display text-3xl sm:text-4xl">{title}</h1>
          <dl className="mt-4 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
            <dt className="text-store-muted">{copy.number}</dt>
            <dd className="m-0 font-semibold tabular-nums">{order.number}</dd>
            <dt className="text-store-muted">{copy.date}</dt>
            <dd className="m-0">{dates.format(new Date(order.placed_at))}</dd>
            <dt className="text-store-muted">{copy.status}</dt>
            <dd className="m-0 font-semibold">{copy.statuses[order.status]}</dd>
          </dl>
        </div>
        {error && (
          <p role="alert" className="rounded-base bg-danger-bg p-3 text-sm text-danger-fg">
            {error}
          </p>
        )}
        <section
          aria-label={copy.payment}
          className="rounded-base border border-store-line bg-store-surface p-5"
        >
          <h2 className="mt-0 text-lg">{copy.payment}</h2>
          <p className="m-0">{order.payment_method === 'cod' ? copy.cod : copy.online}</p>
          <p className="mb-0 font-semibold">{copy.payments[order.payment_status]}</p>
          {order.can_pay && order.payment_due_at && (
            <p className="mb-0 text-sm text-store-muted">
              {copy.payBy}: {dates.format(new Date(order.payment_due_at))}
            </p>
          )}
          {order.status === 'pending_payment' && (
            <div className="mt-4 flex flex-wrap gap-3">
              {order.can_pay && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const { redirect_url } = await startPayment(order.id);
                      window.location.assign(redirect_url);
                    })
                  }
                  className={`h-11 rounded-base bg-store-action px-5 font-semibold text-store-on-action disabled:opacity-50 ${FOCUS}`}
                >
                  {copy.pay}
                </button>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={() => void act(async () => setOrder(await getOrder(order.id)))}
                className={`h-11 rounded-base border border-store-ink px-5 font-semibold disabled:opacity-50 ${FOCUS}`}
              >
                {copy.refresh}
              </button>
            </div>
          )}
        </section>
        {order.requires_shipping && (
          <section
            aria-label={copy.delivery}
            className="rounded-base border border-store-line bg-store-surface p-5"
          >
            <h2 className="mt-0 text-lg">{copy.delivery}</h2>
            <address className="not-italic">
              {address.map((line) => (
                <span key={line} className="block">
                  {line}
                </span>
              ))}
            </address>
            {order.shipping_method_name && (
              <p className="mb-0 text-sm">{order.shipping_method_name}</p>
            )}
            {order.delivery_slot_label && (
              <p className="mb-0 text-sm">
                {copy.slot}: {order.delivery_slot_label}
              </p>
            )}
          </section>
        )}
        <div className="flex flex-wrap gap-4">
          {order.invoice_number && (
            <Link
              href={`/orders/${order.id}/invoice` as Route}
              className={`font-semibold underline ${FOCUS}`}
            >
              {copy.invoice}
            </Link>
          )}
          {order.can_cancel && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (window.confirm(copy.confirmCancel)) {
                  void act(async () => setOrder(await cancelOrder(order.id)));
                }
              }}
              className={`font-semibold text-danger-fg underline disabled:opacity-50 ${FOCUS}`}
            >
              {copy.cancel}
            </button>
          )}
        </div>
      </div>
      <aside className="rounded-base border border-store-line bg-store-surface p-5 lg:sticky lg:top-36">
        <h2 className="mt-0 text-xl">{copy.items}</h2>
        <ul className="m-0 list-none divide-y divide-store-line p-0">
          {order.items.map((item) => (
            <li key={item.id} className="flex justify-between gap-4 py-3 text-sm">
              <span>
                {item.product_name}
                {item.variant_title ? ` · ${item.variant_title}` : ''} × {item.quantity}
              </span>
              <span className="tabular-nums">{formatMoney(item.line_total, order.currency)}</span>
            </li>
          ))}
        </ul>
        {totals.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-1.5 text-sm">
            <span>{label}</span>
            <span className="tabular-nums">{formatMoney(value, order.currency)}</span>
          </div>
        ))}
        <div className="mt-3 flex justify-between gap-4 border-t border-store-line pt-4 text-lg font-bold">
          <span>{copy.total}</span>
          <span className="tabular-nums">{formatMoney(order.grand_total, order.currency)}</span>
        </div>
        {order.amount_paid !== '0.0000' && (
          <div className="flex justify-between gap-4 py-1.5 text-sm">
            <span>{copy.paid}</span>
            <span className="tabular-nums">{formatMoney(order.amount_paid, order.currency)}</span>
          </div>
        )}
        {order.amount_refunded !== '0.0000' && (
          <div className="flex justify-between gap-4 py-1.5 text-sm">
            <span>{copy.refunded}</span>
            <span className="tabular-nums">
              {formatMoney(order.amount_refunded, order.currency)}
            </span>
          </div>
        )}
      </aside>
    </div>
  );
}
