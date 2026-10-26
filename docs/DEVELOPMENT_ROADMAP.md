# Development roadmap

19 approved phases in five stages. Only the active phase may be worked on. The next phase starts only after the owner approves the previous one. The current state is in [PROGRESS.md](PROGRESS.md).

## Status board

| #   | Phase                                       | Stage                         | Status                                                                                                                                    |
| --- | ------------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | Project Foundation                          | A — Foundation                | **Complete — accepted** (`8f3e34b`, CI passed 4/4)                                                                                        |
| 02  | Database & Tenant Architecture              | A — Foundation                | **Accepted and closed**, including 02.1/02.2 (`aceae6f`, CI 40213680998: 4/4, 260 tests).                                                 |
| 03  | Authentication & Security                   | A — Foundation                | **Accepted and closed**, including 03.1 (`5d6152a`, CI 40237897132: 4/4, 463 tests).                                                      |
| 04  | Product & Inventory Engine                  | A — Foundation                | **Accepted and closed**, including 04.1 (`8290fe4`, CI 40249765179: 4/4, 735 tests).                                                      |
| 05  | Presets, Themes & Branding                  | B — Commerce Application      | **Accepted and closed**, including 05.1 (`613cd49`, CI 40301529264: 4/4, 827 tests).                                                      |
| 06  | Customer Storefront                         | B — Commerce Application      | **Accepted and closed**, including 06.1 (`df0490f`, CI 40315255063: 4/4, 913 tests); supplementary 06.2 complete locally, review pending. |
| 07  | Cart & Checkout                             | B — Commerce Application      | Pending                                                                                                                                   |
| 08  | Orders, Payments & Invoices                 | B — Commerce Application      | Pending                                                                                                                                   |
| 09  | Fulfillment & Courier                       | B — Commerce Application      | Pending                                                                                                                                   |
| 10  | Customer Accounts & After-sales             | B — Commerce Application      | Pending                                                                                                                                   |
| 11  | Merchant Management Dashboard               | B — Commerce Application      | Pending                                                                                                                                   |
| 12  | Marketing, SEO & Analytics                  | C — Growth & Integrations     | Pending                                                                                                                                   |
| 13  | Mail & Notification Engine                  | C — Growth & Integrations     | Pending                                                                                                                                   |
| 14  | Social Commerce & Unified Inbox             | C — Growth & Integrations     | Pending                                                                                                                                   |
| 15  | AI & Advanced Extensions                    | C — Growth & Integrations     | Pending                                                                                                                                   |
| 16  | Central Platform Console                    | D — Platform & Infrastructure | Pending                                                                                                                                   |
| 17  | Deployment Automation                       | D — Platform & Infrastructure | Pending                                                                                                                                   |
| 18  | Final Quality, Performance & Security       | D — Platform & Infrastructure | Pending                                                                                                                                   |
| 19  | Production Deployment & Private Beta Launch | E — Production Launch         | Pending (never before 01–18 are accepted)                                                                                                 |

| Standing item                          | Status                                     |
| -------------------------------------- | ------------------------------------------ |
| Public signup / subscription / billing | **Deferred**                               |
| Initial deployment model               | **Single tenant**                          |
| Future architecture                    | **Multi-tenant ready / hybrid**            |
| UI prototype (MateCart-UI)             | **Strictly read-only**                     |
| Business model                         | **Private beta, manual client onboarding** |

## Stage A — Foundation

### Phase 01 — Project Foundation

Repository architecture; Next.js, Django, PostgreSQL, Redis and Celery foundations; isolated local Docker environment; coding standards; tests; CI foundation; documentation.

Quality gates (all must hold):

1. Project structure is coherent and documented.
2. Local dependencies are configured.
3. The dedicated MateCart Docker environment works.
4. Existing unrelated Docker applications remain untouched.
5. Frontend foundation applications run successfully.
6. Backend foundations start and respond to health checks.
7. Database and Redis connections are verified.
8. Celery's basic configuration is validated.
9. Baseline test, lint, type-check and build commands work.
10. No secrets are committed.
11. Architecture and roadmap documents exist.
12. Claude/Codex handoff instructions are ready.
13. Known issues and unresolved decisions are documented.

