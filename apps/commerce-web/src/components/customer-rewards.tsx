'use client';

import { useEffect, useState } from 'react';
import { customerLoyalty, type CustomerLoyalty } from '@matecart/shared-contracts';
import { Alert, errorMessage } from '@matecart/shared-ui';

import { authApi as api } from '@/lib/auth-client';
import { formatMoney } from '@/lib/money';

import { LEDGER_LABEL, LedgerList } from './manage/marketing-insights';

/**
 * The signed-in customer's loyalty points (D-082): usable balance, points waiting for an order
 * to be paid and delivered, totals and history. Hidden when the store has no loyalty program
 * and the customer never had points.
 */
export function CustomerRewards() {
  const [data, setData] = useState<CustomerLoyalty | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    void api
      .get<unknown>('/api/account/loyalty/')
      .then((value) => {
        if (live) setData(customerLoyalty(value));
      })
      .catch((e: unknown) => {
        if (live) setError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, []);
  if (error)
    return (
      <div className="mb-6">
        <Alert>{error}</Alert>
      </div>
    );
  if (!data || (!data.enabled && data.balance === 0 && data.entries.length === 0)) return null;
  const worth =
    data.point_value && data.currency ? formatMoney(data.point_value, data.currency) : null;
  return (
    <section id="rewards" className="mb-6 rounded-base border border-line bg-surface p-6">
      <h2 className="mb-1 text-lg font-bold">Rewards</h2>
      <p className="mb-5 text-sm text-muted">
        {data.enabled && data.earn_points !== null && data.earn_spend && data.currency
          ? `Earn ${data.earn_points} points for every ${formatMoney(data.earn_spend, data.currency)} spent on items, once an order is paid and delivered.${worth ? ` Each point is worth ${worth} at checkout` : ''}${data.min_redeem_points ? ` (from ${data.min_redeem_points} points)` : ''}.${data.expiry_days ? ` Points expire after ${data.expiry_days} days without earning or using any.` : ''} Points have no cash value.`
          : 'The store is not running its rewards program right now. Your points stay with your account.'}
      </p>
      <dl className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(
          [
            ['Usable points', data.balance],
            ['On the way', data.pending],
            ['Earned in total', data.lifetime_earned],
            ['Used in total', data.lifetime_redeemed],
          ] as const
        ).map(([name, value]) => (
          <div key={name} className="rounded-base bg-canvas p-3">
            <dt className="text-xs font-semibold text-muted">{name}</dt>
            <dd className="mt-1 text-xl font-bold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {data.deficit > 0 && (
        <p className="mb-4 text-sm text-muted">
          {data.deficit} points from a refunded order had already been used; your next points cover
          them first.
        </p>
      )}
      <h3 className="mb-2 text-sm font-bold">History</h3>
      <LedgerList entries={data.entries} labels={LEDGER_LABEL} />
    </section>
  );
}
