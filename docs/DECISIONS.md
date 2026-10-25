# Decisions

Architecture and engineering decisions, newest last. Do not reverse a decision without the owner's approval. Add a new entry that supersedes it instead. Format: context → decision → consequences / when to revisit.

Owner-confirmed product decisions (from the Phase 01 brief): multi-tenant-aware core with single-tenant-first deployment and future hybrid capability; separate Central Console with its own backend and data store; the stack in [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md); 19-phase roadmap; private beta with manual onboarding; billing and signup deferred; prototype read-only.

---

### D-001 · Monorepo with pnpm workspaces, no task orchestrator (2026-10-25, Phase 01)

- **Context:** two Next.js apps and two shared TS packages; Python services are managed separately.
- **Decision:** a single repository with pnpm workspaces (`apps/*`, `packages/*`). Root `package.json` scripts are the cross-platform command hub, including Docker and backend commands. No Turborepo or Nx yet.
- **Revisit:** when build times or package count make caching worthwhile.

### D-002 · Node.js 24 LTS and pnpm 11 with supply-chain defaults (2026-10-25, Phase 01)

- **Decision:** Node 24 (`.node-version`), pnpm pinned via `packageManager: pnpm@11.18.0`. pnpm 11 defaults are kept: 24-hour `minimumReleaseAge`, `blockExoticSubdeps`, and dependency install scripts denied unless listed in `allowBuilds`. `unrs-resolver`'s fallback downloader is explicitly denied because its native binding ships as an optional dependency.
- **Consequence:** a package version published less than 24 hours ago cannot be installed. This is intended.

### D-003 · Next.js 16.4, React 19.3, Tailwind CSS 4.3 (2026-10-25, Phase 01)

- **Decision:** App Router with Turbopack, `typedRoutes`, `reactStrictMode`. Tailwind v4 CSS-first configuration. Shared tokens in `packages/shared-ui/src/theme.css` (`@theme`), imported by each app. Internal packages are consumed as TypeScript source via `transpilePackages` (no build step).
- **Note:** the prototype disabled Tailwind preflight to fit its inline styles. Production keeps preflight on because the UI is reimplemented with utilities.

### D-004 · TypeScript 6.0, not 7.0 (2026-10-25, Phase 01)

- **Context:** TypeScript 7.0.2 (native compiler) is the newest, but `typescript-eslint` 8.72 supports `>=4.8.4 <6.1.0`.
- **Decision:** `typescript ~6.0.3` everywhere.
- **Revisit:** when `typescript-eslint` and Next.js declare TypeScript 7 support.

### D-005 · ESLint 9 (end-of-life) instead of ESLint 10 (2026-10-25, Phase 01)

- **Context:** ESLint 9.39.5 is marked unsupported by the ESLint project. However `create-next-app@16.4.3` still scaffolds `eslint: ^9`, and the plugins bundled by `eslint-config-next` (`eslint-plugin-react` 7.37.5, `eslint-plugin-import` 2.32.0, `eslint-plugin-jsx-a11y` 6.10.2) declare peer support only up to ESLint 9.
- **Decision:** ESLint 9.39.5 with one flat config at the root (`eslint-config-next` core-web-vitals and TypeScript), plus Prettier 3 with the Tailwind class-sorting plugin. ESLint is a development tool and never ships to production.
- **Revisit:** as soon as `create-next-app` or `eslint-config-next` moves to ESLint 10.

### D-006 · Python 3.13 and Django 5.2 LTS, not Django 6.1 (2026-10-25, Phase 01)

- **Context:** Django 6.1.2 is current. `drf-spectacular` 0.30 and `django-environ` 0.14 declare support only up to Django 6.0. Celery 5.6 declares Python up to 3.13.
- **Decision:** Python 3.13 (`python:3.13.17-slim-trixie`), Django `>=5.2.18,<5.3` (LTS, security support until April 2028), DRF 3.18, drf-spectacular 0.30, Celery 5.6, psycopg 3.3, redis-py 6.4 (capped below 6.5 by kombu).
- **Revisit:** plan the upgrade to the Django 6.2 LTS (expected April 2027) once the ecosystem declares support, and complete it before Django 5.2's support ends.

### D-007 · uv for Python dependencies, run only in Docker and CI (2026-10-25, Phase 01)

- **Decision:** each service has `pyproject.toml` and `uv.lock` (uv 0.13.5 pinned). The dev image installs with `uv sync --locked` into `/opt/venv`. uv is not installed on the developer host. Lockfiles are regenerated inside containers. The first lock was created in a throwaway `python:3.13.17-slim-trixie` container.
- **Consequence:** IDE intellisense for Python needs an optional host venv (see DEVELOPMENT_SETUP.md).

