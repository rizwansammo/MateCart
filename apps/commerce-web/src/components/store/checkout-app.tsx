'use client';

import type { Checkout } from '@matecart/shared-contracts';
import { errorMessage } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { getCheckout, reviewCheckout, updateCheckout } from '@/lib/checkout-client';
import { formatMoney } from '@/lib/money';

import { FOCUS } from './ui';

const COPY = {
  en: {
    title: 'Checkout',
    contact: 'Contact',
    email: 'Email address',
    phone: 'Phone number',
    delivery: 'Delivery address',
    name: 'Recipient name',
    line1: 'Address',
    line2: 'Apartment or suite',
    city: 'City',
    region: 'Region',
    postal: 'Postal code',
    country: 'Country code',
    notes: 'Delivery notes',
    coupon: 'Coupon code',
    save: 'Update delivery options',
    method: 'Delivery method',
    slot: 'Delivery time',
    summary: 'Order summary',
    subtotal: 'Items',
    discount: 'Discount',
    shipping: 'Delivery',
    tax: 'Tax',
    total: 'Total',
    review: 'Review and reserve stock',
    loading: 'Loading checkout…',
    back: 'Back to cart',
    reviewed: 'Checkout reviewed. Stock is reserved temporarily.',
    boundary:
      'Order creation and payment begin in the next phase. No order or payment has been created.',
    reservedUntil: 'Reserved until',
    required: 'Required for delivery',
    optional: 'Optional',
  },
  bn: {
    title: 'চেকআউট',
    contact: 'যোগাযোগ',
    email: 'ইমেইল ঠিকানা',
    phone: 'ফোন নম্বর',
    delivery: 'ডেলিভারি ঠিকানা',
    name: 'প্রাপকের নাম',
    line1: 'ঠিকানা',
    line2: 'ফ্ল্যাট বা স্যুট',
    city: 'শহর',
    region: 'অঞ্চল',
    postal: 'পোস্ট কোড',
    country: 'দেশের কোড',
    notes: 'ডেলিভারি নির্দেশনা',
    coupon: 'কুপন কোড',
    save: 'ডেলিভারি অপশন আপডেট করুন',
    method: 'ডেলিভারি পদ্ধতি',
    slot: 'ডেলিভারির সময়',
    summary: 'অর্ডার সারাংশ',
    subtotal: 'পণ্য',
    discount: 'ছাড়',
    shipping: 'ডেলিভারি',
    tax: 'কর',
    total: 'মোট',
    review: 'যাচাই করে স্টক সংরক্ষণ করুন',
    loading: 'চেকআউট লোড হচ্ছে…',
    back: 'কার্টে ফিরুন',
    reviewed: 'চেকআউট যাচাই হয়েছে। স্টক সাময়িকভাবে সংরক্ষিত।',
    boundary: 'পরবর্তী ধাপে অর্ডার ও পেমেন্ট তৈরি হবে। এখনো কোনো অর্ডার বা পেমেন্ট তৈরি হয়নি।',
    reservedUntil: 'সংরক্ষিত থাকবে',
    required: 'ডেলিভারির জন্য আবশ্যক',
    optional: 'ঐচ্ছিক',
  },
} as const;

type Fields = Record<string, string>;

function initial(session: Checkout): Fields {
  return {
    contact_email: session.contact_email,
    contact_phone: session.contact_phone,
    recipient_name: session.recipient_name,
    address_line_1: session.address_line_1,
    address_line_2: session.address_line_2,
    city: session.city,
    region: session.region,
    postal_code: session.postal_code,
    country: session.country,
    delivery_notes: session.delivery_notes,
    coupon_code: session.coupon_code,
    shipping_method: session.selected_shipping_method ?? '',
    delivery_slot: session.selected_delivery_slot ?? '',
  };
}

