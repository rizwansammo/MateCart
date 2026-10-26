'use client';

import { useCallback, useEffect, useState } from 'react';
import type {
  AfterSalesCase,
  BuyerLibraryItem,
  Order,
  OrderSummary,
  SavedAddress,
} from '@matecart/shared-contracts';
import {
  afterSalesCase,
  buyerLibraryItem,
  order,
  orderSummary,
  page,
  savedAddress,
  array,
} from '@matecart/shared-contracts';
import { Alert, Button, Loading, errorMessage } from '@matecart/shared-ui';
import { authApi as api } from '@/lib/auth-client';

const fieldClass = 'w-full min-h-11 rounded-base border border-line bg-surface px-3 py-2 text-sm';
const captionClass = 'block text-sm font-semibold mb-1';
const panelClass = 'rounded-base border border-line bg-surface p-5';

function Field({
  label,
  value,
  onChange,
  required = true,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  type?: string;
}) {
  return (
    <label>
      <span className={captionClass}>{label}</span>
      <input
        className={fieldClass}
        type={type}
        value={value}
        required={required}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

export function CustomerCommerceAccount() {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [addresses, setAddresses] = useState<SavedAddress[]>([]);
  const [cases, setCases] = useState<AfterSalesCase[]>([]);
  const [library, setLibrary] = useState<BuyerLibraryItem[]>([]);
  const [selection, setSelection] = useState<Order | null>(null);
  const [orderId, setOrderId] = useState('');
  const [itemId, setItemId] = useState('');
  const [kind, setKind] = useState('support');
  const [subject, setSubject] = useState('');
  const [reason, setReason] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [reviewBody, setReviewBody] = useState('');
  const [rating, setRating] = useState('5');
  const [activeCase, setActiveCase] = useState('');
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [address, setAddress] = useState({
    label: 'Home',
    recipient_name: '',
    phone: '',
    address_line_1: '',
    city: '',
    country: '',
    region: '',
    postal_code: '',
  });
  const refresh = useCallback(async () => {
    try {
      const [history, locations, requests, items] = await Promise.all([
        api.get<unknown>('/api/orders/?page_size=25'),
        api.get<unknown>('/api/account/addresses/'),
        api.get<unknown>('/api/cases/?page_size=25'),
        api.get<unknown>('/api/account/library/'),
      ]);
      setOrders(page(history, orderSummary).results);
      setAddresses(array(locations, savedAddress));
      setCases(page(requests, afterSalesCase).results);
      setLibrary(array(items, buyerLibraryItem));
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function selectOrder(id: string) {
    setOrderId(id);
    setSelection(null);
    setItemId('');
    if (!id) return;
    try {
      const found = order(await api.get<unknown>(`/api/orders/${id}/`));
      setSelection(found);
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  async function submitCase() {
    setBusy(true);
    setError('');
    try {
      await api.post('/api/cases/', {
        kind,
        order_item: kind === 'support' ? null : itemId,
        quantity,
        subject,
        reason,
        idempotency_key: crypto.randomUUID(),
      });
      setNotice('Your request has been submitted.');
      setSubject('');
      setReason('');
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function submitReview() {
    setBusy(true);
    setError('');
    try {
      await api.post('/api/reviews/', {
        order_item: itemId,
        rating: Number(rating),
        body: reviewBody,
      });
      setReviewBody('');
      setNotice('Your review is awaiting moderation.');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function sendReply() {
    setBusy(true);
    try {
      await api.post(`/api/cases/${activeCase}/`, { body: reply });
      setReply('');
      setNotice('Your message has been sent.');
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-8 grid gap-6">
      <h2 className="text-xl font-bold">Orders and purchases</h2>
      {loading ? <Loading /> : null}
      {error ? <Alert>{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}
      <section className={panelClass} aria-label="Order history">
        <h3 className="mb-4 font-bold">Your orders</h3>
        {!orders.length && !loading ? <p className="text-sm text-muted">No orders yet.</p> : null}
        <div className="divide-y divide-line">
          {orders.map((row) => (
            <div
              key={row.id}
              className="flex flex-wrap items-center justify-between gap-4 py-3 text-sm"
            >
              <div>
                <a className="font-semibold text-brand-deep underline" href={`/orders/${row.id}`}>
                  {row.number}
                </a>
                <p className="text-muted">
                  {new Date(row.placed_at).toLocaleDateString()} · {row.status} ·{' '}
                  {row.payment_status}
                </p>
              </div>
              <p className="font-semibold">
                {row.grand_total} {row.currency}
              </p>
            </div>
          ))}
        </div>
      </section>
      <section className={panelClass} aria-label="After sales request">
        <h3 className="mb-2 font-bold">Request help with an order</h3>
        <p className="mb-5 text-sm text-muted">
          Submit a support, return, exchange or warranty request. Approval and refunds require
          merchant review.
        </p>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submitCase();
          }}
        >
          <label>
            <span className={captionClass}>Request type</span>
            <select className={fieldClass} value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="support">Customer support</option>
              <option value="return">Return</option>
              <option value="exchange">Exchange</option>
              <option value="warranty">Warranty claim</option>
            </select>
          </label>
          <label>
            <span className={captionClass}>Related order</span>
            <select
              className={fieldClass}
              value={orderId}
              onChange={(e) => {
                void selectOrder(e.target.value);
              }}
              required={kind !== 'support'}
            >
              <option value="">Choose an order</option>
              {orders.map((row) => (
                <option value={row.id} key={row.id}>
                  {row.number}
                </option>
              ))}
            </select>
          </label>
          {selection && kind !== 'support' ? (
            <label>
              <span className={captionClass}>Purchased item</span>
              <select
                className={fieldClass}
                required
                value={itemId}
                onChange={(e) => setItemId(e.target.value)}
              >
                <option value="">Choose an item</option>
                {selection.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.product_name} {item.variant_title}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {kind !== 'support' ? (
            <Field label="Quantity" value={quantity} type="number" onChange={setQuantity} />
          ) : null}
          <Field label="Subject" value={subject} onChange={setSubject} />
          <label>
            <span className={captionClass}>Explain your request</span>
            <textarea
              className={fieldClass}
              required
              minLength={3}
              maxLength={2000}
              rows={4}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <Button type="submit" busy={busy} disabled={kind !== 'support' && !itemId}>
            Submit request
          </Button>
        </form>
      </section>
      <section className={panelClass} aria-label="Your support requests">
        <h3 className="mb-4 font-bold">Your requests</h3>
        {!cases.length ? <p className="text-sm text-muted">No requests yet.</p> : null}
        {cases.map((row) => (
          <div key={row.id} className="border-t border-line py-4 text-sm">
            <button
              type="button"
              onClick={() => setActiveCase(activeCase === row.id ? '' : row.id)}
              className="font-semibold text-brand-deep underline"
            >
              {row.subject}
            </button>
            <p className="mt-1 text-muted">
              {row.kind} · {row.status}
            </p>
            {row.decision_reason ? <p className="mt-1">{row.decision_reason}</p> : null}
            {activeCase === row.id ? (
              <div className="mt-4 grid gap-3">
                <p>{row.reason}</p>
                {row.messages.map((message) => (
                  <p key={message.id} className="rounded-base bg-canvas p-3">
                    {message.body}
                  </p>
                ))}
                <form
                  className="grid gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void sendReply();
                  }}
                >
                  <label>
                    <span className={captionClass}>Your reply</span>
                    <textarea
                      className={fieldClass}
                      required
                      value={reply}
                      maxLength={2000}
                      onChange={(e) => setReply(e.target.value)}
                    />
                  </label>
                  <Button type="submit" busy={busy} disabled={!reply.trim()}>
                    Send reply
                  </Button>
                </form>
              </div>
            ) : null}
          </div>
        ))}
      </section>
      <section className={panelClass} aria-label="Write a product review">
        <h3 className="mb-2 font-bold">Review a purchase</h3>
        <p className="mb-4 text-sm text-muted">
          Reviews require a verified purchase and are moderated before publishing.
        </p>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submitReview();
          }}
        >
          <label>
            <span className={captionClass}>Purchased item</span>
            <select
              className={fieldClass}
              value={itemId}
              onChange={(e) => setItemId(e.target.value)}
              required
            >
              <option value="">Choose an item from the selected order above</option>
              {selection?.items.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.product_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className={captionClass}>Rating</span>
            <select
              className={fieldClass}
              value={rating}
              onChange={(e) => setRating(e.target.value)}
            >
              {[5, 4, 3, 2, 1].map((n) => (
                <option key={n} value={n}>
                  {n} out of 5
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className={captionClass}>Your review</span>
            <textarea
              className={fieldClass}
              required
              minLength={3}
              maxLength={2000}
              value={reviewBody}
              onChange={(e) => setReviewBody(e.target.value)}
            />
          </label>
          <Button type="submit" busy={busy} disabled={!itemId}>
            Submit review
          </Button>
        </form>
      </section>
      <section className={panelClass} aria-label="Purchased digital products">
        <h3 className="mb-4 font-bold">Digital library</h3>
        {!library.length ? <p className="text-sm text-muted">No digital purchases yet.</p> : null}
        {library.map((entry) => (
          <div
            key={entry.order_item}
            className="flex flex-wrap justify-between gap-3 border-t border-line py-3 text-sm"
          >
            <div>
              <p className="font-semibold">{entry.product_name}</p>
              <p className="text-muted">
                {entry.order_number} · {entry.state.replaceAll('_', ' ')}
              </p>
            </div>
            {entry.state === 'ready' && entry.entitlement_id ? (
              <a
                className="font-semibold text-brand-deep underline"
                href={`/api/account/library/${entry.entitlement_id}/download/`}
              >
                Download ({entry.remaining_downloads} left)
              </a>
            ) : null}
          </div>
        ))}
      </section>
      <section className={panelClass} aria-label="Saved delivery addresses">
        <h3 className="mb-4 font-bold">Saved delivery addresses</h3>
        {addresses.map((row) => (
          <div key={row.id} className="border-t border-line py-3 text-sm">
            <strong>
              {row.label || row.recipient_name}
              {row.is_default ? ' · Default' : ''}
            </strong>
            <p className="text-muted">
              {row.address_line_1}, {row.city}, {row.country}
            </p>
          </div>
        ))}
        <form
          className="mt-5 grid gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            try {
              await api.post('/api/account/addresses/', {
                ...address,
                address_line_2: '',
                is_default: addresses.length === 0,
              });
              setNotice('Delivery address saved.');
              await refresh();
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field
            label="Label"
            value={address.label}
            onChange={(value) => setAddress({ ...address, label: value })}
          />
          <Field
            label="Recipient"
            value={address.recipient_name}
            onChange={(value) => setAddress({ ...address, recipient_name: value })}
          />
          <Field
            label="Phone"
            value={address.phone}
            onChange={(value) => setAddress({ ...address, phone: value })}
          />
          <Field
            label="Street address"
            value={address.address_line_1}
            onChange={(value) => setAddress({ ...address, address_line_1: value })}
          />
          <Field
            label="City"
            value={address.city}
            onChange={(value) => setAddress({ ...address, city: value })}
          />
          <Field
            label="Country code"
            value={address.country}
            onChange={(value) => setAddress({ ...address, country: value.toUpperCase() })}
          />
          <Button type="submit" busy={busy}>
            Save address
          </Button>
        </form>
      </section>
    </div>
  );
}