### D-008 · Hybrid local development: backend in Docker, frontends on the host (2026-10-25, Phase 01)

- **Context:** Next.js and Turbopack file watching through Windows bind mounts into Linux containers is slow and needs polling; pnpm workspace `node_modules` volumes are fragile.
- **Decision:** PostgreSQL, Redis, both Django APIs and the Celery worker run in Compose. Both Next.js apps run on the host with `pnpm dev` on fixed ports bound to 127.0.0.1.
- **Revisit:** production frontend images are built in Phase 17. A containerised frontend profile can be added then if useful.

### D-009 · Development stack isolation (2026-10-25, Phase 01)

- **Context:** Docker Desktop is shared with MateERP, DeltaWave, HonriseSol and others.
- **Decision:** Compose project `matecart-dev`. Separate PostgreSQL instances for commerce and control (separate volumes and credentials), one password-protected Redis for commerce. Two networks (`matecart-dev-commerce`, `matecart-dev-control`) so control cannot reach commerce data. Explicit volume names `matecart-dev-*`.
- **Ports** (127.0.0.1 only; all below 49152 to avoid Windows/Hyper-V dynamic exclusion ranges):

  | Service                       | Port          |
  | ----------------------------- | ------------- |
  | commerce-web                  | 3400          |
  | platform-console              | 3410          |
  | commerce-api                  | 8400          |
  | control-api                   | 8410          |
  | Postgres (commerce / control) | 15440 / 15441 |
  | Redis                         | 16380         |

  Checked against host listeners (native PostgreSQL on 5432/5433), MateERP (8100, 8101, 55432), DeltaWave (54330), HonriseSol (54329) and the reserved ranges observed on 2026-10-25.

### D-010 · `django.contrib.auth` is not installed yet (2026-10-25, Phase 01)

- **Status:** fulfilled in Phase 02 by D-027 (custom user model created before auth migrated).

- **Context:** Django's `AUTH_USER_MODEL` must be set before auth's first migration. MateCart needs distinct customer, merchant-staff and central-admin identities (possibly phone/OTP based), which is a Phase 02–03 design question.
- **Decision:** Phase 01 installs only `contenttypes`, DRF, drf-spectacular and the `core` app. DRF runs with `UNAUTHENTICATED_USER = None` and no authentication classes.
- **Consequence:** Phase 02 or 03 must define the custom user model before adding `django.contrib.auth`, admin or sessions. Local dev databases contain no auth tables, so no reset is needed.

### D-011 · API secure-by-default posture (2026-10-25, Phase 01)

- **Decision:** DRF default permission `IsAuthenticated` with no authenticators, which denies everything that does not opt in. Health views opt in explicitly. JSON-only renderers and parsers (no browsable API). The OpenAPI schema endpoint is on in development and test and off by default in production (`DJANGO_API_SCHEMA_ENABLED`).

### D-012 · Error envelope and request ids (2026-10-25, Phase 01)

- **Decision:** errors are `{"error": {"code", "message", "details"?}}` from DRF and from Django's 404 and 500 handlers. `X-Request-ID` is on every response. Well-formed incoming ids (8–64 characters of `[A-Za-z0-9._-]`) are reused; others are replaced. The id is attached to every log record.

### D-013 · API contracts: committed OpenAPI + hand-written TS mirror (for now) (2026-10-25, Phase 01)

- **Decision:** drf-spectacular schemas are committed at `services/<service>/openapi.yaml`, validated with `--fail-on-warn`, and a pytest test fails when they drift. `packages/shared-contracts` mirrors them by hand, with runtime guards.
- **Context:** `openapi-typescript` 7.13 declares `typescript ^5.x`, which conflicts with D-004.
- **Revisit:** choose a TypeScript code generator when real endpoints arrive (Phase 02–04) and replace the hand-written types.

### D-014 · Health endpoint design (2026-10-25, Phase 01)

- **Decision:** Django `GET /api/health/live/` (process only) and `GET /api/health/ready/` (dependency checks: 200 or 503, per-check `ok`/`error`, details only in logs). Next.js `GET /healthz`, kept outside `/api/` so a proxy can route `/api/*` to Django without collisions.
- **Revisit:** restrict readiness to internal callers in production (Phase 17).

### D-015 · Celery reliability defaults (2026-10-25, Phase 01)

