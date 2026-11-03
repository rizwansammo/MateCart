/**
 * Runtime-validated merchant dashboard responses (Phase 11), mirroring the committed OpenAPI
 * schema. Money and quantities stay decimal strings; nothing here computes a total.
 */
import { array, boolean, number, object, string, strings, type Decoder } from './auth';
import { decimal, nullable, translations, type Page, type Translations } from './catalog';
import { order, type Order } from './orders';
import { afterSalesCase, type AfterSalesCase } from './after-sales';

const MEDIA_URL = /^\/api\/(catalog\/media|manage\/media\/assets)\/[0-9a-f-]{36}\/(file\/)?$/;

function mediaUrl(value: unknown): string {
  const text = string(value);
  if (!MEDIA_URL.test(text)) throw new Error('Invalid response');
  return text;
}
function httpsUrl(value: unknown): string {
  const text = string(value);
  if (text && !text.startsWith('https://')) throw new Error('Invalid response');
  return text;
}
function integer(value: unknown): number {
  const n = number(value);
  if (!Number.isInteger(n)) throw new Error('Invalid response');
  return n;
}
function counts<K extends string>(keys: readonly K[]): Decoder<Record<K, number>> {
  return (value) => {
    const o = object(value);
    return Object.fromEntries(keys.map((key) => [key, integer(o[key])])) as Record<K, number>;
  };
}
/** A paginated list, decoded with the same envelope as every other page. */
export function managePage<T>(decode: Decoder<T>): Decoder<Page<T>> {
  return (value) => {
    const o = object(value);
    return {
      count: integer(o.count),
      page: integer(o.page),
      page_size: integer(o.page_size),
      results: array(o.results, decode),
    };
  };
}

// --- Dashboard and reports ------------------------------------------------------------------------

export interface FinanceRow {
  currency: string;
  order_value: string;
  collected: string;
  refunded: string;
  net_collected: string;
}
const financeRow: Decoder<FinanceRow> = (value) => {
  const o = object(value);
  return {
    currency: string(o.currency),
    order_value: decimal(o.order_value),
    collected: decimal(o.collected),
    refunded: decimal(o.refunded),
    net_collected: decimal(o.net_collected),
  };
};
export interface CodRow {
  currency: string;
  awaiting_collection: string;
  courier_remittance_pending: string;
  courier_remittance_disputed: string;
}
export interface CashPosition {
  cod: CodRow[];
  awaiting_collection_orders: number;
  courier_records_pending: number;
  courier_records_disputed: number;
}
const cashPosition: Decoder<CashPosition> = (value) => {
  const o = object(value);
  return {
    cod: array(o.cod, (row) => {
      const r = object(row);
      return {
        currency: string(r.currency),
        awaiting_collection: decimal(r.awaiting_collection),
        courier_remittance_pending: decimal(r.courier_remittance_pending),
        courier_remittance_disputed: decimal(r.courier_remittance_disputed),
      };
    }),
    awaiting_collection_orders: integer(o.awaiting_collection_orders),
    courier_records_pending: integer(o.courier_records_pending),
    courier_records_disputed: integer(o.courier_records_disputed),
  };
};
export interface Finance {
  totals: FinanceRow[];
  cash: CashPosition;
}
const finance: Decoder<Finance> = (value) => {
  const o = object(value);
  return { totals: array(o.totals, financeRow), cash: cashPosition(o.cash) };
};

export const ORDER_SOURCES = [
  'storefront',
  'facebook',
  'whatsapp',
  'phone',
  'in_store',
  'other',
] as const;
export type OrderSource = (typeof ORDER_SOURCES)[number];
export const ASSISTED_SOURCES = ORDER_SOURCES.filter(
  (source): source is Exclude<OrderSource, 'storefront'> => source !== 'storefront',
);
function orderSource(value: unknown): OrderSource {
  const text = string(value);
  if (!(ORDER_SOURCES as readonly string[]).includes(text)) throw new Error('Invalid response');
  return text as OrderSource;
}

export interface OrderBrief {
  id: string;
  number: string;
  status: string;
  payment_method: string;
  payment_status: string;
  currency: string;
  grand_total: string;
  placed_at: string;
  source: OrderSource;
  recipient_name: string;
}
export const orderBrief: Decoder<OrderBrief> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    number: string(o.number),
    status: string(o.status),
    payment_method: string(o.payment_method),
    payment_status: string(o.payment_status),
    currency: string(o.currency),
    grand_total: decimal(o.grand_total),
    placed_at: string(o.placed_at),
    source: orderSource(o.source),
    recipient_name: string(o.recipient_name),
  };
};

