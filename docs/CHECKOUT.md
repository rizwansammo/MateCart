# Cart and checkout

Phase 07 provides one server-authoritative cart and checkout flow for every preset and compatible
theme. Phase 07.1 closes the final-review coupon, duplicate cart hold and delivery-slot capacity
gaps. Phase 08 will create orders and payments; a reviewed checkout is deliberately not an order.

## Customer journey and APIs

The product page adds the selected variant to the cart. `/cart` supports quantity updates, removal
and checkout entry. `/checkout/<id>` collects contact and, for physical or mixed carts, delivery
details; it then offers eligible shipping methods and Grocery slots, shows server totals and reviews
the checkout into a temporary reservation.

Browser endpoints are:

- `GET /api/cart/`; `POST /api/cart/items/`; `PATCH`/`DELETE /api/cart/items/<id>/`.
- `POST /api/checkout/`; `GET`/`PATCH /api/checkout/<id>/`.
- `POST /api/checkout/<id>/review/` with an 8–100 character idempotency key.
- `POST /api/checkout/<id>/release/` to cancel a temporary hold.
- Authenticated customer address CRUD under `/api/account/addresses/`.

All mutation requests use the existing `mc_csrf` cookie/header flow. Guests receive an HttpOnly
`mc_cart` credential; URLs and local storage never contain it. API amounts are Decimal strings.

## Store configuration

`ShippingMethod`, `TaxRate`, `Coupon` and `DeliverySlot` are tenant-owned internal configuration.
There is no carrier, tax-provider or payment integration in this phase. A first physical checkout
creates a zero-cost `standard` delivery method as a conservative bootstrap only when the store has
no shipping methods. Once any method is configured, that configuration is authoritative: an
ineligible country, currency or subtotal is not bypassed by creating a free global fallback.
`delivery_slots` applies only to Grocery; `preorders` applies to Books and General. Feature
enablement comes from the module registry, never the theme. Digital-only checkouts never require a
shipping method or delivery slot.

## Final review integrity

Review reloads and locks a selected coupon and checks its current state, schedule, usage limit,
minimum subtotal, currency, discount type and value. Totals are recalculated from that authoritative
row before inventory is reserved. Coupon redemption is not recorded at review.

Creation and review serialize on the cart row. An unexpired reviewed checkout is reused by creation,
and another draft for that cart cannot be reviewed until the hold is released or expires. Same-key
replay of the reviewed checkout returns the existing result and does not reserve stock or slot
capacity again.

For a physical Grocery checkout with `delivery_slots` enabled, the selected slot row is locked during
review. Capacity is its committed `bookings` plus reviewed checkout sessions whose 15-minute hold has
not expired. A review therefore holds one place without mutating the confirmed-booking counter.
Release or expiry removes that session from the active count. Other presets and digital-only
checkouts do not participate, and an entirely unconfigured slot pool preserves non-slot checkout.

## Expiry and Phase 08 handoff

Active carts expire after 30 days and draft checkout sessions after 30 minutes. Successful review
revalidates price, publication and sellable inventory, then holds tracked stock for 15 minutes via
the inventory ledger. The tenant-aware `checkout.expire` task releases an abandoned hold and is
safe to retry. Phase 08 must accept only a still-valid reviewed session, revalidate it, create the
order, perform final allocation and decide when a coupon is redeemed. When an order uses a delivery
slot, Phase 08 must lock that slot and the reviewed checkout, increment `bookings` exactly once and
mark the checkout completed in the same transaction so the temporary hold becomes a confirmed
booking without being double-counted.
