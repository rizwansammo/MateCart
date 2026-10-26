# Feature checklist

Long-term product scope with owning phase and status. Legend: `[x]` done and verified · `[~]` in progress · `[ ]` planned · `[-]` deferred (not to be built until approved).
Update the status in the same change that completes a feature.

## Platform foundation (Phase 01)

- [x] pnpm monorepo: `apps/commerce-web`, `apps/platform-console`, `packages/shared-ui`, `packages/shared-contracts`
- [x] Django + DRF services `commerce-api` and `control-api` with environment-based settings (base, development, test, production)
- [x] PostgreSQL connectivity, Redis cache, Celery app and worker (verified round-trip)
- [x] Health endpoints (liveness and readiness) for both APIs and both web apps
- [x] Request ids, structured logging, JSON error envelope, default-deny API permissions
- [x] Isolated Docker Compose stack `matecart-dev`
- [x] Lint, format, type-check, unit and integration tests; CI workflow (checks only)
- [x] OpenAPI schemas committed with drift test
- [x] Canonical documentation and AI handoff protocol

## Database and tenant architecture (Phase 02)

- [x] Domain blueprint and conventions documented (docs/DATA_MODEL.md)
- [x] UUIDv7 primary keys; timestamp, choice, uniqueness and index conventions
- [x] Tenant model, strict single-tenant runtime, configuration-only tenant resolution
- [x] Safe tenant provisioning command (atomic, validated, one tenant per database)
- [x] Fail-closed tenant-scoped data access, guarded writes, immutable ownership
- [x] Database-enforced same-tenant foreign keys; tenancy system checks
- [x] Tenant-aware Celery tasks, cache keys and storage paths
- [x] Store settings foundation (preset, country, currency, time zone, languages, contact)
- [x] Custom user models: per-tenant customer and staff realms (commerce), console administrators (control)
- [x] Append-only, transactional, redacted audit events
- [x] Two-tenant isolation tests at every implemented boundary

## Security hardening (Phase 02.1)

- [x] Tenant-qualified SQL for updates, deletes, refreshes, bulk updates and upserts (forged-instance bypass closed)
- [x] Forged-instance regression tests against PostgreSQL, including privileged access
- [x] Recursive audit sanitisation (nested keys, lists, secret-looking values, unsupported types)
- [x] Explicit audit field allowlists; provisioning audit without contact details
- [x] Identifier policy: generated keys UUIDv7, external ids any RFC 9562 UUID (nil/max rejected); `setup:env` generates UUIDv7
- [x] Exposed local development secret rotated; secret-printing rule added to AGENTS.md

## Tenant read isolation (Phase 02.2)

- [x] Refreshes governed by the active tenant (forged, foreign-loaded and caller-queryset reads refused; no context fails closed)
- [x] Scoped base manager: deferred fields, forward relations, generic relations and FK validation confined to the active tenant
- [x] Async refresh covered; protection verified on more than one tenant-owned model
- [x] System check E005 (scoped default and base managers); state-only migrations verified as no-ops

## Authentication and security (Phase 03)

- [x] Email/password customer registration, verification, sign-in, recovery, logout and profile
- [x] Phone-only OTP registration/sign-in and provider contract; controlled-provider tests
- [x] Google OIDC state/nonce/PKCE, signed tokens, stable subjects and safe account linking tests
- [x] Explicit UI gates for unconfigured Google/SMS; no fake live delivery
- [x] Separate customer, merchant and console credentials, cookies and authorization
- [x] Provisioning-only Owner activation; expiring single-use staff invitations
- [x] Tenant-scoped role permissions, owner-only team management, suspension/reactivation
- [x] Merchant optional MFA; console mandatory MFA and restricted initial enrollment
- [x] Guided TOTP QR/key reveal/confirmation; email OTP resend and expiry handling
- [x] Single-use hashed recovery codes, copy/download/confirmation and regeneration
- [x] Reauthentication, rate limits, cookie rotation, CSRF, session expiration/revocation
- [x] PostgreSQL ownership tests for all nine new commerce authentication models
- [x] Preserve forged-instance, refresh/deferred relation and append-only audit regressions
- [x] Local email capture, separate control Redis, auth response/logging protections
- [x] Customer account, `/manage` login/team/security and separate console account interfaces
- [x] Backend recent-auth enforcement for every Owner team mutation (03.1)
- [x] OIDC binding-before-consumption and safe Google provider/JWKS failures (03.1)
- [x] Phase 03 owner commit and GitHub CI run 40230926210 (4/4, 449 tests)
- [x] Phase 03.1 commit `5d6152a` and GitHub CI run 40237897132 (4/4, 463 tests)
- [ ] Live Google, SMS and external SMTP acceptance (requires genuine provider configuration)
- [x] Independent owner/security acceptance of Phases 03 and 03.1 (accepted and closed)