const ORDER_COUNT_KEYS = [
  'total',
  'placed',
  'pending_payment',
  'confirmed',
  'processing',
  'cancelled',
  'delivered',
] as const;
const OPERATION_KEYS = [
  'awaiting_confirmation',
  'to_prepare',
  'pending_shipments',
  'in_transit',
  'delivery_failed',
  'fraud_review',
] as const;
const AFTERSALES_KEYS = [
  'open_returns',
  'open_exchanges',
  'open_warranty',
  'open_support',
  'pending_reviews',
] as const;

export interface LowStockItem {
  id: string;
  variant: string;
  sku: string;
  product_name: string;
  variant_title: string;
  location_code: string;
  available: string;
  low_stock_threshold: string;
}
export interface Dashboard {
  start: string;
  end: string;
  orders: Record<(typeof ORDER_COUNT_KEYS)[number], number> | null;
  operations: Record<(typeof OPERATION_KEYS)[number], number> | null;
  aftersales: Record<(typeof AFTERSALES_KEYS)[number], number> | null;
  recent_orders: OrderBrief[] | null;
  finance: Finance | null;
  inventory: {
    low_stock: number;
    out_of_stock: number;
    expiring_batches: number;
    low_stock_items: LowStockItem[];
  } | null;
}
export function dashboard(value: unknown): Dashboard {
  const o = object(value);
  return {
    start: string(o.start),
    end: string(o.end),
    orders: nullable(counts(ORDER_COUNT_KEYS))(o.orders),
    operations: nullable(counts(OPERATION_KEYS))(o.operations),
    aftersales: nullable(counts(AFTERSALES_KEYS))(o.aftersales),
    recent_orders: nullable((v: unknown) => array(v, orderBrief))(o.recent_orders),
    finance: nullable(finance)(o.finance),
    inventory: nullable((v: unknown) => {
      const i = object(v);
      return {
        low_stock: integer(i.low_stock),
        out_of_stock: integer(i.out_of_stock),
        expiring_batches: integer(i.expiring_batches),
        low_stock_items: array(i.low_stock_items, (row) => {
          const r = object(row);
          return {
            id: string(r.id),
            variant: string(r.variant),
            sku: string(r.sku),
            product_name: string(r.product_name),
            variant_title: string(r.variant_title),
            location_code: string(r.location_code),
            available: decimal(r.available),
            low_stock_threshold: decimal(r.low_stock_threshold),
          };
        }),
      };
    })(o.inventory),
  };
}

export interface GroupedValue {
  key: string;
  currency: string;
  orders: number;
  value: string;
}
const groupedValue: Decoder<GroupedValue> = (value) => {
  const o = object(value);
  return {
    key: string(o.key),
    currency: string(o.currency),
    orders: integer(o.orders),
    value: decimal(o.value),
  };
};
export interface SalesReport {
  start: string;
  end: string;
  summary: { currency: string; order_value: string; average_order_value: string }[];
  order_counts: { currency: string; orders: number }[];
  totals: FinanceRow[];
  cash: CashPosition;
  by_status: { key: string; orders: number }[];
  by_payment_method: GroupedValue[];
  by_source: GroupedValue[];
  daily: { date: string; currency: string; orders: number; value: string }[];
  top_products: {
    variant: string;
    sku: string;
    product_name: string;
    currency: string;
    quantity: string;
    revenue: string;
    orders: number;
  }[];
  aftersales: { kind: string; status: string; cases: number }[];
  inventory: { low_stock: number; out_of_stock: number };
}
export function salesReport(value: unknown): SalesReport {
  const o = object(value);
  const inventory = object(o.inventory);
  return {
    start: string(o.start),
    end: string(o.end),
    summary: array(o.summary, (row) => {
      const r = object(row);
      return {
        currency: string(r.currency),
        order_value: decimal(r.order_value),
        average_order_value: decimal(r.average_order_value),
      };
    }),
    order_counts: array(o.order_counts, (row) => {
      const r = object(row);
      return { currency: string(r.currency), orders: integer(r.orders) };
    }),
    totals: array(o.totals, financeRow),
    cash: cashPosition(o.cash),
    by_status: array(o.by_status, (row) => {
      const r = object(row);
      return { key: string(r.key), orders: integer(r.orders) };
    }),
    by_payment_method: array(o.by_payment_method, groupedValue),
    by_source: array(o.by_source, groupedValue),
    daily: array(o.daily, (row) => {
      const r = object(row);
      return {
        date: string(r.date),
        currency: string(r.currency),
        orders: integer(r.orders),
        value: decimal(r.value),
      };
    }),
    top_products: array(o.top_products, (row) => {
      const r = object(row);
      return {
        variant: string(r.variant),
        sku: string(r.sku),
        product_name: string(r.product_name),
        currency: string(r.currency),
        quantity: decimal(r.quantity),
        revenue: decimal(r.revenue),
        orders: integer(r.orders),
      };
    }),
    aftersales: array(o.aftersales, (row) => {
      const r = object(row);
      return { kind: string(r.kind), status: string(r.status), cases: integer(r.cases) };
    }),
    inventory: {
      low_stock: integer(inventory.low_stock),
      out_of_stock: integer(inventory.out_of_stock),
    },
  };
}

