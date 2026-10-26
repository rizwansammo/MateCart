/** Runtime-validated order, payment and invoice responses (Phase 08). Money stays a string. */
import { array, boolean, object, string, type Decoder } from './auth';
import { decimal, nullable } from './catalog';

function oneOf<T extends string>(values: readonly T[]): Decoder<T> {
  return (value) => {
    const text = string(value);
    if (!(values as readonly string[]).includes(text)) throw new Error('Invalid response');
    return text as T;
  };
}

export const ORDER_STATUSES = [
  'pending_payment',
  'placed',
  'confirmed',
  'processing',
  'cancelled',
] as const;
export type OrderStatus = (typeof ORDER_STATUSES)[number];

export const PAYMENT_STATUSES = [
  'unpaid',
  'paid',
  'partially_refunded',
  'refunded',
  'failed',
] as const;
export type OrderPaymentStatus = (typeof PAYMENT_STATUSES)[number];

export const PAYMENT_METHODS = ['cod', 'online'] as const;
export type PaymentMethod = (typeof PAYMENT_METHODS)[number];

export const paymentMethod: Decoder<PaymentMethod> = oneOf(PAYMENT_METHODS);

export interface OrderItem {
  id: string;
  product_name: string;
  variant_title: string;
  sku: string;
  kind: 'physical' | 'digital';
  quantity: string;
  unit_price: string;
  line_total: string;
}

export interface Order {
  id: string;
  number: string;
  status: OrderStatus;
  payment_method: PaymentMethod;
  payment_status: OrderPaymentStatus;
  payment_due_at: string | null;
  currency: string;
  item_subtotal: string;
  discount_total: string;
  shipping_total: string;
  tax_total: string;
  grand_total: string;
  amount_paid: string;
  amount_refunded: string;
  contact_email: string;
  contact_phone: string;
  requires_shipping: boolean;
  recipient_name: string;
  address_line_1: string;
  address_line_2: string;
  city: string;
  region: string;
  postal_code: string;
  country: string;
  delivery_notes: string;
  shipping_method_name: string;
  delivery_slot_label: string;
  delivery_slot_starts_at: string | null;
  delivery_slot_ends_at: string | null;
  coupon_code: string;
  placed_at: string;
  cancelled_at: string | null;
  cancel_reason: string;
  items: OrderItem[];
  invoice_number: string | null;
  can_cancel: boolean;
  can_pay: boolean;
}

function orderItem(value: unknown): OrderItem {
  const o = object(value);
  return {
    id: string(o.id),
    product_name: string(o.product_name),
    variant_title: string(o.variant_title),
    sku: string(o.sku),
    kind: oneOf(['physical', 'digital'] as const)(o.kind),
    quantity: decimal(o.quantity),
    unit_price: decimal(o.unit_price),
    line_total: decimal(o.line_total),
  };
}

export function order(value: unknown): Order {
  const o = object(value);
  return {
    id: string(o.id),
    number: string(o.number),
    status: oneOf(ORDER_STATUSES)(o.status),
    payment_method: paymentMethod(o.payment_method),
    payment_status: oneOf(PAYMENT_STATUSES)(o.payment_status),
    payment_due_at: nullable(string)(o.payment_due_at),
    currency: string(o.currency),
    item_subtotal: decimal(o.item_subtotal),
    discount_total: decimal(o.discount_total),
    shipping_total: decimal(o.shipping_total),
    tax_total: decimal(o.tax_total),
    grand_total: decimal(o.grand_total),
    amount_paid: decimal(o.amount_paid),
    amount_refunded: decimal(o.amount_refunded),
    contact_email: string(o.contact_email),
    contact_phone: string(o.contact_phone),
    requires_shipping: boolean(o.requires_shipping),
    recipient_name: string(o.recipient_name),
    address_line_1: string(o.address_line_1),
    address_line_2: string(o.address_line_2),
    city: string(o.city),
    region: string(o.region),
    postal_code: string(o.postal_code),
    country: string(o.country),
    delivery_notes: string(o.delivery_notes),
    shipping_method_name: string(o.shipping_method_name),
    delivery_slot_label: string(o.delivery_slot_label),
    delivery_slot_starts_at: nullable(string)(o.delivery_slot_starts_at),
    delivery_slot_ends_at: nullable(string)(o.delivery_slot_ends_at),
    coupon_code: string(o.coupon_code),
    placed_at: string(o.placed_at),
    cancelled_at: nullable(string)(o.cancelled_at),
    cancel_reason: string(o.cancel_reason),
    items: array(o.items, orderItem),
    invoice_number: nullable(string)(o.invoice_number),
    can_cancel: boolean(o.can_cancel),
    can_pay: boolean(o.can_pay),
  };
}

export interface PaymentStart {
  redirect_url: string;
}

export function paymentStart(value: unknown): PaymentStart {
  const url = string(object(value).redirect_url);
  if (!url.startsWith('https://')) throw new Error('Invalid response');
  return { redirect_url: url };
}

export interface InvoiceParty {
  [key: string]: string;
}

export interface Invoice {
  number: string;
  issued_at: string;
  order_number: string;
  order_placed_at: string;
  currency: string;
  item_subtotal: string;
  discount_total: string;
  shipping_total: string;
  tax_total: string;
  grand_total: string;
  payment_method: PaymentMethod;
  payment_status: OrderPaymentStatus;
  amount_paid: string;
  amount_refunded: string;
  order_cancelled: boolean;
  seller: InvoiceParty;
  buyer: InvoiceParty;
  items: OrderItem[];
}

function party(value: unknown): InvoiceParty {
  const o = object(value);
  return Object.fromEntries(Object.entries(o).map(([key, text]) => [key, string(text)]));
}

export function invoice(value: unknown): Invoice {
  const o = object(value);
  return {
    number: string(o.number),
    issued_at: string(o.issued_at),
    order_number: string(o.order_number),
    order_placed_at: string(o.order_placed_at),
    currency: string(o.currency),
    item_subtotal: decimal(o.item_subtotal),
    discount_total: decimal(o.discount_total),
    shipping_total: decimal(o.shipping_total),
    tax_total: decimal(o.tax_total),
    grand_total: decimal(o.grand_total),
    payment_method: paymentMethod(o.payment_method),
    payment_status: oneOf(PAYMENT_STATUSES)(o.payment_status),
    amount_paid: decimal(o.amount_paid),
    amount_refunded: decimal(o.amount_refunded),
    order_cancelled: boolean(o.order_cancelled),
    seller: party(o.seller),
    buyer: party(o.buyer),
    items: array(o.items, orderItem),
  };
}
