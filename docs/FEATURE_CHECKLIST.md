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

## Commerce core

- [ ] Store modules, theme and branding; preset engine (05)
- [ ] Store domains and TLS (17)
- [ ] Customer, merchant-staff and central-admin sign-in (email and/or phone OTP); tenant-scoped RBAC; MFA; sessions (03)
- [ ] Catalogue, categories, variants, SKUs, pricing, stock movements (04)
- [ ] Preset-specific attributes: fashion variants and size guides; grocery weight, expiry, batch, delivery slots; electronics specs, warranty, serial numbers, comparison, EMI; bookstore ISBN, author, publisher, language, edition, genre, format, pages, series, pre-orders, digital editions (04, 05)
- [ ] Five presets with distinct visual identities; white-label branding; theming (05)
- [ ] Storefront: discovery, search, filters, product detail, responsive, bilingual EN/BN (06)
- [ ] Cart, guest checkout, addresses, coupons, shipping, taxes, delivery options, price calculation (07)
- [ ] Order state machine, stock reservation, payments, COD, refunds, callbacks, invoices (08)
- [ ] Courier integrations, tracking, COD reconciliation (09)
- [ ] Accounts, order history, reviews, returns, exchanges, warranties, support (10)
- [ ] Merchant dashboard: operations, products, orders, inventory, customers, reports, staff, settings (11)

## Reliability and scaling

- [ ] Horizontal scaling of web and API processes where infrastructure permits; stateless processes (17, 18)
- [ ] Redis caching and background workers used appropriately (ongoing; foundation done in 01)
- [ ] Rate limiting and bot protection (03, 12, 17)
- [ ] CDN compatibility (06, 17)
- [ ] Checkout and inventory protection against duplicate operations and overselling (07, 08)
- [ ] Resource-based scaling decisions; client-owned VPS limits respected (17, 18)
- [ ] Distinguish rolling 24-hour visitors from concurrent active users (12, 16)

## Traffic monitoring

- [ ] Rolling 24-hour visitor statistics in the merchant dashboard (12)
- [ ] Rolling 24-hour visitors per store in the Central Console (16)
- [ ] Console: recent active sessions, request rates, error rates, response times, database and queue health, resource utilisation (16)
- [ ] Capacity-aware alert thresholds per store, not one global visitor number (16)

## Email integrations (13)

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