Results: [PROGRESS.md](PROGRESS.md).

### Phase 02 — Database & Tenant Architecture

Production domain modelling, tenant awareness, isolation boundaries, configuration model, schema design, migrations and auditing foundations.
Decided and implemented: UUIDv7 keys, fail-closed tenant scoping with database-enforced tenant foreign keys (RLS deferred to pooled mode), strict single-tenant runtime and provisioning, store settings, the commerce and console user models, append-only audit events, and the domain blueprint ([DATA_MODEL.md](DATA_MODEL.md), D-023 to D-033). Results: [PROGRESS.md](PROGRESS.md).

#### Phase 02.1 — Security hardening and audit fixes (corrective subphase)

Independent review of Phase 02. Closed a confirmed forged-instance tenant-isolation bypass (tenant-qualified SQL writes), nested secrets and contact details in audit payloads (allowlists plus recursive sanitisation), clarified the identifier policy, and rotated an exposed local development secret. No schema changes; the 19-phase roadmap is unchanged. Details: D-034 to D-037 and PROGRESS.md.

#### Phase 02.2 — Tenant read isolation (final corrective subphase)

Closed a confirmed read-isolation gap: refreshes, deferred fields and relation traversal could read another tenant's row from an untrusted instance or queryset. The base manager is now the scoped manager and refreshes follow the active tenant (D-038). Three state-only migrations; no SQL, no data changes. The 19-phase roadmap is unchanged.

### Phase 03 — Authentication & Security

Customer, merchant and central administrator authentication (separate boundaries); RBAC and staff permissions; MFA; session management; secure APIs; CORS and CSRF strategy; rate limiting foundations.

#### Phase 03.1 — Authentication security and final closeout (corrective subphase)

Independent review corrections: enforce recent password confirmation on every
Owner team mutation at the backend, validate Google browser binding before OIDC
state consumption, and normalize expected Google provider/JWKS failures without
hiding programmer errors. Accepted and closed with Phase 03: commit `5d6152a`,
GitHub Actions run 40237897132 (4/4 jobs, 463 tests). The 19 main phases are
unchanged.

### Phase 04 — Product & Inventory Engine

Catalogue, categories, variants, SKUs, stock movements, pricing, and preset-specific product data (fashion variants, grocery weight, expiry and batch, electronics specs, warranty and serials, bookstore ISBN and edition data).

Implemented (D-050 to D-058) and pushed as `1bd61be` (GitHub Actions run 40246230098:
4/4 jobs, 701 tests): one catalogue engine with typed preset details, price lists, an
append-only inventory ledger with locked idempotent operations, batches and serials,
validated media, merchant APIs and a public read-only catalogue API. Reservations are service
extension points for Phases 07/08. Results: [PROGRESS.md](PROGRESS.md).

#### Phase 04.1 — Inventory integrity and security closeout (corrective subphase)

Independent post-push review corrections: idempotency keys bound to a durable request
fingerprint (D-059) and expiry-aware sellable stock across the catalogue, purchasability and
reservations (D-060), with a focused integrity review and regression tests. Accepted and
closed with Phase 04: commit `8290fe4`, GitHub Actions run 40249765179 (4/4 jobs, 735 tests).
The 19 main phases are unchanged.

## Stage B — Commerce Application

### Phase 05 — Presets, Themes & Branding

All five presets with the prototype's distinct visual identities; white-label branding; theming and token system per preset; content management foundations.

Accepted and closed with corrective subphase 05.1 (D-061 to D-064): typed preset definitions
and scoped theme tokens, store branding with validated colours and media-pipeline logos, a
module registry with planned modules locked off, plain-text pages and homepage sections,
merchant appearance and content screens, and a public storefront configuration API. 05.1
kept live content complete, followed the store's non-default language for translations and
confirmed permanent deletions. Commit `613cd49`, GitHub Actions run 40301529264 (4/4, 827 tests).

