'use client';

import { useCallback, useState } from 'react';
import type { MerchantShipment, StaffSession } from '@matecart/shared-contracts';
import {
  codRecord,
  fraudAssessment,
  managePage,
  merchantShipment,
  shipmentSummary,
} from '@matecart/shared-contracts';
import { Button, TextField, errorMessage } from '@matecart/shared-ui';
import { TextArea } from '../appearance-app';
import {
  Card,
  Cell,
  Detail,
  Empty,
  FRAUD_STATUS,
  Feedback,
  Filters,
  Loader,
  PageHeader,
  Pagination,
  ReauthDialog,
  SHIPMENT_STATUS,
  SelectField,
  StatusBadge,
  Table,
  api,
  dateTime,
  label,
  money,
  newKey,
  quantity,
  query,
  useLoad,
} from './kit';

const PAGE_SIZE = 25;
const QUEUES: { value: string; label: string; statuses: string[] }[] = [
  { value: '', label: 'All shipments', statuses: [] },
  { value: 'packing', label: 'To pick and pack', statuses: ['ready', 'picking', 'packed'] },
  { value: 'dispatch', label: 'Ready for dispatch', statuses: ['ready_for_dispatch'] },
  { value: 'moving', label: 'With courier', statuses: ['dispatched', 'in_transit'] },
  { value: 'delivered', label: 'Delivered', statuses: ['delivered'] },
  {
    value: 'problem',
    label: 'Failed or returned',
    statuses: ['delivery_failed', 'return_to_sender'],
  },
  { value: 'cancelled', label: 'Cancelled', statuses: ['cancelled'] },
];

function statusQuery(statuses: string[]): string {
  return statuses.map((status) => `&status=${encodeURIComponent(status)}`).join('');
}

export function ShipmentList() {
  const initial =
    typeof window === 'undefined'
      ? ''
      : (new URLSearchParams(window.location.search).get('status') ?? '');
  const [queue, setQueue] = useState(
    () => QUEUES.find((q) => q.statuses.includes(initial))?.value ?? '',
  );
  const [text, setText] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(async () => {
    const statuses = QUEUES.find((q) => q.value === queue)?.statuses ?? [];
    return managePage(shipmentSummary)(
      await api.get<unknown>(
        `/api/manage/fulfillment/shipments/${query({ q: search, page, page_size: PAGE_SIZE })}${statusQuery(statuses)}`,
      ),
    );
  }, [queue, search, page]);
  const { data, error, loading } = useLoad(load);
  return (
    <div>
      <PageHeader
        eyebrow="Operate"
        title="Shipments"
        intro="Pick, pack and dispatch confirmed orders, record courier details and delivery outcomes. Stock leaves inventory only at dispatch."
      />
      <Feedback error={error} />
      <Card flush>
        <Filters>
          <SelectField
            label="Queue"
            value={queue}
            onChange={(e) => {
              setPage(1);
              setQueue(e.target.value);
            }}
            options={QUEUES.map((q) => ({ value: q.value, label: q.label }))}
          />
          <form
            className="flex items-end gap-2 sm:col-span-1 lg:col-span-3"
            onSubmit={(e) => {
              e.preventDefault();
              setPage(1);
              setSearch(text.trim());
            }}
          >
            <div className="min-w-0 flex-1">
              <TextField
                label="Search"
                placeholder="Shipment, order, tracking or recipient"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>
            <Button type="submit" variant="secondary">
              Search
            </Button>
          </form>
        </Filters>
        {loading && !data ? (
          <Loader>Loading shipments…</Loader>
        ) : !data?.results.length ? (
          <Empty title="No shipments here">
            Shipments are prepared from confirmed orders on the order page.
          </Empty>
        ) : (
          <>
            <Table head={['Shipment', 'Order', 'Recipient', 'Courier', 'Status', 'Risk']}>
              {data.results.map((row) => (
                <tr key={row.id} className="hover:bg-canvas">
                  <Cell>
                    <a
                      className="font-semibold text-brand-deep"
                      href={`/manage/shipments/${row.id}`}
                    >
                      {row.reference}
                    </a>
                    <p className="text-xs text-muted">{dateTime(row.created_at)}</p>
                  </Cell>
                  <Cell>
                    <a className="text-brand-deep" href={`/manage/orders/${row.order_id}`}>
                      {row.order_number}
                    </a>
                  </Cell>
                  <Cell>
                    {row.recipient_name}
                    <p className="text-xs text-muted">{row.city}</p>
                  </Cell>
                  <Cell>
                    {row.courier_name || '—'}
                    {row.tracking_number && (
                      <p className="text-xs text-muted">{row.tracking_number}</p>
                    )}
                  </Cell>
                  <Cell>
                    <StatusBadge value={row.status} map={SHIPMENT_STATUS} />
                  </Cell>
                  <Cell>
                    <StatusBadge value={row.fraud_status} map={FRAUD_STATUS} />
                  </Cell>
                </tr>
              ))}
            </Table>
            <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onPage={setPage} />
          </>
        )}
      </Card>
    </div>
  );
}

