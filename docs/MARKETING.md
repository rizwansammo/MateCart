# Marketing, SEO and analytics (Phase 12)

How campaigns, flash sales, coupons, loyalty points, SEO, consent, first-party analytics and the
Meta integration work in a MateCart store. Decisions: [DECISIONS.md](DECISIONS.md) D-081 to
D-085. Schema: [DATA_MODEL.md](DATA_MODEL.md) "Phase 12". Code: `services/commerce-api/commerce/marketing/`.

Everything lives in the store's own deployment and database. Nothing is sent to the NetaMate
Central Console. External tracking is off until the merchant turns it on, and even then it runs
only for visitors who gave the matching consent.

## 1. Promotions (D-081)

### Campaigns

A campaign has a name, a window (`starts_at` to `ends_at`, entered in the store's time zone and
stored in UTC, at most 366 days) and targets: products, categories (including all their
subcategories) and/or collections, at most 500 targets.

| Kind       | Effect                                                                                 |
| ---------- | -------------------------------------------------------------------------------------- |
| `discount` | Percentage (above 0 and below 100, two decimals) or fixed amount in the store currency |
| `featured` | A badge only; the price does not change                                                |

Lifecycle (stored): `draft` → `active` ↔ `paused` → `cancelled`. What customers see is derived
from the stored status and the **server clock**: `scheduled` (active, not started), `live`
(active, in its window), `ended` (window passed). The browser never sets the state. A live,
ended or cancelled promotion cannot be edited; pause it first. Every create, update, activate,
pause and cancel writes an audit event.

### Flash sales

A flash sale lists variants with a fixed sale price in the store currency (strictly below the
regular price), an optional total unit limit and an optional per-order limit. A flash sale runs
for at most 14 days. A variant cannot be in two active flash sales whose windows overlap
(checked under a store-wide lock at activation). Flash sales work only while the `flash_sales`
module is on; switching the module off removes flash prices at once, everywhere.

### Precedence (one promotion per item, never stacked)

For each variant, at the current server time:

1. A live flash sale item, when its price is below the regular price, its unit limit has room
   for the requested quantity and the quantity is within its per-order limit.
2. Otherwise the live discount campaign giving the lowest price (ties: earliest start, then id).
   A campaign that would bring a price to zero or below does not apply to that variant. A fixed
   campaign applies only to prices in its currency.
3. Otherwise a live featured campaign adds its badge; the price is unchanged.

Promotional prices are rounded half-up to the currency's minor unit.

### Coupons and stacking

Coupons are the Phase 07 coupons, unchanged in calculation and redemption. Merchants can now
create and maintain them in `/manage/marketing/coupons`. A coupon's code never changes. Once a
coupon has been used, its value and minimum are fixed; it can still be paused, given a new end
time or a higher usage limit.

**A line sold at a promotional price never takes a coupon as well.** A coupon discounts only
the regular-price lines (a fixed coupon is capped at their total). A cart where every line is
on sale refuses the coupon with a clear message. A cart with no promotional lines is calculated
exactly as before Phase 12.

### Checkout and orders

`pricing.quote()` returns the selling price, so cart, checkout review, merchant-assisted orders
and order placement all use the same engine. Each checkout line and order item records the
regular price and the promotion (kind, id, name). The existing review rules apply unchanged:

- a promotion that starts, ends or changes between the cart and review produces the normal
  `price_changed` review response, and the customer sees the new total before placing;
- after review, the reviewed price is kept for the 15-minute reservation (as for any price);
- flash sale units are committed at placement under the item's row lock. When a limited sale
  sold out in the meantime, placement is refused with `promotion_unavailable`, the stock hold is
  released and the customer reviews again at the current price;
- cancellation gives the units back exactly once (`FlashSaleUsage` status);
- order items are immutable, so later promotion changes never alter an existing order.

Campaign and flash sale results (orders, units, sales, discount given) come from immutable order
items of orders that were not cancelled.

## 2. Loyalty points (D-082)

| Setting           | Meaning                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| Earning           | `earn_points` for every `earn_spend` of item value after discounts (delivery and tax excluded), rounded down |
| Point value       | Each point is worth `point_value` in the store currency at checkout                                          |
| Minimum           | At least `min_redeem_points` per use                                                                         |
| Cap               | At most `max_redeem_percent` of the items after the coupon                                                   |
| Expiry (optional) | The whole balance expires after `expiry_days` (at least 30) without earning or redeeming                     |

Defaults: off; 1 point per 100; 1 point = 1 unit of currency; minimum 100; cap 50 %; no expiry.
The `loyalty` module and the program switch must both be on.

**Who takes part.** Signed-in customers with a verified email or phone. Guest orders and
merchant-assisted orders (which belong to no account) never earn. Accounts are never matched by
email or phone.

