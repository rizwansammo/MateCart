'use client';

import { useCallback, useEffect, useState } from 'react';
import type { MerchantCase } from '@matecart/shared-contracts';
import {
  managePage,
  merchantCase,
  merchantOrderSummary,
  merchantReview,
} from '@matecart/shared-contracts';
import { Alert, Button, TextField, errorMessage } from '@matecart/shared-ui';
import { TextArea } from '../appearance-app';
import {
  Badge,
  CASE_KIND,
  CASE_STATUS,
  Card,
  Empty,
  Feedback,
  Filters,
  Loader,
  PageHeader,
  Pagination,
  SelectField,
  StatusBadge,
  api,
  dateTime,
  label,
  money,
  quantity,
  query,
  useLoad,
} from './kit';
import { toUnits } from '@/lib/money';

const PAGE_SIZE = 20;
const DECISION_LABEL: Record<string, string> = {
  approved: 'Approve',
  rejected: 'Reject',
  received: 'Goods received',
  inspected: 'Inspected',
  awaiting_refund: 'Ready for refund',
  replacement_pending: 'Bind replacement order',
  resolved: 'Resolve',
  closed: 'Close',
};

function sumSucceeded(item: MerchantCase): bigint {
  return item.attributed_refunds
    .filter((r) => r.status === 'succeeded')
    .reduce((total, r) => total + (toUnits(r.amount) ?? 0n), 0n);
}