const NEXT_LABEL: Record<string, string> = {
  picking: 'Start picking',
  packed: 'Mark packed',
  ready_for_dispatch: 'Ready for dispatch',
  dispatched: 'Dispatch',
  in_transit: 'In transit',
  delivered: 'Delivered',
  delivery_failed: 'Delivery failed',
  return_to_sender: 'Returned to sender',
};

function CourierDetails({
  shipment,
  onSaved,
}: {
  shipment: MerchantShipment;
  onSaved: (s: MerchantShipment) => void;
}) {
  const [form, setForm] = useState({
    courier_name: shipment.courier_name,
    tracking_number: shipment.tracking_number,
    tracking_url: shipment.tracking_url,
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        void api
          .post<unknown>(`/api/manage/fulfillment/shipments/${shipment.id}/courier-details/`, form)
          .then((value) => onSaved(merchantShipment(value)))
          .catch((err: unknown) => setError(errorMessage(err)))
          .finally(() => setBusy(false));
      }}
    >
      <TextField
        label="Courier name"
        required
        maxLength={100}
        value={form.courier_name}
        onChange={(e) => setForm({ ...form, courier_name: e.target.value })}
      />
      <TextField
        label="Tracking number"
        maxLength={120}
        value={form.tracking_number}
        onChange={(e) => setForm({ ...form, tracking_number: e.target.value })}
      />
      <TextField
        label="Tracking link"
        type="url"
        placeholder="https://"
        maxLength={1000}
        value={form.tracking_url}
        onChange={(e) => setForm({ ...form, tracking_url: e.target.value })}
        hint="Only real tracking from your courier. HTTPS links only."
      />
      {error && <Feedback error={error} />}
      <div>
        <Button type="submit" variant="secondary" busy={busy}>
          Save courier details
        </Button>
      </div>
    </form>
  );
}

function CodCollection({
  shipment,
  onSaved,
}: {
  shipment: MerchantShipment;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState({
    collected_amount: '',
    courier_fees: '0',
    deductions: '0',
    deduction_note: '',
    collection_reference: '',
  });
  const [confirm, setConfirm] = useState(false);
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setConfirm(true);
      }}
    >
      <p className="text-sm text-muted">
        Record what the courier collected, from their statement. The order becomes paid only when
        the full amount was collected.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Collected amount"
          inputMode="decimal"
          required
          value={form.collected_amount}
          onChange={(e) => setForm({ ...form, collected_amount: e.target.value })}
        />
        <TextField
          label="Collection reference"
          required
          maxLength={120}
          value={form.collection_reference}
          onChange={(e) => setForm({ ...form, collection_reference: e.target.value })}
        />
        <TextField
          label="Courier fees"
          inputMode="decimal"
          value={form.courier_fees}
          onChange={(e) => setForm({ ...form, courier_fees: e.target.value })}
        />
        <TextField
          label="Other deductions"
          inputMode="decimal"
          value={form.deductions}
          onChange={(e) => setForm({ ...form, deductions: e.target.value })}
        />
      </div>
      <TextArea
        label="Deduction note"
        maxLength={300}
        value={form.deduction_note}
        onChange={(e) => setForm({ ...form, deduction_note: e.target.value })}
        hint="Required when there are deductions."
      />
      <div>
        <Button type="submit" variant="secondary">
          Record collection…
        </Button>
      </div>
      {confirm && (
        <ReauthDialog
          title="Record courier cash collection"
          description="Cash records affect what the store is owed. Confirm with your password."
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            codRecord(
              await api.post<unknown>(
                `/api/manage/fulfillment/shipments/${shipment.id}/cod-collection/`,
                form,
              ),
            );
            setConfirm(false);
            await onSaved();
          }}
        />
      )}
    </form>
  );
}