// --- Merchant orders ------------------------------------------------------------------------------

export interface MerchantOrderSummary extends OrderBrief {
  contact_phone: string;
  item_count: number;
  shipment_status: string | null;
}
export const merchantOrderSummary: Decoder<MerchantOrderSummary> = (value) => {
  const o = object(value);
  return {
    ...orderBrief(value),
    contact_phone: string(o.contact_phone),
    item_count: integer(o.item_count),
    shipment_status: nullable(string)(o.shipment_status),
  };
};

export interface OrderEvent {
  occurred_at: string;
  event: string;
  from_status: string;
  to_status: string;
  payment_status: string;
  reason: string;
}
export interface RefundRecord {
  id: string;
  method: string;
  amount: string;
  currency: string;
  status: string;
  reason: string;
  failure_code: string;
  created_at: string;
  completed_at: string | null;
}
export interface PaymentRecord {
  id: string;
  kind: string;
  status: string;
  provider: string;
  amount: string;
  currency: string;
  occurred_at: string;
}
export interface RelatedCase {
  id: string;
  kind: string;
  status: string;
  subject: string;
  created_at: string;
}
const relatedCase: Decoder<RelatedCase> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    kind: string(o.kind),
    status: string(o.status),
    subject: string(o.subject),
    created_at: string(o.created_at),
  };
};
export interface MerchantOrderDetail extends Order {
  source: OrderSource;
  events: OrderEvent[];
  refunds: RefundRecord[];
  assisted: {
    created_by_name: string;
    created_at: string;
    source_reference: string;
    customer_request_attested: boolean;
  } | null;
  customer: { id: string; full_name: string } | null;
  payments: PaymentRecord[];
  shipment_id: string | null;
  fraud: { id: string; score: number; status: string; assessed_at: string } | null;
  cases: RelatedCase[];
  allowed_transitions: string[];
  can_record_cod: boolean;
  refundable_amount: string;
}
export function merchantOrderDetail(value: unknown): MerchantOrderDetail {
  const o = object(value);
  return {
    ...order(value),
    source: orderSource(o.source),
    events: array(o.events, (row) => {
      const r = object(row);
      return {
        occurred_at: string(r.occurred_at),
        event: string(r.event),
        from_status: string(r.from_status),
        to_status: string(r.to_status),
        payment_status: string(r.payment_status),
        reason: string(r.reason),
      };
    }),
    refunds: array(o.refunds, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        method: string(r.method),
        amount: decimal(r.amount),
        currency: string(r.currency),
        status: string(r.status),
        reason: string(r.reason),
        failure_code: string(r.failure_code),
        created_at: string(r.created_at),
        completed_at: nullable(string)(r.completed_at),
      };
    }),
    assisted: nullable((v: unknown) => {
      const a = object(v);
      return {
        created_by_name: string(a.created_by_name),
        created_at: string(a.created_at),
        source_reference: string(a.source_reference),
        customer_request_attested: boolean(a.customer_request_attested),
      };
    })(o.assisted),
    customer: nullable((v: unknown) => {
      const c = object(v);
      return { id: string(c.id), full_name: string(c.full_name) };
    })(o.customer),
    payments: array(o.payments, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        kind: string(r.kind),
        status: string(r.status),
        provider: string(r.provider),
        amount: decimal(r.amount),
        currency: string(r.currency),
        occurred_at: string(r.occurred_at),
      };
    }),
    shipment_id: nullable(string)(o.shipment_id),
    fraud: nullable((v: unknown) => {
      const f = object(v);
      return {
        id: string(f.id),
        score: integer(f.score),
        status: string(f.status),
        assessed_at: string(f.assessed_at),
      };
    })(o.fraud),
    cases: array(o.cases, relatedCase),
    allowed_transitions: strings(o.allowed_transitions),
    can_record_cod: boolean(o.can_record_cod),
    refundable_amount: decimal(o.refundable_amount),
  };
}