- **Decision:** JSON-only serialisation, `task_acks_late`, `task_reject_on_worker_lost`, prefetch multiplier 1, 240 s soft and 300 s hard time limits, 3600 s visibility timeout, results expire after 1 hour, worker does not hijack Django logging.
- **Consequence:** every task must be idempotent (documented in AGENTS.md).

### D-016 · Two independent Django services; small foundation duplication accepted (2026-10-25, Phase 01)

- **Decision:** `commerce-api` and `control-api` are separate projects with separate dependencies, images and databases. Each has its own copy of the request id, logging, error envelope and health modules (around 150 lines).
- **Revisit:** extract a shared internal Python package if the duplicated surface grows or diverges accidentally.

### D-017 · Fonts self-hosted via `next/font`; Optimus wordmark font not bundled (2026-10-25, Phase 01)

- **Decision:** Geist, Geist Mono and Hind Siliguri are loaded with `next/font/google`. They are downloaded at build time and served from the app, with no runtime requests to Google (better for privacy). Preset fonts (Instrument Serif, Newsreader) are added in Phase 05. Every font CSS variable in the theme has a fallback, because one undefined `var()` voids the whole `font-family`.
- **Open:** the prototype's "Optimus Custom" wordmark font (`MateCart-UI/public/assets/Optimus.otf`) is not copied until its redistribution licence is confirmed (Q1). The wordmark uses Geist bold meanwhile.

### D-018 · Local secrets are generated, never shared (2026-10-25, Phase 01)

- **Decision:** `.env.example` files contain only ports and `__GENERATE__` placeholders. `pnpm setup:env` creates git-ignored `.env` and `.env.local` files with random 64-hex values and never overwrites existing files. Production settings reject placeholder or short secret keys.

### D-019 · CI is checks-only; third-party actions pinned to SHAs (2026-10-25, Phase 01)

- **Decision:** `.github/workflows/ci.yml` runs frontend (format, lint, typecheck, test, build), backend matrix (ruff, format, mypy, `check`, pytest with PostgreSQL and Redis service containers) and `docker compose config`. Read-only `contents` permission. No deploy, publish or GHCR push. `pnpm/action-setup` and `astral-sh/setup-uv` are pinned to commit SHAs (`setup-uv` publishes no floating major tag). `actions/*` use major tags. Validated locally with actionlint.
- **Revisit:** add Dependabot or Renovate and a secret-scanning job (Phase 18 at the latest).

### D-020 · Design tokens: core in Phase 01, presets in Phase 05 (2026-10-25, Phase 01)

- **Decision:** `theme.css` carries the prototype's core palette (ink, muted, subtle, line, canvas, surface, brand green, semantic colours), font roles, radii (badge 3px, base 4px) and elevation. Tailwind's default 4px spacing scale matches the prototype. The five preset palettes and typography are implemented in Phase 05 from the prototype (swatches recorded in PROJECT_CONTEXT.md).

### D-021 · Base images (2026-10-25, Phase 01)

- **Decision:** `postgres:18.6-alpine3.24` (data volume mounted at `/var/lib/postgresql`, as required from PostgreSQL 18), `redis:8.10.2-alpine` with `requirepass` and AOF, `python:3.13.17-slim-trixie`, uv `0.13.5`. Official images are shared read-only layers. Using them does not touch other projects' containers or data.
- **Open:** Redis licensing review (Q2).

### D-022 · Vitest for TypeScript unit tests (2026-10-25, Phase 01)

- **Decision:** Vitest 5 in `packages/shared-contracts`. Component and browser testing tools are added when the UI phases need them.

### D-023 · UUIDv7 primary keys for every MateCart table (2026-10-25, Phase 02)

- **Status:** refined by D-036: applies to keys MateCart generates; externally assigned ids may be any UUID version.

- **Context:** sequential integers leak volumes and are guessable, and they collide when databases are merged. Random UUIDv4 keys fragment B-tree indexes.
- **Decision:** `UUIDModel.id = UUIDField(default=uuid7)`, using an in-house RFC 9562 implementation (`commerce/core/ids.py`, tested; Python 3.13 has no `uuid.uuid7`). Human-readable numbers (orders, invoices) are separate per-tenant sequences in their phases.
- **Consequences:** 16-byte keys (acceptable at e-commerce scale). Stores can move between dedicated and shared databases without re-keying. Creation time is visible inside the id, which is not a secret for these records.
- **Revisit:** switch to `uuid.uuid7()` when the services move to Python 3.14.

### D-024 · Tenant, store settings and identity are separate concepts (2026-10-25, Phase 02)