function Remittance({
  shipment,
  onSaved,
}: {
  shipment: MerchantShipment;
  onSaved: () => Promise<void>;
}) {
  const record = shipment.cod;
  const [form, setForm] = useState({
    actual_remittance: record?.expected_remittance ?? '',
    remittance_reference: '',
    remitted_at: '',
  });
  const [confirm, setConfirm] = useState(false);
  if (!record) return null;
  return (
    <form
      className="mt-4 grid gap-3 border-t border-line pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        setConfirm(true);
      }}
    >
      <p className="text-sm font-semibold">Record the courier&apos;s remittance</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField
          label="Amount received"
          inputMode="decimal"
          required
          value={form.actual_remittance}
          onChange={(e) => setForm({ ...form, actual_remittance: e.target.value })}
        />
        <TextField
          label="Payment reference"
          required
          maxLength={120}
          value={form.remittance_reference}
          onChange={(e) => setForm({ ...form, remittance_reference: e.target.value })}
        />
        <TextField
          label="Received on"
          type="datetime-local"
          required
          value={form.remitted_at}
          onChange={(e) => setForm({ ...form, remitted_at: e.target.value })}
        />
      </div>
      <div>
        <Button type="submit" variant="secondary">
          Reconcile…
        </Button>
      </div>
      {confirm && (
        <ReauthDialog
          title="Reconcile courier remittance"
          description={`Expected ${money(record.expected_remittance, record.currency)}. A different amount is kept as a discrepancy.`}
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            codRecord(
              await api.post<unknown>(
                `/api/manage/fulfillment/cod-records/${record.id}/remittance/`,
                {
                  ...form,
                  remitted_at: new Date(form.remitted_at).toISOString(),
                },
              ),
            );
            setConfirm(false);
            await onSaved();
          }}
        />
      )}
    </form>
  );
}

