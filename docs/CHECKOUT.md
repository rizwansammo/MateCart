# Cart and checkout

Phase 07 provides one server-authoritative cart and checkout flow for every preset and compatible
theme. Phase 08 will create orders and payments; a reviewed checkout is deliberately not an order.

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
creates a zero-cost `standard` delivery method as a conservative bootstrap; stores may replace it
with appropriate configuration. `delivery_slots` applies only to Grocery; `preorders` applies to
Books and General. Feature enablement comes from the module registry, never the theme.

## Expiry and Phase 08 handoff

Active carts expire after 30 days and draft checkout sessions after 30 minutes. Successful review
revalidates price, publication and sellable inventory, then holds tracked stock for 15 minutes via
the inventory ledger. The tenant-aware `checkout.expire` task releases an abandoned hold and is
safe to retry. Phase 08 must accept only a still-valid reviewed session, revalidate it, create the
order, perform final allocation and decide when a coupon is redeemed.