### Phase 06 — Customer Storefront

Production storefronts matching the approved prototype: discovery, search, filters, product detail, responsive layouts, bilingual (English and Bangla) support.

Implemented locally (D-065 to D-067): server-rendered storefront pages for all five presets
from one implementation, homepage from the Phase 05 sections, URL-driven discovery over the
public catalogue (with a narrow price-range and search extension), product pages with variant
choice, English and Bangla with fallback, and honest non-functional purchase controls until
Phase 07. Pushed as `46c2e1a` (GitHub Actions run 40311886344, 4/4, 894 tests). Corrective
subphase 06.1 (D-068): search by translated product names, store-local dates for pre-order
labels and a homepage fallback whenever no section can render. Accepted and closed:
commit `df0490f` (GitHub Actions run 40315255063, 4/4, 913 tests).

**Phase 06.2 — Multi Theme Architecture Readiness** (supplementary subphase before Phase 07,
not a new main phase; D-069): business presets and visual themes separated, a typed registry
with the five approved designs as default themes, a platform-owned active theme per store,
safe resolution, presentation extension points with functional parity between themes, and a
trusted audited assignment command. The managed theme marketplace (showcase, licences, console
assignment) is later work. Results: [PROGRESS.md](PROGRESS.md).

### Phase 07 — Cart & Checkout

Cart, guest checkout, addresses, coupons, shipping, taxes, delivery options and slots, price calculation. Duplicate-submission and overselling protection.

### Phase 08 — Orders, Payments & Invoices

Order state machine, stock reservation, payments (gateway integrations), COD, refunds, payment callbacks and idempotency, invoice generation.

### Phase 09 — Fulfillment & Courier

Courier integrations, shipping operations, tracking, COD reconciliation, fake-order prevention and fraud review.

### Phase 10 — Customer Accounts & After-sales

Accounts, order history, reviews, returns, exchanges, warranties, support, digital product fulfilment and buyer libraries.

### Phase 11 — Merchant Management Dashboard

Production merchant interface: operations, products, orders, inventory, customers, reports, staff and settings.

## Stage C — Growth & Integrations

### Phase 12 — Marketing, SEO & Analytics

Campaigns, loyalty, analytics, SEO, Meta Pixel and CAPI, tracking, consent management, rolling 24-hour visitor metrics.

### Phase 13 — Mail & Notification Engine

SMTP and IMAP, the MateMail native connected app, transactional email, templates, queues, retries, in-app notifications, notification logs.

### Phase 14 — Social Commerce & Unified Inbox

Facebook, Messenger, WhatsApp, website chat, social commerce, unified customer communications.

### Phase 15 — AI & Advanced Extensions

Provider-independent AI layer (GroqCloud first), product content, image analysis, support replies, marketing, business intelligence, advanced import and export, WooCommerce and Shopify migrations.

## Stage D — Platform & Infrastructure

### Phase 16 — Central Platform Console

Internal console: client registry, store configuration, operational dashboards, permissions, management workflows, fleet monitoring and alerts.

### Phase 17 — Deployment Automation

Docker releases, GHCR, dedicated deployments, client-owned VPS deployment, SSH installer, secure deployment agent, domain and SSL automation, health checks, controlled updates, production reverse proxy and images.

### Phase 18 — Final Quality, Performance & Security

Comprehensive testing, vulnerability assessment, tenant isolation verification, load testing, scalability validation, monitoring, backup and restore, recovery testing, release readiness.

## Stage E — Production Launch

### Phase 19 — Production Deployment & Private Beta Launch

Deploy the platform, Central Console and initial client stores; monitoring and backups; verify approved integrations; smoke tests; launch the private beta. **Never initiated before Phases 01–18 satisfy their acceptance criteria.**
