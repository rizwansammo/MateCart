/** Runtime validation for customer order history, support cases and digital purchases. */
import { array, boolean, number, object, string, type Decoder } from './auth';
import { decimal, nullable } from './catalog';

export interface Page<T> {
  count: number;
  page: number;
  page_size: number;
  results: T[];
}

export function page<T>(raw: unknown, decode: Decoder<T>): Page<T> {
  const o = object(raw);
  return {
    count: number(o.count),
    page: number(o.page),
    page_size: number(o.page_size),
    results: array(o.results, decode),
  };
}

export interface OrderSummary {
  id: string;
  number: string;
  status: string;
  payment_method: string;
  payment_status: string;
  currency: string;
  grand_total: string;
  placed_at: string;
}

export const orderSummary: Decoder<OrderSummary> = (raw) => {
  const o = object(raw);
  return {
    id: string(o.id),
    number: string(o.number),
    status: string(o.status),
    payment_method: string(o.payment_method),
    payment_status: string(o.payment_status),
    currency: string(o.currency),
    grand_total: decimal(o.grand_total),
    placed_at: string(o.placed_at),
  };
};

export interface SupportMessage {
  id: string;
  author: string;
  body: string;
  created_at: string;
}

export const supportMessage: Decoder<SupportMessage> = (raw) => {
  const o = object(raw);
  return {
    id: string(o.id),
    author: string(o.author),
    body: string(o.body),
    created_at: string(o.created_at),
  };
};

export interface AfterSalesCase {
  id: string;
  kind: string;
  status: string;
  order_item: string | null;
  quantity: string;
  subject: string;
  reason: string;
  decision_reason: string;
  refund: string | null;
  replacement_order: string | null;
  created_at: string;
  updated_at: string;
  messages: SupportMessage[];
}

export const afterSalesCase: Decoder<AfterSalesCase> = (raw) => {
  const o = object(raw);
  return {
    id: string(o.id),
    kind: string(o.kind),
    status: string(o.status),
    order_item: nullable(string)(o.order_item),
    quantity: decimal(o.quantity),
    subject: string(o.subject),
    reason: string(o.reason),
    decision_reason: string(o.decision_reason),
    refund: nullable(string)(o.refund),
    replacement_order: nullable(string)(o.replacement_order),
    created_at: string(o.created_at),
    updated_at: string(o.updated_at),
    messages: array(o.messages, supportMessage),
  };
};

export interface PurchaseReview {
  id: string;
  order_item: string;
  rating: number;
  body: string;
  status: string;
  decision_reason: string;
  created_at: string;
}

export const purchaseReview: Decoder<PurchaseReview> = (raw) => {
  const o = object(raw);
  return {
    id: string(o.id),
    order_item: string(o.order_item),
    rating: number(o.rating),
    body: string(o.body),
    status: string(o.status),
    decision_reason: string(o.decision_reason),
    created_at: string(o.created_at),
  };
};

export interface BuyerLibraryItem {
  order_item: string;
  order_number: string;
  product_name: string;
  variant_title: string;
  purchased_at: string;
  state: string;
  entitlement_id: string | null;
  remaining_downloads: number;
}

export const buyerLibraryItem: Decoder<BuyerLibraryItem> = (raw) => {
  const o = object(raw);
  return {
    order_item: string(o.order_item),
    order_number: string(o.order_number),
    product_name: string(o.product_name),
    variant_title: string(o.variant_title),
    purchased_at: string(o.purchased_at),
    state: string(o.state),
    entitlement_id: nullable(string)(o.entitlement_id),
    remaining_downloads: number(o.remaining_downloads),
  };
};

export interface SavedAddress {
  id: string;
  label: string;
  recipient_name: string;
  phone: string;
  address_line_1: string;
  address_line_2: string;
  city: string;
  region: string;
  postal_code: string;
  country: string;
  is_default: boolean;
}

export const savedAddress: Decoder<SavedAddress> = (raw) => {
  const o = object(raw);
  return {
    id: string(o.id),
    label: string(o.label),
    recipient_name: string(o.recipient_name),
    phone: string(o.phone),
    address_line_1: string(o.address_line_1),
    address_line_2: string(o.address_line_2),
    city: string(o.city),
    region: string(o.region),
    postal_code: string(o.postal_code),
    country: string(o.country),
    is_default: boolean(o.is_default),
  };
};