// --- Merchant-assisted orders ---------------------------------------------------------------------

export interface AssistedVariant {
  id: string;
  sku: string;
  product_name: string;
  variant_title: string;
  kind: string;
  inventory_unit: string;
  unit_price: string | null;
  currency: string | null;
  available_quantity: string | null;
}
export const assistedVariant: Decoder<AssistedVariant> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    sku: string(o.sku),
    product_name: string(o.product_name),
    variant_title: string(o.variant_title),
    kind: string(o.kind),
    inventory_unit: string(o.inventory_unit),
    unit_price: nullable(decimal)(o.unit_price),
    currency: nullable(string)(o.currency),
    available_quantity: nullable(decimal)(o.available_quantity),
  };
};
export interface AssistedQuoteLine {
  variant: string;
  sku: string;
  product_name: string;
  variant_title: string;
  kind: string;
  quantity: string;
  unit_price: string | null;
  currency: string | null;
  subtotal: string | null;
  available_quantity: string | null;
  issue: string;
}
export interface AssistedQuote {
  valid: boolean;
  lines: AssistedQuoteLine[];
  totals: {
    currency: string;
    requires_shipping: boolean;
    item_subtotal: string;
    discount_total: string;
    shipping_total: string;
    tax_total: string;
    grand_total: string;
    coupon_code: string;
    shipping_method: string | null;
    delivery_slot: string | null;
  } | null;
  shipping_methods: {
    id: string;
    name: string;
    description: string;
    amount: string;
    currency: string;
    free_above: string | null;
  }[];
  delivery_slots: { id: string; label: string; starts_at: string; ends_at: string }[];
  payment_methods: string[];
}
export function assistedQuote(value: unknown): AssistedQuote {
  const o = object(value);
  return {
    valid: boolean(o.valid),
    lines: array(o.lines, (row) => {
      const r = object(row);
      return {
        variant: string(r.variant),
        sku: string(r.sku),
        product_name: string(r.product_name),
        variant_title: string(r.variant_title),
        kind: string(r.kind),
        quantity: decimal(r.quantity),
        unit_price: nullable(decimal)(r.unit_price),
        currency: nullable(string)(r.currency),
        subtotal: nullable(decimal)(r.subtotal),
        available_quantity: nullable(decimal)(r.available_quantity),
        issue: string(r.issue),
      };
    }),
    totals: nullable((v: unknown) => {
      const t = object(v);
      return {
        currency: string(t.currency),
        requires_shipping: boolean(t.requires_shipping),
        item_subtotal: decimal(t.item_subtotal),
        discount_total: decimal(t.discount_total),
        shipping_total: decimal(t.shipping_total),
        tax_total: decimal(t.tax_total),
        grand_total: decimal(t.grand_total),
        coupon_code: string(t.coupon_code),
        shipping_method: nullable(string)(t.shipping_method),
        delivery_slot: nullable(string)(t.delivery_slot),
      };
    })(o.totals),
    shipping_methods: array(o.shipping_methods, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        name: string(r.name),
        description: string(r.description),
        amount: decimal(r.amount),
        currency: string(r.currency),
        free_above: nullable(decimal)(r.free_above),
      };
    }),
    delivery_slots: array(o.delivery_slots, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        label: string(r.label),
        starts_at: string(r.starts_at),
        ends_at: string(r.ends_at),
      };
    }),
    payment_methods: strings(o.payment_methods),
  };
}

// --- Customers ------------------------------------------------------------------------------------

export interface CustomerSummary {
  id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  email_verified: boolean;
  phone_verified: boolean;
  is_active: boolean;
  created_at: string;
  order_count: number;
  last_order_at: string | null;
}
export const customerSummary: Decoder<CustomerSummary> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    full_name: string(o.full_name),
    email: nullable(string)(o.email),
    phone: nullable(string)(o.phone),
    email_verified: boolean(o.email_verified),
    phone_verified: boolean(o.phone_verified),
    is_active: boolean(o.is_active),
    created_at: string(o.created_at),
    order_count: integer(o.order_count),
    last_order_at: nullable(string)(o.last_order_at),
  };
};
export interface CustomerOrder {
  id: string;
  number: string;
  status: string;
  payment_status: string;
  currency: string;
  grand_total: string;
  placed_at: string;
}
export interface CustomerDetail extends CustomerSummary {
  orders: CustomerOrder[];
  cases: RelatedCase[];
}
export function customerDetail(value: unknown): CustomerDetail {
  const o = object(value);
  return {
    ...customerSummary(value),
    orders: array(o.orders, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        number: string(r.number),
        status: string(r.status),
        payment_status: string(r.payment_status),
        currency: string(r.currency),
        grand_total: decimal(r.grand_total),
        placed_at: string(r.placed_at),
      };
    }),
    cases: array(o.cases, relatedCase),
  };
}