**When points count.** At placement the order gets a pending award, fixed from its totals and
the rule at that moment. Points become usable only when the order is **paid in full** (a verified
online payment or recorded cash) **and delivered** (shipment marked delivered; digital-only
orders once paid). A promised award follows its order even if the merchant later turns the
program off.

**Idempotent sync.** `loyalty.sync_order()` runs after cancellation, payment, refund and delivery.
It is target-based: the earned points always converge on
`floor(award × (1 − refunded / paid))`, and 0 for a cancelled order. Each ledger change has a
unique business reference (`earn:<order>`, `redeem:<order>`, `restore:<order>`,
`reverse:<order>:<target>`, `adjust:<key>`), so retries and duplicate callbacks cannot apply a
change twice.

**Redemption.** The customer chooses points at checkout; the value is part of the order
discount and reduces the taxable item value like a coupon. Placement debits the points under the
account lock. A balance that dropped after review refuses placement with `loyalty_unavailable`
and releases the stock hold. Redeemed points come back once when the order is cancelled or fully
refunded; partial refunds do not restore them.

**Refunds after points were spent.** A reversal never makes the balance negative. The part that
was already spent becomes an explicit `deficit`, recorded in the ledger, and later earnings
repay it first. Already-redeemed points can never be used again.

**Staff adjustments** need `promotions.manage` and `customers.view`, a recent sign-in, a reason
and an idempotency key. A deduction cannot exceed the usable balance. Each adjustment is audited.

The ledger is append-only (ORM guard and PostgreSQL trigger). Points have no cash value and
cannot be transferred.

## 3. SEO (D-085)

**Indexing** is allowed only when all three hold:

