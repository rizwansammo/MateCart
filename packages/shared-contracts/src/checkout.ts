/** Runtime-validated cart and checkout responses. Decimal values remain strings. */
import { array, boolean, number, object, string, type Decoder } from './auth';
import { decimal, nullable } from './catalog';
import { paymentMethod, type PaymentMethod } from './orders';

function oneOf<T extends string>(values: readonly T[]): Decoder<T> {
  return (value) => {
    const text = string(value);
    if (!(values as readonly string[]).includes(text)) throw new Error('Invalid response');
    return text as T;
  };
}

export type CartIssue =
  '' | 'unavailable' | 'price_unavailable' | 'insufficient_stock' | 'currency_mismatch';

export interface CartLine {
  id: string;
  variant: string;
  product_slug: string;
  product_name: string;
  variant_title: string;
  sku: string;
  kind: 'physical' | 'digital';
  quantity: string;
  unit_price: string | null;
  currency: string | null;
  subtotal: string | null;
  available_quantity: string | null;
  issue: CartIssue;
  /** Phase 12: the list price and promotion behind ``unit_price`` ('' when none). */
  regular_unit_price: string | null;
  promotion_kind: string;
  promotion_name: string;
}

export interface Cart {
  id: string;
  lines: CartLine[];
  subtotal: string;
  currency: string;
  item_count: number;
  valid: boolean;
}

function cartLine(value: unknown): CartLine {
  const o = object(value);
  return {
    id: string(o.id),
    variant: string(o.variant),
    product_slug: string(o.product_slug),
    product_name: string(o.product_name),
    variant_title: string(o.variant_title),
    sku: string(o.sku),
    kind: oneOf(['physical', 'digital'] as const)(o.kind),
    quantity: decimal(o.quantity),
    unit_price: nullable(decimal)(o.unit_price),
    currency: nullable(string)(o.currency),
    subtotal: nullable(decimal)(o.subtotal),
    available_quantity: nullable(decimal)(o.available_quantity),
    issue: oneOf([
      '',
      'unavailable',
      'price_unavailable',
      'insufficient_stock',
      'currency_mismatch',
    ] as const)(o.issue),
    regular_unit_price: nullable(decimal)(o.regular_unit_price),
    promotion_kind: string(o.promotion_kind),
    promotion_name: string(o.promotion_name),
  };
}

export function cart(value: unknown): Cart {
  const o = object(value);
  return {
    id: string(o.id),
    lines: array(o.lines, cartLine),
    subtotal: decimal(o.subtotal),
    currency: string(o.currency),
    item_count: number(o.item_count),
    valid: boolean(o.valid),
  };
}

export interface CheckoutLine {
  variant: string;
  product_name: string;
  variant_title: string;
  sku: string;
  kind: 'physical' | 'digital';
  quantity: string;
  unit_price: string;
  line_subtotal: string;
  regular_unit_price: string | null;
  promotion_kind: string;
  promotion_name: string;
  /** False for lines at a promotional price: coupons never stack on them (D-081). */
  coupon_eligible: boolean;
}

/** Points the signed-in customer could use on this checkout (D-082). */
export interface LoyaltyCheckout {
  balance: number;
  point_value: string;
  min_redeem_points: number;
  max_points: number;
}

export interface ShippingMethod {
  id: string;
  code: string;
  name: string;
  description: string;
  amount: string;
  currency: string;
  free_above: string | null;
}

export interface DeliverySlot {
  id: string;
  label: string;
  starts_at: string;
  ends_at: string;
}

export type CheckoutStatus = 'draft' | 'reviewed' | 'released' | 'expired' | 'completed';

