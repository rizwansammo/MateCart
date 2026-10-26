'use client';

import { useCallback, useEffect, useState } from 'react';
import type { AfterSalesCase, PurchaseReview } from '@matecart/shared-contracts';
import { afterSalesCase, afterSalesPage, purchaseReview } from '@matecart/shared-contracts';
import { Alert, Button, Loading, errorMessage } from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';

const selectClass = 'min-h-11 w-full border border-line bg-surface px-3 py-2 text-sm rounded-base';
const panelClass = 'rounded-base border border-line bg-surface p-5';

export function AfterSalesManage() {
  const [cases, setCases] = useState<AfterSalesCase[]>([]);
  const [reviews, setReviews] = useState<PurchaseReview[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(true);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [replies, setReplies] = useState<Record<string, string>>({});
  const [evidence, setEvidence] = useState<Record<string, string>>({});
  const reload = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        api.get<unknown>('/api/manage/aftersales/cases/?page_size=25'),
        api.get<unknown>('/api/manage/aftersales/reviews/?page_size=25'),
      ]);
      setCases(afterSalesPage(a, afterSalesCase).results);
      setReviews(afterSalesPage(b, purchaseReview).results);
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void Promise.resolve().then(reload);
  }, [reload]);

  async function changeCase(id: string) {
    setBusy(id);
    try {
      const target = cases.find((item) => item.id === id);
      const nextStatus = statuses[id] || 'approved';
      await api.patch(`/api/manage/aftersales/cases/${id}/`, {
        status: nextStatus,
        reason: reasons[id] || '',
        ...(nextStatus === 'resolved' && target?.kind === 'return'
          ? { refund: evidence[id] || null }
          : {}),
        ...(nextStatus === 'resolved' && target?.kind === 'exchange'
          ? { replacement_order: evidence[id] || null }
          : {}),
      });
      setNotice('Request updated.');
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy('');
    }
  }

  async function moderate(id: string, outcome: 'published' | 'rejected') {
    setBusy(id);
    try {
      await api.post(`/api/manage/aftersales/reviews/${id}/`, {
        status: outcome,
        reason: reasons[id] || '',
      });
      setNotice('Review moderation saved.');
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy('');
    }
  }

  async function reply(id: string) {
    setBusy(id);
    try {
      await api.post(`/api/manage/aftersales/cases/${id}/`, { body: replies[id] || '' });
      setReplies({ ...replies, [id]: '' });
      setNotice('Reply sent.');
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy('');
    }
  }
  if (loading) return <Loading />;
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-bold">Customer requests and reviews</h1>
        <p className="mt-2 text-sm text-muted">
          Review purchase requests and customer feedback. Refunds, replacements and inventory
          changes use existing authorized operations.
        </p>
      </div>
      {error ? <Alert>{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}
      <section className={panelClass}>
        <h2 className="mb-4 text-lg font-bold">Customer requests</h2>
        {!cases.length ? <p className="text-sm text-muted">No requests to review.</p> : null}
        {cases.map((item) => (
          <div key={item.id} className="grid gap-3 border-t border-line py-4">
            <div>
              <strong>{item.subject}</strong>
              <p className="text-sm text-muted">
                {item.kind} · {item.status} · {item.quantity} units
              </p>
              <p className="mt-2 text-sm">{item.reason}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm">
                Change status
                <select
                  className={selectClass}
                  value={statuses[item.id] ?? 'approved'}
                  onChange={(e) => setStatuses({ ...statuses, [item.id]: e.target.value })}
                >
                  {[
                    'approved',
                    'rejected',
                    'received',
                    'inspected',
                    'awaiting_refund',
                    'replacement_pending',
                    'resolved',
                    'closed',
                  ].map((status) => (
                    <option key={status} value={status}>
                      {status.replaceAll('_', ' ')}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-sm">
                Decision reason
                <input
                  className={selectClass}
                  maxLength={300}
                  value={reasons[item.id] ?? ''}
                  onChange={(e) => setReasons({ ...reasons, [item.id]: e.target.value })}
                />
              </label>
            </div>
            {(statuses[item.id] === 'resolved' ||
              (!statuses[item.id] && item.status === 'replacement_pending')) &&
            (item.kind === 'return' || item.kind === 'exchange') ? (
              <label className="text-sm">
                {item.kind === 'return' ? 'Verified refund ID' : 'Delivered replacement order ID'}
                <input
                  className={selectClass}
                  required
                  maxLength={36}
                  placeholder="UUID from verified order operations"
                  value={evidence[item.id] ?? ''}
                  onChange={(event) => setEvidence({ ...evidence, [item.id]: event.target.value })}
                />
              </label>
            ) : null}
            <Button
              type="button"
              busy={busy === item.id}
              onClick={() => {
                void changeCase(item.id);
              }}
            >
              Update request
            </Button>
            {item.messages.map((message) => (
              <p key={message.id} className="rounded-base bg-canvas p-3 text-sm">
                {message.body}
              </p>
            ))}
            <label className="text-sm">
              Reply to customer
              <textarea
                className={selectClass}
                maxLength={2000}
                value={replies[item.id] ?? ''}
                onChange={(e) => setReplies({ ...replies, [item.id]: e.target.value })}
              />
            </label>
            <Button
              variant="secondary"
              type="button"
              disabled={!replies[item.id]?.trim()}
              busy={busy === item.id}
              onClick={() => {
                void reply(item.id);
              }}
            >
              Send reply
            </Button>
          </div>
        ))}
      </section>
      <section className={panelClass}>
        <h2 className="mb-4 text-lg font-bold">Product reviews</h2>
        {!reviews.length ? <p className="text-sm text-muted">No reviews yet.</p> : null}
        {reviews.map((review) => (
          <div key={review.id} className="grid gap-2 border-t border-line py-4 text-sm">
            <p className="font-semibold">
              {review.rating} / 5 · {review.status}
            </p>
            <p>{review.body}</p>
            {review.status === 'pending' ? (
              <div className="flex flex-wrap gap-3">
                <Button
                  busy={busy === review.id}
                  onClick={() => {
                    void moderate(review.id, 'published');
                  }}
                >
                  Publish
                </Button>
                <Button
                  variant="secondary"
                  busy={busy === review.id}
                  onClick={() => {
                    void moderate(review.id, 'rejected');
                  }}
                >
                  Reject
                </Button>
              </div>
            ) : null}
          </div>
        ))}
      </section>
    </div>
  );
}
