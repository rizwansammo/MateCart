'use client';

import {
  cart as decodeCart,
  checkout as decodeCheckout,
  invoice as decodeInvoice,
  order as decodeOrder,
  paymentStart as decodePaymentStart,
  type Cart,
  type Checkout,
  type Invoice,
  type Order,
  type PaymentMethod,
  type PaymentStart,
} from '@matecart/shared-contracts';

import { authApi } from './auth-client';

export const CART_UPDATED_EVENT = 'matecart:cart-updated';

function changed(cart: Cart): Cart {
  window.dispatchEvent(new CustomEvent<Cart>(CART_UPDATED_EVENT, { detail: cart }));
  return cart;
}

export async function getCart(): Promise<Cart> {
  return decodeCart(await authApi.get<unknown>('/api/cart/'));
}

export async function addToCart(variant: string, quantity: string): Promise<Cart> {
  return changed(
    decodeCart(await authApi.post<unknown>('/api/cart/items/', { variant, quantity })),
  );
}

export async function updateCartItem(item: string, quantity: string): Promise<Cart> {
  return changed(
    decodeCart(await authApi.patch<unknown>(`/api/cart/items/${item}/`, { quantity })),
  );
}

export async function removeCartItem(item: string): Promise<Cart> {
  return changed(decodeCart(await authApi.delete<unknown>(`/api/cart/items/${item}/`)));
}

export async function createCheckout(): Promise<Checkout> {
  return decodeCheckout(await authApi.post<unknown>('/api/checkout/', {}));
}

export async function getCheckout(id: string): Promise<Checkout> {
  return decodeCheckout(await authApi.get<unknown>(`/api/checkout/${id}/`));
}

export async function updateCheckout(
  id: string,
  values: Record<string, string | null>,
): Promise<Checkout> {
  return decodeCheckout(await authApi.patch<unknown>(`/api/checkout/${id}/`, values));
}

export async function reviewCheckout(id: string, idempotencyKey: string): Promise<Checkout> {
  return decodeCheckout(
    await authApi.post<unknown>(`/api/checkout/${id}/review/`, {
      idempotency_key: idempotencyKey,
    }),
  );
}

export async function releaseCheckout(id: string): Promise<Checkout> {
  return decodeCheckout(await authApi.post<unknown>(`/api/checkout/${id}/release/`, {}));
}

export async function placeOrder(
  checkoutId: string,
  paymentMethod: PaymentMethod,
  idempotencyKey: string,
): Promise<Order> {
  return decodeOrder(
    await authApi.post<unknown>(`/api/checkout/${checkoutId}/order/`, {
      payment_method: paymentMethod,
      idempotency_key: idempotencyKey,
    }),
  );
}

export async function getOrder(id: string): Promise<Order> {
  return decodeOrder(await authApi.get<unknown>(`/api/orders/${id}/`));
}

export async function cancelOrder(id: string): Promise<Order> {
  return decodeOrder(await authApi.post<unknown>(`/api/orders/${id}/cancel/`, {}));
}

export async function startPayment(id: string): Promise<PaymentStart> {
  return decodePaymentStart(await authApi.post<unknown>(`/api/orders/${id}/payment/`, {}));
}

export async function getInvoice(id: string): Promise<Invoice> {
  return decodeInvoice(await authApi.get<unknown>(`/api/orders/${id}/invoice/`));
}
