# Orders, payments and invoices

Phase 08 turns a reviewed checkout into an order and handles payment, refunds and invoices. This
is one engine for every preset and theme. Decisions: D-071 (orders and stock), D-072 (payments)
and D-073 (refunds and invoices).

## Customer journey

1. `/checkout/<id>` reviews the checkout and reserves stock for 15 minutes (Phase 07).
2. The reviewed checkout lists the payment methods that can really complete it. The customer picks
   one and presses **Place order**, which calls `POST /api/checkout/<id>/order/` with
   `payment_method` and an `idempotency_key`. A retry with the same key and the same payment
   method returns the same order; the same key with a different method is `409
idempotency_conflict`, and another key on a converted checkout is `409 checkout_completed`.
   Placement rechecks the reviewed delivery method with the checkout's own eligibility rules
   (active, currency, country, minimum subtotal). If it no longer qualifies, placement answers `409
shipping_unavailable`, creates nothing and releases the hold so the customer reviews again; no
   other method is substituted and the reviewed totals are never changed (Phase 08.1).
3. `/orders/<id>` shows the order number, status, payment status, delivery details, items and
   totals. It can cancel an unpaid order, start or retry an online payment and open the invoice.
4. `/orders/<id>/invoice` is a printable invoice (browser print or save as PDF).

Guests receive an HttpOnly `mc_order` cookie for their latest order. Signed-in customers see only
their own orders. Knowing an order number or id never grants access.

## Order states

| Status            | Meaning                                        | Next                                |
| ----------------- | ---------------------------------------------- | ----------------------------------- |
| `pending_payment` | Online order waiting for a verified payment    | `confirmed` (payment), `cancelled`  |
| `placed`          | Cash-on-delivery order waiting for the store   | `confirmed` (merchant), `cancelled` |
| `confirmed`       | Accepted                                       | `processing`, `cancelled`           |
| `processing`      | Being prepared                                 | `cancelled` (before fulfilment)     |
| `cancelled`       | Final; stock, coupon and booking were returned | none                                |

The order's payment status is separate: `unpaid`, `paid`, `partially_refunded`, `refunded` or
`failed` (the online payment window expired). Each payment attempt (`PaymentIntent`), money
movement (`PaymentTransaction`) and refund has its own status. Every order transition appends an
`OrderStatusEvent`; the table is append-only in PostgreSQL.

Shipping, delivery and courier states are added in Phase 09.

## Stock

- **Checkout hold:** review records a `reserved` movement for tracked physical stock.
- **Order reservation:** placement turns each checkout reservation into an `OrderAllocation`
  without a new movement. Checkout expiry and release ignore a completed checkout.
- **Deduction:** `orders.services.fulfil_order_stock()` is the Phase 09 dispatch handoff. It
  deducts each allocation once. Batch stock leaves from unexpired lots first-expired-first-out at
  that moment; serial-tracked stock needs the shipped serial numbers.

At placement, a batch-tracked hold must still be covered by unexpired lots after earlier orders. A
lot that expired since review is replaced by any other unexpired lot at that location; when none
is left, placement answers `409 stock_expired`, releases the hold and creates no order. Digital,
untracked and pre-order lines hold no stock and write no movements.

Cancelling releases every open allocation once, voids the coupon redemption and returns the
delivery booking if its slot has not started.

## Payments

**Cash on delivery** is implemented and on by default for every preset. It is offered only when
every item is physical. The order stays `unpaid` until the merchant records the collection
(`POST /api/manage/orders/<id>/cod-collection/`, confirmed orders only, once).

**Online payment** works through one adapter configured with `MATECART_PAYMENT_PROVIDER` (dotted
path to a `commerce.orders.providers.PaymentProvider`). **No gateway has been selected or
configured**, so online payment is not offered, the `online_payments` module cannot be switched
on, and EMI stays unavailable. Production refuses test fakes. Adding a gateway means writing its
adapter (initiate, authenticate callback, refund), configuring its credentials as secrets and
testing it against the gateway's real sandbox. That work needs the owner's choice of gateway.

With a provider configured, an online order waits 30 minutes for payment. `POST /api/orders/<id>/payment/`
returns the provider's `https` redirect. The provider then calls
`POST /api/payments/callbacks/<provider>/`. This endpoint is anonymous, and the adapter
authenticates every request. A callback changes money only when its reference, amount and currency
match the payment attempt. Duplicate event ids and duplicate transaction references have no effect.
Unpaid online orders are cancelled when the window passes. A payment that arrives late is recorded
as paid on the cancelled order so the merchant can refund it.

Every verified provider payment is recorded as received money, including a late payment or a
second payment for the same order. Overpayment reconciliation (detecting and returning money taken
twice) is not automated; it must be verified against the chosen gateway's behaviour before online
payment is activated in production.

**Zero-total orders are not supported.** No payment method is offered when the grand total is
zero (a free product or a 100% discount), so such a checkout cannot be placed. Supporting free
orders needs a separate business decision.

## Refunds

`POST /api/manage/orders/<id>/refunds/` (`orders.manage` and a sign-in or password confirmation
within 10 minutes) with `amount`, `reason` and `idempotency_key`. A refund never exceeds the
verified amount still refundable on a payment. Provider refunds stay `pending` until the provider
confirms them, and a timeout can be retried with the same key. Cash refunds are recorded by the
merchant as `manual`. Failed refunds are never counted.

## Invoices

Each order gets one invoice (`INV-000001`, numbered per store) in the transaction that places
it. The invoice freezes the store's name, legal name and contact details, the buyer's details and
the totals. Its lines are the order's immutable items. Payment status and amounts come from
verified records, so an unpaid invoice shows an amount due and never claims payment was received.
Tax IDs, registration numbers, signatures, seals, QR codes and layout customisation are not
included. Invoices do not follow the storefront theme.

## Merchant API

Phase 11 builds the order dashboard. Phase 08 provides the following APIs under
`/api/manage/orders/` (`orders.manage`):

- list
- detail, with history and refunds
- `transition/`: `confirmed`, `processing` or `cancelled`, with an optional reason code
- `cod-collection/`
- `refunds/`