export interface Checkout {
  id: string;
  status: CheckoutStatus;
  expires_at: string;
  contact_email: string;
  contact_phone: string;
  recipient_name: string;
  address_line_1: string;
  address_line_2: string;
  city: string;
  region: string;
  postal_code: string;
  country: string;
  delivery_notes: string;
  coupon_code: string;
  requires_shipping: boolean;
  currency: string;
  item_subtotal: string;
  discount_total: string;
  shipping_total: string;
  tax_total: string;
  grand_total: string;
  loyalty_points: number;
  loyalty_discount: string;
  loyalty: LoyaltyCheckout | null;
  lines: CheckoutLine[];
  shipping_methods: ShippingMethod[];
  delivery_slots: DeliverySlot[];
  selected_shipping_method: string | null;
  selected_delivery_slot: string | null;
  payment_methods: PaymentMethod[];
  order_creation_available: boolean;
  order: string | null;
}

function checkoutLine(value: unknown): CheckoutLine {
  const o = object(value);
  return {
    variant: string(o.variant),
    product_name: string(o.product_name),
    variant_title: string(o.variant_title),
    sku: string(o.sku),
    kind: oneOf(['physical', 'digital'] as const)(o.kind),
    quantity: decimal(o.quantity),
    unit_price: decimal(o.unit_price),
    line_subtotal: decimal(o.line_subtotal),
    regular_unit_price: nullable(decimal)(o.regular_unit_price),
    promotion_kind: string(o.promotion_kind),
    promotion_name: string(o.promotion_name),
    coupon_eligible: boolean(o.coupon_eligible),
  };
}

function loyaltyCheckout(value: unknown): LoyaltyCheckout {
  const o = object(value);
  return {
    balance: number(o.balance),
    point_value: decimal(o.point_value),
    min_redeem_points: number(o.min_redeem_points),
    max_points: number(o.max_points),
  };
}

function shippingMethod(value: unknown): ShippingMethod {
  const o = object(value);
  return {
    id: string(o.id),
    code: string(o.code),
    name: string(o.name),
    description: string(o.description),
    amount: decimal(o.amount),
    currency: string(o.currency),
    free_above: nullable(decimal)(o.free_above),
  };
}

function deliverySlot(value: unknown): DeliverySlot {
  const o = object(value);
  return {
    id: string(o.id),
    label: string(o.label),
    starts_at: string(o.starts_at),
    ends_at: string(o.ends_at),
  };
}

export function checkout(value: unknown): Checkout {
  const o = object(value);
  return {
    id: string(o.id),
    status: oneOf(['draft', 'reviewed', 'released', 'expired', 'completed'] as const)(o.status),
    expires_at: string(o.expires_at),
    contact_email: string(o.contact_email),
    contact_phone: string(o.contact_phone),
    recipient_name: string(o.recipient_name),
    address_line_1: string(o.address_line_1),
    address_line_2: string(o.address_line_2),
    city: string(o.city),
    region: string(o.region),
    postal_code: string(o.postal_code),
    country: string(o.country),
    delivery_notes: string(o.delivery_notes),
    coupon_code: string(o.coupon_code),
    requires_shipping: boolean(o.requires_shipping),
    currency: string(o.currency),
    item_subtotal: decimal(o.item_subtotal),
    discount_total: decimal(o.discount_total),
    shipping_total: decimal(o.shipping_total),
    tax_total: decimal(o.tax_total),
    grand_total: decimal(o.grand_total),
    loyalty_points: number(o.loyalty_points),
    loyalty_discount: decimal(o.loyalty_discount),
    loyalty: nullable(loyaltyCheckout)(o.loyalty),
    lines: array(o.lines, checkoutLine),
    shipping_methods: array(o.shipping_methods, shippingMethod),
    delivery_slots: array(o.delivery_slots, deliverySlot),
    selected_shipping_method: nullable(string)(o.selected_shipping_method),
    selected_delivery_slot: nullable(string)(o.selected_delivery_slot),
    payment_methods: array(o.payment_methods, paymentMethod),
    order_creation_available: boolean(o.order_creation_available),
    order: nullable(string)(o.order),
  };
}