- **Decision:** `tenancy.Tenant` is the commerce isolation and ownership root (UUID id, DNS-label `handle`, lifecycle `status`). `stores.StoreSettings` (one per tenant) holds business configuration. NetaMate clients, store registrations and deployments belong to control-api (Phase 16). Customers and staff are `identity.User` rows. The commerce tenant id **is** the console's store registration id, the only cross-service reference.
- **Consequences:** one tenant serves one store. Several storefronts for one client would be several tenants (a new decision if ever needed).

### D-025 · Tenant isolation: fail-closed application scoping plus database-enforced tenant foreign keys; RLS deferred (2026-10-25, Phase 02)

- **Status:** extended by D-034: writes to existing rows are tenant-qualified in SQL.

- **Context:** a `tenant_id` column alone does not isolate anything. Options assessed: hand-written filters, scoped managers, composite foreign keys, PostgreSQL RLS, schema-per-tenant (comparison in ARCHITECTURE §3).
- **Decision:** `TenantOwnedModel` with a scoped default manager that **raises** without a tenant context; guarded `save`, `delete` and bulk operations; immutable ownership; `TenantForeignKeyConstraint` composite foreign keys enforced by PostgreSQL; system checks E001–E004 that fail the build when a model skips a rule; `unscoped_access(reason)` as the single logged escape hatch.
- **Not chosen now:** PostgreSQL RLS. Dedicated deployments hold one tenant, so RLS adds complexity (per-transaction settings, pooler behaviour, migration roles) without extra protection. It becomes the defence-in-depth layer if pooled mode is approved.
- **Consequences:** Django's `_base_manager` stays unscoped (used to follow already-scoped foreign keys); the composite foreign keys make that safe. `QuerySet.update()` and bulk operations are scoped, but raw SQL is not, so raw SQL on tenant data needs review.

### D-026 · Strict single-tenant runtime, configuration-only resolution and explicit provisioning (2026-10-25, Phase 02)

- **Decision:** `MATECART_TENANCY_MODE` accepts only `single`. `MATECART_TENANT_ID` (UUID) identifies the served tenant and is required in production. `TenantMiddleware` activates it per request from configuration only (request data is never read). Readiness fails unless that tenant exists and is the only one. `provision_tenant` is the only way to create a tenant: it must match the configured id, refuses a second tenant, validates input and writes tenant, settings and audit event atomically (`--if-missing` makes it idempotent for automation).
- **Future pooled mode** (separate decision, not built): verified domain-to-tenant resolution in `resolve_request_tenant_id`, RLS, per-tenant quotas and rate limits, provisioning without the one-tenant rule.

### D-027 · Commerce identity: one per-tenant user table with customer and staff realms (2026-10-25, Phase 02)

- **Context:** D-010 required the custom user model before auth migrations. Login method (email, phone OTP or both) is decided in Phase 03 (Q6), so the table must support every option.
- **Decision:** `identity.User` (`AUTH_USER_MODEL`) extends `TenantOwnedModel` and `AbstractBaseUser`. `account_type` is `customer` or `staff` (separate realms: the same email may hold one customer and one staff account, never sharing credentials). `email` (lowercase) and `phone` (E.164 via `phonenumbers`) are nullable, at least one required, with verification timestamps, unique per `(tenant, account_type)` through partial unique indexes. `USERNAME_FIELD = "id"` (opaque); Phase 03 backends resolve email or phone within tenant and realm. No `PermissionsMixin`: Django's groups and permissions are global, not tenant-scoped, so store RBAC is designed in Phase 03. Until then `AUTHENTICATION_BACKENDS = [NoLoginBackend]` (an empty list would make `authenticate()` raise).
- **Why not separate customer and staff tables:** Django supports one `AUTH_USER_MODEL`; two tables would push one population outside Django's auth framework. The realm column gives the same separation with one, well-tested model. (This is also the mainstream Django e-commerce pattern.)
- **Reversibility:** nothing is deployed yet, so this initial migration can still be reshaped before the first production launch (Phase 19). After that, user-table changes need data migrations.
- **Status of D-010:** fulfilled. `django.contrib.auth` is now installed with this model in place before its first migration.

### D-028 · Console identity: `accounts.AdminUser` in control-api only (2026-10-25, Phase 02)

- **Decision:** control-api's `AUTH_USER_MODEL` is `accounts.AdminUser`: UUIDv7 id, email unique and lowercase (database `CHECK`), no store or tenant fields. Console identities never exist in store databases and vice versa (tested). `NoLoginBackend` until Phase 03; MFA will be mandatory for console sign-in.

### D-029 · Append-only audit events, written in the same transaction (2026-10-25, Phase 02)

