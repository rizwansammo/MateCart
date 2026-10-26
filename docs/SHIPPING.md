# Fulfillment, couriers, tracking and courier COD

Phase 09 adds physical fulfillment to the Phase 08 order/allocation foundation. Decision D-074 is
the canonical boundary. The implementation is shared by every preset and theme.

## Fulfillment lifecycle

An order has at most one shipment in this phase. The shipment contains its physical order items;
digital-only orders are rejected and mixed orders include only their physical items.

```text
ready -> picking -> packed -> ready_for_dispatch -> dispatched -> in_transit -> delivered
                                                      |             |
                                                      +-------------+-> delivery_failed
                                                                       |        |
                                                                       +--------+-> in_transit
                                                                       +--------+-> return_to_sender
```

`cancelled` is pre-dispatch only and is reached only through full Order cancellation, never through
the standalone shipment transition endpoint. The Order operation locks Order then Shipment,
refuses an active courier booking, releases eligible Order commitments exactly once, and records
both states in one transaction. `return_to_sender` is an operational shipping state, not the Phase
10 return/exchange workflow. Order and shipment status remain distinct.

Dispatch is the stock boundary. In one transaction it locks order then shipment, calls
`orders.services.fulfil_order_stock()` and only then records `dispatched`. That existing service
owns idempotent allocation deduction, FEFO unexpired batches, serial validation and inventory
ledger entries. Picking or packing never deducts stock. Two dispatches replay safely; cancellation
and dispatch cannot both win.

## Merchant operations

All endpoints require a staff session and the existing `couriers.manage` or `orders.manage`
permission. Financial assertions also require recent authentication.

- `GET /api/manage/fulfillment/shipments/`
- `GET /api/manage/fulfillment/shipments/<id>/`
- `POST /api/manage/fulfillment/orders/<order-id>/shipment/`
- `POST /api/manage/fulfillment/shipments/<id>/transition/`
- `POST /api/manage/fulfillment/shipments/<id>/courier-details/`
- `POST /api/manage/fulfillment/shipments/<id>/courier-booking/`
- `POST /api/manage/fulfillment/shipments/<id>/courier-booking/cancel/`
- `POST /api/manage/fulfillment/shipments/<id>/cod-collection/`
- `POST /api/manage/fulfillment/cod-records/<id>/remittance/`
- `POST /api/manage/fulfillment/orders/<order-id>/fraud-assessment/`
- `POST /api/manage/fulfillment/fraud-assessments/<id>/review/`

There is no Phase 11 merchant screen yet. These are production API operations, not a claim of a
completed dashboard workflow.

## Manual shipping and customer tracking

Manual shipping is always available. Staff record the actual courier display name and optional real
tracking number/HTTPS link, then use the controlled transitions. MateCart never generates a
tracking number or courier label. Each applied state creates an append-only `TrackingEvent`; failed
attempts receive an attempt number.

The existing secure order endpoint returns a public shipment view only after guest/customer
ownership succeeds. It includes the shipment reference, state, timestamps, courier display name,
real tracking fields and a reduced event timeline. Operational notes, callback digests, booking
references, staff identities, fraud evidence and COD records are not public. The order page renders
this view in English and Bangla.

## Courier adapter and callbacks

`MATECART_COURIER_PROVIDER` optionally names one adapter. It supplies:

- idempotent booking using the local booking id as the provider idempotency reference;
- idempotent cancellation;
- authenticated tracking callback decoding.

Provider calls run outside database locks. A timeout leaves a booking `pending` or `cancelling`,
which blocks dispatch until an idempotent retry resolves it; it never claims success. Booking
references and tracking fields are accepted only after a successful provider response.

`POST /api/couriers/callbacks/<provider>/tracking/` is the only anonymous fulfillment endpoint.
The configured adapter verifies authenticity before domain lookup. The callback log deduplicates
provider event ids, keeps a SHA-256 body digest instead of raw payload, rejects unknown references,
and records stale/illegal outcomes without changing shipment history. Callback insertion, locked
booking/shipment revalidation, tracking transition and outcome recording commit atomically. An
unexpected failure rolls back the event id and any transition so a provider retry can recover;
concurrent copies still apply at most one tracking event. A callback cannot perform the
staff-controlled dispatch transition or stock deduction.

No real adapter is configured. Production rejects `tests.*`; `FakeCourierProvider` exists only for
automated tests. Approving a provider still requires an adapter, secret configuration and real
sandbox verification.

## Courier COD reconciliation

One `CourierCodRecord` separates:

1. order amount due;
2. courier-collected cash;
3. courier fees and documented deductions;
4. expected merchant remittance;
5. actual merchant remittance;
6. the outstanding/disputed difference.

The database enforces `expected = collected - fees - deductions`. Delivery never means paid. Staff
must explicitly record attributable collection evidence after delivery. Only an exact collection of
the order total is sufficient to call Phase 08's idempotent `record_cod_collection()`; underpayment
or overpayment remains an unpaid discrepancy. Remittance is a later, separate assertion and becomes
`reconciled` only on an exact match, otherwise `disputed`. This is not a ledger, bank feed, payout
platform or accounting system.

## Fraud review

The initial rules are deliberately conservative and explainable for COD orders:

- at least three prior COD orders with the same eligible contact in seven days;
- at least two recent failed/returned shipments for that contact;
- at least two prior same-item COD orders for that contact in 24 hours.

Evidence stores only counts and windows. It does not expose other order ids, customer identities,
IP addresses, device fingerprints or protected attributes. Reviews record reviewer, time, decision
and reason in append-only history. A flag never auto-cancels, blacklists or blocks dispatch; staff
must use the normal authorized order workflow for any action.

## Deliberate limits

- one shipment per order; no partial packages or multi-carrier split;
- no real carrier, official label, route planning or delivery workforce management;
- no customer returns/exchanges or digital entitlement delivery (Phase 10);
- no broad merchant dashboard (Phase 11);
- no bank synchronization, accounting ledger, courier wallet or automated payout reconciliation.
