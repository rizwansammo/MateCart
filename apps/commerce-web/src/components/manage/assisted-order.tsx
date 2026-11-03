'use client';

import { useEffect, useRef, useState } from 'react';
import type { AssistedQuote, AssistedVariant, StaffSession } from '@matecart/shared-contracts';
import {
  ASSISTED_SOURCES,
  assistedQuote,
  assistedVariant,
  merchantOrderDetail,
} from '@matecart/shared-contracts';
import { Alert, Button, PhoneField, TextField, errorMessage } from '@matecart/shared-ui';
import { TextArea } from '../appearance-app';
import { toUnits } from '@/lib/money';
import {
  Card,
  Feedback,
  PageHeader,
  SOURCE,
  SelectField,
  api,
  label,
  money,
  newKey,
  quantity,
} from './kit';

interface Line {
  variant: AssistedVariant;
  quantity: string;
}

const ISSUE: Record<string, string> = {
  insufficient_stock: 'Not enough stock',
  unavailable: 'No longer for sale',
  price_unavailable: 'No current price',
  currency_mismatch: 'Different currency',
};

function useDebounced<T>(value: T, delay = 350): T {
  const [current, setCurrent] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setCurrent(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return current;
}

function ProductPicker({
  onAdd,
  chosen,
}: {
  onAdd: (variant: AssistedVariant) => void;
  chosen: Set<string>;
}) {
  const [text, setText] = useState('');
  const search = useDebounced(text);
  const [results, setResults] = useState<AssistedVariant[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    void api
      .get<unknown[]>(`/api/manage/orders/assisted/variants/?q=${encodeURIComponent(search)}`)
      .then((rows) => {
        if (live) {
          setResults(rows.map(assistedVariant));
          setError('');
        }
      })
      .catch((e: unknown) => {
        if (live) setError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, [search]);
  return (
    <div className="grid gap-3">
      <TextField
        label="Find a product"
        placeholder="Name, SKU or barcode"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {error && <Alert>{error}</Alert>}
      {results && !results.length && (
        <p className="text-sm text-muted">
          No published product matches. Only published products can be ordered.
        </p>
      )}
      {results && results.length > 0 && (
        <ul className="max-h-72 divide-y divide-line overflow-y-auto rounded-base border border-line">
          {results.map((row) => {
            const units = row.available_quantity === null ? null : toUnits(row.available_quantity);
            const out = units !== null && units <= 0n;
            return (
              <li key={row.id} className="flex items-center justify-between gap-3 p-3 text-sm">
                <span className="min-w-0">
                  <span className="block truncate font-semibold">{row.product_name}</span>
                  <span className="text-xs text-muted">
                    {[row.variant_title, row.sku].filter(Boolean).join(' · ')} ·{' '}
                    {row.unit_price ? money(row.unit_price, row.currency) : 'No price'} ·{' '}
                    {row.available_quantity === null
                      ? 'Stock not tracked'
                      : `${quantity(row.available_quantity)} available`}
                  </span>
                </span>
                <Button
                  variant="secondary"
                  type="button"
                  disabled={chosen.has(row.id) || out || !row.unit_price}
                  onClick={() => onAdd(row)}
                >
                  {chosen.has(row.id) ? 'Added' : out ? 'Out of stock' : 'Add'}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function AssistedOrderForm({ session }: { session: StaffSession }) {
  const [lines, setLines] = useState<Line[]>([]);
  const [details, setDetails] = useState({
    recipient_name: '',
    contact_phone: '',
    contact_email: '',
    address_line_1: '',
    address_line_2: '',
    city: '',
    region: '',
    postal_code: '',
    country: session.store_country || '',
    delivery_notes: '',
    coupon_code: '',
  });
  const [shippingMethod, setShippingMethod] = useState('');
  const [slot, setSlot] = useState('');
  const [source, setSource] = useState<string>('phone');
  const [reference, setReference] = useState('');
  const [attested, setAttested] = useState(false);
  const [latestQuote, setQuote] = useState<AssistedQuote | null>(null);
  const [quoteError, setQuoteError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // One key per order being entered: a retry or double click can only ever create one order.
  const key = useRef(newKey('assisted'));
  const pricing = useDebounced(
    JSON.stringify({
      items: lines.map((line) => ({ variant: line.variant.id, quantity: line.quantity })),
      country: details.country,
      region: details.region,
      coupon_code: details.coupon_code,
      shipping_method: shippingMethod || null,
      delivery_slot: slot || null,
    }),
    400,
  );

  useEffect(() => {
    const body = JSON.parse(pricing) as { items: unknown[] };
    if (!body.items.length) return;
    let live = true;
    void api
      .post<unknown>('/api/manage/orders/assisted/quote/', body)
      .then((value) => {
        if (!live) return;
        const next = assistedQuote(value);
        setQuote(next);
        setQuoteError('');
        // A method that stopped being eligible is cleared rather than kept silently; a single
        // eligible method is preselected.
        setShippingMethod((current) =>
          next.shipping_methods.some((m) => m.id === current)
            ? current
            : next.shipping_methods.length === 1
              ? (next.shipping_methods[0]?.id ?? '')
              : '',
        );
      })
      .catch((e: unknown) => {
        if (live) setQuoteError(errorMessage(e));
      });
    return () => {
      live = false;
    };
  }, [pricing]);

  const set = (name: keyof typeof details) => (e: { target: { value: string } }) =>
    setDetails((current) => ({ ...current, [name]: e.target.value }));
  const quote = lines.length ? latestQuote : null;
  const cod = quote?.payment_methods.includes('cod') ?? false;
  const chosen = new Set(lines.map((line) => line.variant.id));

  async function submit() {
    setBusy(true);
    setError('');
    try {
      const order = merchantOrderDetail(
        await api.post<unknown>('/api/manage/orders/assisted/', {
          items: lines.map((line) => ({ variant: line.variant.id, quantity: line.quantity })),
          ...details,
          shipping_method: shippingMethod || null,
          delivery_slot: slot || null,
          payment_method: 'cod',
          source,
          source_reference: reference,
          customer_request_attested: attested,
          idempotency_key: key.current,
        }),
      );
      window.location.assign(`/manage/orders/${order.id}`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <form
      className="grid gap-6"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <PageHeader
        eyebrow="Orders"
        title="Create manual order"
        intro="For orders customers place by phone, Facebook, WhatsApp or in person. Prices, stock, delivery charges and tax come from your store's normal rules; the order is unpaid until cash is collected."
      />
      <Feedback error={error} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid content-start gap-6">
          <Card title="1. Products">
            <ProductPicker
              chosen={chosen}
              onAdd={(variant) => setLines((current) => [...current, { variant, quantity: '1' }])}
            />
            {lines.length > 0 && (
              <ul className="mt-5 divide-y divide-line rounded-base border border-line">
                {lines.map((line, index) => {
                  const quoted = quote?.lines.find((row) => row.variant === line.variant.id);
                  return (
                    <li
                      key={line.variant.id}
                      className="grid gap-3 p-3 sm:grid-cols-[1fr_auto_auto] sm:items-end"
                    >
                      <div className="min-w-0 text-sm">
                        <p className="truncate font-semibold">{line.variant.product_name}</p>
                        <p className="text-xs text-muted">
                          {[line.variant.variant_title, line.variant.sku]
                            .filter(Boolean)
                            .join(' · ')}
                          {quoted?.unit_price &&
                            ` · ${money(quoted.unit_price, quoted.currency)} each`}
                        </p>
                        {quoted?.issue && (
                          <p className="mt-1 text-xs font-semibold text-danger-fg">
                            {label(ISSUE, quoted.issue)}
                            {quoted.available_quantity !== null &&
                              ` · ${quantity(quoted.available_quantity)} available`}
                          </p>
                        )}
                      </div>
                      <div className="w-28">
                        <TextField
                          label="Quantity"
                          inputMode="decimal"
                          required
                          value={line.quantity}
                          onChange={(e) =>
                            setLines((current) =>
                              current.map((row, at) =>
                                at === index ? { ...row, quantity: e.target.value } : row,
                              ),
                            )
                          }
                        />
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        aria-label={`Remove ${line.variant.product_name}`}
                        onClick={() =>
                          setLines((current) => current.filter((_, at) => at !== index))
                        }
                      >
                        Remove
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
          <Card title="2. Customer and delivery">
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label="Customer name"
                required
                maxLength={150}
                value={details.recipient_name}
                onChange={set('recipient_name')}
              />
              <PhoneField
                label="Phone number"
                required
                value={details.contact_phone}
                onChange={set('contact_phone')}
              />
              <TextField
                label="Email (optional)"
                type="email"
                value={details.contact_email}
                onChange={set('contact_email')}
              />
              <TextField
                label="Country code"
                required
                maxLength={2}
                value={details.country}
                onChange={(e) =>
                  setDetails((c) => ({ ...c, country: e.target.value.toUpperCase() }))
                }
                hint="Two letters, for example BD."
              />
              <TextField
                label="Address"
                required
                maxLength={200}
                value={details.address_line_1}
                onChange={set('address_line_1')}
              />
              <TextField
                label="Address line 2 (optional)"
                maxLength={200}
                value={details.address_line_2}
                onChange={set('address_line_2')}
              />
              <TextField
                label="City"
                required
                maxLength={100}
                value={details.city}
                onChange={set('city')}
              />
              <TextField
                label="Area or region (optional)"
                maxLength={100}
                value={details.region}
                onChange={set('region')}
              />
              <TextField
                label="Postal code (optional)"
                maxLength={32}
                value={details.postal_code}
                onChange={set('postal_code')}
              />
            </div>
            <div className="mt-4">
              <TextArea
                label="Delivery instructions (optional)"
                maxLength={500}
                value={details.delivery_notes}
                onChange={set('delivery_notes')}
              />
            </div>
            <p className="mt-3 text-xs leading-5 text-muted">
              No customer account is created or linked. The details are kept on this order only.
            </p>
          </Card>
          <Card title="3. Where the order came from">
            <div className="grid gap-4 sm:grid-cols-2">
              <SelectField
                label="Order source"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                options={ASSISTED_SOURCES.map((value) => ({ value, label: label(SOURCE, value) }))}
              />
              <TextField
                label="Reference (optional)"
                maxLength={120}
                placeholder="e.g. chat name or call time"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
              />
            </div>
            <label className="mt-4 flex gap-3 rounded-base border border-line p-3 text-sm">
              <input
                type="checkbox"
                required
                checked={attested}
                onChange={(e) => setAttested(e.target.checked)}
                className="mt-1 accent-brand-deep"
              />
              <span>
                I confirm the customer asked for this order with these products and details. This is
                recorded as my statement, with my name and the time.
              </span>
            </label>
          </Card>
        </div>
        <div className="grid content-start gap-6 xl:sticky xl:top-6">
          <Card title="4. Delivery, payment and total">
            {!lines.length ? (
              <p className="text-sm text-muted">
                Add a product to see delivery options and the total.
              </p>
            ) : (
              <div className="grid gap-4">
                {quoteError && <Alert>{quoteError}</Alert>}
                {quote && !quote.valid && (
                  <Alert>
                    Some items cannot be ordered as entered. Adjust the quantities marked above.
                  </Alert>
                )}
                {quote?.valid && (
                  <>
                    <SelectField
                      label="Delivery method"
                      required
                      value={shippingMethod}
                      onChange={(e) => setShippingMethod(e.target.value)}
                      options={[
                        {
                          value: '',
                          label: quote.shipping_methods.length
                            ? 'Choose a delivery method'
                            : 'No method serves this address',
                        },
                        ...quote.shipping_methods.map((m) => ({
                          value: m.id,
                          label: `${m.name} · ${money(m.amount, m.currency)}`,
                        })),
                      ]}
                      hint={
                        details.country ? undefined : 'Enter the country to see delivery methods.'
                      }
                    />
                    {quote.delivery_slots.length > 0 && (
                      <SelectField
                        label="Delivery time"
                        required
                        value={slot}
                        onChange={(e) => setSlot(e.target.value)}
                        options={[
                          { value: '', label: 'Choose a time' },
                          ...quote.delivery_slots.map((s) => ({ value: s.id, label: s.label })),
                        ]}
                      />
                    )}
                    <TextField
                      label="Coupon (optional)"
                      maxLength={32}
                      value={details.coupon_code}
                      onChange={(e) =>
                        setDetails((c) => ({ ...c, coupon_code: e.target.value.toUpperCase() }))
                      }
                      hint="Only your store's active coupons apply. Prices cannot be edited here."
                    />
                    {quote.totals && (
                      <dl className="grid gap-1.5 rounded-base bg-canvas p-4 text-sm">
                        {(
                          [
                            ['Items', quote.totals.item_subtotal],
                            ['Discount', quote.totals.discount_total],
                            ['Delivery', quote.totals.shipping_total],
                            ['Tax', quote.totals.tax_total],
                          ] as const
                        ).map(([name, value]) => (
                          <div key={name} className="flex justify-between">
                            <dt className="text-muted">{name}</dt>
                            <dd className="tabular-nums">{money(value, quote.totals?.currency)}</dd>
                          </div>
                        ))}
                        <div className="flex justify-between border-t border-line pt-2 text-base font-bold">
                          <dt>Total to collect</dt>
                          <dd className="tabular-nums">
                            {money(quote.totals.grand_total, quote.totals.currency)}
                          </dd>
                        </div>
                      </dl>
                    )}
                    <div className="rounded-base border border-line p-3 text-sm">
                      <p className="font-semibold">Payment</p>
                      {cod ? (
                        <p className="mt-1 text-muted">
                          Cash on delivery. The order stays unpaid until the cash is collected and
                          recorded.
                        </p>
                      ) : (
                        <p className="mt-1 text-danger-fg">
                          Cash on delivery is not available for this order (digital items, or cash
                          on delivery is switched off). Manual orders cannot take online payment.
                        </p>
                      )}
                    </div>
                  </>
                )}
                <Button
                  type="submit"
                  busy={busy}
                  disabled={!quote?.valid || !cod || !shippingMethod || !attested}
                >
                  Place order
                </Button>
                <p className="text-xs leading-5 text-muted">
                  Stock is checked again and reserved when you place the order. Submitting twice
                  creates only one order.
                </p>
              </div>
            )}
          </Card>
        </div>
      </div>
    </form>
  );
}