export function CheckoutApp({ id, lang }: { id: string; lang: string }) {
  const copy = lang === 'bn' ? COPY.bn : COPY.en;
  const [session, setSession] = useState<Checkout | null>(null);
  const [fields, setFields] = useState<Fields>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const key = useMemo(() => crypto.randomUUID(), []);

  useEffect(() => {
    let live = true;
    getCheckout(id)
      .then((value) => {
        if (live) {
          setSession(value);
          setFields(initial(value));
        }
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason));
      });
    return () => {
      live = false;
    };
  }, [id]);

  function field(name: string, label: string, options: { required?: boolean; type?: string } = {}) {
    return (
      <label className="flex flex-col gap-1.5 text-sm font-semibold">
        {label}
        <input
          name={name}
          type={options.type ?? 'text'}
          required={options.required}
          value={fields[name] ?? ''}
          onChange={(event) => setFields((current) => ({ ...current, [name]: event.target.value }))}
          className={`h-11 rounded-base border border-store-line bg-store-canvas px-3 font-normal text-store-ink ${FOCUS}`}
        />
      </label>
    );
  }

  async function save(event?: React.FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError('');
    try {
      const value = await updateCheckout(id, {
        ...fields,
        shipping_method: fields.shipping_method || null,
        delivery_slot: fields.delivery_slot || null,
      });
      setSession(value);
      setFields(initial(value));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function review() {
    setBusy(true);
    setError('');
    try {
      let value = await updateCheckout(id, {
        ...fields,
        shipping_method: fields.shipping_method || null,
        delivery_slot: fields.delivery_slot || null,
      });
      value = await reviewCheckout(id, key);
      setSession(value);
      setFields(initial(value));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!session && !error) return <p className="text-store-muted">{copy.loading}</p>;
  if (!session)
    return (
      <p role="alert" className="text-danger-fg">
        {error}
      </p>
    );
  const locked = session.status !== 'draft';
  const totals: Array<[string, string]> = [
    [copy.subtotal, session.item_subtotal],
    [copy.discount, `-${session.discount_total}`],
    [copy.shipping, session.shipping_total],
    [copy.tax, session.tax_total],
  ];

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">
      <div className="min-w-0">
        <div className="mb-6 flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="m-0 store-display text-3xl sm:text-4xl">{copy.title}</h1>
          <Link href={'/cart' as Route} className={`text-sm font-semibold underline ${FOCUS}`}>
            {copy.back}
          </Link>
        </div>
        {error && (
          <p role="alert" className="rounded-base bg-danger-bg p-3 text-sm text-danger-fg">
            {error}
          </p>
        )}
        {locked ? (
          <div
            role="status"
            className="rounded-base border border-store-line bg-success-bg p-5 text-success-fg"
          >
            <p className="mt-0 font-bold">{copy.reviewed}</p>
            <p>
              {copy.reservedUntil}:{' '}
              {new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeStyle: 'short' }).format(
                new Date(session.expires_at),
              )}
            </p>
            <p className="mb-0">{copy.boundary}</p>
          </div>
        ) : (
          <form onSubmit={(event) => void save(event)} className="flex flex-col gap-6">
            <fieldset className="grid gap-4 rounded-base border border-store-line bg-store-surface p-5 sm:grid-cols-2">
              <legend className="px-2 text-lg font-bold">{copy.contact}</legend>
              {field('contact_email', copy.email, { type: 'email' })}
              {field('contact_phone', copy.phone)}
            </fieldset>
            {session.requires_shipping && (
              <fieldset className="grid gap-4 rounded-base border border-store-line bg-store-surface p-5 sm:grid-cols-2">
                <legend className="px-2 text-lg font-bold">{copy.delivery}</legend>
                {field('recipient_name', copy.name, { required: true })}
                {field('address_line_1', copy.line1, { required: true })}
                {field('address_line_2', `${copy.line2} (${copy.optional})`)}
                {field('city', copy.city, { required: true })}
                {field('region', copy.region)}
                {field('postal_code', copy.postal)}
                {field('country', `${copy.country} (${copy.required})`, { required: true })}
                {field('delivery_notes', `${copy.notes} (${copy.optional})`)}
              </fieldset>
            )}
            <div className="grid gap-4 rounded-base border border-store-line bg-store-surface p-5 sm:grid-cols-2">
              {field('coupon_code', `${copy.coupon} (${copy.optional})`)}
              {session.requires_shipping && session.shipping_methods.length > 0 && (
                <label className="flex flex-col gap-1.5 text-sm font-semibold">
                  {copy.method}
                  <select
                    name="shipping_method"
                    required
                    value={fields.shipping_method ?? ''}
                    onChange={(event) =>
                      setFields((current) => ({ ...current, shipping_method: event.target.value }))
                    }
                    className={`h-11 rounded-base border border-store-line bg-store-canvas px-3 font-normal ${FOCUS}`}
                  >
                    <option value="">—</option>
                    {session.shipping_methods.map((method) => (
                      <option key={method.id} value={method.id}>
                        {method.name} · {formatMoney(method.amount, method.currency)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {session.delivery_slots.length > 0 && (
                <label className="flex flex-col gap-1.5 text-sm font-semibold">
                  {copy.slot}
                  <select
                    name="delivery_slot"
                    required
                    value={fields.delivery_slot ?? ''}
                    onChange={(event) =>
                      setFields((current) => ({ ...current, delivery_slot: event.target.value }))
                    }
                    className={`h-11 rounded-base border border-store-line bg-store-canvas px-3 font-normal ${FOCUS}`}
                  >
                    <option value="">—</option>
                    {session.delivery_slots.map((slot) => (
                      <option key={slot.id} value={slot.id}>
                        {slot.label}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <button
                type="submit"
                disabled={busy}
                className={`h-11 self-end rounded-base border border-store-ink px-4 font-semibold disabled:opacity-50 ${FOCUS}`}
              >
                {copy.save}
              </button>
            </div>
          </form>
        )}
      </div>
      <aside className="rounded-base border border-store-line bg-store-surface p-5 lg:sticky lg:top-36">
        <h2 className="mt-0 text-xl">{copy.summary}</h2>
        <ul className="m-0 list-none divide-y divide-store-line p-0">
          {session.lines.map((line) => (
            <li key={line.variant} className="flex justify-between gap-4 py-3 text-sm">
              <span>
                {line.product_name} × {line.quantity}
              </span>
              <span className="tabular-nums">
                {formatMoney(line.line_subtotal, session.currency)}
              </span>
            </li>
          ))}
        </ul>
        {totals.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-1.5 text-sm">
            <span>{label}</span>
            <span className="tabular-nums">{formatMoney(value, session.currency)}</span>
          </div>
        ))}
        <div className="mt-3 flex justify-between gap-4 border-t border-store-line pt-4 text-lg font-bold">
          <span>{copy.total}</span>
          <span className="tabular-nums">{formatMoney(session.grand_total, session.currency)}</span>
        </div>
        {!locked && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void review()}
            className={`mt-5 h-12 w-full rounded-base bg-store-action px-4 font-semibold text-store-on-action disabled:opacity-50 ${FOCUS}`}
          >
            {copy.review}
          </button>
        )}
      </aside>
    </div>
  );
}
