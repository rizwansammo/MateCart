# Progress and handoff

Updated: 2026-10-27 (Asia/Dhaka).

**Phase 10 — Customer Accounts & After-sales is implemented and CI-verified on
feature branch `feature/phase-10-customer-after-sales` (PR #1).** The last
code commit `c604313fd670d0cb175701166a511abe60158900` passed GitHub Actions
run 40417354396: 4/4 jobs successful, 1,080 automated tests passed,
0 failures. Phases 01–09 and their accepted corrective subphases remain closed.
Phase 11 must not start before Phase 10 owner acceptance.

## Phase 10 implementation and verification

- Customer account UI extends the previously accepted authentication flows
  with paginated, customer-owned order history and saved delivery addresses.
- Purchase-verified product reviews and authenticated merchant moderation.
- Tenant-owned return, exchange and warranty cases with quantity guards,
  idempotency, restricted state transitions, staff decisions and audit events.
  Return resolution requires a succeeded refund bound to the original order.
  Exchange resolution requires a separately delivered matching replacement
  order for the same customer; neither process bypasses existing order,
  payment, inventory or courier services.
- Customer support request threads, customer and merchant replies, and
  ownership checks.
- Payment-gated digital library: immutable order-item entitlements,
  private PDF downloads, quota tracking and revocation after refunds.
  Public catalogue media never serves an asset bound to a digital entitlement.
- Module registry activated for the implemented Phase 10 functionality,
  with the existing dedicated-tenant and preset rules.
- Three new aftersales schema migrations. Django migration drift check reported
  no missing changes. Committed OpenAPI schema and runtime TypeScript contracts
  updated for the new endpoints.
- Tests on PostgreSQL and Redis in CI: 795 commerce Python, 57 control Python,
  51 shared contracts, 76 shared UI, 96 storefront, and 5 Node tests.
  Frontend lint, formatting, types, unit tests, production builds, backend
  Ruff, mypy, Django system checks and Compose validation all passed.
- Temporary verification workflow removed; normal `CI` is the only
  active pull request quality gate.

## Remaining acceptance and external limitations

- The owner has not yet performed interactive localhost review of the
  customer and merchant interfaces. GitHub CI does not constitute
  visual acceptance or a live production deployment.
- Digital files are PDF only, matching the currently validated media format.
  EPUB and other document formats require additional safe upload validators.
- No real payment gateway, courier, Google, SMS or external SMTP provider
  credentials were configured or used. The existing provider gates remain.
- Manual returns/refunds and replacement-order handling use separately
  authorized merchant operations. Phase 10 does not create a second stock
  or payment engine.
- No unrelated repositories, production services or Docker environments
  were modified through this ChatGPT GitHub workflow.

Next step: complete code review, merge the verified PR if all checks still
pass, then have the owner pull `main` and smoke-test the account, reviews,
returns, support and buyer library. Record any visual or real-provider
acceptance findings without starting Phase 11 prematurely.

---

## Accepted baseline and boundaries

| Item            | Recorded state                                                                                                                                                                                                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 01        | Accepted: `8f3e34b2a2930a08084902d0d5beb9789d6eb4ca`; owner subsequently pushed and confirmed CI 4/4. Its earlier unrun-CI report was historically correct.                                                                                               |
| Phase 02        | Accepted: `72f277e154dab40c586a48dcf5d3852e691993fc`; CI 4/4, 179 tests.                                                                                                                                                                                  |
| Correction 02.1 | Accepted: `ba513b5927cdfcadd7e8b98dcb08c71d834146f8`, run 40211394370, 4/4, 245 tests; forged-instance writes/deletes, audit sanitation, UUID policy and secret rotation.                                                                                 |
| Correction 02.2 | Accepted and Phase 02 closed: `aceae6fa4ed6cc186ba6a77bdd1869e818f0dceb`, run 40213680998, 4/4, 260 tests; active-context refresh and scoped base/relation managers, state-only migrations.                                                               |
| Phase 03        | Implemented: `f2c18d0b533d1bf94b486e452c75cb2f4db63d89`; run 40230926210, 4/4, 449 automated tests. Independent review requested three 03.1 corrections.                                                                                                  |
| Correction 03.1 | Accepted and Phase 03 closed (owner decision 2026-10-26): `5d6152ab98a0d01993845173b92370e75cffa936`, run 40237897132, 4/4 jobs, 463 tests (370 commerce, 57 control, 17 contracts, 16 UI, 3 Node); 12 local live smoke checks. Findings closed in scope. |
| Phase 04        | Implemented and pushed (GitHub-verified): `1bd61be0a49278e25cb8d201a809e171d32eaf38`, run 40246230098, SUCCESS 4/4 jobs, 701 tests (596 commerce, 57 control, 29 contracts, 16 UI, 3 Node). Post-push review requested the 04.1 corrections.              |
| Correction 04.1 | Accepted and Phase 04 closed: `8290fe424ea7729d46c392e2ab974fa646f2fcf6`, run 40249765179, 4/4 jobs, 735 tests (630 commerce, 57 control, 29 contracts, 16 UI, 3 Node).                                                                                   |
| Phase 05        | Implemented and pushed (GitHub-verified): `e3810cb465a771cc9b18437290f67aba1bbd7f02`, run 40297666311, SUCCESS 4/4 jobs, 807 tests (667 commerce, 57 control, 34 contracts, 46 UI, 3 Node). Review requested the 05.1 corrections.                        |
| Correction 05.1 | Accepted and Phase 05 closed: `613cd49edee5c16b31e3beb34d0b84b74f6dce72`, run 40301529264, 4/4 jobs, 827 tests (678 commerce, 57 control, 34 contracts, 55 UI, 3 Node).                                                                                   |
| Phase 06        | Implemented and pushed (GitHub-verified): `46c2e1af3078a46e1531c6783304f3430dacfbf0`, run 40311886344, SUCCESS 4/4 jobs, 894 tests (679 commerce, 57 control, 39 contracts, 55 UI, 61 web, 3 Node). Review requested the 06.1 corrections.                |
| Correction 06.1 | Accepted and Phase 06 closed: `df0490f65a80f7b0a883bdfb9278808302d21fa2`, run 40315255063, 4/4 jobs, 913 tests (682 commerce, 57 control, 40 contracts, 55 UI, 76 web, 3 Node).                                                                           |
| Phase 06.2      | **Accepted and closed:** `cd4ca9112b7c8b525869d14ef97c7406f0ba5365`, run 40322387368, 4/4 jobs, 960 tests. Supplementary architecture subphase before Phase 07; no additional main phase.                                                                 |
| Phase 07        | Implemented and pushed: `9e5a7343d9dbe1e647286b7a2ddcadb14f32d11e`, run 40359693463, 4/4 jobs, 990 tests. Review requested the 07.1 corrections.                                                                                                          |
| Correction 07.1 | **Accepted and Phase 07 closed:** `12f2d5b14c7a744126764c86e116d02d69f56abe`, run 40366676323, 4/4 jobs, 1,007 tests, 0 failures.                                                                                                                         |
| Phase 08        | Implemented and pushed: `a806baced20b7f39547b4bed7c92a4f1afb1809a`, run 40376575339, 4/4 jobs, 1,045 tests. Review requested the 08.1 corrections.                                                                                                        |
| Correction 08.1 | **Accepted and Phase 08 closed:** `b2eb55f75a8e878599328c92b139eed6b7230d33`, run 40380408247, 4/4 jobs, 1,050 tests, 0 failures.                                                                                                                         |
| Phase 09        | Implemented and pushed: `84d790e0dbf8d4eae269bbcb5cd0f258c72f678f`, run 40393494776, 4/4 jobs, 1,067 tests. Independent review requested the two 09.1 integrity corrections.                                                                              |
| Architecture    | Dedicated single-tenant deployment, tenant-aware modular monolith, future hybrid only if approved; commerce/control independent.                                                                                                                          |
| Product         | Private beta, manual onboarding. Public commercial signup/subscriptions/billing deferred. Public customer registration is authorized.                                                                                                                     |
| Prototype       | MateCart-UI strictly read-only; no edits/build/install (HEAD `9ff1361`, clean).                                                                                                                                                                           |
| Roadmap         | All 19 main phases preserved; 02.1/02.2/03.1 are corrective subphases, not additional main phases.                                                                                                                                                        |

## Phase 09.1 shipment and courier integrity corrections (implemented locally; acceptance pending)

Corrective subphase after independent review of `84d790e` (GitHub Actions 40393494776, 4/4 jobs,
1,067 tests). It is not a new main phase and introduces no model, migration, API, contract, UI,
provider or dependency change.

- **Cancellation integrity.** Direct shipment cancellation is refused with
  `cancel_order_instead`; pre-dispatch cancellation must use the existing authorized Order
  cancellation operation. That operation locks Order then Shipment, refuses active courier
  bookings and dispatched goods, cancels both records, and releases the existing Order-owned stock,
  coupon, slot and payment commitments once in the same transaction. A failure in shipment history
  recording rolls the complete cancellation back.
- **Callback atomicity.** Authenticated courier callback insertion, locked booking/shipment
  revalidation, stale/transition handling, shipment history and the durable callback outcome now
  commit together. Unexpected failures roll back the event ID and any shipment work so the same
  provider event can retry. The existing unique constraint serializes concurrent copies; recognized
  duplicate, stale, invalid-transition and unknown-reference outcomes remain explicit and safe.
- **Verification.** The fulfillment and fulfillment-concurrency modules pass (20 tests), including
  five new regressions. Existing Order cancellation and FEFO/idempotent fulfillment tests pass (2
  tests). Final gates pass: `pnpm check`, `pnpm build`, `pnpm api:lint`, `pnpm api:test`,
  `pnpm api:schema`, `pnpm health` and `docker compose config --quiet`. **1,072 automated tests
  pass, 0 fail:** commerce API 787, control API 57, shared contracts 51, shared UI 76, commerce-web
  96 and Node 5. OpenAPI remains valid and byte-identical; no migration workflow was repeated
  because this correction has no schema change. All six health probes pass with the eight existing
  Compose services healthy and the storefront/console running for review.

## Phase 09 fulfillment and courier (implemented and pushed; correction 09.1 pending)

Decision D-074; operational details in [SHIPPING.md](SHIPPING.md).

- **Physical fulfillment.** New tenant-owned `commerce.fulfillment` domain with one practical
  shipment per physical/mixed order, physical `ShipmentItem` rows, controlled picking → packing →
  dispatch → delivery/failure/return transitions and an address snapshot. Digital-only orders are
  refused. Dispatch calls the existing `orders.services.fulfil_order_stock()` inside the shipment
  transaction, so reservations, FEFO batches, serials and the append-only stock ledger remain the
  Phase 08 implementation and deduct once.
- **Manual and provider shipping.** Staff with `couriers.manage` can prepare, list and inspect
  shipment work, record genuine courier/tracking details, dispatch and append tracking updates.
  A minimal adapter supports idempotent booking/cancellation and authenticated tracking callbacks;
  callback event ids and body digests are stored without raw payloads. `MATECART_COURIER_PROVIDER`
  is empty and `courier_booking` is off by default, so no real carrier, tracking number or label is
  claimed. Isolated tests use `FakeCourierProvider` only.
- **Tracking and customer view.** `TrackingEvent` is append-only in the ORM and PostgreSQL.
  Duplicate callbacks replay safely, stale updates are ignored, illegal backward transitions are
  refused, and provider callbacks can never perform the dispatch/stock transition. Secure guest or
  customer order details now show only the real shipment reference, current state, recorded events
  and genuine tracking details, in English/Bangla.
- **Courier COD.** `CourierCodRecord` separates amount due, collected cash, fees/deductions,
  expected remittance, actual remittance and discrepancy. Collection and remittance references are
  tenant-unique and actions require a recent authorized staff session. Delivery alone never means
  paid; only explicitly recorded evidence for exactly the order total calls the existing
  `record_cod_collection()` operation, once. Under/over-collection remains unpaid and visible for
  review; actual remittance is independently pending, reconciled or disputed.
- **Fraud review.** Conservative, explainable flags cover at least three prior COD orders using the
  same contact in seven days, at least two recent delivery failures, and at least two same-item COD
  orders in 24 hours. Evidence contains counts/windows, not other buyers' data. Reviews are
  authorized and append-only; flags never auto-cancel, blacklist or block dispatch.
- **Schema/API/UI.** Additive migrations `fulfillment.0001`/`0002`; nine tenant-owned tables with
  composite tenant FKs, database constraints and append-only triggers. Restricted APIs live under
  `/api/manage/fulfillment/`; the only anonymous endpoint is the adapter-authenticated tracking
  callback. No Phase 11 merchant UI was built; the existing customer order page received the
  minimal truthful tracking view. OpenAPI and shared TypeScript contracts were updated.
- **Verification.** Final gates pass: `pnpm check`, `pnpm build`, `pnpm api:lint`,
  `pnpm api:test`, `pnpm api:schema`, `pnpm health`, `pnpm api:celery-check` and
  `docker compose config --quiet`. **1,067 automated tests pass, 0 fail:** commerce API 782,
  control API 57, shared contracts 51, shared UI 76, commerce-web 96 and Node 5. This includes 11
  fulfillment/domain tests and 4 committed-transaction race tests. Migration verification on
  temporary commerce/control databases passed fresh forward, fulfillment/order reversal,
  reapplication, drift and system checks; both new migrations are applied in the dev database.
  The first `pnpm check` reported three formatting differences, which were formatted before the
  passing rerun. Two sandboxed build attempts could not replace desktop-owned `.next` output; the
  same build passed with the required workspace permission. All eight Compose services are healthy,
  and the storefront and console are running on ports 3400 and 3410 for review.

## Phase 08.1 order placement integrity corrections (accepted and closed)

Corrective subphase after the independent review of `a806bac` (GitHub Actions 40376575339, 4/4
jobs, 1,045 tests). Accepted at `b2eb55f75a8e878599328c92b139eed6b7230d33` (GitHub Actions
40380408247, 4/4 jobs, 1,050 tests). Not a new main phase; no model, migration, API schema,
contract, dependency or UI change.

- **Shipping eligibility at placement.** `_place()` used the method chosen at review without
  checking it again, so a method disabled or restricted after review could still produce an order.
  Placement now calls `_check_shipping()`, which reuses `checkout.available_shipping_methods()`
  (active, currency, country, minimum subtotal) for physical and mixed checkouts. An ineligible
  method raises `409 shipping_unavailable` (a `PlacementBlocked` rule). No order, invoice, intent,
  coupon redemption or slot booking is created, and the hold is released so the customer reviews
  again. No method is substituted and the reviewed totals are not changed. Digital-only checkouts
  skip the check.
- **Payment-method-bound replay.** A completed checkout's same-key replay returned the order
  whatever `payment_method` was sent. The replay now also requires the stored
  `Order.payment_method`; a mismatch is `409 idempotency_conflict` and changes nothing (the guest
  credential is not re-issued). A different key keeps `409 checkout_completed`.
- **Documented, unchanged:** zero-total orders cannot be placed (no payment method is offered at a
  zero total); every verified provider payment, late or additional, is recorded as received, and
  overpayment reconciliation must be verified against the chosen gateway before production
  (ORDERS.md).
- **Files.** `services/commerce-api/commerce/orders/services.py`, `tests/test_orders.py`,
  `docs/ORDERS.md`, `docs/FEATURE_CHECKLIST.md`, this file.
- **Verification.** The 5 new regression tests (4 shipping-change cases and the replay matrix in
  both directions) failed before the fix and pass after it. The order and concurrency suites passed
  (30 tests). Final gates passed: `pnpm check`, `pnpm build`, `pnpm api:lint`, `pnpm api:test`,
  `pnpm api:schema` (no warnings, `openapi.yaml` unchanged), `pnpm health` (all six) and
  `docker compose config --quiet`. Suites: **1,050 tests, 0 failures**: commerce API 767, control
  API 57, shared contracts 50, shared UI 76, commerce-web 95, Node 5. The migration workflow was not
  repeated because there is no schema change.

## Phase 08 orders, payments and invoices (accepted and closed with 08.1)

Decisions D-071 to D-073; full description in [ORDERS.md](ORDERS.md).

- **Order placement.** New `commerce.orders` app. `POST /api/checkout/<id>/order/` converts only a
  live reviewed checkout, atomically and idempotently (placement key; DB unique per checkout; a
  different key gets `409 checkout_completed`; drafts get `checkout_not_reviewed`, expired/released
  get `checkout_expired`). Per-store numbers `ORD-000001`/`INV-000001`. Items, prices, contact,
  delivery, slot and coupon are snapshotted; items and invoices refuse updates.
- **State machine.** `pending_payment`/`placed` → `confirmed` → `processing`, any → `cancelled`.
  Online orders are confirmed only by verified payment; merchants confirm COD. Append-only
  `OrderStatusEvent` (ORM guard plus PostgreSQL trigger) and audit events on every edge.
- **Stock.** The checkout hold becomes an order-owned `OrderAllocation` with no second ledger
  movement; checkout release/expiry ignores completed checkouts. Batch holds are rechecked against
  unexpired lots (other unexpired lots replace one that expired; otherwise `409 stock_expired`, hold
  released, no order). Cancellation releases once. `fulfil_order_stock()` is the Phase 09 dispatch
  handoff; `inventory.fulfil` now deducts batch stock FEFO from unexpired lots and requires named
  serials. Digital/untracked/pre-order lines write no movements.
- **Slots and coupons.** Slot hold → `bookings + 1` under the slot lock; coupon redeemed once at
  placement (usage limit under lock); cancellation voids the redemption and returns a future booking.
- **Payments.** COD implemented and on by default for all presets (all-physical orders only), unpaid
  until the merchant records collection. Provider-neutral adapter boundary with
  `MATECART_PAYMENT_PROVIDER` (empty); authenticated, deduplicated, amount/currency/reference-checked
  callbacks; 30-minute payment window with Celery and on-read expiry; late payments kept for refund.
  **No real gateway is selected or configured**: online payment is not offered, the
  `online_payments` module refuses to switch on, EMI stays unavailable. All online behaviour was
  tested only with the isolated `tests.fakes.FakePaymentProvider`.
- **Refunds and invoices.** Merchant refunds (`orders.manage` + recent sign-in), capped by verified
  money, idempotent, pending/failed never counted; COD refunds are recorded as manual. One invoice
  per order issued at placement, frozen seller/buyer/totals, printable page, honest payment status.
- **UI.** Checkout shows payment methods and Place order after review; released/expired checkouts
  now show their real state (the Phase 07 review note); completed checkouts redirect to the order.
  New `/orders/<id>` (confirmation, pay/retry, refresh, cancel, invoice link) and
  `/orders/<id>/invoice` (printable). English and Bangla; shared components for all five presets.
- **APIs/contracts.** Customer: order get/cancel/payment/invoice; anonymous provider callback; merchant
  list/detail/transition/cod-collection/refunds under `/api/manage/orders/`. OpenAPI regenerated;
  `packages/shared-contracts` adds `orders.ts` and checkout `payment_methods`/`order`.
- **Files.** New: `commerce/orders/` (models, services, payments, providers, serializers, views,
  urls, manage_urls, tasks, migrations 0001/0002), `tests/test_orders.py`,
  `tests/test_orders_concurrency.py`, `packages/shared-contracts/src/orders.ts` (+ test),
  `components/store/order-app.tsx`, `invoice-app.tsx`, `order.test.tsx`, routes
  `(store)/orders/[id]` and `[id]/invoice`, `docs/ORDERS.md`. Changed: inventory `fulfil`, checkout
  serializer, module registry, settings/urls/compose (`MATECART_PAYMENT_PROVIDER`), test fakes,
  checkout UI/tests/client, migration verification script, OpenAPI and docs.
- **Verification.** Final gates: `pnpm check`, `pnpm build`, `pnpm api:lint`, `pnpm api:test`,
  `pnpm api:schema` (validated, no warnings), `pnpm health` (all six checks) and
  `docker compose config --quiet` passed. Suites: **1,045 tests, 0 failures**: commerce API 762
  (25 new: 22 order/payment/refund/invoice tests and 3 real-PostgreSQL concurrency tests), control
  API 57, shared contracts 50, shared UI 76, commerce-web 95, Node 5. Migration workflow
  (`scripts/verify_auth_migrations.py`, now including `orders`): fresh forward, reversal to zero,
  reapply and drift check passed for both APIs on temporary databases; `orders.0001`/`0002` applied
  additively to the development database; `makemigrations --check` clean; SQL reviewed for the
  one-order-per-checkout, single-conversion and one-open-intent constraints. The worker was restarted
  to register `orders.expire_payment`. Live smoke (Django HTTP stack inside the API container against
  the development database; the test client skips CSRF, which is covered by the existing suites):
  add, review, `payment_methods` = `[cod]`, online refused (400), COD placed (201,
  `ORD-000002`/`INV-000002`, unpaid), same-key replay returned the same order, stock reserved 1 and
  not deducted, owner 200 and stranger 403, invoice unpaid with 0 received, unconfigured callback
  404, cancel released the reservation. An aborted first smoke run left `ORD-000001`; it was
  cancelled through the service, so no reservation remains, and both "Phase 08 Smoke Tea" products
  are archived. `/orders/<id>` and `/orders/<id>/invoice` answer 200 on the dev server. **Not
  verified:** any real payment gateway or sandbox (none is configured), a manual browser visual pass
  of the new pages (covered by component tests only), and live CSRF on the new endpoints.

## Phase 07 cart and checkout (accepted and closed with 07.1)

- **Cart and ownership.** New `commerce.checkout` app with persistent guest/customer carts,
  opaque hashed guest credentials, 30-day expiry, sign-in association/merge, unique lines,
  quantity/line limits and current catalogue, price and sellable-stock checks. Storefront product
  pages add the selected variant and quantity; every enabled preset header has a live cart count;
  `/cart` supports quantity changes, removal, totals and checkout entry.
- **Checkout.** Server-owned checkout sessions snapshot current lines/prices, collect international
  contact and address data, support physical, digital and mixed carts, calculate basic coupons,
  eligible shipping, regional tax and grand total, and expose Grocery slots where enabled. The
  shared responsive `/checkout/<id>` interface has English/Bangla copy and preserves theme tokens.
- **Inventory and transition safety.** Review locks variants in stable order, requotes and checks
  unexpired sellable stock, then reserves through the existing append-only inventory service for
  15 minutes. Idempotency rejects a different replay; a tenant-aware Celery task and explicit
  release safely return abandoned holds. The reviewed state explicitly creates no order or payment;
  Phase 08 owns both plus final allocation and coupon redemption (D-070).
- **Schema and APIs.** Additive `checkout.0001` creates Cart, CartItem, Address, ShippingMethod,
  TaxRate, Coupon, DeliverySlot, CheckoutSession, CheckoutLine and CheckoutReservation with tenant
  composite FKs and integrity constraints. Cart, checkout/review/release and customer-address APIs
  are in the validated OpenAPI schema; runtime-checked TypeScript contracts keep Decimal values as
  strings. `cart_checkout`, Grocery `delivery_slots` and Books/General `preorders` are implemented.
- **Verification.** `pnpm check`, `pnpm build`, `pnpm api:lint`, `pnpm api:test`,
  `pnpm api:schema`, `pnpm health` and `docker compose config --quiet` passed. The suites contain
  990 tests: commerce API 720, control API 57, shared contracts 46, shared UI 76, commerce-web 86
  and Node 5. The temporary-database workflow passed forward migration, checkout reversal,
  reapplication, drift and system checks for both APIs; the development migration was applied
  additively. Celery round-trip passed. A live guest HTTP smoke added physical and digital variants,
  rejected incomplete delivery details (400), offered one eligible method, calculated BDT 700.0000,
  reviewed and replayed idempotently, created no order, then released the hold. All six health checks
  pass. The first local build was blocked by restricted Google Fonts DNS and then passed with network
  permission; stale pre-existing Next dev processes were restarted after an initial port conflict.
  No manual browser visual pass was performed; responsive/theme behavior is covered by component
  tests and the live pages are running for owner review.

## Phase 07.1 checkout integrity corrections (accepted and closed)

- **Coupon review revalidation.** The selected coupon was validated while editing a checkout but
  not at its final review transition. Review now locks and reloads the coupon, checks its current
  active window, usage limit, minimum subtotal, currency, discount type and amount, and recalculates
  all totals from the current row before any stock reservation. A rejected coupon leaves the
  checkout in draft and reserves nothing; redemption remains Phase 08.
- **One active hold per cart.** Checkout creation could create a new draft after a reviewed session,
  and a direct review of another historical draft could reserve the same cart again. Creation and
  review now serialize on the cart row, release expired reviewed sessions, reuse an unexpired
  reviewed session at creation, and return `active_checkout_exists` for a second direct review.
  Existing same-key replay remains idempotent; explicit release or expiry permits a later checkout.
- **Atomic Grocery delivery-slot holds.** Availability previously considered only committed
  `bookings`; review did not protect capacity. A reviewed, unexpired checkout is now the temporary
  slot hold. Review locks the slot row and counts committed bookings plus other active holds before
  reserving inventory. Availability uses the same count; release and expiry free capacity through
  checkout status/expiry without changing `bookings`. Slots remain Grocery-only and physical-only,
  and a configured-but-full slot pool cannot be bypassed.
- **Shipping safety check.** The free `standard` bootstrap could bypass an explicitly configured
  method that was ineligible for the checkout country. It is now created only when the store has no
  shipping methods at all. Any explicit configuration remains authoritative; no shipping pricing or
  provider feature was added.
- **Files and schema.** Changed `commerce/checkout/services.py`, `tests/test_checkout.py`, new
  `tests/test_checkout_integrity_concurrency.py`, and this phase's three handoff documents. There is
  no model, migration, dependency, OpenAPI or shared-contract change, and no new decision was needed.
- **Verification.** Focused Ruff/format/mypy passed and 39 checkout tests passed, including real
  PostgreSQL concurrency for same-cart reviews and capacity-one slots. Final gates passed:
  `pnpm check`, `pnpm build`, `pnpm api:lint`, `pnpm api:test`, `pnpm api:schema`, `pnpm health` and
  `docker compose config --quiet`. The suites now contain **1,007 passing tests, 0 failures**:
  commerce API 737, control API 57, shared contracts 46, shared UI 76, commerce-web 86 and Node 5.
  A live guest checkout smoke reviewed, replayed the same key, reused the same reviewed session on a
  second checkout creation, created no order and released successfully. All six health checks pass.

Phase 03/03.1 history (accepted): realm-separated customer, merchant and console
authentication; optional merchant and mandatory console MFA; owner provisioning
and staff invitations; 03.1 closed backend team step-up, OIDC binding order and
narrow Google provider failure handling (D-039 to D-049, AUTHENTICATION.md,
SECURITY.md). External Google, SMS and SMTP acceptance remains outstanding until
genuine providers are configured.

## Phase 06.2 multi-theme architecture readiness (supplementary subphase, accepted)

Decision D-069. Architecture readiness only: no new theme, layout, marketplace, showcase,
licensing, console UI or merchant theme control. Rule: one commerce engine, shared functional
components, several visual themes; themes are presentation only.

- **Preset versus theme.** `StoreSettings.preset` stays the business preset (product types,
  modules, homepage defaults, feature availability). A theme is a visual design for exactly one
  preset; merchant branding (`StoreTheme`) overrides a few of its tokens.
- **Catalogue and registry.** Identities in `packages/shared-contracts/src/themes.json` (+
  `themes.ts`: `STORE_THEMES`, `DEFAULT_STORE_THEMES`, `isThemeOfPreset`), mirrored in
  `commerce/stores/themes.py` for validation; `scripts/themes.test.mjs` checks the API enum
  against the catalogue. Visual definitions in `@matecart/shared-ui` `THEMES` (`presets.ts`):
  id, preset, name, style, description, revision, swatches, colours, fonts, layout and
  `presentation` variants (header, hero, card, sections, product). Registered:
  `fashion-editorial`, `grocery-quickshop`, `electronics-spec-first`, `general-marketplace`,
  `books-literary` (one default each; the existing designs, token for token).
- **Assignment.** Additive `StoreSettings.active_theme` (migration `stores.0004`, blank =
  preset default, format CHECK, model validation of preset compatibility). Only the trusted
  service `stores.services.assign_theme` changes it, audited as `store.theme.assigned`, reached
  only from `manage.py assign_store_theme --theme <id> | --default`. Merchant and public APIs
  cannot change it (crafted payloads are ignored). `change_store_preset` clears an assignment
  the new preset cannot use, in the same audited change.
- **Resolution.** `effective_theme()` (API) and `resolveTheme(preset, branding, themeId)` (UI)
  return the assigned theme when it belongs to the preset, else the preset default with a
  logged recovery; never another preset's theme. The public config and the merchant appearance
  response carry the read-only `active_theme`; the storefront reads nothing theme-related from
  the request. `StoreThemeScope` marks `data-preset` and `data-theme`.
- **Presentation extension points.** Storefront components switch on the theme's variants
  (header, hero, cards and grids, sections, product page) instead of the preset; business
  choices (General's product-type filter, the book search wording) stay on the preset. A new
  theme of a preset is a registry entry choosing variants (plus any new variant in the shared
  components), not a copy of the storefront.
- **Merchant screen.** `/manage/appearance` previews with the assigned theme and states that the
  preset and theme are set by NetaMate; no selection, activation or upload control exists.

## Verification record (Phase 06.2, observed locally)

**960 automated tests passed, 0 failed** (698 commerce + 57 control + 44 contracts + 76 shared
UI + 80 commerce-web + 5 Node): 16 new commerce tests (`test_themes.py`), 4 contract, 21 shared
UI (`themes.test.tsx`: registry, frozen token baseline for all presets, resolution and
recovery, test-only second theme), 4 commerce-web parity tests (`themes.test.tsx`) and 2 Node
catalogue checks. Existing tests were migrated to the new shapes without weakening them.

| Check         | Actual observation                                                                                                                                                                                                                                                                                                                                             |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend       | `pnpm api:test`: 698 + 57 passed (OpenAPI drift test included). `pnpm api:lint` passed (178 + 52 files) after wrapping two lines and a raw-string fix; `test_themes.py` re-run (16 passed). `makemigrations --check`: no changes. `verify_auth_migrations.py`: fresh, reverse, reapply and drift checks passed for both services; temporary databases removed. |
| Frontend      | `pnpm check` passed (Prettier, ESLint, typecheck, 44 + 76 + 80 + 5 tests). `pnpm build` passed (both apps).                                                                                                                                                                                                                                                    |
| API contract  | `pnpm api:schema`: `active_theme` (enum `ActiveThemeEnum`) on the storefront configuration and the merchant appearance response; nothing removed.                                                                                                                                                                                                              |
| Visual parity | 25 headless-Chrome captures (5 presets × home, listing and product at 1440/390 px) before and after: 24 byte-identical; the one difference (22 pixels in product images) also appears between two captures of the unchanged code, so it is capture noise. Theme tokens equal a baseline frozen before the change.                                              |
| Live          | Dev store migrated additively (`stores.0004`); it resolves `general-marketplace`; storefront, merchant and console routes respond; `pnpm health` and `docker compose config` passed.                                                                                                                                                                           |
| Not verified  | No Central Console integration exists to test (Phases 16-17). No screen-reader or real-device run.                                                                                                                                                                                                                                                             |
| GitHub CI     | Accepted commit `cd4ca9112b7c8b525869d14ef97c7406f0ba5365`, run 40322387368, SUCCESS 4/4 jobs, 960 tests.                                                                                                                                                                                                                                                      |

## Phase 06.1 corrections (accepted)

Three gaps from the independent post-push review of Phase 06, fixed without redesign. No
model, migration or dependency change; inventory, pricing, authentication and content
management are untouched.

1. **Translated product names were not searchable.** Public search matched `name`, tags,
   brand, authors and ISBN, but not `translations[lang]["name"]`, so a Bangla name (or the
   English name of a Bangla-default store) found nothing. `_search()` now also matches the
   translated name for each of the store's supported languages (JSON key lookups in the ORM,
   keys limited to platform language codes), inside the same filtered queryset before
   pagination, so active-only and tenant scoping are unchanged.
2. **Pre-order labels used the UTC date.** The product page passed
   `new Date().toISOString()` while availability uses the store's IANA time zone
   (`store_today()`). `/api/storefront/config/` now returns `local_date` from `store_today()`
   (D-068); the contract decodes it as `YYYY-MM-DD` and the product page passes it to the
   existing label logic. Availability responses are unchanged.
3. **The homepage could render without main content.** The fallback ran only for zero
   sections, while showcases without categories, empty collection highlights and empty trust
   strips render nothing. `isRenderable()` / `hasRenderableSection()` in `home-sections.tsx`
   now decide both what the renderer shows and when the route uses the existing
   `HomeFallback`; the fallback's newest-products request is made only in that case.

Files: `commerce/catalog/public.py`, `commerce/content/public.py`, `openapi.yaml`,
`tests/test_catalog_api.py`, `tests/test_appearance.py`;
`packages/shared-contracts/src/{appearance.ts, appearance.test.ts}`;
`apps/commerce-web/src/{app/(store)/page.tsx, app/(store)/products/[slug]/page.tsx,
components/store/{home-sections.tsx, product-experience.tsx, storefront.test.tsx},
test/fixtures.ts}`.

## Verification record (Phase 06.1, observed locally)

**913 automated tests passed, 0 failed** (682 commerce + 57 control + 40 contracts + 55 shared
UI + 76 commerce-web + 3 Node): 3 new commerce tests (translated-name search; store-local date
in Asia/Dhaka and America/New_York with availability around the release day), 1 new contract
test and 15 new commerce-web tests (pre-order labels before, on and after release with digital
and regular variants; fallback decisions for each empty-section case, mixed order and all five
presets).

| Check        | Actual observation                                                                                                                                                                                                                                                                                                              |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend      | `pnpm api:test`: 682 + 57 passed (includes the OpenAPI drift test). `pnpm api:lint` passed after ruff sorted two inserted imports and one test line was wrapped; the two touched test files were re-run (69 passed). `makemigrations --check`: no changes.                                                                      |
| Frontend     | `pnpm check` passed (Prettier, ESLint, typecheck, 40 + 55 + 76 + 3 tests). `pnpm build` passed (both apps).                                                                                                                                                                                                                     |
| API contract | `pnpm api:schema`: only `local_date` (string, date) added to the storefront configuration.                                                                                                                                                                                                                                      |
| Live         | Bangla search for "গীতাঞ্জলি" returns Gitanjali through the API and `/search`; `local_date` is the Dhaka date; the pre-order book shows "Pre-order · Releases 15 March 2027" and Gitanjali "In stock". `pnpm health` passed (a first run hit the console's first-compile delay; re-run passed). `docker compose config` passed. |
| Not verified | The empty-homepage fallback was not exercised against the review store (that would mean disabling its sections); it is covered by the component tests. Preset screenshots were not repeated (no layout change).                                                                                                                 |
| GitHub CI    | After push: `df0490f`, run 40315255063, SUCCESS 4/4 jobs, 913 tests.                                                                                                                                                                                                                                                            |

## Phase 06 implemented scope

Decisions D-065 to D-067. Controls: SECURITY.md. Review data: `scripts/storefront-seed.mjs`.

- **Pages** (commerce-web route group `app/(store)/`): `/` (homepage), `/shop`, `/search`,
  `/categories/[slug]`, `/collections/[slug]`, `/products/[slug]`, `/pages/[slug]`; a store
  404 page (`app/not-found.tsx`, real 404 status) and a neutral error page with retry
  (`app/error.tsx`). The Phase 01 developer page, its readiness widget and
  `lib/commerce-api.ts` were removed. `/account`, `/manage`, `/api` and `/healthz` are unchanged.
- **Data** (`lib/storefront-api.ts`, server-only): anonymous public endpoints only, no browser
  cookies forwarded, `no-store` reads at request time (`connection()`), per-render
  de-duplication with `cache()`, every response decoded by `shared-contracts`, slugs checked
  before use. API failures show the error page; missing or unpublished items are 404s.
- **Frame**: `StoreShell` applies the preset theme on the server (`StoreThemeScope`, no flash),
  marks the language, adds a skip link, the header and the footer. The header has five desktop
  layouts after the prototype (Fashion and Books centre the brand; Grocery and General are
  search-first with category rows; Electronics is a dark technical header) and one compact
  mobile row with an accessible drawer (native modal dialog). The footer shows only configured
  identity, categories, published footer pages and contact details. No cart, wishlist,
  compare, notifications, social links or invented promises.
- **Five presets**, one implementation: header, product card (Fashion 3:4 editorial, Grocery
  dense bordered tiles with the other-language name, Electronics spec cards with brand and
  stock state, General marketplace tiles, Books cover-first cards with authors), grid density,
  display type (`store-display` utility), heroes (editorial, promo panel, dark spotlight,
  marketplace banner, featured title), category showcases, brand stories and trust strips all
  vary by preset.
- **Homepage**: the Phase 05 sections in their saved order; collection highlights fetched in
  parallel from the public catalogue (merchant order); sections with nothing real to show are
  left out; CTAs only to internal paths; a fallback (store name and newest products) when no
  sections exist.
- **Discovery**: listing with result count, category tree (current branch opened), price range,
  brand (from product links), product type (General only), sort (newest, price both ways,
  name; "Featured" on collections), pagination, active-filter chips, empty states and image
  placeholders. All state is in the URL; filtering, sorting and paging run in the API over
  the whole catalogue. Search covers names, tags, brands, book authors and ISBN-13.
- **Product page**: gallery with variant images first, option chips (swatches when the merchant
  set them; values marked unavailable only when no purchasable variant has them), price,
  compare-at and saving for the selected variant, stock/pre-order/not-available states, pack
  size, digital format, ISBN, SKU (Electronics), type highlights (spec grid, book facts, fit and
  size guide), a details table, accessible tabs, related products and `?variant=` in the URL.
  "Add to cart" is visibly disabled with "Online ordering is not available yet."
- **Bilingual**: `mc_lang` cookie read on the server; switcher only when the store has two or
  more languages; interface text in English and Bangla; merchant translations for products,
  categories, collections, sections, pages, footer links and brand texts with per-field
  fallback; prices and dates use Latin digits; slugs, prices and stock never change by language.
- **Metadata**: title template from the store name, descriptions, favicon, Open Graph basics;
  `noindex` unless `STOREFRONT_INDEXING=allow`; search pages are always `noindex`.
- **Backend (narrow, additive, public)**: `min_price`/`max_price` on `/api/catalog/products/`
  (any active variant priced in range on the default list; `min > max` is a 400); search also
  matches brand names, book authors and an exact ISBN-13; `authors` on product summaries;
  `isbn` on public variants; title-only translations on footer links. OpenAPI regenerated;
  contracts gained the full apparel, grocery, electronics and book details, public
  collections, storefront sections and pages.
- **Tooling**: Vitest and Testing Library in commerce-web (same versions as shared-ui);
  `node scripts/storefront-seed.mjs --local` (additive review data); development-only
  `STOREFRONT_PREVIEW_PRESET` for preset smoke checks without touching the store.
- **Deliberately not built (later phases)**: cart, checkout, reservations by customers,
  delivery slots and working quantity steppers (07); orders, payments, COD (08); shipping and
  courier (09); reviews, ratings, wishlists, buyer libraries (10); comparison and EMI (later,
  with payments); merchant catalogue dashboard (11); SEO platform and analytics (12); console
  changes (16).

## Verification record (Phase 06, observed locally)

**894 automated tests passed, 0 failed** (679 commerce + 57 control + 39 contracts + 55
shared UI + 61 commerce-web + 3 Node): 1 new commerce test (discovery: search, price range,
authors, ISBN) and an extended footer test, 5 new contract tests and 61 new commerce-web tests
(24 library, 5 data-layer, 32 component).

| Check        | Actual observation                                                                                                                                                                                                                                                   |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend      | `pnpm api:test`: 679 + 57 passed (includes the OpenAPI drift test). `pnpm api:lint`: Ruff, format, strict mypy (175 + 52 files). `makemigrations --check`: no changes. `pnpm api:schema` regenerated (additions only).                                               |
| Frontend     | `pnpm check` passed (Prettier, ESLint, typecheck, 39 + 55 + 61 + 3 tests). `pnpm build` passed (Webpack, both apps; every storefront route dynamic; no build-time API reads).                                                                                        |
| Live         | All storefront routes 200; unknown product, category, collection, page and URL 404; invalid query values ignored; `mc_lang=bn` renders Bangla on the server with fallback; unsupported values use the default. `pnpm health` passed; `docker compose config` passed. |
| Visual smoke | Headless Chrome with device emulation (scratchpad helper, nothing installed): homepage, listing and product pages for all five presets at 1440 px and 390 px (21 captures), reviewed; no horizontal overflow. Store data unchanged by the preview.                   |
| Review data  | `storefront-seed.mjs --local` run once (7 categories, 10 products of all five types, 1 collection, homepage sections, 2 pages, appearance texts, Bangla translations); option order then corrected once through the merchant API.                                    |
| Not verified | Screen-reader run, real phones and Safari, Lighthouse/performance budgets. The auth smoke was not re-run (no authentication change).                                                                                                                                 |
| Preservation | Only `matecart-dev` used; other projects untouched; prototype `9ff1361` clean; no env, key or font files in the change set.                                                                                                                                          |
| GitHub CI    | After push: `46c2e1a`, run 40311886344, SUCCESS 4/4 jobs, 894 tests.                                                                                                                                                                                                 |

## Phase 05.1 corrections (accepted)

No migration, model, API shape or OpenAPI change; the existing `translations` JSON is
reused. Phase 04, authentication and preset defaults are untouched.

1. **Published pages could be emptied.** Publishing required text, but `save_page()` did
   not re-check it on later edits, so a live page could be saved with an empty or
   whitespace-only body. Now refused with HTTP 400 (`body`); the stored text and status
   are kept. Drafts may still be empty.
2. **Blank homepage sections could be shown.** `_check_section()` had no title rule and
   the UI created new sections enabled. A section now needs a non-blank title to be
   enabled (enabling a blank one or clearing an enabled one's title: HTTP 400, `title`);
   incomplete sections save while hidden. The UI creates new sections hidden. The
   collection-highlight rule and the five preset default homepages are unchanged and
   still valid (tested).
3. **Bangla-default stores offered Bangla translation fields.** Merchant screens
   hard-coded `bn` as the translation language and hid it when Bangla was the default.
   New `translationLanguage()` in `@matecart/shared-ui` picks the store's non-default
   supported language (Bangla for English-default stores, English for Bangla-default,
   none for single-language stores); labels name it ("Tagline in English", "Page text
   in Bangla"). `withTranslation()` merges the edited language into the stored map
   without dropping other languages or fields (previously page saves replaced the map).
4. **Pages and sections were deleted without confirmation.** New shared `ConfirmDialog`
   names the item, states the deletion is permanent and offers Cancel/Delete; Cancel
   sends nothing, Delete sends one DELETE.

Files: `commerce/content/services.py`, `tests/test_appearance.py`;
`packages/shared-ui/src/{translations.ts (new), auth-controls.tsx, index.ts,
content-ui.test.tsx (new)}`; `apps/commerce-web/src/components/{appearance-app.tsx,
content-app.tsx}`.

## Verification record (Phase 05.1, observed locally)

**827 automated tests passed, 0 failed** (678 commerce + 57 control + 34 contracts + 55
shared UI + 3 Node): 11 new commerce tests in `test_appearance.py` (published-page
emptying ×2, drafts, section titles, preset defaults ×5, translations both directions ×2)
and 9 new UI tests in `content-ui.test.tsx`. One existing section test was updated to
give its sections titles and assert specific error keys.

| Check        | Actual observation                                                                                                                                         |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend      | `pnpm api:test`: 678 + 57 passed (includes the OpenAPI drift test). `pnpm api:lint`: Ruff, format, strict mypy (175 + 52). `makemigrations --check`: none. |
| Frontend     | `pnpm check` passed (Prettier, ESLint, typecheck, 34 + 55 + 3 tests). `pnpm build` passed (Webpack, both apps).                                            |
| Live         | `/manage/appearance`, `/manage/content`, `/api/storefront/config/` and `/home/` return 200; `pnpm health` passed; `docker compose config` passed.          |
| Not verified | No manual browser walk-through of the dialogs or bilingual fields; auth smoke not re-run (no auth change, per instruction).                                |
| GitHub CI    | After push: `613cd49`, run 40301529264, SUCCESS 4/4 jobs, 827 tests.                                                                                       |

## Phase 05 implemented scope

Decisions D-061 to D-064. Schema: DATA_MODEL.md §2. Controls: SECURITY.md.

- **Presets and themes:** typed definitions of the five approved identities in
  `@matecart/shared-ui` (`presets.ts`: palette, typography, card aspect, density, columns,
  quantity stepper, dark header, hero style), `resolveTheme` with validated overrides and a
  General fallback, `themeVariables`, `StoreThemeScope` (server-renderable, scoped CSS
  variables) and `ThemePreview`. `theme.css` gained `store-*` tokens via `@theme inline`;
  core and status tokens are unchanged. Instrument Serif and Newsreader are self-hosted via
  `next/font`; Optimus is still not bundled.
- **Branding:** `stores.StoreTheme` (brand/accent colours with a 4.5:1 text rule, logo,
  dark logo, favicon via the Phase 04 media pipeline, tagline, description, announcement,
  footer text, Bangla translations). Store name stays on `StoreSettings` and needs
  `store.settings` to change. Restore preset colours. Business preset changes only through
  `manage.py change_store_preset`, refused while products would become incompatible.
- **Modules:** code registry (implemented and planned modules, phases, presets, defaults,
  prerequisites) plus `stores.StoreModule` overrides; planned modules can never be enabled.
- **Content:** `content.ContentPage` (draft/published, plain text, footer links) and
  `content.HomeSection` (typed sections, ordering, enabled flag, image, collection, internal
  link); preset default homepage; all changes audited.
- **APIs:** `/api/manage/store/appearance/` (+ `reset/`), `/api/manage/store/modules/`,
  `/api/manage/content/pages|sections/` (staff realm, CSRF; `content.manage`, modules
  `store.settings`), public `/api/storefront/config|home|pages/`. OpenAPI regenerated (146
  operations); `shared-contracts` `appearance.ts` decoders; the shared API client gained
  `put`, `delete` and multipart `upload`.
- **Merchant UI:** `/manage/appearance` (preset, brand texts, colours with live contrast
  feedback, logo/dark logo/icon upload, live preview, all five preset previews, store
  features) and `/manage/content` (homepage sections and pages). Shown only to staff with
  `content.manage`; the features panel only with `store.settings`.
- **Deferred:** storefront pages that render the theme and content (06), full merchant
  dashboard (11), console module provisioning (16), contact address/social links and SEO
  fields (later phases).
- **Phase 07/08 follow-up recorded (not implemented):** batch-expiry revalidation at
  reservation, allocation and fulfilment, in FEATURE_CHECKLIST.md.

## Verification record (Phase 05, observed locally)

**807 automated tests passed, 0 failed** (667 commerce + 57 control + 34 contracts + 46
shared UI + 3 Node): 37 new commerce tests (35 in `test_appearance.py`, 2 guard cases),
30 new UI and 5 new contract tests.

| Check        | Actual observation                                                                                                                                                                                        |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend      | `pnpm api:test`: 667 + 57 passed. `pnpm api:lint`: Ruff, format, strict mypy (175 + 52 files). `pnpm api:schema`: valid, regenerated.                                                                     |
| Frontend     | `pnpm check` passed (Prettier, ESLint, typecheck, 34 + 46 + 3 tests). `pnpm build` passed (Webpack, both apps, new fonts self-hosted).                                                                    |
| Migrations   | `content.0001` and `stores.0003` (new tables only). `verify_auth_migrations.py` passed (now reversing them too). Dev: applied; tenants, users 16, audit 109, products 4, movements 8 unchanged; no drift. |
| Live         | `/manage/appearance`, `/manage/content`, `/api/storefront/config/` and `/home/` respond through the Next.js proxy; anonymous mutation refused; `pnpm health` passed; `docker compose config` passed.      |
| Auth smoke   | `node scripts/auth-smoke.mjs --local`, run once: **12/12 passed**, closing the item carried from Phase 04.1.                                                                                              |
| Preservation | Other projects' containers, volumes and networks unchanged; prototype `9ff1361` clean; no env files, keys or the Optimus font in the change set.                                                          |
| GitHub CI    | After push: `e3810cb`, run 40297666311, SUCCESS 4/4 jobs, 807 tests.                                                                                                                                      |

## Phase 04.1 corrections (accepted)

Both review findings were reproduced first: new regression tests failed against the
unchanged `1bd61be` code for the expected reasons (14 wrongful replays, HTTP 200
instead of 409, two concurrent false replays, expired stock shown in stock and
reserved). The clock-boundary test needed a patchable clock and is not counted as a
defect reproduction (`store_today()` already used the store's IANA zone).

1. **Idempotency request identity (high, D-059).** Keys are now bound to a SHA-256
   fingerprint of the request's canonical identity (type, variant, locations,
   quantity, batch code and dates, serial set, reversed operation, reference),
   stored on the operation's first movement (`inventory.0003`, additive nullable
   column). Different request, same key: `409 idempotency_conflict`. Pre-04.1 keys
   (three in the development ledger) are verified from the recorded movements.
2. **Expired batch availability (medium, D-060).** `sellable_quantities()` is the
   single definition for the public list and detail, `is_purchasable()` and
   `reserve()`: unexpired batch stock at active locations minus the location's
   reservations (expiry inclusive, store calendar). Physical stock and the ledger
   are unchanged by expiry.
3. **Related finding (confirmed and fixed):** batch-tracked movements could remove
   unexpired units that reservations depended on, because the reserved-stock check
   used the aggregate level, which includes expired units. Such removals are now refused,
   as for plain stock; expired stock can still be written off or moved.
4. **Reviewed without change:** append-only ledger and trigger, transfer pairing,
   single reversal, serial uniqueness and location/status CHECKs, `fulfil` refusing
   batch/serial allocation, tenant scoping of the new column (same table, key unique
   per tenant), RBAC/CSRF on all inventory routes, audit content. No authentication,
   identity or staff code changed. The OpenAPI contract is unchanged.

## Phase 04 implemented scope

Decisions D-050 to D-058. Schema: DATA_MODEL.md §2. Controls: SECURITY.md.

- **Apps:** `commerce.media`, `commerce.catalog`, `commerce.pricing`,
  `commerce.inventory` (27 tenant-owned models, 45 composite tenant foreign keys,
  five additive migrations including the ledger trigger).
- **Catalogue:** category tree (five levels, cycle-safe moves under a store-level
  lock), collections with explicit order, brands, typed attributes (text, number,
  boolean, option, measurement; variant vs specification scope), products with a
  draft/active/archived lifecycle and publication checks, variants with
  database-unique option combinations, store-unique SKUs, GTIN barcodes and ISBN-13
  per format, translations for the store's additional languages.
- **Presets:** one engine; `product_type` per product. Apparel (audience, material,
  fit, care, size guide), grocery (pack size, perishable, storage, shelf life,
  origin, ingredients/allergens), electronics (model, warranty policies, typed
  specs, serial tracking), books (authors, publisher, date, edition, language,
  genre, pages, series; paperback/hardcover/eBook variants with their own ISBN;
  digital format; pre-order release date), general (all types, none forced).
- **Pricing:** price lists (default in the store currency), Decimal(19,4) prices
  with currency minor-unit precision, compare-at above price, zero allowed, cost
  prices restricted to the new `catalog.costs` permission (Owner, Manager) and
  never written to audit values; `quote()` for checkout snapshots.
- **Inventory:** locations (MAIN default), levels with database-generated
  `available`, append-only ledger (ORM + trigger) with balances after each change,
  receipts, signed count adjustments, damaged/lost/returned, transfers, whole
  operation reversals, idempotency keys, no negative stock or overselling (locks +
  CHECKs), reconciliation (API and `manage.py reconcile_inventory`), batches with
  expiry and FEFO listing, store-unique serial numbers with one location. Reserve,
  release and fulfil are tested service extension points for Phases 07/08.
- **Media:** uploads validated from bytes (JPEG/PNG/WebP fully decoded, PDF), size,
  dimension and bomb limits checked before parsing, tenant-namespaced generated
  paths (fail closed), named volume locally, hardened streaming, public files only
  while published.
- **APIs:** merchant `/api/manage/catalog|pricing|inventory|media/` (staff realm,
  CSRF, `catalog.manage`), public read-only `/api/catalog/` (publication-safe),
  pagination, allowlisted filters/sorting/search, standard error envelope; 126
  OpenAPI operations in `openapi.yaml`; runtime-validated `shared-contracts`
  decoders for the public catalogue and inventory responses.
- **Deferred by scope:** storefront/product UI (06), merchant catalogue UI (11),
  themes (05), cart/checkout and reservation API use (07), orders, FEFO allocation
  and payments (08), couriers (09), warranty claims/returns/digital delivery (10),
  promotions/coupons (12), image processing/CDN and public rate limits (12/17).

## Defects found and fixed during Phase 04

1. Uploaded files were stored under `tenants/None/...` because the path was built
   before the row had its tenant (caught by a media test). The upload service now
   assigns the tenant first, and `tenant_storage_path` uses the active tenant for
   unsaved rows and fails closed without one (regression test added).
2. `.gitignore` ignored every `media/` directory, which would have silently left the
   whole `commerce/media` app out of the commit. The rule is now anchored to
   `/services/*/media/` (local upload folders only).
3. The upload size limit ran after CSRF had already parsed the form body; it now runs
   first, before any parsing or temporary-file spooling.
4. Draft books could not be saved without an author; authors are now optional until
   publication (catalogue migration regenerated before it was ever applied).
5. The shared `never_cache` base prevented public media caching; the public media
   route now uses a plain read-only view. Primary-category-only updates are applied.

## Changed areas

| Area          | Files/modules                                                                                                                                                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| New apps      | `commerce/{media,catalog,pricing,inventory}/` (models, services, serializers, views, urls, migrations; catalogue `public.py`/`public_urls.py`; `reconcile_inventory` command).                                     |
| Core          | `commerce/core/{domain,fields,pagination}.py` (domain errors, tenant-scoped reference fields, pagination); `tenancy/keys.py` (fail-closed storage path).                                                           |
| Staff         | `staff/permissions.py` (`catalog.costs`), `staff/views.py` (`CatalogStaffView`).                                                                                                                                   |
| Config        | `config/settings/base.py` (apps, media storage, schema enums), `config/urls.py`, `pyproject.toml`/`uv.lock` (Pillow 12.3.0), `Dockerfile` (media dir), `compose.yaml` (media volume and env), `.gitignore`.        |
| Contracts     | `packages/shared-contracts/src/catalog.ts` and tests; `index.ts` export.                                                                                                                                           |
| Tests         | `tests/catalog_helpers.py`, `test_catalog_{isolation,domain,api}.py`, `test_pricing.py`, `test_inventory.py`, `test_inventory_concurrency.py`, `test_media.py`; `conftest.py` (per-test media root), `helpers.py`. |
| Scripts       | `scripts/catalog-smoke.mjs` (new); `scripts/verify_auth_migrations.py` (Phase 04 reversal).                                                                                                                        |
| Documentation | AGENTS (Phase 04 guardrails), README, ARCHITECTURE, DATA_MODEL, DECISIONS (D-050 to D-058), roadmap, checklist, SECURITY, AUTHENTICATION, DEVELOPMENT_SETUP, this handoff.                                         |

## Verification record (Phase 04.1, observed locally)

**735 automated tests passed, 0 failed, 0 skipped, 0 errors** (630 commerce + 57
control + 29 contracts + 16 shared UI + 3 Node): 34 new commerce regression tests
in `tests/test_inventory_integrity.py`.

| Check            | Actual observation                                                                                                                                                                                                                                                                                                                                                                     |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Regression tests | 34 passed (were 19 failing / 6 passing before the fix; the reserved-batch test failed before its fix). Covers every operation type, tenants, rollback-then-retry, historical keys, HTTP 409 and expiry cases A–H.                                                                                                                                                                      |
| Concurrency      | Real committed transactions: 8 identical receipts apply once (existing test), 2 conflicting receipts and 2 conflicting transfers apply exactly one, cross-variant key race keeps one owner, 8 parallel requests across two stores apply once each.                                                                                                                                     |
| Suites           | `pnpm api:test`: commerce 630, control 57. `pnpm check`: contracts 29, UI 16, Node 3. `pnpm api:lint`: Ruff, format, strict mypy (158 + 52 files).                                                                                                                                                                                                                                     |
| Build/schema     | `pnpm build` passed (Webpack). `pnpm api:schema`: valid, byte-identical (no API change).                                                                                                                                                                                                                                                                                               |
| Migrations       | `inventory.0003` SQL: `ADD COLUMN ... NULL` and `ADD CONSTRAINT` only. `verify_auth_migrations.py`: fresh, reverse, reapply, drift passed for both services. Dev upgrade: users 14, audit 92, products 3, movements 6, levels identical before and after; reconcile consistent.                                                                                                        |
| Historical keys  | The 3 real pre-04.1 keys in the dev ledger: identical retry replayed, changed quantity `idempotency_conflict` (checked inside rolled-back transactions; nothing written).                                                                                                                                                                                                              |
| Runtime          | `pnpm health` all six, `pnpm api:celery-check` pong, `docker compose config --quiet` passed, `pnpm dev` on 3400/3410.                                                                                                                                                                                                                                                                  |
| Live smoke       | `catalog-smoke.mjs --local`: 7/7 (includes HTTP idempotent replay and oversell refusal). `auth-smoke.mjs --local`: not completed — the first 5 checks passed, then the review Owner password-reset step was rate-limited (HTTP 429, 3 resets per account per hour) after earlier runs. Security counters were not cleared. No authentication code changed in 04.1; not a 04.1 failure. |
| GitHub CI        | Phase 04: run 40246230098, 4/4, 701 tests (GitHub-verified). Phase 04.1: not run (uncommitted).                                                                                                                                                                                                                                                                                        |

## Verification record (Phase 04, observed locally before push)

**701 automated tests passed** (596 commerce + 57 control + 29 contracts + 16
shared UI + 3 Node), up from the accepted 463: 226 new commerce tests and 12 new
contract tests. Plus 7 live catalogue and 12 live authentication smoke checks.

| Check                     | Actual observation                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Commerce PostgreSQL suite | 596 passed (`pnpm api:test`). Phase 04 files: isolation 78, domain 58, API 19, pricing 16, inventory 31, concurrency 5, media 19. Phase 02/03 suites (tenancy, forged instances, auth, 03.1) unchanged and passing.                                                                                                                                                                                                                                                                      |
| Two-tenant isolation      | All 27 models unreachable from another tenant (read, update, delete, refresh, no context); every one of the 45 tenant foreign keys refused by PostgreSQL; deferred fields, relation traversal, forged save/delete refused; AST guard for the four new apps.                                                                                                                                                                                                                              |
| Concurrency               | Parallel committed transactions: no lost updates (gap-free balances), no overselling (5 of 8 reservations), decrements stop at zero, one operation for concurrent retries, opposite transfers without deadlock. With the row locks removed in a throwaway copy, 4 of these 5 tests fail (the idempotency test is also protected by its unique constraint).                                                                                                                               |
| Control PostgreSQL suite  | 57 passed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Lint/types                | `pnpm api:lint`: Ruff, Ruff format and strict mypy passed (commerce 157 files, control 52).                                                                                                                                                                                                                                                                                                                                                                                              |
| Frontend                  | `pnpm check` passed (Prettier, ESLint, strict typecheck, 29 contract + 16 UI + 3 Node tests). `pnpm build` passed for both apps with Webpack.                                                                                                                                                                                                                                                                                                                                            |
| Schema                    | `pnpm api:schema` validated both schemas without warnings; regenerated files byte-identical to the worktree (drift test green). 126 commerce operations.                                                                                                                                                                                                                                                                                                                                 |
| Migrations                | `python scripts/verify_auth_migrations.py`: fresh migrate, reverse Phase 04 and auth migrations, reapply, drift and system checks passed for both services on temporary databases (only those removed). Dev DB: five Phase 04 migrations applied on container start; `makemigrations --check`: no changes; `reconcile_inventory`: consistent.                                                                                                                                            |
| Live smoke                | `node scripts/catalog-smoke.mjs --local`: 7 checks through the Next.js proxy (anonymous denial, content editor without costs/team, typed options, byte-validated upload with SVG refused, product with prices/gallery/publication and cost refusal, idempotent receipt/oversell refusal/damage/reconciliation, public catalogue without costs or quantities and safe image serving, archive removal). Ran twice. `node scripts/auth-smoke.mjs --local`: 12 checks (Phase 03 regression). |
| Runtime                   | Only `matecart-dev` affected: image rebuilt with Pillow; `commerce-api` and `commerce-worker` recreated with the media volume; all eight services healthy. `pnpm api:celery-check` pong. `pnpm health` all six checks. `pnpm dev` running on 3400/3410.                                                                                                                                                                                                                                  |
| Preservation/secrets      | Tenant `dev-store` and store settings unchanged; existing 9 users, 5 memberships and 34 audit events intact before migration; new rows are labelled review data. Other projects' five containers kept IDs/exited states; networks unchanged; new volume `matecart-dev-commerce-media`. No `.env`/secret files changed; `git diff --check` passed.                                                                                                                                        |
| GitHub CI                 | Later verified by the owner and independent reviewer: run 40246230098 on `1bd61be`, 4/4 jobs, 701 tests.                                                                                                                                                                                                                                                                                                                                                                                 |

## Local review data

- Merchant review accounts: `review-owner@example.test` (Owner; use Forgot
  password and the newest Mailpit link) and `review-catalog-editor@example.test`
  (Content editor, re-keyed by each catalogue smoke run and by the storefront seed;
  reset by the same flow).
- Storefront review data (Phase 06, `node scripts/storefront-seed.mjs --local`, additive and
  idempotent): categories Home & Kitchen (Cookware, Lighting), Fashion, Groceries,
  Electronics, Books; brands Nova and Shapla Home; collection "New this week"; 10 published
  products with `SF-` SKUs, generated images, stock and Bangla names (one out of stock, one
  without an image, one pre-order book, one eBook); homepage sections; pages "Shipping
  information" and "About us"; a review announcement and tagline. The store stays General.
- Catalogue smoke runs leave archived "Review Smoke Shirt <run id>" products,
  review attributes, a review image and their ledger history. Merchant endpoints are
  documented in `services/commerce-api/openapi.yaml`; there is no catalogue UI yet
  (Phase 11).
- Storefront: http://127.0.0.1:3400 · merchant: http://127.0.0.1:3400/manage · console:
  http://127.0.0.1:3410.
- Mailpit http://127.0.0.1:18025 holds local messages only. No real provider is
  configured.

## Remaining scope, limits and next step

- **Next step:** owner review of the Phase 09.1 working tree, then owner commit/push, GitHub CI and
  independent review. Do not mark Phase 09 accepted or start Phase 10 before that review.
- **Courier provider decision needed:** no real provider is selected. External booking and provider
  callbacks remain unavailable until the owner approves an adapter, credentials and real sandbox
  verification; manual shipment and tracking operations remain available without one.
- **Owner decision needed for online payment:** choose a gateway (none is selected). Its adapter,
  secrets and real sandbox testing are the remaining provider-dependent Phase 08 acceptance items;
  until then online payment, provider refunds and EMI stay unavailable.
- Phase 08 limits (documented, not defects): guest order access covers the browser's latest guest
  order (`mc_order`); reservations stay location-level, so placement does not relocate stock
  between locations; orders with a zero total cannot be placed (no payment method applies);
  provider refund completion arrives only via the refund call (no refund callbacks yet); invoices
  have no QR, tax-ID or layout customisation; there is no merchant order UI (Phase 11) and no
  order emails (Phase 13).
  The managed theme marketplace (showcase, licensing, console assignment UI) remains deferred.
- Phase 06.2 limits (documented, not defects): the theme catalogue is kept in three places by
  design (API validation, shared identities, visual definitions), guarded by tests; assignment
  is a NetaMate command in the store's container until the console and deployment agent exist
  (16-17); the five variant names equal the five designs, so a future theme reuses them or
  adds a variant to the shared components; product information shown only by some designs
  (authors on literary cards, SKU on spec product pages, other-language names) is
  presentation, while capabilities are tested equal across themes of a preset.
- Phase 06 limits (documented, not defects): no "in stock only" filter or brand, author,
  publisher and format facet lists (the API has no facet counts; an exact stock filter would
  duplicate D-060 in SQL); Grocery cards do not show pack sizes (product pages do); images are served as uploaded (no resizing or `srcset`); HTML is not cached by shared
  caches, only media; the `<html>` element stays `lang="en"` while the store frame carries
  the customer's language.
- Phase 05 limits carried forward: social links are not modelled yet. Module states now gate
  implemented Phase 07 cart, checkout, slots and pre-order behavior.
- Phase 04.1 limits (documented, not defects): reservations are not batch-assigned, so
  sellable batch stock subtracts the location's whole reservation; batches expiring
  after a reservation can leave it uncovered until Phase 08 allocation; pre-04.1 keys
  are verified without the batch manufacture date; merchant views still show physical
  `available` (on-hand minus reserved), the public catalogue shows sellable stock.
- Known limits: no public catalogue rate limits/CDN, no image resizing or malware
  scanning (only images and PDFs accepted; PDFs download), public media may stay in
  shared caches for five minutes after unpublishing, order reservations are
  location-level with FEFO batch selection at dispatch (Phase 09 calls the handoff), no merchant catalogue UI (11).
- Carried forward: live Google/SMS/SMTP acceptance; production DB roles and proxy
  hardening (17); final security, load and recovery audit (18); font (Q1) and Redis
  licence (Q2) questions.
- Owner commits and pushes. No co-author trailers.