// --- Shipments ------------------------------------------------------------------------------------

export interface ShipmentSummary {
  id: string;
  reference: string;
  order_id: string;
  order_number: string;
  recipient_name: string;
  city: string;
  status: string;
  courier_name: string;
  tracking_number: string;
  created_at: string;
  fraud_status: string | null;
}
export const shipmentSummary: Decoder<ShipmentSummary> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    reference: string(o.reference),
    order_id: string(o.order_id),
    order_number: string(o.order_number),
    recipient_name: string(o.recipient_name),
    city: string(o.city),
    status: string(o.status),
    courier_name: string(o.courier_name),
    tracking_number: string(o.tracking_number),
    created_at: string(o.created_at),
    fraud_status: nullable(string)(o.fraud_status),
  };
};
export interface CodRecord {
  id: string;
  courier_code: string;
  currency: string;
  amount_due: string;
  collected_amount: string;
  courier_fees: string;
  deductions: string;
  deduction_note: string;
  expected_remittance: string;
  actual_remittance: string | null;
  outstanding_amount: string;
  collection_reference: string;
  collection_recorded_at: string;
  remittance_reference: string;
  remitted_at: string | null;
  status: string;
}
export interface FraudAssessment {
  id: string;
  score: number;
  status: string;
  assessed_at: string;
  reviewed_at: string | null;
  flags: { code: string; description: string; weight: number }[];
  reviews: { decision: string; reason: string; occurred_at: string }[];
}
export interface MerchantShipment {
  id: string;
  order_id: string;
  order_number: string;
  reference: string;
  status: string;
  courier_name: string;
  tracking_number: string;
  tracking_url: string;
  dispatched_at: string | null;
  delivered_at: string | null;
  failed_at: string | null;
  operational_notes: string;
  events: {
    id: string;
    occurred_at: string;
    from_status: string;
    status: string;
    source: string;
    note: string;
  }[];
  items: { id: string; sku: string; product_name: string; quantity: string }[];
  bookings: {
    id: string;
    provider: string;
    status: string;
    provider_reference: string;
    failure_code: string;
    created_at: string;
  }[];
  cod: CodRecord | null;
  fraud: FraudAssessment | null;
  payment_method: string;
  next_statuses: string[];
  serial_allocations: { allocation: string; sku: string; quantity: string }[];
  courier_booking_available: boolean;
}
export function fraudAssessment(value: unknown): FraudAssessment {
  const o = object(value);
  return {
    id: string(o.id),
    score: integer(o.score),
    status: string(o.status),
    assessed_at: string(o.assessed_at),
    reviewed_at: nullable(string)(o.reviewed_at),
    flags: array(o.flags, (row) => {
      const r = object(row);
      return {
        code: string(r.code),
        description: string(r.description),
        weight: integer(r.weight),
      };
    }),
    reviews: array(o.reviews, (row) => {
      const r = object(row);
      return {
        decision: string(r.decision),
        reason: string(r.reason),
        occurred_at: string(r.occurred_at),
      };
    }),
  };
}
export function codRecord(value: unknown): CodRecord {
  const o = object(value);
  return {
    id: string(o.id),
    courier_code: string(o.courier_code),
    currency: string(o.currency),
    amount_due: decimal(o.amount_due),
    collected_amount: decimal(o.collected_amount),
    courier_fees: decimal(o.courier_fees),
    deductions: decimal(o.deductions),
    deduction_note: string(o.deduction_note),
    expected_remittance: decimal(o.expected_remittance),
    actual_remittance: nullable(decimal)(o.actual_remittance),
    outstanding_amount: decimal(o.outstanding_amount),
    collection_reference: string(o.collection_reference),
    collection_recorded_at: string(o.collection_recorded_at),
    remittance_reference: string(o.remittance_reference),
    remitted_at: nullable(string)(o.remitted_at),
    status: string(o.status),
  };
}
export function merchantShipment(value: unknown): MerchantShipment {
  const o = object(value);
  return {
    id: string(o.id),
    order_id: string(o.order_id),
    order_number: string(o.order_number),
    reference: string(o.reference),
    status: string(o.status),
    courier_name: string(o.courier_name),
    tracking_number: string(o.tracking_number),
    tracking_url: httpsUrl(o.tracking_url),
    dispatched_at: nullable(string)(o.dispatched_at),
    delivered_at: nullable(string)(o.delivered_at),
    failed_at: nullable(string)(o.failed_at),
    operational_notes: string(o.operational_notes),
    events: array(o.events, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        occurred_at: string(r.occurred_at),
        from_status: string(r.from_status),
        status: string(r.status),
        source: string(r.source),
        note: string(r.note),
      };
    }),
    items: array(o.items, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        sku: string(r.sku),
        product_name: string(r.product_name),
        quantity: decimal(r.quantity),
      };
    }),
    bookings: array(o.bookings, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        provider: string(r.provider),
        status: string(r.status),
        provider_reference: string(r.provider_reference),
        failure_code: string(r.failure_code),
        created_at: string(r.created_at),
      };
    }),
    cod: nullable(codRecord)(o.cod),
    fraud: nullable(fraudAssessment)(o.fraud),
    payment_method: string(o.payment_method),
    next_statuses: strings(o.next_statuses),
    serial_allocations: array(o.serial_allocations, (row) => {
      const r = object(row);
      return {
        allocation: string(r.allocation),
        sku: string(r.sku),
        quantity: decimal(r.quantity),
      };
    }),
    courier_booking_available: boolean(o.courier_booking_available),
  };
}