- **Status:** extended by D-035: allowlisted, recursively sanitised payloads.

- **Decision:** implement a minimal audit foundation now: `audit.AuditEvent` (tenant, time, dotted action, actor user or system label, resource type and id, redacted field changes, request id). `record_audit_event()` refuses to run outside a transaction, so an event exists exactly when its change commits. Sensitive keys (password, secret, token, otp, api_key, cvv, card number…) are stored as `[REDACTED]`. Immutable in the ORM and in PostgreSQL (trigger refuses UPDATE and DELETE). Actors are `PROTECT`ed, so accounts with history are deactivated or anonymised, not deleted.
- **Not chosen:** event sourcing. Business modules write explicit audit events for important changes; `created_at`/`updated_at` metadata stays separate from history.
- **Later:** retention and archival (Phase 18); console audit log in control-api (Phase 16); runtime database role without table ownership so TRUNCATE is also unavailable to the application (Phase 17).

### D-030 · No global soft delete; explicit lifecycle per model (2026-10-25, Phase 02)

- **Decision:** no `deleted_at` on every table (it silently breaks uniqueness and leaks deleted rows into queries). Each model states its lifecycle (`Tenant.status`, `User.is_active`, future `archived_at`). History-bearing rows (orders, payments, stock movements, audit) are never hard-deleted. Personal-data erasure anonymises (Phases 10 and 18).

### D-031 · International data standards, money convention and new dependencies (2026-10-25, Phase 02)

- **Decision:** ISO 3166-1 countries and ISO 4217 currencies validated with **Babel** (CLDR, BSD-3-Clause; chosen over `pycountry`, which is LGPL), IANA time zones via `zoneinfo` plus the **tzdata** package, phones in E.164 via **phonenumbers** (Apache-2.0), languages from `PLATFORM_LANGUAGES` (`en`, `bn`). Store settings enforce formats and language rules with database `CHECK`s. Money (from Phase 04): `Decimal(19,4)` plus currency code, never float. Bangladesh is the first market, not a schema assumption (answers Q3).
- **Note:** `phonenumbers` 9.0.41 lists classifiers only up to Python 3.12; it is pure Python and passes its tests on 3.13.

### D-032 · Tenant readiness and the `tenant_unavailable` error (2026-10-25, Phase 02)

- **Decision:** commerce readiness gains a `tenant` check (configured, provisioned, only tenant). Any `TenancyError` reaching the API becomes `503 {"error": {"code": "tenant_unavailable"}}` with details only in logs. A fresh development stack therefore reports `tenant: error` until `pnpm api:provision` runs. The OpenAPI schema changed accordingly; `shared-contracts` already models checks as a map, so no TypeScript change was needed.

### D-033 · Migration verification workflow (2026-10-25, Phase 02)

- **Decision:** every new migration is (1) generated with `makemigrations`, (2) reviewed with `sqlmigrate`, (3) applied forward, reversed to zero and re-applied on a **temporary** database created and dropped inside the MateCart PostgreSQL container, (4) checked for drift (`makemigrations --check`, also a test), and only then (5) applied to development databases, additively. Generated migrations are excluded from ruff; hand-written SQL uses `RunSQL` with `reverse_sql`.

### D-034 · Writes to existing rows are tenant-qualified in SQL (2026-10-25, Phase 02.1)

- **Status:** complemented by D-038 for reads (refresh, deferred fields, relations).