## Product and inventory engine (Phase 04)

- [x] Category tree (5 levels, cycle-safe moves), collections with explicit order, brands
- [x] Products with draft/active/archived lifecycle and publication checks; archive instead of delete
- [x] Variants with typed option combinations, store-unique SKUs, GTIN barcodes, ISBN-13 per format
- [x] Typed attributes (text, number, boolean, option, measurement) for variants and specifications
- [x] Preset product types: apparel (material, fit, size guide), grocery (pack size, perishables), electronics (model, warranty policies), books (authors, publisher, edition, language, series, formats, eBook, pre-order date), general
- [x] Price lists and prices: Decimal per-currency precision, compare-at, zero prices, private cost prices (`catalog.costs`)
- [x] Stock locations, levels with generated availability, append-only ledger with reversals
- [x] Locked, idempotent stock operations: receipt, adjustment, damaged, lost, returned, transfer
- [x] Reservation, release and fulfilment service extension points (API exposure with Phases 07/08)
- [x] Batches with expiry and FEFO listing; store-unique serial numbers with one location
- [x] Media uploads validated from bytes, tenant-namespaced storage, gated serving
- [x] Merchant catalogue/pricing/inventory/media APIs; public read-only catalogue API; OpenAPI and shared contracts
- [x] Two-tenant isolation, concurrency, ledger, media and API tests; live catalogue smoke checks
- [x] Phase 04 owner commit `1bd61be` and GitHub CI run 40246230098 (4/4, 701 tests)
- [x] Idempotency keys bound to a durable request fingerprint; historical keys verified from the ledger (04.1)
- [x] Expired batch stock excluded from sellable stock, purchasability and reservations (04.1)
- [x] Phase 04.1 commit `8290fe4` and GitHub CI run 40249765179 (4/4, 735 tests); Phase 04 accepted and closed

## Presets, themes and branding (Phase 05)

- [x] Typed definitions of the five preset identities (palette, typography, layout preferences)
- [x] Scoped `store-*` theme tokens, server-renderable theme scope, General fallback
- [x] Instrument Serif and Newsreader self-hosted; Optimus still excluded pending licence (Q1)
- [x] Store branding: colours with readable-text validation, logos and favicon via the media pipeline, brand texts, Bangla
- [x] Store module registry and overrides; planned modules locked off; prerequisites enforced
- [x] Plain-text pages (draft/published) and ordered homepage sections with preset defaults
- [x] Merchant appearance and content screens; public storefront configuration API; contracts
- [x] Controlled business preset change command (refuses incompatible products)
- [x] Authentication smoke test closed (12/12, carried from Phase 04.1)
- [x] Phase 05 commit `e3810cb` and GitHub CI run 40297666311 (4/4, 807 tests)
- [x] Published pages cannot be emptied; drafts may be empty (05.1)
- [x] Homepage sections need a title to be shown; new sections start hidden (05.1)
- [x] Translation fields follow the store's non-default language (English or Bangla) (05.1)
- [x] Confirmation before permanently deleting pages and sections (05.1)
- [x] Phase 05.1 commit `613cd49` and GitHub CI run 40301529264 (4/4, 827 tests); Phase 05 accepted and closed

## Customer storefront (Phase 06)