1. the deployment opts in: `STOREFRONT_INDEXING=allow` on the web app;
2. the deployment has a trusted public HTTPS address: `STOREFRONT_PUBLIC_URL` (never localhost,
   never taken from the request's Host header);
3. the merchant has not turned indexing off (`/manage/marketing/seo`, needs `store.settings`).

Otherwise every page is `noindex`, `robots.txt` disallows everything and the sitemap is empty.
Development and preview environments therefore stay private. Domain verification and TLS remain
Phase 17.

**Metadata.** The store's home title and description, a default social image and per-item
overrides (products in the product editor; categories, collections and pages in the SEO page).
Overrides are plain text (≤ 70 and ≤ 320 characters, `<` and `>` refused) and apply to the
store's default language; other languages use the translated names and descriptions. Canonical
URLs, Open Graph and Twitter cards use only the trusted public origin.

**Languages.** The storefront chooses English or Bangla by cookie on a single URL per page, so
there are no alternate-language URLs to advertise. MateCart emits no `hreflang`, and the
canonical URL is the page's one URL.

**Sitemap and robots.** The sitemap index lists one pages segment and product segments of 5,000
URLs each, built from published, active records only. It never lists drafts, archived items,
accounts, carts, checkout, orders, digital downloads, the dashboard or the API. `robots.txt`
disallows those paths.

**Structured data.** JSON-LD for Organization/WebSite, Product with its real offer (selling
price, currency, availability from real stock or pre-order dates, `priceValidUntil` when a
promotion ends), BreadcrumbList and ItemList where useful. A rating appears only when there are
published reviews. Values come from real records; `<` is escaped so merchant text cannot close
the script element.

## 4. Consent (D-083)

| Category           | Covers                                                                          | Default   |
| ------------------ | ------------------------------------------------------------------------------- | --------- |
| Strictly necessary | Sign-in, cart, checkout, security, the consent cookie                           | Always on |
| Analytics          | Visitor id (`mc_vid`), campaign source (`mc_src`), unique visitors, attribution | Off       |
| Marketing          | Meta Pixel, Conversions API, `_fbp`/`_fbc`                                      | Off       |

The banner offers Accept all, Reject optional and Manage preferences with equal prominence, and
can be reopened from the footer. Each decision is posted to `POST /api/consent/`, recorded in
`ConsentRecord` (the SHA-256 of a random consent id; no IP, user agent or account) and kept in
the `mc_consent` cookie (`1:<a><m>:<id>`, 180 days, readable by page scripts so they can respect
it).

**Enforcement.**

- Without marketing consent the Pixel script is never requested and no server event is queued.
- Without analytics consent no visitor id or source cookie is created and a sent id is ignored.
- Withdrawing consent removes the cookies, discards queued server events and deletes the
  visitor's presence row.

Consent is never needed for shopping. A banner by itself does not make a store compliant with
every jurisdiction; merchants remain responsible for their privacy notice.

## 5. First-party analytics (D-083)

| Metric                                          | Definition                                                                                                                                                                                                |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Page views; product, category, collection views | Storefront page loads reported by the browser, all visitors, hourly aggregates without identifiers                                                                                                        |
| Add to cart, checkout started                   | Counted by the server when the cart/checkout API succeeds                                                                                                                                                 |
| Unique visitors                                 | Consented visitors only. Last 24 hours: seen in exactly the last 24 hours. Day ranges: the sum of each store day's unique visitors (returning on another day counts again)                                |
| Converting visitors, conversion rate            | Consented visitors who placed a storefront order ÷ consented unique visitors                                                                                                                              |
| Consent coverage                                | Consented page views ÷ all page views                                                                                                                                                                     |
| Counter windows                                 | "Last 24 hours" counters cover the current hour and the 23 before it                                                                                                                                      |
| Orders, order value, collected                  | From order and payment records only, never browser events. Storefront and merchant-assisted orders are separate                                                                                           |
| Traffic sources                                 | UTM source/medium/campaign of the landing URL (consented visitors), last tagged visit within 30 days. A tracked channel, not proof of causation or payment. Merchant-assisted orders are never attributed |

**Integrity.**

- Events come from a strict allowlist and are validated: product, category and collection ids
  must be active records.
- No monetary values are accepted from the browser.
- Bots are ignored.
- Each IP is limited to 120 events a minute.
- Invented campaign tags fold into "other" after 200 distinct sources in an hour.
- Analytics failures never block a cart or checkout.

**Storage.** `EventCounter` holds hourly aggregates. `VisitorPresence` holds one row per consented
visitor (a keyed hash of the cookie id plus the last-seen and last-order times) and is kept for 48
hours. `VisitorDailyStat` holds per-day uniques. MateCart stores no IP addresses, fingerprints or
browsing histories.

**Retention.**

| Data                          | Kept for |
| ----------------------------- | -------- |
| Presence rows                 | 48 hours |
| Counters and daily stats      | 400 days |
| Consent records               | 2 years  |
| Finished Conversions API rows | 30 days  |

There is no scheduler. Clean-up is queued at most once an hour by storefront traffic, and
operators can run `manage.py prune_marketing_data`.

## 6. Meta Pixel and Conversions API (D-084)

**Merchant settings** (`/manage/marketing/tracking`, `store.settings`):

- Meta on/off and a Pixel id (5–20 digits).
- Server events on/off.
- Hashed contact matching (off by default).
- Purchase timing.
- An optional Meta test event code.

**Browser Pixel.** Loaded only when Meta is on, a Pixel id is set and the visitor gave marketing
consent. It sends PageView once per navigation, ViewContent, AddToCart (server-confirmed cart
values) and InitiateCheckout. The browser Purchase event uses
`eventID = purchase.<order id>` and fires once per order.

**Purchase timing.**

- `payment_verified` (default): Purchase is sent when the order is paid in full (verified online
  payment or recorded cash).
- `order_placed`: Purchase is sent at placement. A cash-on-delivery order is then reported with
  `payment_status: "unpaid"` and is never described as paid.

**Conversions API.** One server event, `Purchase`, with the same event id as the browser, so Meta
deduplicates them.

- It is queued only for storefront orders whose visitor had marketing consent at placement.
- It is discarded if consent is withdrawn or the integration is turned off before sending.
- It is sent by a Celery task outside database locks, with at most five attempts (backoff for
  network, 429 and 5xx; no retry for other 4xx).
- A delivery failure never affects the order.

**Data sent.** The event body is built at send time and never stored. It contains:

- currency, value, product ids, item count, order number and payment status;
- `fbp`/`fbc` and the user agent, when present. These are kept only until the event finishes;
- SHA-256 hashed email and phone, only when the merchant enables hashed matching.

**Credentials.** `META_CAPI_ACCESS_TOKEN` and the public HTTPS address
(`MATECART_PUBLIC_BASE_URL`) are deployment settings managed by NetaMate. The token is never
stored in the database, returned by an API, shown in the dashboard, written to audit or logged.
`META_GRAPH_API_VERSION` defaults to `v26.0`.

**Local development** has no token, so server events stay unavailable and nothing is sent to
Meta. Tests replace the client with a fake. Live delivery is a deployment acceptance step and
has not been exercised.

## 7. Permissions

| Area                                           | Permission                                                          | Roles                          |
| ---------------------------------------------- | ------------------------------------------------------------------- | ------------------------------ |
| Campaigns, flash sales, coupons, loyalty rules | `promotions.manage`                                                 | Owner, Manager                 |
| Loyalty members and adjustments                | `promotions.manage` + `customers.view` (+ recent sign-in to adjust) | Owner, Manager                 |
| SEO text and overrides                         | `content.manage`                                                    | Owner, Manager, Content editor |
| Search indexing switch                         | `store.settings`                                                    | Owner, Manager                 |
| Marketing analytics, 24-hour visitors          | `reports.view`                                                      | Owner, Manager                 |
| Tracking and consent settings                  | `store.settings`                                                    | Owner, Manager                 |

Every endpoint checks its permission on the server; hiding navigation is not authorization.