- **Finding (confirmed, high):** Django targets existing rows by primary key alone, and Phase 02 checked only the tenant id on the Python instance. An instance built in code with another tenant's primary key passed that check. Reproduced against PostgreSQL: `save(force_update=True)` overwrote the other tenant's row **and moved it into the active tenant**; `save()` / `save(update_fields=...)` on an instance marked as stored updated it; `delete()` deleted it; `refresh_from_db()` read it; `bulk_create(update_conflicts=True)` overwrote it; with `unscoped_access`, `bulk_update` and `delete` acted on it under a wrong tenant claim. (`save()` and `save(update_fields=...)` on a new instance were already safe: Django inserts, and the duplicate key fails.)
- **Decision:** never trust an instance's tenant as proof of ownership; qualify every statement that touches an existing row by tenant:
  - `TenantOwnedModel._do_update` adds `AND tenant_id = %s` to the UPDATE every `save()` path issues (atomic, no extra query) and raises `CrossTenantAccessError` when the key exists under another tenant.
  - `delete()` locks the row (`SELECT … FOR UPDATE`) and checks its **stored** tenant before Django's primary-key delete, inside a savepoint.
  - `refresh_from_db()` reads through a tenant-qualified queryset (Django's public `from_queryset` hook).
  - `bulk_update` issues one UPDATE per tenant, qualified by it (also under `unscoped_access`).
  - `bulk_create(update_conflicts=True)` requires `tenant` in `unique_fields` (a conflict can then only match the same tenant's row) and never updates `tenant`.
- **Trade-off:** `_do_update` is a private Django hook (stable since Django 1.6, also overridden by django-concurrency). Regression tests in `tests/test_forged_instances.py` fail if a Django upgrade stops routing saves through it. RLS was not needed: the statement-level qualification closes the gap without changing the architecture (D-025 stands).

### D-035 · Audit payloads: explicit allowlists plus recursive sanitisation (2026-10-25, Phase 02.1)

- **Finding (confirmed, high):** redaction looked only at top-level keys, so nested secrets were stored (reproduced with an API key inside `integration.credentials`). Tenant provisioning recorded the whole store profile, including the owner's contact email and phone.
- **Decision:** two layers. (1) `field_changes(before, after, fields=...)` now **requires** an explicit allowlist; provisioning records only operational facts (`handle`, `name`, `preset`, `country`, `default_currency`, `timezone`, `default_language`, `supported_languages`), never contact details or legal identity. (2) `sanitize_audit_value()` runs on every payload: it walks nested mappings and lists, redacts values under sensitive keys in any naming style (word, compound and fragment matching: passwords and hashes, tokens, API and private keys, secrets, OTP and recovery codes, credentials, authorization, cookies, card and bank numbers, CVV, PIN), redacts secret-looking values under any key (PEM private keys, Django password hashes, JWTs, `Bearer`/`Basic` credentials, Luhn-valid card numbers), reduces unsupported objects to a type marker, caps depth and string length, and never mutates the caller's data. Matching errs towards redaction.
- Provisioning failures no longer echo database error text, which can quote submitted values. Audit immutability (ORM and trigger, D-029) is unchanged.

### D-036 · Identifier policy: generated keys vs externally assigned ids (2026-10-25, Phase 02.1)

- **Finding (low):** `setup:env` generated `MATECART_TENANT_ID` with `crypto.randomUUID()` (UUIDv4) while the docs implied every identifier is UUIDv7.
- **Decision:**
  - **Primary keys MateCart generates** (default on `UUIDModel`) are UUIDv7 (D-023).
  - **Externally assigned identifiers** (`MATECART_TENANT_ID`, which becomes `Tenant.id`; future console store registration ids) may be **any RFC 9562 UUID version**. They are validated as UUIDs with the RFC variant; the nil and max UUIDs are rejected as placeholders.
  - New ids MateCart generates outside the database are UUIDv7: `setup:env` now uses `scripts/uuidv7.mjs` (tested with `node --test`), and the console will issue UUIDv7 (Phase 16).
  - Existing ids are never rewritten: the current development tenant keeps its UUIDv4 id (no reset, no re-provisioning).

### D-037 · Secret values are never printed; exposed local secrets are rotated (2026-10-25, Phase 02.1)

- **Incident:** during Phase 02 a command printed the full local `MATECART_CONTROL_SECRET_KEY`. It was never committed (verified).
- **Action:** rotated in `.env` with a script that prints no values, `control-api` alone recreated, verified by fingerprint comparison that it uses the new key and that no container still holds the old one, readiness 200. Phase 01 output had shown only masked 8-character prefixes of the local secrets (32 of 256 bits), which is not a usable exposure, so they were not rotated.
- **Rule (AGENTS.md):** never print secret values or `.env` lines; check secrets by presence, format or fingerprint comparison. Rotating a value does not erase it from old transcripts, so the old value must simply never be used again.

### D-038 · Reads of tenant-owned rows follow the active tenant, never the instance (2026-10-25, Phase 02.2)

- **Finding (confirmed, high):** the read side still trusted untrusted input. Reproduced against PostgreSQL with tenant A active and the row owned by tenant B:
  - `refresh_from_db()` read B's row for an instance built with B's id and for an instance loaded earlier under B;
  - a caller-supplied `from_queryset` (manager-free, built under `unscoped_access`, or filtered to B) was used as given;
  - without any tenant context, refreshes and deferred-field loads read the row (fail-open);
  - `arefresh_from_db()` (which delegates to the sync method) and a second model (`StoreSettings`) behaved the same;
  - **forward relation traversal** on an instance built in code (`AuditEvent(actor_user_id=<B's user>).actor_user`) read B's user through Django's unscoped `_base_manager`.
- **Root cause:** `refresh_from_db` took the tenant from the instance before the active context and never checked one against the other. Django reads related objects, deferred fields and refreshes through `_base_manager`, which Phase 02 had left unscoped.
- **Decision:**
  1. `TenantOwnedModel.Meta.base_manager_name = "objects"`: the base manager is the scoped manager. Every Django-internal read (refresh, deferred fields, forward foreign keys and one-to-ones, generic relations, foreign-key validation, deletion collection) is confined to the active tenant, fails closed without a context, and is unfiltered only inside `unscoped_access`.
  2. `refresh_from_db()`: the active tenant decides. An instance claiming another tenant raises `CrossTenantAccessError` before any query; a caller's `from_queryset` is always filtered to the active tenant; no context raises `TenantContextMissing`; under `unscoped_access` it reads only the row stored under the instance's own tenant.
  3. Ownership checks on write paths (D-034) use `_stored_tenant_id()`, a manager-free query, so the scoped base manager can never hide another tenant's row from them.
  4. System check `tenancy.E005`: a tenant-owned model's default and base managers must be `TenantScopedManager`s.
- **Migration:** Django records `base_manager_name` in migration state, so three **state-only** `AlterModelOptions` migrations were added (`audit.0003`, `identity.0002`, `stores.0002`); `sqlmigrate` shows each as `(no-op)`, with no SQL. Rejected alternatives: a custom model metaclass, a custom foreign-key field class (also a migration) or patching Django's cached `_meta` internals. All are obscure and fragile.
- **Consequences:** `dumpdata` and `loaddata` of tenant-owned data need a tenant context or `unscoped_access`. A queryset built directly with `QuerySet(model=...)` bypasses every manager and is reserved for ownership checks inside `tenancy`; application code must not use it.

---

### D-039 · Authentication cryptography and dependencies (2026-10-26, Phase 03)

- **Decision:** Argon2 password hashing; random opaque tokens stored as SHA-256 hashes;
  purpose-bound HMAC for short OTPs and recovery codes; Fernet/HKDF with a separate
  encryption key for TOTP/PKCE material. No self-written password or TOTP algorithm.
  Use argon2-cffi, cryptography, pyotp, segno and PyJWT with cryptography, locked
  independently for both Python 3.13/Django 5.2 services. Segno renders QR locally.
- **Verification:** both locked environments import and exercise these libraries in
  their backend suites, including tamper, wrong token, expiry and TOTP replay tests.
  Testing Library, user-event and jsdom exercise shared React 19 components.
- **Consequence:** encryption keys are independent per service, must be backed up
  securely and cannot be replaced without a migration/re-enrollment plan.

### D-040 · Realm-specific browser sessions and same-origin CSRF (2026-10-26, Phase 03)

- **Decision:** explicit database-backed customer/staff/console sessions with
  different cookie names, paths and policies; HttpOnly, host-only, Secure in
  deployments. Next proxies local `/api/*`; no browser CORS is necessary. All
  state-changing requests, including sign-in, enforce CSRF. Backend permissions
  and current membership are checked on every protected request. Django's generic
  NoLoginBackend remains disabled; it is not the new realm login API.
- **Consequence:** no browser localStorage tokens. Pending MFA/enrollment is a
  restricted state, not authorization. Sessions rotate on sign-in and security
  changes; password resets invalidate every existing session.

### D-041 · Transactional authentication delivery and provider gates (2026-10-26, Phase 03)

- **Decision:** send authentication email/SMS only after commit, without placing
  secrets on Celery. Local SMTP is isolated Mailpit; control has its own Redis.
  Public email acceptance messages remain enumeration-safe. Provider errors are
  logged without credentials or exception payloads. SMS failure is unavailable,
  not a fake success. SMS stays disabled without a genuine configured adapter;
  production rejects `tests.*` providers.
- **Trade-off:** synchronous after-commit delivery is small and inspectable, but
  not a durable exactly-once delivery system. Queues/retries/deduplication and
  managed masking senders are Phase 13. Tenant/purpose are passed to SMS adapters.
  Included allowance before prepaid credits is approved future billing scope;
  subscription, purchase, recharge and ledger features are not implemented.

### D-042 · Optional merchant and mandatory console MFA (2026-10-26, Phase 03)

- **Decision:** merchant MFA is optional, enforced once enabled. Console MFA is
  mandatory, with restricted first enrollment and no removal of its last method.
  Both offer TOTP, verified-email OTP and single-use hashed backup codes. Sensitive
  settings require recent authentication; the UI explicitly confirms identity.
- **UX:** QR visible by default, setup key revealed only deliberately; guided
  confirmation and once-only recovery codes with Copy/Download and acknowledgment.
  Email code inputs accept paste and explain destination, expiry and cooldown.

### D-043 · Google OIDC and verified account linking (2026-10-26, Phase 03)

- **Decision:** Google is customer-only and gated on server configuration. Validate
  signed ID tokens, issuer/audience/expiry, nonce, state, browser binding and PKCE.
  Use stable provider subjects. Never merge by email. Linking proves the current
  account and the new method; phone-only accounts do not require an email.
- **Consequence:** email/password, phone OTP and Google are supported schema/API
  paths, but live Google and SMS remain unavailable until genuine providers are
  configured and live-tested. Customer identity is distinct from delivery details.

### D-044 · Provisioned owners, invited staff and `/manage` (2026-10-26, Phase 03)

- **Decision:** first Owner activated from a provisioning-only invitation; no
  public merchant registration. Owner invites staff. Roles are fixed code-owned
  permission sets: Owner, Manager, Order handler, Content editor. Only Owner can
  manage staff; Owner cannot be assigned through staff APIs. Every membership and
  invitation is tenant-owned. Dashboard authentication/account/team screens use
  `/manage` in commerce-web on the storefront domain.
- **Scope:** full merchant operations are Phase 11. Role editing, suspension,
  activation, expiry, revocation and single-use invitations are implemented now.

### D-045 · Independent console identity and enrollment (2026-10-26, Phase 03)

- **Decision:** console administrators are created only through an authorized
  personnel command. Activation sets credentials and grants only MFA enrollment;
  protected console sessions require the second step. Separate database, Redis,
  master keys, cookies and origins. Commerce never calls control-api for sign-in.
- **Scope:** only a protected account shell now. Registry, infrastructure,
  management permissions and full central audit functionality remain Phase 16.

### D-046 · Supported Webpack compiler fallback (2026-10-26, Phase 03)

- **Evidence:** local Next 16.4.3 Turbopack builds crashed while starting the CSS
  subprocess on Windows; repeating outside the restricted process had the same
  failure. Installed Next CLI documentation explicitly supports `--webpack` for
  both dev and build. Both apps compiled and built successfully using it.
- **Decision:** use that flag in both scripts; keep the approved Next/React stack,
  strict type checking and typed routes. Do not run dev and typegen/build against
  the same generated `.next` files concurrently. Re-evaluate Turbopack separately.

### D-047 · Authentication review guardrails (2026-10-26, Phase 03)

- **Decision:** reject repeated pending-session activation under row locks;
  serialize resend and last-console-factor removal; limit password step-up and
  TOTP enrollment attempts. Rotate active credentials after password/MFA changes
  without extending expiry. All authentication responses are no-store. Strip
  auth query values from Django access logs and disable Next request logging.
- **Evidence:** negative PostgreSQL/API tests cover stale sessions, cross-tenant
  rows for all nine new commerce models, query bypass guardrails, cooldowns,
  provider-error sanitization and cookie rotation. Old private `_do_update()`
  regression tests remain mandatory. Production proxy logs/database privileges
  are Phase 17; final Django compatibility and isolation audit are Phase 18.

## Open questions for the owner

| #   | Question                                                                                                                                   | Status                                                                                                    | Needed by |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- | --------- |
| Q1  | Does the **Optimus Custom** font licence allow redistribution and web embedding?                                                           | Open. Not relevant to Phase 02.                                                                           | Phase 05  |
| Q2  | Is **Redis 8** licensing (RSALv2 / SSPLv1 / AGPLv3) acceptable for client deployments, or should we switch to **Valkey** (BSD-3, drop-in)? | Open. Redis stays as adopted; nothing in Phase 02 depends on it. Reviewed with infrastructure.            | Phase 17  |
| Q3  | Initial market: Bangladesh?                                                                                                                | **Resolved (Phase 02 brief):** Bangladesh is the first market; data structures are international (D-031). | —         |
| Q4  | Merchant dashboard placement                                                                                                               | **Resolved:** `/manage` on the storefront domain in commerce-web (D-044).                                 | —         |
| Q5  | Branching and first commit.                                                                                                                | **Resolved:** Phase 01 committed on `main` (`8f3e34b`).                                                   | —         |
| Q6  | Customer login methods                                                                                                                     | **Resolved:** email/password, phone OTP and Google OIDC; real provider gates apply (D-043).               | —         |
| Q7  | Customer versus merchant identity                                                                                                          | **Resolved:** separate accounts, credentials, sessions and permissions, even for matching emails (D-040). | —         |
