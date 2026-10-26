'use client';

import type { Invoice, OrderPaymentStatus } from '@matecart/shared-contracts';
import { errorMessage } from '@matecart/shared-ui';
import type { Route } from 'next';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { getInvoice } from '@/lib/checkout-client';
import { formatMoney } from '@/lib/money';

import { FOCUS } from './ui';

const COPY = {
  en: {
    loading: 'Loading invoice…',
    title: 'Invoice',
    issued: 'Issued',
    order: 'Order',
    ordered: 'Order date',
    billTo: 'Bill to',
    from: 'From',
    item: 'Item',
    quantity: 'Qty',
    price: 'Unit price',
    amount: 'Amount',
    subtotal: 'Items',
    discount: 'Discount',
    shipping: 'Delivery',
    tax: 'Tax',
    total: 'Total',
    method: 'Payment method',
    status: 'Payment status',
    received: 'Payment received',
    refunded: 'Refunded',
    due: 'Amount due',
    cod: 'Cash on delivery',
    online: 'Online payment',
    cancelled: 'This order was cancelled.',
    print: 'Print or save as PDF',
    back: 'Back to order',
    payments: {
      unpaid: 'Unpaid',
      paid: 'Paid',
      partially_refunded: 'Partly refunded',
      refunded: 'Refunded',
      failed: 'Payment not completed',
    } satisfies Record<OrderPaymentStatus, string>,
  },
  bn: {
    loading: 'ইনভয়েস লোড হচ্ছে…',
    title: 'ইনভয়েস',
    issued: 'ইস্যুর তারিখ',
    order: 'অর্ডার',
    ordered: 'অর্ডারের তারিখ',
    billTo: 'ক্রেতা',
    from: 'বিক্রেতা',
    item: 'পণ্য',
    quantity: 'পরিমাণ',
    price: 'একক মূল্য',
    amount: 'মূল্য',
    subtotal: 'পণ্য',
    discount: 'ছাড়',
    shipping: 'ডেলিভারি',
    tax: 'কর',
    total: 'মোট',
    method: 'পেমেন্ট পদ্ধতি',
    status: 'পেমেন্টের অবস্থা',
    received: 'পরিশোধিত অর্থ',
    refunded: 'ফেরত',
    due: 'বকেয়া',
    cod: 'ক্যাশ অন ডেলিভারি',
    online: 'অনলাইন পেমেন্ট',
    cancelled: 'এই অর্ডারটি বাতিল হয়েছে।',
    print: 'প্রিন্ট বা PDF হিসেবে সংরক্ষণ',
    back: 'অর্ডারে ফিরুন',
    payments: {
      unpaid: 'অপরিশোধিত',
      paid: 'পরিশোধিত',
      partially_refunded: 'আংশিক ফেরত',
      refunded: 'ফেরত দেওয়া হয়েছে',
      failed: 'পেমেন্ট সম্পন্ন হয়নি',
    } satisfies Record<OrderPaymentStatus, string>,
  },
} as const;