export function ShipmentDetail({ id, session }: { id: string; session: StaffSession }) {
  const load = useCallback(
    async () =>
      merchantShipment(await api.get<unknown>(`/api/manage/fulfillment/shipments/${id}/`)),
    [id],
  );
  const { data: shipment, setData, error: loadError, loading } = useLoad(load);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState('');
  const [serials, setSerials] = useState<Record<string, string>>({});
  const [review, setReview] = useState({ decision: 'clear', reason: '' });
  const [bookingKey] = useState(() => newKey('booking'));
  const refresh = async () =>
    setData(merchantShipment(await api.get<unknown>(`/api/manage/fulfillment/shipments/${id}/`)));
  async function run(name: string, action: () => Promise<void>, message: string) {
    setBusy(name);
    setError('');
    setNotice('');
    try {
      await action();
      setNotice(message);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy('');
    }
  }
  if (loading && !shipment) return <Loader>Loading shipment…</Loader>;
  if (!shipment) return <Feedback error={loadError || 'This shipment could not be loaded.'} />;
  const needsSerials =
    shipment.next_statuses.includes('dispatched') && shipment.serial_allocations.length > 0;
  return (
    <div className="grid gap-6">
      <PageHeader
        eyebrow="Shipments"
        title={shipment.reference}
        intro={
          <>
            For order{' '}
            <a
              className="font-semibold text-brand-deep"
              href={`/manage/orders/${shipment.order_id}`}
            >
              {shipment.order_number}
            </a>
          </>
        }
        actions={<StatusBadge value={shipment.status} map={SHIPMENT_STATUS} />}
      />
      <Feedback error={error} notice={notice} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-6">
          <Card title="Next step">
            {shipment.next_statuses.length ? (
              <div className="grid gap-4">
                {needsSerials && (
                  <div className="grid gap-3">
                    <p className="text-sm text-muted">
                      Enter the serial or IMEI of each unit you are shipping, one per line.
                    </p>
                    {shipment.serial_allocations.map((row) => (
                      <TextArea
                        key={row.allocation}
                        label={`${row.sku} · ${quantity(row.quantity)} unit(s)`}
                        value={serials[row.allocation] ?? ''}
                        onChange={(e) =>
                          setSerials({ ...serials, [row.allocation]: e.target.value })
                        }
                      />
                    ))}
                  </div>
                )}
                <TextField
                  label="Note (optional)"
                  maxLength={300}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
                <div className="flex flex-wrap gap-2">
                  {shipment.next_statuses.map((status) => (
                    <Button
                      key={status}
                      variant={
                        ['delivery_failed', 'return_to_sender'].includes(status)
                          ? 'secondary'
                          : 'primary'
                      }
                      busy={busy === status}
                      onClick={() =>
                        void run(
                          status,
                          async () => {
                            const body: Record<string, unknown> = { status, note };
                            if (status === 'dispatched' && shipment.serial_allocations.length) {
                              body.serials = Object.fromEntries(
                                shipment.serial_allocations.map((row) => [
                                  row.allocation,
                                  (serials[row.allocation] ?? '').split(/\s+/).filter(Boolean),
                                ]),
                              );
                            }
                            setData(
                              merchantShipment(
                                await api.post<unknown>(
                                  `/api/manage/fulfillment/shipments/${id}/transition/`,
                                  body,
                                ),
                              ),
                            );
                            setNote('');
                          },
                          `Shipment is now ${label(SHIPMENT_STATUS, status).toLowerCase()}.`,
                        )
                      }
                    >
                      {NEXT_LABEL[status] ?? status}
                    </Button>
                  ))}
                </div>
                {shipment.next_statuses.includes('dispatched') && (
                  <p className="text-xs text-muted">
                    Dispatch deducts the reserved stock (first-expiring first for batches).
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted">
                No further steps. This shipment is{' '}
                {label(SHIPMENT_STATUS, shipment.status).toLowerCase()}.
              </p>
            )}
            {['ready', 'picking', 'packed', 'ready_for_dispatch'].includes(shipment.status) && (
              <p className="mt-4 text-xs text-muted">
                To stop this shipment before dispatch, cancel the order: stock and other commitments
                are then released together.
              </p>
            )}
          </Card>
          <Card title="Items" flush>
            <Table head={['Product', 'SKU', 'Qty']}>
              {shipment.items.map((item) => (
                <tr key={item.id}>
                  <Cell>{item.product_name}</Cell>
                  <Cell>{item.sku}</Cell>
                  <Cell className="tabular-nums">{quantity(item.quantity)}</Cell>
                </tr>
              ))}
            </Table>
          </Card>
          <Card title="Tracking history">
            <ol className="grid gap-3 text-sm">
              {shipment.events.map((event) => (
                <li key={event.id}>
                  <strong>{label(SHIPMENT_STATUS, event.status)}</strong>
                  <span className="text-muted"> · {event.source}</span>
                  {event.note && <span className="text-muted"> · {event.note}</span>}
                  <span className="block text-xs text-muted">{dateTime(event.occurred_at)}</span>
                </li>
              ))}
            </ol>
          </Card>
        </div>
        <div className="grid content-start gap-6">
          <Card title="Courier">
            <CourierDetails
              shipment={shipment}
              onSaved={(next) => {
                setData(next);
                setNotice('Courier details saved.');
              }}
            />
            <div className="mt-5 border-t border-line pt-4 text-sm">
              <p className="font-semibold">Courier booking</p>
              {shipment.bookings.map((b) => (
                <p key={b.id} className="mt-1 text-muted">
                  {b.provider} · {b.status} {b.provider_reference && `· ${b.provider_reference}`}
                </p>
              ))}
              {shipment.courier_booking_available ? (
                ['ready', 'picking', 'packed', 'ready_for_dispatch'].includes(shipment.status) && (
                  <Button
                    className="mt-2"
                    variant="secondary"
                    busy={busy === 'book'}
                    onClick={() =>
                      void run(
                        'book',
                        async () => {
                          await api.post(
                            `/api/manage/fulfillment/shipments/${id}/courier-booking/`,
                            { idempotency_key: bookingKey },
                          );
                          await refresh();
                        },
                        'Courier booking requested.',
                      )
                    }
                  >
                    Book with courier
                  </Button>
                )
              ) : (
                <p className="mt-1 text-muted">
                  No courier provider is connected to this store, so bookings are not available.
                  Enter the courier and real tracking details manually.
                </p>
              )}
            </div>
          </Card>
          {shipment.payment_method === 'cod' && (
            <Card title="Cash on delivery">
              {shipment.cod ? (
                <>
                  <dl className="grid gap-2 sm:grid-cols-2">
                    <Detail label="Due">
                      {money(shipment.cod.amount_due, shipment.cod.currency)}
                    </Detail>
                    <Detail label="Collected">
                      {money(shipment.cod.collected_amount, shipment.cod.currency)}
                    </Detail>
                    <Detail label="Fees and deductions">
                      {money(shipment.cod.courier_fees, shipment.cod.currency)} ·{' '}
                      {money(shipment.cod.deductions, shipment.cod.currency)}
                    </Detail>
                    <Detail label="Expected remittance">
                      {money(shipment.cod.expected_remittance, shipment.cod.currency)}
                    </Detail>
                    <Detail label="Received">
                      {money(shipment.cod.actual_remittance, shipment.cod.currency)}
                    </Detail>
                    <Detail label="Status">
                      <StatusBadge
                        value={shipment.cod.status}
                        map={{
                          pending: 'Awaiting remittance',
                          reconciled: 'Reconciled',
                          disputed: 'Discrepancy',
                        }}
                      />
                    </Detail>
                  </dl>
                  {shipment.cod.actual_remittance === null && (
                    <Remittance
                      shipment={shipment}
                      onSaved={async () => {
                        await refresh();
                        setNotice('Remittance recorded.');
                      }}
                    />
                  )}
                </>
              ) : shipment.status === 'delivered' ? (
                <CodCollection
                  shipment={shipment}
                  onSaved={async () => {
                    await refresh();
                    setNotice('Collection recorded.');
                  }}
                />
              ) : (
                <p className="text-sm text-muted">Collection is recorded after delivery.</p>
              )}
            </Card>
          )}
          <Card title="Risk review">
            {shipment.fraud ? (
              <div className="grid gap-3 text-sm">
                <p>
                  <StatusBadge value={shipment.fraud.status} map={FRAUD_STATUS} /> · score{' '}
                  {shipment.fraud.score}
                </p>
                <ul className="grid gap-1">
                  {shipment.fraud.flags.map((flag) => (
                    <li key={flag.code} className="text-muted">
                      {flag.description} (+{flag.weight})
                    </li>
                  ))}
                  {!shipment.fraud.flags.length && <li className="text-muted">No risk signals.</li>}
                </ul>
                {shipment.fraud.reviews.map((r, index) => (
                  <p key={index} className="text-xs text-muted">
                    {r.decision} · {r.reason} · {dateTime(r.occurred_at)}
                  </p>
                ))}
                {session.permissions.includes('orders.manage') &&
                  shipment.fraud.flags.length > 0 && (
                    <form
                      className="grid gap-2 border-t border-line pt-3"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run(
                          'review',
                          async () => {
                            fraudAssessment(
                              await api.post<unknown>(
                                `/api/manage/fulfillment/fraud-assessments/${shipment.fraud?.id}/review/`,
                                review,
                              ),
                            );
                            await refresh();
                            setReview({ decision: 'clear', reason: '' });
                          },
                          'Review recorded.',
                        );
                      }}
                    >
                      <SelectField
                        label="Decision"
                        value={review.decision}
                        onChange={(e) => setReview({ ...review, decision: e.target.value })}
                        options={[
                          { value: 'clear', label: 'Clear' },
                          { value: 'monitor', label: 'Monitor' },
                          { value: 'confirm_risk', label: 'Confirm risk' },
                        ]}
                      />
                      <TextField
                        label="Reason"
                        required
                        maxLength={500}
                        value={review.reason}
                        onChange={(e) => setReview({ ...review, reason: e.target.value })}
                      />
                      <div>
                        <Button type="submit" variant="secondary" busy={busy === 'review'}>
                          Record review
                        </Button>
                      </div>
                    </form>
                  )}
              </div>
            ) : (
              <p className="text-sm text-muted">No assessment yet.</p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