- [x] Store homepage replaces the developer page; Phase 05 sections in saved order; empty sections left out
- [x] Store layout: announcement, preset headers, search, category navigation, account link, mobile drawer, footer
- [x] Five preset experiences from one implementation (headers, cards, grids, type, heroes, sections)
- [x] All products, search, category and collection listings with counts, sort, pagination and URL state
- [x] Filters: category tree, price range, brand, product type (General); active-filter chips
- [x] Search by name, tag, brand, book author and ISBN-13 (narrow public API extension)
- [x] Product page: gallery, variant options, variant price, compare-at, stock, pre-order, digital format, ISBN, type details, related products
- [x] Published information and policy pages; store 404 and error pages
- [x] English and Bangla interface and content with fallback; language kept in a first-party cookie
- [x] Page titles, descriptions, sharing basics; development stores not indexable
- [x] Honest purchase state: "Add to cart" disabled until Phase 07
- [x] Storefront tests (commerce-web Vitest), five-preset desktop and mobile smoke, review seed data
- [ ] Owner commit, GitHub CI and independent acceptance of Phase 06
- [ ] Deferred from Phase 06: "in stock only" filter and facet lists (brand, author, publisher, format); pack sizes on Grocery cards; responsive image sizes and shared HTML caching (12/17)

## Carried into Phases 07 and 08 (from Phase 04.1, not implemented yet)

- [ ] Phase 07: validate batch expiry when checkout reserves stock
- [ ] Phase 08: revalidate batch expiry before order allocation and fulfilment
- [ ] Phase 08: prefer an eligible unexpired batch when reserved stock becomes unusable
- [ ] Phase 08: handle insufficient replacement stock through an explicit order resolution state
- [ ] Phase 08: never fulfil expired goods
- [ ] Phase 08: keep reservations, stock and orders consistent; prevent duplicate allocation and deduction

## Commerce core

- [x] Store modules, theme and branding; preset engine (05)
- [ ] Store domains and TLS (17)
- [x] Customer, merchant-staff and central-admin sign-in foundations, tenant-scoped RBAC, MFA and sessions (03; provider limits above)
- [x] Catalogue, categories, variants, SKUs, pricing, stock movements (04)
- [~] Preset-specific attributes (data in 04, storefront presentation in 06; delivery slots, comparison and EMI remain): fashion variants and size guides; grocery weight, expiry, batch, delivery slots; electronics specs, warranty, serial numbers, comparison, EMI; bookstore ISBN, author, publisher, language, edition, genre, format, pages, series, pre-orders, digital editions (04, 05)
- [x] Five presets with distinct visual identities; white-label branding; theming (05; storefront rendering 06)
- [x] Storefront: discovery, search, filters, product detail, responsive, bilingual EN/BN (06; acceptance pending)
- [ ] Cart, guest checkout, addresses, coupons, shipping, taxes, delivery options, price calculation, batch-expiry check at reservation (07)
- [ ] Order state machine, stock reservation, expiry-safe batch allocation, payments, COD, refunds, callbacks, invoices (08)
- [ ] Courier integrations, tracking, COD reconciliation (09)
- [ ] Accounts, order history, reviews, returns, exchanges, warranties, support (10)
- [ ] Merchant dashboard: operations, products, orders, inventory, customers, reports, staff, settings (11)

## Reliability and scaling

- [ ] Horizontal scaling of web and API processes where infrastructure permits; stateless processes (17, 18)
- [ ] Redis caching and background workers used appropriately (ongoing; foundation done in 01)
- [x] Authentication rate-limiting foundation (03)
- [ ] General bot protection and edge limits (12, 17, 18)
- [~] CDN compatibility (06: per-request HTML, cacheable public media; shared HTML caching 17)
- [ ] Checkout and inventory protection against duplicate operations and overselling (07, 08)
- [ ] Resource-based scaling decisions; client-owned VPS limits respected (17, 18)
- [ ] Distinguish rolling 24-hour visitors from concurrent active users (12, 16)

## Traffic monitoring