// --- After sales ----------------------------------------------------------------------------------

export interface MerchantCase extends AfterSalesCase {
  order_id: string | null;
  order_number: string | null;
  product_name: string | null;
  sku: string | null;
  currency: string | null;
  customer_name: string;
  next_statuses: string[];
  required_refund: string | null;
  attributed_refunds: { id: string; amount: string; currency: string; status: string }[];
}
export function merchantCase(value: unknown): MerchantCase {
  const o = object(value);
  return {
    ...afterSalesCase(value),
    order_id: nullable(string)(o.order_id),
    order_number: nullable(string)(o.order_number),
    product_name: nullable(string)(o.product_name),
    sku: nullable(string)(o.sku),
    currency: nullable(string)(o.currency),
    customer_name: string(o.customer_name),
    next_statuses: strings(o.next_statuses),
    required_refund: nullable(decimal)(o.required_refund),
    attributed_refunds: array(o.attributed_refunds, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        amount: decimal(r.amount),
        currency: string(r.currency),
        status: string(r.status),
      };
    }),
  };
}
export interface MerchantReview {
  id: string;
  rating: number;
  body: string;
  status: string;
  decision_reason: string;
  created_at: string;
  product_name: string;
  customer_name: string;
}
export function merchantReview(value: unknown): MerchantReview {
  const o = object(value);
  return {
    id: string(o.id),
    rating: integer(o.rating),
    body: string(o.body),
    status: string(o.status),
    decision_reason: string(o.decision_reason),
    created_at: string(o.created_at),
    product_name: string(o.product_name),
    customer_name: string(o.customer_name),
  };
}

// --- Catalogue management (/api/manage/catalog/, pricing, media) ----------------------------------