function CaseDetail({
  item,
  onChanged,
}: {
  item: MerchantCase;
  onChanged: (next: MerchantCase) => void;
}) {
  const [status, setStatus] = useState(item.next_statuses[0] ?? '');
  const [reason, setReason] = useState('');
  const [refund, setRefund] = useState('');
  const [orderNumber, setOrderNumber] = useState('');
  const [reply, setReply] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const succeeded = item.attributed_refunds.filter((r) => r.status === 'succeeded');
  const covered =
    item.required_refund !== null && sumSucceeded(item) >= (toUnits(item.required_refund) ?? 0n);

  async function decide() {
    setBusy('decide');
    setError('');
    setNotice('');
    try {
      let replacement: string | null = null;
      if (status === 'replacement_pending') {
        const found = managePage(merchantOrderSummary)(
          await api.get<unknown>(
            `/api/manage/orders/${query({ q: orderNumber.trim(), page_size: 5 })}`,
          ),
        ).results.find((o) => o.number.toLowerCase() === orderNumber.trim().toLowerCase());
        if (!found) {
          setError('No order has that number.');
          return;
        }
        replacement = found.id;
      }
      const next = merchantCase(
        await api.patch<unknown>(`/api/manage/aftersales/cases/${item.id}/`, {
          status,
          reason,
          ...(status === 'resolved' && item.kind === 'return' ? { refund: refund || null } : {}),
          ...(replacement ? { replacement_order: replacement } : {}),
        }),
      );
      onChanged(next);
      setReason('');
      setStatus(next.next_statuses[0] ?? '');
      setNotice('Request updated.');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="grid gap-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="info">{label(CASE_KIND, item.kind)}</Badge>
          <StatusBadge value={item.status} map={CASE_STATUS} />
        </div>
        <h2 className="mt-2 text-lg font-bold">{item.subject}</h2>
        <p className="mt-1 text-sm text-muted">
          {item.customer_name || 'Customer'} · {dateTime(item.created_at)}
          {item.order_number && (
            <>
              {' · '}
              <a className="text-brand-deep" href={`/manage/orders/${item.order_id}`}>
                {item.order_number}
              </a>
            </>
          )}
        </p>
        {item.product_name && (
          <p className="mt-1 text-sm">
            {item.product_name} ({item.sku}) · {quantity(item.quantity)} unit(s)
          </p>
        )}
        <p className="mt-3 rounded-base bg-canvas p-3 text-sm whitespace-pre-line">{item.reason}</p>
        {item.decision_reason && (
          <p className="mt-2 text-sm text-muted">Last decision: {item.decision_reason}</p>
        )}
      </div>
      <Feedback error={error} notice={notice} />
      {item.kind === 'return' && item.required_refund !== null && (
        <div className="rounded-base border border-line p-3 text-sm">
          <p className="font-semibold">Refund evidence</p>
          <p className="mt-1 text-muted">
            Returned goods value {money(item.required_refund, item.currency)}. Refunds count only
            when they were requested for this return on the order page and have succeeded.
          </p>
          <ul className="mt-2 grid gap-1">
            {item.attributed_refunds.map((r) => (
              <li key={r.id}>
                {money(r.amount, r.currency)} ·{' '}
                <StatusBadge
                  value={r.status}
                  map={{ pending: 'Pending', succeeded: 'Refunded', failed: 'Failed' }}
                />
              </li>
            ))}
            {!item.attributed_refunds.length && (
              <li className="text-muted">No refund is attributed yet.</li>
            )}
          </ul>
          {item.order_id && (
            <a
              className="mt-2 inline-block font-semibold text-brand-deep"
              href={`/manage/orders/${item.order_id}`}
            >
              Refund on the order →
            </a>
          )}
        </div>
      )}
      {item.kind === 'exchange' && item.replacement_order && (
        <p className="text-sm">
          Replacement order bound:{' '}
          <a className="text-brand-deep" href={`/manage/orders/${item.replacement_order}`}>
            view order
          </a>
          . Resolve once it is delivered.
        </p>
      )}
      {item.next_statuses.length > 0 ? (
        <form
          className="grid gap-3 rounded-base border border-line p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void decide();
          }}
        >
          <SelectField
            label="Next step"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            options={item.next_statuses.map((s) => ({ value: s, label: DECISION_LABEL[s] ?? s }))}
          />
          {status === 'replacement_pending' && (
            <TextField
              label="Replacement order number"
              required
              placeholder="ORD-000123"
              value={orderNumber}
              onChange={(e) => setOrderNumber(e.target.value)}
              hint="The customer's own order for the same item, not yet dispatched."
            />
          )}
          {status === 'resolved' && item.kind === 'return' && (
            <SelectField
              label="Refund that settles this return"
              required
              value={refund}
              onChange={(e) => setRefund(e.target.value)}
              options={[
                {
                  value: '',
                  label: succeeded.length ? 'Choose a refund' : 'No succeeded refund yet',
                },
                ...succeeded.map((r) => ({ value: r.id, label: money(r.amount, r.currency) })),
              ]}
              hint={covered ? undefined : 'Attributed refunds do not cover the returned value yet.'}
            />
          )}
          <TextArea
            label={
              status === 'rejected' || status === 'resolved'
                ? 'Decision reason (required)'
                : 'Note (optional)'
            }
            required={status === 'rejected' || status === 'resolved'}
            maxLength={300}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div>
            <Button type="submit" busy={busy === 'decide'} disabled={!status}>
              {DECISION_LABEL[status] ?? 'Update'}
            </Button>
          </div>
          <p className="text-xs text-muted">
            Only steps the request can take now are offered; the store checks the evidence again
            when you submit.
          </p>
        </form>
      ) : (
        <p className="text-sm text-muted">This request has no further steps.</p>
      )}
      <div className="grid gap-3">
        <p className="text-sm font-semibold">Conversation</p>
        {item.messages.map((m) => (
          <div key={m.id} className="rounded-base bg-canvas p-3 text-sm">
            <p className="whitespace-pre-line">{m.body}</p>
            <p className="mt-1 text-xs text-muted">{dateTime(m.created_at)}</p>
          </div>
        ))}
        {item.status !== 'closed' && (
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setBusy('reply');
              setError('');
              void api
                .post(`/api/manage/aftersales/cases/${item.id}/`, { body: reply })
                .then(async () => {
                  setReply('');
                  onChanged(
                    merchantCase(
                      await api.get<unknown>(`/api/manage/aftersales/cases/${item.id}/`),
                    ),
                  );
                  setNotice('Reply sent.');
                })
                .catch((err: unknown) => setError(errorMessage(err)))
                .finally(() => setBusy(''));
            }}
          >
            <TextArea
              label="Reply to the customer"
              required
              maxLength={2000}
              value={reply}
              onChange={(e) => setReply(e.target.value)}
            />
            <div>
              <Button type="submit" variant="secondary" busy={busy === 'reply'}>
                Send reply
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function Requests() {
  const wanted =
    typeof window === 'undefined'
      ? ''
      : (new URLSearchParams(window.location.search).get('case') ?? '');
  const [filters, setFilters] = useState({ kind: '', status: '' });
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<MerchantCase | null>(null);
  const load = useCallback(
    async () =>
      managePage(merchantCase)(
        await api.get<unknown>(
          `/api/manage/aftersales/cases/${query({ ...filters, page, page_size: PAGE_SIZE })}`,
        ),
      ),
    [filters, page],
  );
  const { data, setData, error, loading } = useLoad(load);
  useEffect(() => {
    if (!wanted) return;
    void api
      .get<unknown>(`/api/manage/aftersales/cases/${wanted}/`)
      .then((value) => setSelected(merchantCase(value)))
      .catch(() => undefined);
  }, [wanted]);
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <Card flush>
        <Feedback error={error} />
        <Filters>
          <SelectField
            label="Kind"
            value={filters.kind}
            onChange={(e) => {
              setPage(1);
              setFilters({ ...filters, kind: e.target.value });
            }}
            options={[
              { value: '', label: 'All kinds' },
              ...Object.entries(CASE_KIND).map(([value, text]) => ({ value, label: text })),
            ]}
          />
          <SelectField
            label="Status"
            value={filters.status}
            onChange={(e) => {
              setPage(1);
              setFilters({ ...filters, status: e.target.value });
            }}
            options={[
              { value: '', label: 'All statuses' },
              ...Object.entries(CASE_STATUS).map(([value, text]) => ({ value, label: text })),
            ]}
          />
        </Filters>
        {loading && !data ? (
          <Loader>Loading requests…</Loader>
        ) : !data?.results.length ? (
          <Empty title="No requests">
            Returns, exchanges, warranty claims and support requests from customers appear here.
          </Empty>
        ) : (
          <>
            <ul className="divide-y divide-line">
              {data.results.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(item)}
                    className={`w-full px-5 py-3 text-left hover:bg-canvas ${selected?.id === item.id ? 'bg-canvas' : ''}`}
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <Badge tone="info">{label(CASE_KIND, item.kind)}</Badge>
                      <StatusBadge value={item.status} map={CASE_STATUS} />
                    </span>
                    <span className="mt-1 block truncate text-sm font-semibold">
                      {item.subject}
                    </span>
                    <span className="block text-xs text-muted">
                      {item.customer_name} · {item.order_number ?? 'No order'} ·{' '}
                      {dateTime(item.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
          </>
        )}
      </Card>
      <Card>
        {selected ? (
          <CaseDetail
            key={`${selected.id}-${selected.updated_at}`}
            item={selected}
            onChanged={(next) => {
              setSelected(next);
              setData(
                data
                  ? {
                      ...data,
                      results: data.results.map((row) => (row.id === next.id ? next : row)),
                    }
                  : data,
              );
            }}
          />
        ) : (
          <p className="text-sm text-muted">Choose a request to review it.</p>
        )}
      </Card>
    </div>
  );
}

function Reviews() {
  const [status, setStatus] = useState('pending');
  const [page, setPage] = useState(1);
  const load = useCallback(
    async () =>
      managePage(merchantReview)(
        await api.get<unknown>(
          `/api/manage/aftersales/reviews/${query({ status, page, page_size: PAGE_SIZE })}`,
        ),
      ),
    [status, page],
  );
  const { data, error, loading, reload } = useLoad(load);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState('');
  async function moderate(id: string, outcome: 'published' | 'rejected') {
    setActionError('');
    try {
      await api.post(`/api/manage/aftersales/reviews/${id}/`, {
        status: outcome,
        reason: reasons[id] ?? '',
      });
      await reload();
    } catch (e) {
      setActionError(errorMessage(e));
    }
  }
  return (
    <Card flush>
      <Feedback error={error || actionError} />
      <Filters>
        <SelectField
          label="Show"
          value={status}
          onChange={(e) => {
            setPage(1);
            setStatus(e.target.value);
          }}
          options={[
            { value: 'pending', label: 'Waiting for moderation' },
            { value: 'published', label: 'Published' },
            { value: 'rejected', label: 'Rejected' },
            { value: '', label: 'All reviews' },
          ]}
        />
      </Filters>
      {loading && !data ? (
        <Loader />
      ) : !data?.results.length ? (
        <Empty title="No reviews here">
          Only customers who bought and received a product can review it.
        </Empty>
      ) : (
        <>
          <ul className="divide-y divide-line">
            {data.results.map((review) => (
              <li key={review.id} className="grid gap-2 px-5 py-4 text-sm">
                <p>
                  <strong aria-label={`${review.rating} out of 5`}>
                    {'★'.repeat(review.rating)}
                    {'☆'.repeat(5 - review.rating)}
                  </strong>{' '}
                  <span className="text-muted">
                    {review.product_name} · {review.customer_name} · {dateTime(review.created_at)}
                  </span>
                </p>
                <p className="whitespace-pre-line">{review.body}</p>
                {review.status === 'pending' ? (
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="min-w-48 flex-1">
                      <TextField
                        label="Reason (optional)"
                        maxLength={300}
                        value={reasons[review.id] ?? ''}
                        onChange={(e) => setReasons({ ...reasons, [review.id]: e.target.value })}
                      />
                    </div>
                    <Button onClick={() => void moderate(review.id, 'published')}>Publish</Button>
                    <Button
                      variant="secondary"
                      onClick={() => void moderate(review.id, 'rejected')}
                    >
                      Reject
                    </Button>
                  </div>
                ) : (
                  <p className="text-xs text-muted">
                    {review.status === 'published' ? 'Published' : 'Rejected'}
                    {review.decision_reason && ` · ${review.decision_reason}`}
                  </p>
                )}
              </li>
            ))}
          </ul>
          <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

export function AfterSalesWorkspace() {
  const [tab, setTab] = useState<'requests' | 'reviews'>('requests');
  return (
    <div>
      <PageHeader
        eyebrow="Operate"
        title="Customer requests"
        intro="Returns, exchanges, warranty claims, support and product reviews. Refunds and replacements are made through orders and verified before a request can be resolved."
      />
      <Alert tone="info">
        Changing a request&apos;s status never moves money or stock by itself.
      </Alert>
      <div role="tablist" aria-label="Request sections" className="my-6 flex gap-2">
        {(
          [
            ['requests', 'Requests'],
            ['reviews', 'Reviews'],
          ] as const
        ).map(([key, text]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`rounded-base border px-3 py-1.5 text-sm font-semibold ${tab === key ? 'border-ink bg-ink text-surface' : 'border-line-strong bg-surface'}`}
          >
            {text}
          </button>
        ))}
      </div>
      {tab === 'requests' ? <Requests /> : <Reviews />}
    </div>
  );
}