- [ ] Rolling 24-hour visitor statistics in the merchant dashboard (12)
- [ ] Rolling 24-hour visitors per store in the Central Console (16)
- [ ] Console: recent active sessions, request rates, error rates, response times, database and queue health, resource utilisation (16)
- [ ] Capacity-aware alert thresholds per store, not one global visitor number (16)

## Email integrations (13)

Authentication-only SMTP abstraction and local Mailpit are implemented in 03.
Full notification delivery, queues/retries, native MateMail and masking SMS remain below.

- [ ] Managed masking SMS gateway and approved store sender identities (13)
- [ ] Per-store SMS accounting: included allowance before prepaid credits (future billing scope; no recharge/purchase UI now)

- [ ] Standard SMTP and IMAP connection
- [ ] Native MateMail Connected App with properly scoped tenant and application credentials
- [ ] Audit of MateMail native API capabilities, implemented where necessary. An existing tenant id and secret is not assumed to be a secure integration API.
- [ ] Merchant and NetaMate administrative email configurations isolated

## Notifications (13; console alerts 16)

Merchant-side:

- [ ] New orders · order confirmations · payment success and failure · order status changes
- [ ] Shipment and delivery updates · refunds and returns · low stock · suspicious COD orders
- [ ] Customer messages and reviews · staff and account security notifications
- [ ] Optional sales summaries and consent-based marketing reminders

Central Console alerts:

- [ ] Deployment failures · server or storefront downtime · traffic spikes · capacity warnings
- [ ] High response times or error rates · database and background worker failures · backup failures
- [ ] SSL expiry · security incidents · payment and courier integration failures
- [ ] Deployment agent disconnections · recovery notifications

Delivery requirements:

- [ ] Asynchronous delivery, retries, failure logging, deduplication
- [ ] Independent escalation path for critical infrastructure alerts

## Growth, social and AI

- [ ] Campaigns, loyalty, SEO, analytics, Meta Pixel and CAPI, consent management (12)
- [ ] Facebook, Messenger, WhatsApp, website chat; unified inbox; social commerce (14)
- [ ] Provider-independent AI layer, GroqCloud first; product content, image analysis, support replies, marketing, BI (15)

## Other pending features (must not be dropped)

- [ ] Delivery address selection and order recipient snapshots (07/08)
- [ ] Multiple customer addresses and a default address, separate recipient phone/name (10)

- [ ] Consistent customer and merchant order synchronisation (08, 10, 11)
- [ ] Product publishing and stock synchronisation (04, 11)
- [ ] Complete bookstore-specific merchant workflows (04, 05, 11)
- [ ] Fake COD order prevention and courier return risk analysis (09)
- [ ] WooCommerce and Shopify migration; bulk product import (15)
- [ ] Secure digital download delivery and buyer libraries (10)
- [ ] Advanced invoice customisation and QR support (08)
- [ ] Privacy consent and advanced marketing tracking (12)
- [ ] Reliable cross-module reporting (11)
- [ ] Full production-quality type safety and tests (ongoing; enforced from 01; verified in 18)

## Platform and operations

- [ ] Production PostgreSQL runtime/migration role separation and privilege hardening (17)
- [ ] Final Django private-hook compatibility and tenant/authentication audit (18)

- [ ] Central Console: client registry, onboarding, store configuration, preset selection, feature and module configuration, server registry (16)
- [ ] Deployment orchestration, release and update management, domain and SSL workflows, integration provisioning (16, 17)
- [ ] Backup and recovery status; secure support and administrative operations (16, 17, 18)
- [ ] GHCR releases, dedicated deployments, client-owned VPS installer, deployment agent (17)
- [ ] Tenant isolation verification, load tests, vulnerability assessment, backup and restore drills (18)
- [ ] Production deployment and private beta launch (19)

## Deferred (do not build until approved)

- [-] Public self-service signup
- [-] Subscription checkout and automated package purchasing
- [-] Commercial billing
- [-] Final licence pricing
- [-] Shared multi-tenant SaaS hosting mode (architecture keeps it possible; not built for the first release)
