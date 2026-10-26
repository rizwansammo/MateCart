'use client';

import {
  cart as decodeCart,
  checkout as decodeCheckout,
  type Cart,
  type Checkout,
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