export function InvoiceApp({ id, lang }: { id: string; lang: string }) {
  const copy = lang === 'bn' ? COPY.bn : COPY.en;
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState('');
  const dates = new Intl.DateTimeFormat(lang, { dateStyle: 'long' });

  useEffect(() => {
    let live = true;
    getInvoice(id)
      .then((value) => {
        if (live) setInvoice(value);
      })
      .catch((reason: unknown) => {
        if (live) setError(errorMessage(reason));
      });
    return () => {
      live = false;
    };
  }, [id]);

  if (!invoice && !error) return <p className="text-store-muted">{copy.loading}</p>;
  if (!invoice)
    return (
      <p role="alert" className="text-danger-fg">
        {error}
      </p>
    );

  const money = (value: string) => formatMoney(value, invoice.currency);
  const seller = invoice.seller;
  const buyer = invoice.buyer;
  const buyerLines = [
    buyer.name,
    buyer.address_line_1,
    buyer.address_line_2,
    [buyer.city, buyer.region, buyer.postal_code].filter(Boolean).join(', '),
    buyer.country,
    buyer.email,
    buyer.phone,
  ].filter(Boolean);
  const totals: Array<[string, string]> = [
    [copy.subtotal, invoice.item_subtotal],
    [copy.discount, `-${invoice.discount_total}`],
    [copy.shipping, invoice.shipping_total],
    [copy.tax, invoice.tax_total],
  ];

  return (
    <article className="mx-auto max-w-3xl rounded-base border border-store-line bg-store-surface p-5 sm:p-8 print:border-0 print:p-0">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3 print:hidden">
        <Link
          href={`/orders/${id}` as Route}
          className={`text-sm font-semibold underline ${FOCUS}`}
        >
          {copy.back}
        </Link>
        <button
          type="button"
          onClick={() => window.print()}
          className={`h-10 rounded-base border border-store-ink px-4 text-sm font-semibold ${FOCUS}`}
        >
          {copy.print}
        </button>
      </div>
      <header className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <h1 className="m-0 text-3xl">{copy.title}</h1>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-store-muted">{copy.title}</dt>
            <dd className="m-0 font-semibold tabular-nums">{invoice.number}</dd>
            <dt className="text-store-muted">{copy.issued}</dt>
            <dd className="m-0">{dates.format(new Date(invoice.issued_at))}</dd>
            <dt className="text-store-muted">{copy.order}</dt>
            <dd className="m-0 tabular-nums">{invoice.order_number}</dd>
            <dt className="text-store-muted">{copy.ordered}</dt>
            <dd className="m-0">{dates.format(new Date(invoice.order_placed_at))}</dd>
          </dl>
        </div>
        <div className="text-sm sm:text-right">
          <p className="m-0 text-store-muted">{copy.from}</p>
          <p className="m-0 font-semibold">{seller.legal_name || seller.name}</p>
          {seller.legal_name && seller.legal_name !== seller.name && (
            <p className="m-0">{seller.name}</p>
          )}
          {seller.email && <p className="m-0">{seller.email}</p>}
          {seller.phone && <p className="m-0">{seller.phone}</p>}
        </div>
      </header>
      {invoice.order_cancelled && (
        <p role="status" className="mt-6 rounded-base bg-danger-bg p-3 text-sm text-danger-fg">
          {copy.cancelled}
        </p>
      )}
      {buyerLines.length > 0 && (
        <section className="mt-6 text-sm">
          <p className="m-0 text-store-muted">{copy.billTo}</p>
          {buyerLines.map((line) => (
            <p key={line} className="m-0">
              {line}
            </p>
          ))}
        </section>
      )}
      <div className="mt-6 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-store-line text-left">
              <th className="py-2 pr-3 font-semibold">{copy.item}</th>
              <th className="py-2 pr-3 text-right font-semibold">{copy.quantity}</th>
              <th className="py-2 pr-3 text-right font-semibold">{copy.price}</th>
              <th className="py-2 text-right font-semibold">{copy.amount}</th>
            </tr>
          </thead>
          <tbody>
            {invoice.items.map((item) => (
              <tr key={item.id} className="border-b border-store-line align-top">
                <td className="py-2 pr-3">
                  {item.product_name}
                  {item.variant_title && (
                    <span className="block text-store-muted">{item.variant_title}</span>
                  )}
                  <span className="block text-xs text-store-muted">{item.sku}</span>
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{item.quantity}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{money(item.unit_price)}</td>
                <td className="py-2 text-right tabular-nums">{money(item.line_total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="mt-4 ml-auto grid max-w-xs grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
        {totals.map(([label, value]) => (
          <div key={label} className="contents">
            <dt>{label}</dt>
            <dd className="m-0 text-right tabular-nums">{money(value)}</dd>
          </div>
        ))}
        <dt className="border-t border-store-line pt-2 text-base font-bold">{copy.total}</dt>
        <dd className="m-0 border-t border-store-line pt-2 text-right text-base font-bold tabular-nums">
          {money(invoice.grand_total)}
        </dd>
      </dl>
      <dl className="mt-6 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-store-muted">{copy.method}</dt>
        <dd className="m-0">{invoice.payment_method === 'cod' ? copy.cod : copy.online}</dd>
        <dt className="text-store-muted">{copy.status}</dt>
        <dd className="m-0 font-semibold">{copy.payments[invoice.payment_status]}</dd>
        {invoice.amount_paid !== '0.0000' && (
          <>
            <dt className="text-store-muted">{copy.received}</dt>
            <dd className="m-0 tabular-nums">{money(invoice.amount_paid)}</dd>
          </>
        )}
        {invoice.amount_refunded !== '0.0000' && (
          <>
            <dt className="text-store-muted">{copy.refunded}</dt>
            <dd className="m-0 tabular-nums">{money(invoice.amount_refunded)}</dd>
          </>
        )}
        {invoice.payment_status === 'unpaid' && !invoice.order_cancelled && (
          <>
            <dt className="text-store-muted">{copy.due}</dt>
            <dd className="m-0 tabular-nums">{money(invoice.grand_total)}</dd>
          </>
        )}
      </dl>
    </article>
  );
}
