# Project context

## What MateCart is

MateCart is a complete, white-label e-commerce product developed by **NetaMate Solutions**. NetaMate sells, configures, deploys, maintains and supports independent online stores for its clients. Each client gets:

- a branded storefront for their customers;
- an easy merchant dashboard to run the business.

Merchants are non-technical business owners. They must never need to understand servers, Docker, databases, API credentials or deployment.

## Business model (current)

| Item                                                       | Status                                                      |
| ---------------------------------------------------------- | ----------------------------------------------------------- |
| Business model                                             | **Private beta, manual client onboarding** (contract-based) |
| Public self-service signup                                 | Deferred                                                    |
| Subscription checkout, automated package purchase, billing | Deferred                                                    |
| Final licence pricing                                      | Deferred                                                    |

Nothing deferred is built until a future phase explicitly approves it.

## Five business presets, one commerce engine

All presets share one universal commerce engine. A preset controls storefront layout, merchandising, relevant product attributes, business rules and enabled modules.

| Preset                   | Characteristic data and features                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------------ |
| Fashion & Apparel        | Sizes, colours, collections, size guides, variants                                                     |
| Grocery & Essentials     | Weight-based products, expiry and batch information, delivery slots, quick ordering                    |
| Electronics & Gadgets    | Technical specifications, warranties, serial numbers, comparisons, EMI                                 |
| General / Multi-category | Flexible catalogue and category structures                                                             |
| Bookstore & Booksellers  | ISBN, author, publisher, language, edition, genre, format, pages, series, pre-orders, digital editions |

The approved prototype gives each preset a distinct visual identity, recorded in its design system:

| Preset      | Direction                                                      | Swatches (canvas · primary · accent) |
| ----------- | -------------------------------------------------------------- | ------------------------------------ |
| Fashion     | Editorial: ivory canvas, serif headlines, tall 3:4 imagery     | `#FBF8F3` · `#2A2620` · `#9C4A2B`    |
| Grocery     | Quick shop: fresh green, dense grid, steppers on cards         | `#F6F8F4` · `#00B050` · `#FFF3DC`    |
| Electronics | Spec-first: dark header, electric blue, mono specs             | `#0B0F14` · `#2F6BFF` · `#F3F5F8`    |
| General     | Marketplace: neutral base, red deals, carousels                | `#FAFAF9` · `#0D0F0E` · `#C8312B`    |
| Books       | Literary: paper canvas, oxblood, Newsreader serif, cover grids | `#F7F2E8` · `#6E1F2A` · `#1B1A17`    |

These identities must be preserved in production (Phase 05).

## Approved UI prototype

- Location: `C:\Users\Rizwan\Desktop\MateCart-UI` · github.com/rizwansammo/MateCart-UI · commit `9ff1361`.
- **Strictly read-only.** It is the visual and functional reference, not the production architecture.
- Contents: five storefront presets with the full shopping journey, the merchant dashboard (orders, catalogue, inventory, people, promotions, operations, content, inbox, AI, reports, settings) and a design system page.
- Its stack (Next 14, inline-styled generated class components, `localStorage` state and seeded mock data) is not carried into production.
- Its top-level Storefront / Merchant / Design System / preset switcher is a demo device and must never appear in client stores.

### Market signals in the prototype (to confirm, see DECISIONS Q3)

The prototype targets Bangladesh. It uses Bangladeshi taka (৳), cash on delivery across all 64 districts, bKash, Nagad, Rocket and card payments, Pathao, Steadfast, RedX and Paperfly couriers, and English plus Bangla (Hind Siliguri) text. Treat this as the initial market unless the owner says otherwise.

## Deployment model

**Multi-tenant-aware commerce core + single-tenant-first deployment + future hybrid capability.**

Every client receives an independent MateCart deployment with its own application instance, PostgreSQL database, secrets and configuration, media storage, domain and TLS, branding and preset, and payment and courier credentials.

- Several independent instances may share one VPS when resources and isolation allow. One VPS per client is not mandatory.
- Deployment to a client's own Linux VPS must be supported.
- A shared multi-tenant SaaS mode must stay possible later without rewriting the commerce domain, but it is **not** built for the first release.

## NetaMate Central Platform Console

A separate internal application, used by NetaMate and never by merchants. It has its own backend, data store and administrative security boundary. Its planned responsibilities:

- Client registry and onboarding; store configuration and preset selection; feature and module configuration.
- Server registry, deployment orchestration, release and update management, domain and SSL workflows, integration provisioning.
- Health and capacity monitoring, rolling 24-hour visitor metrics per store, resource utilisation, backup and recovery status.
- Central notifications and administrative alerts; secure support and administrative operations.

It never stores complete copies of client business data, and client stores keep operating when it is unavailable. It is built in Phase 16. Phase 01 only lays its foundation.

## Technology stack

| Layer          | Choice                                                                                                                            |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Frontend       | Next.js (App Router), React, TypeScript (strict), Tailwind CSS, shared UI package                                                 |
| Backend        | Python, Django, Django REST Framework, PostgreSQL, Redis, Celery                                                                  |
| Infrastructure | Docker, Docker Compose, Nginx (or equivalent) reverse proxy, GitHub Actions, GHCR, deployment automation, future deployment agent |
| AI             | Provider-independent integration layer. GroqCloud preferred first.                                                                |

Exact versions and the reasoning behind them are in [DECISIONS.md](DECISIONS.md).

## Ways of working

Claude Code and OpenAI Codex alternate phases. All rules are in [AGENTS.md](../AGENTS.md). The current state and handoff are in [PROGRESS.md](PROGRESS.md).