export interface ManagedProductSummary {
  id: string;
  name: string;
  slug: string;
  product_type: string;
  status: string;
  variant_count: number;
  price_range: { currency: string; min_amount: string; max_amount: string } | null;
  available: string;
  updated_at: string;
}
export const managedProductSummary: Decoder<ManagedProductSummary> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    name: string(o.name),
    slug: string(o.slug),
    product_type: string(o.product_type),
    status: string(o.status),
    variant_count: integer(o.variant_count),
    price_range: nullable((v: unknown) => {
      const p = object(v);
      return {
        currency: string(p.currency),
        min_amount: decimal(p.min_amount),
        max_amount: decimal(p.max_amount),
      };
    })(o.price_range),
    available: decimal(o.available),
    updated_at: string(o.updated_at),
  };
};
export interface ManagedPrice {
  id: string;
  price_list: string;
  currency: string;
  amount: string;
  compare_at_amount: string | null;
  cost_amount?: string | null;
}
export interface ManagedVariant {
  id: string;
  sku: string;
  title: string;
  options: { attribute: string; name: string; option: string; value: string }[];
  position: number;
  is_active: boolean;
  kind: string;
  barcode: string | null;
  isbn: string | null;
  weight_grams: number | null;
  inventory_unit: string;
  net_quantity: string | null;
  net_unit: string;
  release_date: string | null;
  prices: ManagedPrice[];
  stock: { on_hand: string; reserved: string; available: string };
}
export function managedVariant(value: unknown): ManagedVariant {
  const o = object(value);
  const stock = object(o.stock);
  return {
    id: string(o.id),
    sku: string(o.sku),
    title: string(o.title),
    options: array(o.options, (row) => {
      const r = object(row);
      return {
        attribute: string(r.attribute),
        name: string(r.name),
        option: string(r.option),
        value: string(r.value),
      };
    }),
    position: integer(o.position),
    is_active: boolean(o.is_active),
    kind: string(o.kind),
    barcode: nullable(string)(o.barcode),
    isbn: nullable(string)(o.isbn),
    weight_grams: nullable(integer)(o.weight_grams),
    inventory_unit: string(o.inventory_unit),
    net_quantity: nullable(decimal)(o.net_quantity),
    net_unit: string(o.net_unit),
    release_date: nullable(string)(o.release_date),
    prices: array(o.prices, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        price_list: string(r.price_list),
        currency: string(r.currency),
        amount: decimal(r.amount),
        compare_at_amount: nullable(decimal)(r.compare_at_amount),
        ...(r.cost_amount === undefined ? {} : { cost_amount: nullable(decimal)(r.cost_amount) }),
      };
    }),
    stock: {
      on_hand: decimal(stock.on_hand),
      reserved: decimal(stock.reserved),
      available: decimal(stock.available),
    },
  };
}
/** Preset details keep the API's own field names; the editor shows only the store's type. */
export type ProductDetails = Record<string, string | number | boolean | string[] | null>;
function details(value: unknown): ProductDetails | null {
  if (value === null) return null;
  const result: ProductDetails = {};
  for (const [key, field] of Object.entries(object(value))) {
    if (
      field === null ||
      typeof field === 'string' ||
      typeof field === 'number' ||
      typeof field === 'boolean'
    )
      result[key] = field;
    else if (Array.isArray(field)) result[key] = strings(field);
  }
  return result;
}
export interface ManagedProduct {
  id: string;
  name: string;
  slug: string;
  product_type: string;
  status: string;
  brand: { id: string; name: string } | null;
  summary: string;
  description: string;
  tags: string[];
  seo_title: string;
  seo_description: string;
  track_inventory: boolean;
  tracking_mode: string;
  translations: Translations;
  published_at: string | null;
  categories: { id: string; name: string; is_primary: boolean }[];
  options: { attribute: string; code: string; name: string; position: number }[];
  variants: ManagedVariant[];
  media: {
    asset: string;
    variant: string | null;
    position: number;
    is_primary: boolean;
    alt_text: string;
    url: string;
  }[];
  apparel: ProductDetails | null;
  grocery: ProductDetails | null;
  electronics: ProductDetails | null;
  book: ProductDetails | null;
  publication_problems: string[];
  updated_at: string;
}
export function managedProduct(value: unknown): ManagedProduct {
  const o = object(value);
  return {
    id: string(o.id),
    name: string(o.name),
    slug: string(o.slug),
    product_type: string(o.product_type),
    status: string(o.status),
    brand: nullable((v: unknown) => {
      const b = object(v);
      return { id: string(b.id), name: string(b.name) };
    })(o.brand),
    summary: string(o.summary),
    description: string(o.description),
    tags: strings(o.tags),
    seo_title: string(o.seo_title),
    seo_description: string(o.seo_description),
    track_inventory: boolean(o.track_inventory),
    tracking_mode: string(o.tracking_mode),
    translations: translations(o.translations),
    published_at: nullable(string)(o.published_at),
    categories: array(o.categories, (row) => {
      const r = object(row);
      return { id: string(r.id), name: string(r.name), is_primary: boolean(r.is_primary) };
    }),
    options: array(o.options, (row) => {
      const r = object(row);
      return {
        attribute: string(r.attribute),
        code: string(r.code),
        name: string(r.name),
        position: integer(r.position),
      };
    }),
    variants: array(o.variants, managedVariant),
    media: array(o.media, (row) => {
      const r = object(row);
      return {
        asset: string(r.asset),
        variant: nullable(string)(r.variant),
        position: integer(r.position),
        is_primary: boolean(r.is_primary),
        alt_text: string(r.alt_text),
        url: mediaUrl(r.url),
      };
    }),
    apparel: details(o.apparel),
    grocery: details(o.grocery),
    electronics: details(o.electronics),
    book: details(o.book),
    publication_problems: strings(o.publication_problems),
    updated_at: string(o.updated_at),
  };
}

