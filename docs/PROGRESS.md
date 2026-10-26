# Progress and handoff

Updated: 2026-10-26 (Asia/Dhaka). **Phase 06 — Customer Storefront is implemented and
locally verified, ready for the owner's commit, push, GitHub CI and independent
acceptance.** Phases 01–05 (with 02.1, 02.2, 03.1, 04.1 and 05.1) are accepted and closed.
Phase 07 has not started and needs explicit authorization.

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
| Phase 06        | **Implemented and verified locally, not committed.** Baseline `613cd49` on `main`; no commit, push or PR was created by the assistant. CI and independent acceptance pending.                                                                             |
| Architecture    | Dedicated single-tenant deployment, tenant-aware modular monolith, future hybrid only if approved; commerce/control independent.                                                                                                                          |
| Product         | Private beta, manual onboarding. Public commercial signup/subscriptions/billing deferred. Public customer registration is authorized.                                                                                                                     |
| Prototype       | MateCart-UI strictly read-only; no edits/build/install (HEAD `9ff1361`, clean).                                                                                                                                                                           |
| Roadmap         | All 19 main phases preserved; 02.1/02.2/03.1 are corrective subphases, not additional main phases.                                                                                                                                                        |

Phase 03/03.1 history (accepted): realm-separated customer, merchant and console
authentication; optional merchant and mandatory console MFA; owner provisioning
and staff invitations; 03.1 closed backend team step-up, OIDC binding order and
narrow Google provider failure handling (D-039 to D-049, AUTHENTICATION.md,
SECURITY.md). External Google, SMS and SMTP acceptance remains outstanding until
genuine providers are configured.

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
| GitHub CI    | Phase 06 not run (uncommitted). Last CI: run 40301529264 (Phase 05.1), 4/4, 827 tests.                                                                                                                                                                               |

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

- **Next step:** owner reviews the uncommitted Phase 06 diff, commits and pushes it,
  verifies GitHub CI and requests independent acceptance. Phase 07 starts only after
  explicit authorization.
- Phase 06 limits (documented, not defects): no "in stock only" filter or brand, author,
  publisher and format facet lists (the API has no facet counts; an exact stock filter would
  duplicate D-060 in SQL); Grocery cards do not show pack sizes (product pages do); pre-order
  labels compare with the UTC date (the API decides purchasability in the store's time zone);
  images are served as uploaded (no resizing or `srcset`); HTML is not cached by shared
  caches, only media; the `<html>` element stays `lang="en"` while the store frame carries
  the customer's language.
- Phase 05 limits: module states are configuration only. Contact address and social links
  are not modelled yet.
- Phase 04.1 limits (documented, not defects): reservations are not batch-assigned, so
  sellable batch stock subtracts the location's whole reservation; batches expiring
  after a reservation can leave it uncovered until Phase 08 allocation; pre-04.1 keys
  are verified without the batch manufacture date; merchant views still show physical
  `available` (on-hand minus reserved), the public catalogue shows sellable stock.
- Known limits: no public catalogue rate limits/CDN, no image resizing or malware
  scanning (only images and PDFs accepted; PDFs download), public media may stay in
  shared caches for five minutes after unpublishing, reservations not yet exposed
  (07/08), FEFO is listing only (allocation 08), no merchant catalogue UI (11).
- Carried forward: live Google/SMS/SMTP acceptance; production DB roles and proxy
  hardening (17); final security, load and recovery audit (18); font (Q1) and Redis
  licence (Q2) questions.
- Owner commits and pushes. No co-author trailers.