export interface NamedEntity {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
}
export const namedEntity: Decoder<NamedEntity> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    name: string(o.name),
    slug: string(o.slug),
    is_active: boolean(o.is_active),
  };
};
export interface ManagedCategory extends NamedEntity {
  parent: string | null;
  description: string;
  position: number;
}
export const managedCategory: Decoder<ManagedCategory> = (value) => {
  const o = object(value);
  return {
    ...namedEntity(value),
    parent: nullable(string)(o.parent),
    description: string(o.description),
    position: integer(o.position),
  };
};
export interface ManagedCollection extends NamedEntity {
  description: string;
  product_count: number;
}
export const managedCollection: Decoder<ManagedCollection> = (value) => {
  const o = object(value);
  return {
    ...namedEntity(value),
    description: string(o.description),
    product_count: integer(o.product_count),
  };
};
export interface ManagedAttribute {
  id: string;
  code: string;
  name: string;
  value_type: string;
  scope: string;
  unit: string;
  is_filterable: boolean;
  options: { id: string; code: string; value: string; swatch: string; position: number }[];
}
export const managedAttribute: Decoder<ManagedAttribute> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    code: string(o.code),
    name: string(o.name),
    value_type: string(o.value_type),
    scope: string(o.scope),
    unit: string(o.unit),
    is_filterable: boolean(o.is_filterable),
    options: array(o.options, (row) => {
      const r = object(row);
      return {
        id: string(r.id),
        code: string(r.code),
        value: string(r.value),
        swatch: string(r.swatch),
        position: integer(r.position),
      };
    }),
  };
};
export interface ManagedPriceList {
  id: string;
  code: string;
  name: string;
  currency: string;
  is_default: boolean;
  is_active: boolean;
}
export const managedPriceList: Decoder<ManagedPriceList> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    code: string(o.code),
    name: string(o.name),
    currency: string(o.currency),
    is_default: boolean(o.is_default),
    is_active: boolean(o.is_active),
  };
};
export interface MediaAsset {
  id: string;
  kind: string;
  width: number | null;
  height: number | null;
  alt_text: string;
  url: string;
}
export const mediaAsset: Decoder<MediaAsset> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    kind: string(o.kind),
    width: nullable(integer)(o.width),
    height: nullable(integer)(o.height),
    alt_text: string(o.alt_text),
    url: mediaUrl(o.url),
  };
};

// --- Inventory management -------------------------------------------------------------------------

export interface StockLocation {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
  is_default: boolean;
  city: string;
}
export const stockLocation: Decoder<StockLocation> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    code: string(o.code),
    name: string(o.name),
    is_active: boolean(o.is_active),
    is_default: boolean(o.is_default),
    city: string(o.city),
  };
};
export interface StockBatch {
  id: string;
  variant: string;
  sku: string;
  code: string;
  manufactured_on: string | null;
  expires_on: string | null;
  received_on: string;
  supplier_reference: string;
  levels: { location: string; location_code: string; on_hand: string }[];
}
export const stockBatch: Decoder<StockBatch> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    variant: string(o.variant),
    sku: string(o.sku),
    code: string(o.code),
    manufactured_on: nullable(string)(o.manufactured_on),
    expires_on: nullable(string)(o.expires_on),
    received_on: string(o.received_on),
    supplier_reference: string(o.supplier_reference),
    levels: array(o.levels, (row) => {
      const r = object(row);
      return {
        location: string(r.location),
        location_code: string(r.location_code),
        on_hand: decimal(r.on_hand),
      };
    }),
  };
};
export interface SerialUnit {
  id: string;
  serial: string;
  variant: string;
  sku: string;
  status: string;
  location: string | null;
  updated_at: string;
}
export const serialUnit: Decoder<SerialUnit> = (value) => {
  const o = object(value);
  return {
    id: string(o.id),
    serial: string(o.serial),
    variant: string(o.variant),
    sku: string(o.sku),
    status: string(o.status),
    location: nullable(string)(o.location),
    updated_at: string(o.updated_at),
  };
};
export interface Reconciliation {
  consistent: boolean;
  discrepancies: {
    kind: string;
    variant_id: string;
    location_id: string;
    batch_id: string | null;
    expected: string;
    actual: string;
  }[];
}
export function reconciliation(value: unknown): Reconciliation {
  const o = object(value);
  return {
    consistent: boolean(o.consistent),
    discrepancies: array(o.discrepancies, (row) => {
      const r = object(row);
      return {
        kind: string(r.kind),
        variant_id: string(r.variant_id),
        location_id: string(r.location_id),
        batch_id: nullable(string)(r.batch_id),
        expected: decimal(r.expected),
        actual: decimal(r.actual),
      };
    }),
  };
}
