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

### D-048 · Merchant team mutations require backend step-up (2026-10-26, Phase 03.1)

- **Finding (confirmed, high):** the Team UI confirmed the Owner's password before
  staff mutations, but the six APIs enforced only the active staff session and
  `staff.manage`. A direct API call could bypass the intended recent-authentication
  policy while still requiring an Owner session and CSRF token.
- **Decision:** invitation create/resend/revoke, role changes and member
  suspension/reactivation share one API base class that first preserves CSRF,
  staff-realm authentication and Owner permission checks, then enforces the
  existing ten-minute `require_recent_authentication()` window. Services and role
  policy are unchanged; merchant MFA remains optional.
- **Evidence:** PostgreSQL/API tests age the Owner session, deny all six operations
  without side effects, reject a wrong password, permit the intended action after
  correct reauthentication, and retain customer, non-Owner, tenant and CSRF denial.

### D-049 · Validate OIDC browser ownership before consuming state (2026-10-26, Phase 03.1)

- **Finding (confirmed, medium):** `_claim()` marked a Google authorization used
  before checking expiry and the browser-binding cookie. A caller who knew the
  random state could invalidate that browser's attempt, although it could not sign
  in or link an account.
- **Decision:** under one transaction and row lock, locate an unused state, verify
  expiry and constant-time browser binding, then write `used_at`. Normalize only
  expected provider transport, JSON/JWKS and JWT-data failures to the existing
  generic Google failure. Do not catch unrelated programming exceptions.
- **Evidence:** wrong/missing bindings leave state usable by the original browser;
  exactly one concurrent valid claim succeeds; transport, malformed payload/key,
  malformed token, signature, issuer, audience, nonce and code-exchange failures
  issue no session or identity and return the friendly generic result.

### D-050 · Pillow for server-side image verification (2026-10-26, Phase 04)

- **Context:** uploaded catalogue images must be validated from their bytes, not
  from the client's filename or Content-Type, and decompression bombs refused.
- **Decision:** add `pillow>=12.3.0,<13` (MIT-CMU) to commerce-api. 13.0.0 was ten
  days old, so the mature 12.3 line is pinned as a supply-chain precaution, matching
  the pnpm minimum-release-age policy. Pillow only identifies, verifies and fully
  decodes images; there is no image-processing pipeline (resizing/CDN are later).
  The development image gained a non-root-owned `/var/lib/matecart/media` directory
  and Compose a named volume `matecart-dev-commerce-media`.

### D-051 · One catalogue engine with preset-specific typed details (2026-10-26, Phase 04)

- **Decision:** a single model set (Category, Collection, Brand, typed attributes,
  Product, ProductVariant) serves all five presets. `Product.product_type`
  (`standard`, `apparel`, `grocery`, `electronics`, `book`) selects one typed
  one-to-one detail table (`ApparelDetails`, `GroceryDetails`, `ElectronicsDetails`
  with `WarrantyPolicy`, `BookDetails`) and its publication rules. Specialised
  stores offer `standard` plus their own type; General offers all types and forces
  none of them.
- **Attributes:** `AttributeDefinition` has a value type (text, number, boolean,
  option, measurement) and a scope: `variant` attributes must be options and define
  variants; `specification` attributes describe products. Values live in typed
  columns (`ProductAttributeValue` checks exactly one), not JSON.
- **Variants:** every product sells through variants (a simple product has one
  default variant). `option_signature` makes duplicate combinations impossible in
  PostgreSQL. SKUs are normalised to uppercase and unique per store.
- **Lifecycle:** draft, active, archived. Publishing checks name, preset/type, an
  active variant, a default price for each active variant, the grocery pack size
  and a book author. Products that were published or have inventory history are
  archived, never deleted.
- **JSON:** only translated display text (`translations`), validated against the
  store's supported non-default languages and a field allowlist.

### D-052 · ISBN belongs to the variant; barcodes are GTINs (2026-10-26, Phase 04)

- An ISBN identifies one edition in one format, so paperback, hardcover and eBook
  are variants of one book product and each variant owns its ISBN-13 (ISBN-10 input
  is converted; check digits verified). This refines the blueprint's
  `BookDetails (ISBN)`: `BookDetails` keeps work-level data (authors, publisher,
  language, pages, series) and uniqueness is `(tenant, isbn)` on variants.
- `barcode` accepts GTIN-8/12/13/14 with a valid check digit, unique per store.
  Both are nullable so absence never collides.

### D-053 · Price lists, currency precision and private costs (2026-10-26, Phase 04)

- `PriceList` (one default per store, created in the store currency on first use)
  and `Price` (one per list and variant). Money is `Decimal(19, 4)` plus ISO 4217;
  amounts may not have more decimals than the currency's minor unit (Babel: BDT 2,
  JPY 0, KWD 3). A price takes its list's currency; there is no conversion.
- Zero is a valid price (free items). `compare_at_amount` must exceed the price
  (service rule and database CHECK). Omitted optional fields keep stored values;
  `null` clears them. Prices change under a lock on the variant.
- New permission `catalog.costs` ("See and change cost prices"): Owner and Manager
  get it through the existing code-owned role rules; Content editor keeps
  `catalog.manage` without it. Without it, cost prices are absent from responses
  and refused on write (403). Audit events record `cost_changed`, never the value.
- `pricing.services.quote()` returns the amount, currency and source price row:
  the snapshot input for checkout (Phase 07). No coupons or promotions (Phase 12).

### D-054 · Append-only inventory ledger with locked materialised levels (2026-10-26, Phase 04)

- **Quantities:** per variant and location, `on_hand` and `reserved`;
  `available = on_hand - reserved` is a PostgreSQL generated column. Quantities are
  `Decimal(14, 3)`: whole numbers for `each`, up to three decimals for kg, g, l, ml
  and m. Digital and untracked variants have no stock.
- **Ledger:** every change appends `StockMovement` rows (signed deltas and the
  balances after) in the transaction that updates the level. UPDATE/DELETE are
  refused by the ORM and by a PostgreSQL trigger (migration `inventory.0002`).
  Corrections are compensating `reversal` movements (once per movement; a transfer
  is reversed as a whole). Types: receipt, adjustment, damaged, lost, returned,
  transfer out/in, reserved, released, fulfilled, reversal; CHECK constraints fix
  each type's signs.
- **Concurrency:** one transaction per operation, locking the variant
  (`FOR NO KEY UPDATE`), then its levels in primary-key order, then batch levels and
  serials. CHECK constraints refuse negative stock and reservations beyond on-hand
  stock as a backstop. Tested with parallel committed transactions; removing the
  locks makes those tests fail.
- **Policy:** no negative stock and no backorders in Phase 04. Optional client
  `idempotency_key` (unique per store, stored on the operation's first movement):
  a retry returns the original operation; a different request with the same key is
  refused.
- `reserve`, `release` and `fulfil` are implemented and tested service extension
  points for checkout and orders (Phases 07/08) but not exposed through the API.
  `reconcile()` (API and `manage.py reconcile_inventory`) proves levels, batch
  levels and serial counts equal the ledger.

### D-055 · Media validation, storage and gated serving (2026-10-26, Phase 04)

- Accepted: JPEG, PNG, WebP images (fully decoded, at most 8000 px per side and
  25 megapixels) and PDF documents (`%PDF-` with an EOF marker). SVG, HTML, GIF and
  anything else are refused. Maximum 10 MB (`MATECART_MEDIA_MAX_BYTES`), checked
  from Content-Length before CSRF or form parsing touches the body; 300 uploads per
  store per hour.
- Files are stored at `tenants/<tenant>/media/mediaasset/<uuid7>.<ext>` with the
  extension derived from the detected type. `tenant_storage_path` now uses the
  active tenant for unsaved rows and fails closed without one (a test caught files
  being written under `tenants/None/` before this fix).
- Binaries never go into PostgreSQL. Nothing serves `MEDIA_ROOT` directly: staff
  stream files through the API (`private, no-store`); the public route serves only
  files used by an active product, category or collection, cached for five
  minutes. Every file response carries `nosniff`, a sandboxing CSP and a fixed
  generic filename; PDFs download as attachments.

### D-056 · Batches, expiry and serial numbers (2026-10-26, Phase 04)

- `Product.tracking_mode`: `none`, `batch` or `serial`; fixed once inventory
  history exists. Serial tracking needs physical variants counted in units.
- Batches are unique per variant and code, record manufacture/expiry/receipt dates
  and keep per-location `BatchLevel`s that add up to the location's on-hand stock.
  Expired batches cannot be received; perishable grocery products need an expiry
  date. `batches_by_expiry()` lists unexpired batch stock first-expired-first-out as
  the foundation for Phase 08 allocation; no order allocation happens yet.
- Serial numbers are unique per store (normalised uppercase), one movement per
  unit, and have exactly one location while available or reserved and none once
  sold, damaged or lost (database CHECK). Serial movements are corrected with
  adjustments, not reversals, because reversal would not restore unit identities.

### D-057 · Category tree structure and moves (2026-10-26, Phase 04)

- Adjacency list (`parent`), at most five levels. Creates, moves and deletes take a
  `FOR NO KEY UPDATE` lock on the store's tenant row, so concurrent moves cannot
  race into a cycle without blocking unrelated writes. Moves refuse cycles and
  depth overflow (subtree height included). Categories with children or products
  are not deleted. The storefront reads the tree in one query.

### D-058 · Public read-only catalogue API (2026-10-26, Phase 04)

- `/api/catalog/` (products, product by slug, category tree, collections, media) is
  an explicit anonymous GET-only opt-in for the Phase 06 storefront. It returns
  active products, active categories and collections, default-list selling prices
  and a per-variant `in_stock` flag (pre-orders and untracked items count as in
  stock). It never returns drafts, archived products, cost prices, stock
  quantities, publication problems, staff or audit data. Filters: search, category
  (including descendants), collection (with "featured" order), brand, product type;
  sorting by newest, name or price. Rate limiting and response caching are part of
  the Phase 12/17 traffic work.

### D-059 · Idempotency keys are bound to the request's identity (2026-10-26, Phase 04.1)

- **Finding (confirmed, high):** `_replay()` compared only the first movement's type,
  variant and location. A different request reusing a key (another quantity, batch,
  serial set, transfer destination, reversal target or reservation quantity) received
  the original operation as a successful replay. Reproduced on PostgreSQL by 14
  service tests, the HTTP API (200 instead of 409) and two concurrent tests.
- **Decision:** each operation builds an `Intent`: movement type, variant, location,
  destination, signed quantity (3-decimal string), batch code with expiry and
  manufacture dates, the normalised set of serials (order and case do not matter),
  the reversed operation and the business reference (`reference_type`,
  `reference_id`). Its canonical JSON (sorted keys, no floats, versioned `v: 1`) is
  hashed with SHA-256 and stored as `StockMovement.request_fingerprint` on the
  operation's first movement, next to the key (additive migration
  `inventory.0003`; CHECK: a fingerprint requires a key and is 64 hex characters).
  A retry replays only when the fingerprints match; otherwise `409
idempotency_conflict`. The free-text reason, actor and a batch's supplier
  reference are descriptive and excluded, so a reworded retry still replays.
- **Historical keys:** movements recorded before Phase 04.1 keep `NULL`
  fingerprints (the ledger is never rewritten). Their identity is rebuilt from the
  recorded movements (type, variant, locations, quantities, batch code and expiry,
  serials, reversed operation, reference); a mismatch is a conflict. The manufacture
  date of such batches is not compared. Verified on the three real keys in the
  development ledger (identical retry replays, changed quantity conflicts).
- **Concurrency:** unchanged locking. Same-variant duplicates are serialised by the
  variant lock and re-checked under it; a key taken concurrently on another variant
  surfaces as the unique-key violation and is resolved by the same comparison.

### D-060 · Sellable stock excludes expired batches (2026-10-26, Phase 04.1)

- **Finding (confirmed, medium):** the public catalogue (`_in_stock`) and
  `is_purchasable()` used `StockLevel.available`, which still counts expired batch
  stock, so a product whose batches had all expired was advertised as in stock.
  `reserve()` had the same gap and could reserve expired units.
- **Decision:** `inventory.services.sellable_quantities()` is the single definition
  (three queries for any number of variants). Plain and serial-tracked variants:
  `available` at active locations. Batch-tracked variants: unexpired batch on-hand
  at active locations minus the location's whole reservation, floored at zero per
  location. Reservations are not assigned to batches in Phase 04, so assuming they
  use unexpired stock never overstates what can be sold. Untracked and digital
  variants are not stock-limited; pre-orders (future release date) stay orderable
  through their own rule and are not counted as physical stock.
- **Expiry policy:** a batch is sellable through its expiry date, inclusive, in the
  store's IANA time zone (`store_today()`, now based on `django.utils.timezone.now`).
  Undated batches never expire. Expiry changes sellability only; on-hand stock and
  the ledger are unchanged and reconciliation still balances.
- **Reservations:** `reserve()` for batch-tracked variants requires all reservations
  at the location to fit in unexpired stock. Any movement that removes unexpired batch
  stock may not leave the location's reservations uncovered (as plain stock already
  refuses to dip into reserved units); expired stock can still be written off or
  moved. Batches expiring after a reservation was made can leave it uncovered; the
  catalogue then shows zero sellable stock, and Phase 08 allocation must handle it.

### D-061 · Preset themes, brand overrides and theme application (2026-10-26, Phase 05)

- **One engine, five presentations.** The five visual identities are typed definitions in
  `@matecart/shared-ui` `presets.ts` (palette, typography roles, card aspect, density,
  columns, stepper, dark header, hero style), taken from the read-only prototype. Business
  defaults stay in commerce-api: product types (D-051), modules (D-062) and homepage sections
  (D-063). The public API returns the preset id; the browser resolves the visual theme.
- **Swatches versus tokens.** Each preset keeps the approved identity triple as `swatches`.
  Electronics' `#0B0F14` is its dark header and action colour; its page canvas follows the
  prototype storefront (`#F3F5F8`). Grocery's call-to-action uses the deeper green
  `#00843C` so white button text meets WCAG AA; `#00B050` remains its primary colour.
- **Overrides.** `StoreTheme` (one per store, `stores` app) holds an optional brand and
  accent colour (`#RRGGBB`), logo, dark-background logo, favicon (Phase 04 media assets,
  shape-checked), tagline, description, announcement, footer text and Bangla translations.
  A colour is accepted only if white or ink text on it reaches 4.5:1 (the same WCAG formula
  runs in `stores/colors.py` and `presets.ts`); the browser ignores any value that fails.
  A brand colour also becomes the action colour. "Restore preset colours" clears overrides.
- **Application.** `StoreThemeScope` sets CSS custom properties on an element; Tailwind v4
  `@theme inline` `store-*` tokens read them where used, so a server-rendered scope has the
  right preset on first paint and nothing leaks into the MateCart dashboard or console.
  Values are colours and font stacks from definitions, never merchant CSS. Status colours
  are not themeable.
- **Fonts.** Instrument Serif (Fashion) and Newsreader (Books) join Geist, Geist Mono and
  Hind Siliguri through `next/font` (downloaded at build, served by the app). Optimus is
  still not bundled (Q1).
- **Business preset changes** are not an appearance setting: `manage.py change_store_preset`
  refuses while any product uses a type the new preset does not offer, and audits the change.
  There is no customer-facing or merchant-facing preset switcher.

### D-062 · Store module registry (2026-10-26, Phase 05)

- A code-owned registry (`stores/modules.py`) lists each module's key, name, description,
  owning phase, applicable presets, preset defaults, prerequisites and whether it is
  implemented. Core modules (catalogue, inventory, pages, customer accounts) are always on.
- `StoreModule` rows store only explicit store choices; no row means the preset default.
  A module is effective only when implemented, applicable to the preset, switched on and all
  prerequisites are effective (blocked modules report `blocked_by`). Planned modules (cart,
  delivery slots, COD, payments, EMI, couriers, reviews, comparison, chat, AI and others)
  can never be switched on before their phase (`409 module_unavailable`).
- Module states are configuration consumed by the storefront (06) and later phases; in Phase
  05 they change no catalogue, pricing or inventory rule. Managing them needs `store.settings`.
  The Central Console (16) can later push the same shape; stores keep working without it.

### D-063 · Store content: pages and homepage sections (2026-10-26, Phase 05)

- `ContentPage` (slug unique per store, title, body, page/policy type, draft/published,
  footer link, position, Bangla translation) and `HomeSection` (hero, category showcase,
  collection highlight, brand story, trust strip; position, enabled flag, short texts, image,
  collection, internal call-to-action link, translations) in the `content` app.
- Content is **plain text**: anything resembling an HTML tag is refused, call-to-action links
  must be internal paths, images must be Phase 04 image assets of the same store, and
  collection highlights need a collection while enabled. The storefront renders text escaped.
- Publishing a page needs a body; reordering must name every section exactly once; the
  preset's suggested homepage (from the prototype's Content screen) can be applied once,
  while the homepage is empty. All changes are audited. No page builder, HTML editor, SEO
  engine or campaigns.

### D-064 · Public storefront configuration API (2026-10-26, Phase 05)

- `/api/storefront/config/` (store name, brand texts, public contact details, currency,
  languages, preset, brand colours, logo URLs, effective modules, published footer pages),
  `/api/storefront/home/` (enabled sections; highlights of inactive collections are omitted)
  and `/api/storefront/pages/<slug>/` (published pages) are anonymous GET-only.
- They never return staff data, module overrides, drafts, audit history or unassigned media.
  Branding images and enabled-section images are added to the public media gate only while
  assigned; reassigning or disabling removes public access.

### D-065 · Customer storefront rendering and data (2026-10-26, Phase 06)

- Storefront pages are React Server Components in commerce-web (`app/(store)/`), rendered per
  request. They read only the anonymous public APIs over `COMMERCE_API_INTERNAL_URL`, never
  forward browser cookies and never cache API responses across requests (`no-store`; prices,
  stock, publication and branding change at any time). HTML is therefore private per
  response; public media keep their short public cache. Shared HTML caching, if wanted, is a
  Phase 17 decision with explicit cache keys.
- The preset theme is resolved on the server (`StoreThemeScope`), so the first paint is already
  correct. One component set covers all presets; presets choose layouts and classes, never
  separate sites. Merchant content renders as escaped plain text; CTAs link to internal paths
  only.
- Listing state (filters, sort, page) lives in the URL and is applied by the API to the whole
  catalogue. Features owned by later phases are absent or visibly disabled ("Add to cart"),
  never simulated.
- Indexing is opt-in per deployment (`STOREFRONT_INDEXING=allow`); search pages are always
  `noindex`. `STOREFRONT_PUBLIC_URL` enables absolute sharing URLs. `STOREFRONT_PREVIEW_PRESET`
  is honoured only by development servers, for smoke checks of other presets.

### D-066 · Storefront language (2026-10-26, Phase 06)

- The customer's language is the first-party cookie `mc_lang` (one year, `SameSite=Lax`, no
  personal data), read on the server and accepted only if the store supports it; otherwise the
  store default applies. The switcher appears only for stores with more than one language.
- Merchant text uses the existing `translations` maps with per-field fallback to the default
  language; interface text has English and Bangla dictionaries. Prices and dates keep Latin
  digits; slugs, prices and stock never depend on language. No translation service is used.

### D-067 · Public discovery extensions and storefront tests (2026-10-26, Phase 06)

- `/api/catalog/products/` gains `min_price` and `max_price` (a product matches when any
  active variant's default-list price is in range; `min > max` is a validation error), and its
  search also matches active brand names, book authors and an exact ISBN-13 of an active
  variant. Summaries expose book `authors`; public variants expose `isbn`; footer links in
  `/api/storefront/config/` carry title-only translations. All additive and public by nature.
- An "in stock only" filter is not added: exact sellable stock (D-060) is computed per variant
  and would have to be duplicated in SQL. Facet lists wait for a facet API.
- commerce-web has its own Vitest and Testing Library setup (same versions as shared-ui).
  Storefront components stay in commerce-web because no other app uses them.

### D-068 · Storefront search by translated names and store-local date (2026-10-26, Phase 06.1)

- Public product search also matches `translations[lang]["name"]` for each of the store's
  supported languages, through the ORM's JSON key lookups on the already filtered queryset
  (before pagination). Keys come only from the store's languages, which the database limits to
  platform codes. No search engine, index or migration; translated summaries and
  descriptions are not searched.
- `/api/storefront/config/` returns `local_date`: `store_today()`, the store's calendar date
  in its IANA time zone, the same day availability uses. Storefront date decisions (pre-order
  labels) use it instead of the browser's or server's UTC date. It carries no time of day.

### D-069 · NetaMate managed theme architecture (2026-10-26, Phase 06.2)

- **Business preset ≠ visual theme ≠ merchant branding.** The preset (`StoreSettings.preset`)
  decides products, modules and feature availability. A theme is a visual design for exactly
  one preset. Branding (`StoreTheme`) overrides a few tokens of the assigned theme and is never
  written into the registry; resetting colours returns to the theme's own colours.
- **Functional parity.** Themes are presentation only: one commerce engine, shared functional
  components and APIs, several visual themes. Themes of one preset never differ in business
  rules, data, permissions, prices, stock, search, cart, checkout, orders or enabled modules,
  and never hide a capability. Feature availability follows the preset, modules and platform
  configuration, never theme identity. Tests compare capabilities across themes of a preset.
- **Registry.** Code-owned and released with MateCart: identities in shared-contracts
  `themes.json` (validated by commerce-api `stores/themes.py`), visual definitions in
  shared-ui `THEMES` with presentation variants per storefront region. Identifiers are
  permanent, never display names; each preset has exactly one default. A design change raises
  `revision` and ships in a normal release; no remote theme fetching, uploads or plugins.
- **Assignment.** `StoreSettings.active_theme` (blank: the preset default) is platform-owned.
  Only the trusted, audited `assign_theme` service changes it (today the `assign_store_theme`
  command; later the Central Console through signed deployment operations, Phases 16-17).
  Merchants cannot select, activate, switch, buy, upload or remove themes; merchant and public
  APIs expose it read-only. Self-service switching between purchased themes is not approved.
- **Resolution and independence.** The store resolves its theme locally from its own database
  (assignment, else default); an unknown or incompatible value falls back to the preset default
  with an operator log, never another preset's theme. Rendering never calls the console or a
  licence service.
- **Licences (future).** Theme definition, assignment, entitlement and branding are separate. A
  premium licence check belongs in the platform before a trusted assignment; the store keeps no
  licence, price or purchase records. The showcase (themes.matecart.pro) and marketplace are
  later work.

### D-070 · Server-owned cart, checkout quote and temporary reservation boundary (2026-10-26, Phase 07)

- **Ownership.** A cart belongs to exactly one customer or one guest credential. Guest credentials
  are 256-bit opaque values kept only in an HttpOnly, SameSite=Lax, secure-in-production cookie;
  only their SHA-256 digest is stored. Customer sign-in atomically associates or merges the guest
  cart without accepting an owner from request data. Active carts expire after 30 days.
- **Authoritative checkout.** The API derives every line and amount from the current catalogue,
  `pricing.services.quote()` and inventory. A checkout stores product/variant/price snapshots,
  contact and delivery snapshots and Decimal totals. Drafts expire after 30 minutes. Changed
  prices or cart contents refresh the snapshot and require review again rather than silently
  proceeding. Purely digital checkouts require no delivery address or method.
- **Review and stock.** Review takes a client-generated idempotency key, locks variants in stable
  order, revalidates the snapshot and uses the existing inventory reservation ledger. A reviewed
  checkout holds tracked physical stock for 15 minutes; an idempotent Celery expiry task releases
  abandoned holds. Adding to a cart never reserves stock. Phase 08 consumes a reviewed checkout,
  revalidates it and owns the irreversible order, allocation and payment transitions.
- **Calculation.** One service calculates item subtotal, a basic fixed or percentage coupon,
  shipping, configured regional tax and grand total, rounding at ISO 4217 minor units. Shipping
  methods use store-owned prices, currency, country eligibility, minimum subtotal and optional
  free threshold. Grocery delivery slots are capacity- and time-filtered when that module is on.
- **D-053 reconciliation.** Phase 07 implements only coupon validation and quote calculation.
  Redemption is not recorded until Phase 08 creates an order. Campaigns, stacking, scheduling,
  flash sales, loyalty, analytics and promotion administration remain Phase 12. This narrows, but
  does not reverse, D-053's deferral of the broader promotion engine.
- **Configuration and independence.** `cart_checkout` is implemented and on by default for all
  presets; `delivery_slots` is Grocery-only and `preorders` is Books/General. All follow the
  existing module rules. Checkout is one theme-independent engine and never calls control-api,
  a carrier, tax provider or payment provider. The zero-cost “Standard delivery” row is a safe
  bootstrap default a merchant can replace with store-owned configuration; it makes no carrier or
  country-specific claim.

### D-071 · Order placement, state machine and stock ownership (2026-10-27, Phase 08)

- **Placement.** `POST /api/checkout/<id>/order/` turns a reviewed, unexpired checkout into one
  order in one transaction (locks: cart, checkout, variants, coupon, slot, number sequence; the
  checkout-review order). A database unique `(tenant, checkout)` and the placement key make it
  idempotent: the same key replays the order; another key gets `409 checkout_completed`. Items,
  prices, contact, delivery, slot and coupon are snapshotted; `OrderItem` and `Invoice` refuse
  updates. Numbers come from a locked per-store `DocumentSequence` (`ORD-000001`, `INV-000001`).
- **Stock lifecycle.** Three stages: checkout hold (Phase 07 `reserved` movement), order-owned
  reservation (`OrderAllocation`, created from each `CheckoutReservation` with no new ledger
  movement; unique per source reservation), and deduction at dispatch. Checkout release/expiry
  ignores a completed checkout, so a converted hold can never be released by it. Cancellation
  releases each allocation once (state guard + idempotency key). `fulfil_order_stock()` is the
  Phase 09 handoff: it deducts each allocation once; `inventory.fulfil` now takes batch stock from
  unexpired lots first-expired-first-out at dispatch and requires named serials (sold once).
  Placement does not deduct stock and is not dispatch.
- **Batch expiry at placement.** Reservations remain location-level (D-060), so any unexpired lot
  at the location is the replacement for one that expired since review. Placement requires
  unexpired lot stock to cover already-placed allocations plus this one; otherwise
  `409 stock_expired`. A rule that fails at placement (`stock_expired`, `coupon_unavailable`,
  `delivery_slot_unavailable`, `item_unavailable`) rolls back and then releases the checkout hold,
  so no order exists and nothing stays reserved. Cross-location relocation is not attempted.
- **States.** `pending_payment` (online) and `placed` (COD) → `confirmed` → `processing`; any of
  those → `cancelled` (terminal). Online orders are confirmed only by a verified payment; merchants
  confirm COD orders. Customers may cancel unpaid `placed`/`pending_payment` orders. Every edge
  writes an append-only `OrderStatusEvent` (ORM guard and PostgreSQL trigger, `orders.0002`) and an
  audit event. Shipping and delivery states belong to Phase 09. Fulfilled orders cannot be cancelled.
- **Slots and coupons.** The reviewed checkout's slot hold becomes `bookings + 1` under the slot
  lock (capacity rechecked); completing the checkout stops it counting as a hold. The coupon is
  redeemed at placement for every payment method (`CouponRedemption`, unique per order, usage
  limit rechecked under the coupon lock), so unpaid online orders cannot oversubscribe it.
  Cancellation (including payment expiry) voids the redemption and returns a booking whose slot
  has not started. Refunds do not restore coupon use.
- **Guest access.** A guest order gets its own 256-bit credential in the HttpOnly `mc_order`
  cookie (digest stored); a replay by the checkout owner re-issues it. It covers the latest guest
  order in that browser; guest order lookup by email belongs to Phases 10/13. Customers see only
  their own orders. An order number alone never grants access.

### D-072 · Payment boundary, cash on delivery and callbacks (2026-10-27, Phase 08)

- **Provider-neutral.** No gateway has been selected by the owner. `MATECART_PAYMENT_PROVIDER`
  (empty by default) names one adapter implementing `initiate`, `authenticate_callback` and
  `refund`; adapters own credentials, signatures and gateway validation calls. Production refuses
  `tests.*`. Without a provider, checkout never offers online payment and the `online_payments`
  module refuses to switch on (`409 payment_provider_unavailable`). EMI stays unavailable.
- **COD.** `cash_on_delivery` is implemented and on by default for every preset. It is offered
  only for all-physical orders with a total above zero. A COD order has an `awaiting_collection`
  intent and stays `unpaid`; the merchant records collection (`orders.manage`, confirmed orders,
  once), which creates the only COD transaction. Courier COD reconciliation is Phase 09.
- **Online flow.** An online order waits `pending_payment` for 30 minutes (Celery task and on read).
  Starting payment creates or reuses one open intent and calls the provider outside database locks;
  a timeout returns `503` and is retried safely. Only an authenticated callback changes money: it is
  deduplicated by event id (`ProviderCallback`, body digest and allowlisted summary only) and by
  transaction reference (unique per provider), checked against the intent's reference, amount and
  currency, and applied under the order lock. Failures after success are ignored; a success after a
  failure or after expiry is recorded. A late payment never revives a cancelled order; it is kept as
  paid for a refund. Browser return URLs change nothing.

### D-073 · Refunds and invoices (2026-10-27, Phase 08)

- **Refunds.** Merchant only (`orders.manage` plus a recent sign-in, like D-048). Each refund is tied
  to one verified payment or COD collection and capped by its unrefunded amount, counting pending
  refunds; the order's `amount_refunded <= amount_paid` is a database constraint. The idempotency
  key returns the same refund; a different amount with that key conflicts. Provider refunds stay
  `pending` until the provider confirms; failures are `failed` and never counted. COD refunds are
  `manual`: the merchant attests the cash was returned. Returns and exchanges are Phase 10.
- **Invoices.** One invoice per order, issued at placement in the same transaction, with frozen
  seller (store name, legal name, contact) and buyer details and totals; lines are the immutable
  order items. Payment status and received/refunded amounts come from verified records, so an unpaid
  invoice never claims payment. Presentation is a printable page (browser print/PDF) without new
  dependencies, independent of the storefront theme. No tax IDs, registration numbers, signatures
  or QR codes are invented; customisation and QR remain deferred.

### D-074 · Physical shipment, courier COD and fraud-review boundary (2026-10-27, Phase 09)

- **Shipment boundary.** An order has at most one physical shipment in Phase 09. It contains only
  physical order items and has its own controlled state machine; order status remains the commercial
  lifecycle. Partial packages, split fulfillment, labels, route planning, returns and a WMS are not
  introduced. Digital-only orders have no shipment. Dispatch is the single irreversible edge that
  calls Phase 08's `fulfil_order_stock()` in the same transaction; shipment/order locks are always
  acquired before inventory locks, so cancellation and dispatch cannot both win.
- **Manual first, provider ready.** Authenticated `couriers.manage` staff can operate shipments and
  tracking without any integration. One optional `MATECART_COURIER_PROVIDER` adapter implements
  idempotent booking/cancellation and authenticated tracking callbacks. It is empty by default;
  production refuses `tests.*`, provider calls run outside database locks, and pending/unknown
  outcomes never claim a booking. Provider callbacks are deduplicated, tenant/reference scoped and
  can update only post-dispatch states; they cannot dispatch stock.
- **Tracking history.** Applied tracking events are append-only in the ORM and PostgreSQL. Provider
  callback logs retain only event/reference identifiers, a body digest and outcome, never raw
  payloads. Stale/out-of-order or illegal backward events are recorded as callback outcomes without
  altering the shipment. Customers see only real references/events and HTTPS tracking links.
- **Courier COD.** One courier COD record snapshots amount due and separately records collection,
  fees, permitted documented deductions, expected remittance, actual remittance and discrepancy.
  Delivery never implies payment. Authorized evidence is sufficient for Phase 08's one COD payment
  transaction only when collected cash exactly equals the order total; under/over-collection remains
  unpaid for review. Remittance is a separate recent-authenticated assertion and is not accounting,
  bank synchronization or a payout system.
- **Fraud review.** Initial deterministic rules use only the store's own recent COD/order/shipment
  history: three prior matching-contact COD orders in seven days, two recent failed/returned
  shipments, or two same-item COD orders in 24 hours. Flags expose counts/windows but no other
  customer's identifiers. Authorized reviews are append-only. Scores and flags never automatically
  cancel, blacklist or block dispatch; a merchant must use the normal order decision path.

### D-075 · Verified after sales and digital entitlement boundaries (2026-10-27, Phase 10)

- Customer return and exchange requests never directly issue refunds, restore
  stock or dispatch replacements. Existing payment, inventory and fulfillment
  services remain authoritative; a merchant must supply independently verified
  refund or replacement order evidence before resolving a case.
- Purchase linked product reviews require payment verification and merchant
  moderation. Published reviews expose rating and review text but not account
  details; all submissions and decisions use tenant scoped persistence.
- Download entitlements bind one immutable purchased item to a private document.
  Payment callbacks (or verified existing payments during asset assignment)
  grant access. Refunds conservatively revoke access; the private file endpoint
  enforces customer ownership, paid status and a bounded download counter.
  Public media serving refuses digital assets to prevent an accidental leak.
- Digital resource formats are limited to already validated PDF documents until
  safe additional byte validators and upload contracts are implemented.
  No customer support flow creates payments or stock changes itself.

### D-076 · Attributed return refunds and pre-dispatch replacement proof (Phase 10.1)

- Unrelated refunds on the same order cannot settle a return. The existing
  authorized merchant refund API accepts optional `return_case` and records
  immutable, tenant-scoped attribution when the refund is requested, before
  any payment outcome. One refund has one case; multiple partial refunds can
  meet a single case's net returned-item value.
- The refundable goods share is calculated from immutable order item quantity
  and line total, with order discount and tax apportioned by the original
  item subtotal. Shipping charges are excluded. A successful sum at least
  equal to the returned goods value is required for case resolution.
- An exchange must bind a matching customer order after case creation and
  before dispatch, using the case's existing one-to-one replacement field.
  The binding is audited; only the originally bound, delivered order proves
  resolution. Neither route creates money or shipments independently.
- Current digital asset mappings and all historical entitlement snapshots
  are private in the public media route even if later assigned to content.

### D-077 · Merchant-assisted (manual) orders (2026-11-03, Phase 11)

- Staff with `orders.manage` enter orders that customers requested by phone, Facebook,
  WhatsApp, in store or elsewhere. `commerce.orders.assisted` is a thin orchestration layer,
  not a second order engine: inside one transaction it builds a staff-owned cart and checkout,
  runs the storefront's checkout rules (`quote_cart`, `update_checkout`), the same review step
  (variant locks, temporary reservations, slot holds, coupon validation, eligibility) and the
  same placement step (`orders.services._place`: number, allocations, coupon redemption, slot
  booking, invoice, history, audit). Any failure rolls everything back; nothing stays reserved.
- Ownership is explicit. `Cart` and `CheckoutSession` gain `assisted_by` (the staff user) as a
  third owner kind beside customer and guest credential. No guest token is minted for staff.
  An assisted order has no customer account and no guest credential (`orders_order_one_owner`
  allows an empty credential only for non-storefront sources), so it is never reachable from a
  browser. Accounts are never created or linked by matching an email or phone.
- `Order.source` (default `storefront`) records the channel; existing and website orders keep
  `storefront`. `orders.AssistedOrder` (one per order, immutable in the ORM and a PostgreSQL
  trigger) records the creating staff member, time, optional channel reference, a request
  fingerprint and the staff attestation that the customer asked for the order. The
  attestation is the staff member's statement, not a customer signature or verified consent.
- Payment is cash on delivery only, under the existing COD rules (all-physical orders with
  shipping; digital or mixed orders refused). Staff cannot hand an online payment page to a
  customer and no in-store collection method exists, so no other method is offered. The order is
  unpaid until the existing collection operations record real cash.
- Prices, discounts, shipping and tax are server-calculated by the existing services. There is
  no price override; a future, separately authorized override would need its own decision.
- Idempotency: the request key is unique per store (`orders_assisted_key_unique`). A replay
  returns the same order (HTTP 200); the same key with a different request fingerprint is
  `409 idempotency_conflict`. A concurrent duplicate that loses the race (unique violation or a
  business refusal such as stock taken by the winner) returns the winner's order.
- A draft quote endpoint prices the order inside a rolled-back transaction: it reports per-line
  stock issues, totals, eligible methods and slots without reserving anything.
- Phase 14 may later call `place_assisted_order` from its own channels; Phase 11 adds no
  external integration.

### D-078 · Dashboard and reports from authoritative records (2026-11-03, Phase 11)

- `commerce.orders.reports` reads only stored records: order value from placed, not-cancelled
  orders; verified collections from succeeded payment and COD-collection transactions; refunds
  from succeeded refund transactions; COD still to collect from open COD payment intents; courier
  cash to remit and shortfalls from COD records. Placing an order is never reported as revenue.
- Ranges are store-local calendar days (store time zone), at most 366 days; `days=N` means the
  last N store days including today. Every figure is an indexed aggregate (`orders_order_placed`
  index added). Money stays Decimal, grouped per currency and rounded to its minor unit.
- `GET /api/manage/dashboard/` is open to any active staff member but each section is gated:
  orders, operations and after-sales need `orders.manage`; money needs `reports.view`; stock
  alerts need `catalog.manage`. `GET /api/manage/reports/sales/` needs `reports.view`.
- No visitor, traffic, campaign or attribution figures (Phase 12). The only export is a CSV of
  the server's daily rows, generated in the browser without recalculation.

### D-079 · Merchant workspace, customer records and dashboard contracts (2026-11-03, Phase 11)

- `/manage` remains one client application behind the catch-all route. A shared route table
  (`apps/commerce-web/src/lib/manage-routes.ts`) 404s unknown paths on the server and builds the
  grouped, permission-filtered navigation (Overview · Sell · Operate · Store) in the client.
  Hiding a link is not authorization; every API keeps its own permission.
- Existing fixed roles are kept; no custom role editor. Order handlers get an orders-scoped,
  read-only variant lookup for manual orders because they have no catalogue access.
- `GET /api/manage/customers/` (`customers.view`) lists registered customer accounts only, with
  their own orders and requests. Password hashes, MFA state, recovery data, sessions and tokens
  are never returned, and staff cannot act as a customer.
- Merchant response shapes add facts the UI needs instead of re-implementing rules in the
  browser: order `allowed_transitions`, `can_record_cod`, `refundable_amount`, payments,
  provenance and related cases; shipment `next_statuses` (never cancellation, per Phase 09.1),
  serial allocations needed at dispatch and whether a courier provider is configured; case
  `next_statuses`, attributed refunds and the required refund value. The server enforces every
  rule again on submission. The staff session adds the store's preset, country, currency and
  time zone for formatting and form defaults.
- Merchant pages reuse `@matecart/shared-ui` controls and theme tokens; `DashboardShell` gains
  optional grouped navigation, an active-page marker and a collapsible phone menu while the
  console keeps its default navigation.

### D-080 · Merchant dashboard integrity: reviewed totals, confirmed cash, sellable alerts (2026-11-03, Phase 11.1)

- **Manual order totals.** The form keys each server quote to the exact pricing inputs it was
  calculated from (items, quantities, country, region, coupon, delivery method and slot). A
  changed input hides the total and disables placement at once; only the response to the latest
  request is kept. `POST /api/manage/orders/assisted/` requires `expected_totals` (currency, item
  subtotal, discount, delivery, tax, grand total from the reviewed quote). After the normal
  review step the service compares them with its own totals; any difference is
  `409 quote_changed` and the whole transaction rolls back (no order, reservation, coupon
  redemption or slot booking). The merchant sees the new quote and places again with the same
  idempotency key. `expected_totals` is not part of the request fingerprint, so a replay of a
  placed order still returns it. Pricing stays in the checkout services.
- **COD collection.** `POST /api/manage/orders/<id>/cod-collection/` now also requires recent
  authentication (`sessions.require_recent_authentication`), like refunds and shipment COD
  operations, on top of `orders.manage`. The UI confirms with the order number, amount and
  currency, states that the money must actually have been received, and asks for the password.
  The collection operation, amount, state checks and idempotency are unchanged.
- **Stock alerts.** Dashboard and report low/out-of-stock counts use sellable stock:
  `inventory.services.with_sellable` computes, in one SQL query, the same per-location figure as
  `sellable_quantities` (unexpired batch stock minus the location's reservation for batch
  tracking, `available` otherwise; active locations only; never negative). Only physical,
  inventory-tracked variants count, and each variant is counted once: low when an active
  location's sellable stock is at or below its alert, out when no active location can promise
  any. Alert rows show on-hand, available and sellable separately. The inventory page's level
  filters still describe physical levels.

### D-081 · Promotion engine: campaigns, flash sales and no stacking (2026-11-04, Phase 12)

- New app `commerce.marketing`. Campaigns (`discount` percent/fixed or `featured` badge-only)
  target products, categories (with subcategories) and collections. Flash sales list variants
  with a fixed sale price, an optional unit limit and a per-order limit, and need the
  `flash_sales` module. Stored lifecycle `draft/active/paused/cancelled`. The displayed state
  (`scheduled/live/ended`) is derived from the server clock, never from the browser. Live,
  ended or cancelled promotions are read-only.
- One promotion per variant, never stacked. A live flash sale wins when it fits the quantity and
  limits. Otherwise the lowest-price live discount campaign applies (ties: earliest start, then
  id); a campaign never brings a price to zero or below and fixed amounts need the same
  currency. Otherwise a featured campaign adds a badge only.
- `pricing.quote()` returns the promotional selling price on the default price list (quantity
  aware), so cart, review, assisted orders and placement share it. Checkout lines and order
  items snapshot regular price and promotion. Changes before review follow the existing
  `price_changed` review. The reviewed price holds for the reservation window. Limited flash
  units are committed at placement under the item row lock, so they are never oversold
  (`promotion_unavailable` releases the hold). Cancellation releases them once.
- Coupons (Phase 07) are unchanged and still validated and redeemed by checkout and placement.
  Lines at a promotional price are not coupon-eligible: a coupon discounts only regular-price
  lines, and a cart that is entirely on sale refuses it. Without promotional lines the
  calculation is exactly the previous one. Merchants now manage coupons in `/manage/marketing`.
  A used coupon's value and minimum are fixed.
- Price filters and sorting in the public catalogue still use regular prices (documented
  limitation).

### D-082 · Loyalty points: verified earning, append-only ledger, deficit recovery (2026-11-04, Phase 12)

- One program per store (off by default; the `loyalty` module and the program switch are both
  needed). Only signed-in customers with a verified email or phone take part. Guest and
  merchant-assisted orders never earn, and nothing is matched by email or phone.
- A pending award is fixed at placement. It becomes usable only when the order is paid in full
  and delivered (digital-only: paid). `sync_order` is target-based and idempotent, and runs after
  cancellation, payment, refund and delivery. Ledger references are unique per business event.
- Redemption is part of the order discount (`Order.loyalty_discount`, `loyalty_points_redeemed`).
  It is capped by minimum, percentage cap and balance, and debited at placement under the account
  lock. Points are restored once on cancellation or full refund, not on partial refunds.
- Refund reversals never make the balance negative: the spent part becomes an explicit
  `deficit`, repaid first from later earnings. Optional inactivity expiry is applied lazily.
  Staff adjustments need `promotions.manage` + `customers.view`, a recent sign-in, a reason and
  an idempotency key, and are audited. The ledger is append-only (ORM and PostgreSQL trigger).

### D-083 · Consent and first-party analytics (2026-11-04, Phase 12)

- Categories: strictly necessary (always), analytics and marketing (off until chosen). Decisions
  are appended to `ConsentRecord` under the SHA-256 of a random consent id (no IP, agent or
  account) and kept in the `mc_consent` cookie. Updates are refused by a trigger; deletion after
  two years is the retention policy. Withdrawal clears tracking cookies, discards queued Meta
  events and deletes the visitor's presence row.
- Analytics are aggregates: hourly `EventCounter` rows (all visitors, no identifiers),
  server-counted add-to-cart and checkout-started events, and one `VisitorPresence` row per
  consented visitor (a keyed hash of a random cookie id, kept 48 h). `VisitorDailyStat` holds
  per-day uniques. Rolling 24-hour unique visitors are exact. Orders and money always come from
  order and payment records.
- **Correction (CI follow-up).** Rolling 24-hour page, product, cart and checkout totals now come
  from `RecentEventCounter`. These are per-minute totals with no dimension or identifier, kept 25 h
  (at most about 1,500 rows per event and consent flag). The window starts at the first whole
  minute inside the last 24 hours. Before this, the hourly buckets covered only 23 to 24 hours.
  Rolling orders use the same `(now - 24 h, now]` window as visitors. Hourly breakdowns keep hourly
  buckets and report their own start (`breakdown_start`). The dashboard labels figures as exact,
  per-minute or hourly.
- Attribution is the last tagged (UTM) landing within 30 days, recorded only with analytics
  consent, never for merchant-assisted orders. It is labelled a tracked channel, not causation.
- No scheduler is added: clean-up is queued at most hourly by traffic and is available as
  `manage.py prune_marketing_data`. Events are allowlisted, validated, bot-filtered and limited
  to 120 per IP per minute. Analytics failures never block commerce.

### D-084 · Meta Pixel and Conversions API (2026-11-04, Phase 12)

- Off by default. The Pixel loads only with Meta enabled, a valid Pixel id and the visitor's
  marketing consent. The server sends only `Purchase`, with `event_id = purchase.<order id>`
  shared with the browser Pixel for deduplication.
- Purchase timing is a merchant choice: on verified payment or collected cash (default), or at
  placement. In the latter mode COD orders are reported `unpaid`.
- Delivery rows (`MetaEventDelivery`) are unique per event id and created only with marketing
  consent at placement. They are sent by an idempotent Celery task outside locks, with five
  attempts at most, and discarded on withdrawal or when the integration is switched off.
  Browser identifiers are cleared when an event finishes. Hashed contact matching is opt-in.
- The access token is a deployment environment secret (`META_CAPI_ACCESS_TOKEN`, empty locally
  and in tests), never stored, returned or logged. Server events also require the HTTPS
  `MATECART_PUBLIC_BASE_URL`. The Graph API version is configurable (`v26.0` default). The
  adapter uses the standard library, so no new dependency was added.

### D-085 · SEO, indexing and canonical safety (2026-11-04, Phase 12)

- Indexing needs `STOREFRONT_INDEXING=allow`, a trusted HTTPS `STOREFRONT_PUBLIC_URL` (not
  localhost) and the merchant's preference (`store.settings`); otherwise `noindex`, a disallow-all
  `robots.txt` and an empty sitemap. The Host header is never used.
- `SeoSettings` (home title, description, social image, indexing preference) plus plain-text
  `seo_title`/`seo_description` on categories, collections and pages (products already had
  them). Editing needs `content.manage`. `<`/`>` are refused, and JSON-LD escapes `<`.
- Sitemaps come from `GET /api/storefront/sitemap/`: a pages segment plus product segments of
  5,000 URLs, published records only. Language is cookie-based on one URL per page, so no
  `hreflang` is emitted. Public category and collection payloads now include `id` for view
  events.

### D-086 · Email transports: SMTP, IMAP verification, MateMail Connected App (2026-11-04, Phase 13)

- **Transports.** One store transport at a time: the enabled, connected `MailConnection` (SMTP or
  MateMail), otherwise the deployment default (`EMAIL_URL`). There is no automatic failover after
  a provider may have accepted a message.
- **SMTP.**
  - Credentials are encrypted with `commerce.core.security` and never returned, logged or audited
    by value.
  - Connected requires a successful login test **and** sender proof: a code sent to the From
    address through that server. A changed host, credential or From address resets this.
  - Test messages go only to the signed-in staff member's verified address.
- **Network safety.**
  - Hosts are resolved once, every address must be public, and the socket is pinned to the
    vetted IP while TLS verifies the host name.
  - Standard mail ports only; TLS 1.2+ with certificate validation; 15-second timeouts; safe
    error codes only.
  - `MATECART_MAIL_INTERNAL_HOSTS` (development: `mailpit`) is the only exception.
- **IMAP** is configuration plus read-only login verification. Inbound mail processing is out of
  scope.
- **MateMail.** The native connector uses MateMail's documented Connected App API (audited
  read-only; no MateMail change):
  - tenant ID plus one-time integration secret, then MateMail approval, then a revocable `mmc_`
    token with `mail.send`;
  - the From address is forced to the approved mailbox, and the idempotency key is
    `matecart-<notification id>`;
  - 409 means outcome unknown, 401 means revoked; reconnecting rotates and disconnects the old
    token;
  - `MATEMAIL_BASE_URL` is deployment-only.
- Credential and transport changes need `store.settings` and a recent sign-in.

### D-087 · Notification engine: durable outbox, single attempt policy, events (2026-11-04, Phase 13)

- **Outbox.** `commerce.notifications`: `Notification` rows are the durable outbox. They are
  written in the business transaction inside a savepoint that never propagates failure, and are
  unique per `(event_key, channel, recipient hash)`. Delivery is queued after commit; a
  MateCart-only Celery beat sweeper re-queues pending and stranded rows.
- **Delivery.** Delivery claims a row, sends outside locks, and appends a `DeliveryAttempt`
  (UPDATE refused by trigger).
  - Retries: at most 5 attempts, with backoff of 1, 5, 15 and 30 minutes, for temporary errors
    only.
  - A permanent error fails the message.
  - An unknown outcome (failure after SMTP DATA, MateMail 409, or a worker dying mid-send) is
    failed and never resent automatically. Staff may resend deliberately (audited, rate limited).
- **Events.** Hooks observe committed services: placement, status transitions, verified payment
  and COD collection, refunds, shipment transitions, after-sales, reviews, fraud flags, password,
  MFA and role changes. They never change business state. COD is "paid" only after collection,
  and courier replays send nothing.
- **Staff routing.** In-app staff rows go to members whose fixed role grants the event's
  permission, re-checked on read. Staff email is opt-in per topic.
- **Stock alerts** use the Phase 11 sellable-stock definition and fire on worsening transitions
  only.

### D-088 · Templates, rendering and retention (2026-11-04, Phase 13)

- **Templates.** System texts in English and Bangla for every kind. Merchants override plain text
  per language with allowlisted `{variables}` only, and can disable a kind except security kinds.
- **Rendering.** The server builds the HTML layout from store branding. All values are escaped,
  subjects are single-line, and links use the store's public URL only.
- **Versioning.** Content is rendered at emission and stored with the template version, so edits
  never change committed messages.
- **Retention.**
  - Message content and recipient address are cleared after 90 days.
  - Read in-app rows are deleted after 180 days; records after 400 days.
  - SMS usage and consent evidence are kept.
- Authentication codes, links and invitations stay on the synchronous after-commit path (D-041),
  never in Celery or notification records.

### D-089 · SMS gateway: approved senders and segment accounting (2026-11-04, Phase 13)

- **Provider.** The existing `SmsProvider` boundary gains an optional `sender` argument. The
  provider is per deployment.
- **Sender ids.** Merchants request one (`pending`). Only NetaMate records approval
  (`manage.py approve_sms_sender`), and SMS can be enabled only after approval.
- **Accounting.** `SmsUsage` records segments (GSM-7 160/153, UCS-2 70/67) per submission,
  including authentication SMS. The monthly allowance is used first, then provisioned prepaid
  credits. When both are exhausted, SMS is suppressed (authentication SMS is never blocked).
- **Limits.** 6 SMS per recipient per hour, 500 per store per hour.
- No SMS purchase, recharge or billing (deferred).

### D-090 · In-app inbox, preferences, marketing consent and platform boundary (2026-11-04, Phase 13)

- **Inbox.** In-app rows belong to one user. Staff inbox rows are filtered by current
  permissions; customers see only their own. Links are internal paths behind the same checks.
- **Preferences.**
  - Essential order and security email cannot be disabled.
  - Customers can disable SMS order updates; staff choose email per topic.
- **Marketing.**
  - Requires an explicit opt-in, recorded append-only in `CommunicationConsent` (trigger refuses
    UPDATE and DELETE). Phase 12 tracking consent never counts.
  - Signed, store-bound unsubscribe links (90 days) need a click and are idempotent.
  - Queued marketing is suppressed after withdrawal.
- **Platform boundary.** The Control API keeps its own email configuration and never uses a
  store's credentials. Central infrastructure alerts are Phase 16 and are not implemented.

### D-091 · SMS delivery integrity: outcome classes and atomic unit reservation (2026-11-04, Phase 13.1)

- **Context.** Phase 13 sent an SMS and only then recorded it.
  - Any provider exception was treated as "not accepted" and retried, so a timeout after the
    gateway accepted could deliver the message twice.
  - The balance check ran without a lock, so concurrent deliveries could all pass it.
  - Units were assigned after sending, which led to an `overage` fallback, and a message could
    not combine allowance and prepaid units.
  - Authentication SMS consumed the store's units.
- **Outcomes.** Only `SmsRejected` raised by the adapter proves non-acceptance (retryable or
  permanent). Any other exception is unknown: no automatic resend, units held, manual
  reconciliation (`reconcile_sms`, audited). Retries stay bounded (5 attempts).
- **Idempotency.**
  - Optional and declared by the provider (`supports_idempotency_key`).
  - The key is stable per notification: `matecart-sms-<id>`.
  - The contract stays backward compatible: authentication calls are unchanged, and providers
    without the declaration never receive the argument.
- **Reservation.**
  - Reserved in the delivery claim transaction under a `SELECT … FOR UPDATE` lock on
    `SmsSettings`.
  - The store-local month's allowance is used first, then prepaid credits (deducted at once,
    non-negative column), split within one message when needed. Insufficient units suppress the
    message: no overage, no negative balance.
  - Lifecycle: `reserved`, then `accepted`, `released` or `held`. A partial unique constraint
    allows one reservation per notification attempt.
  - Lock order: notification, then `SmsSettings` when reserving; notification, then the
    reservation, then `SmsSettings` when settling.
- **Authentication exemption.** Sign-in, linking and reauthentication SMS never draw on or wait
  for notification units. They are recorded as `funded_by = auth` with zero units, for usage
  visibility only.
  - Who bears their gateway cost is a billing question for the deferred billing scope.
- **Schema.** `notifications.0003` adds `attempt`, `state`, `allowance_segments`,
  `prepaid_segments`, `resolved_at` and `resolution` to `SmsUsage`, with check constraints.
  - Historical rows are mapped as they were charged; legacy `overage` rows keep their label and
    the value is never written again.
- **Not changed:** the engine, email transports, the dashboard and the API shape (`balance`
  keeps its fields; `used` now includes reserved and held units).

### D-092 · Real-time transport: Django Channels on ASGI with a Redis channel layer (2026-11-05, Phase 14)

- **Choice.** `channels` 4.3.2 (declares Django 5.2 and Python 3.13) with `channels-redis`
  4.3.0 on the store's own Redis (database 3), served by `uvicorn[standard]` 0.54. One ASGI
  application (`config.asgi`) serves HTTP (unchanged Django) and WebSocket. No separate
  messaging service. Daphne was not chosen (Twisted stack); `types-channels` needs django-stubs 6,
  so mypy ignores the untyped packages narrowly.
- **Record versus transport.** PostgreSQL is the record. Events are published after commit; an
  acknowledgement is sent only for a committed message; a broker failure never fails the
  operation and clients reconcile on reconnect. Redis is never the only copy of anything.
- **Routing.** Next.js proxies `/api/*` upgrades in development and `next start`; production
  routes `/api/*` (including upgrades) to the API in Phase 17. Staff sockets are under
  `/api/manage/ws/` because the staff cookie is path-scoped there.
- **Development server.** `uvicorn --reload` with polling (Windows bind mounts), no access log
  (URLs can carry one-time tokens), `--ws-max-size 65536`, no proxy-header trust.
- **Production guard.** `MATECART_CHANNEL_LAYER_URL` is required, so every worker shares events.

### D-093 · Website live chat: MateCart-native widget, guest credential and genuine availability (2026-11-05, Phase 14)

- No third-party chat widget or script. The `live_chat` module (registry) is now available and
  off by default; off removes the widget, refuses `/api/chat/*` and sockets, keeps history.
- Guests get an opaque `mc_chat` credential (HttpOnly, Lax, path `/api/chat`, 30 days sliding,
  hashed at rest), issued only when they write. It authorises one participant and nothing else;
  web storage never holds it.
- "Online" means opening hours (store time zone) and at least one staff socket with a live
  heartbeat. Otherwise the away message shows. No fabricated availability or reply times.
- Hidden on checkout pages and layered below the consent banner, so it never covers payment,
  order or consent controls.

### D-094 · Meta channels: one signed webhook, provider-scoped identities, honest delivery (2026-11-05, Phase 14)

- Official APIs only (Graph API version in `META_GRAPH_API_VERSION`): Messenger Send API, the
  Messenger Platform for Instagram professional accounts linked to a Page, and the WhatsApp
  Cloud API. No scraping, personal-account automation or unofficial clients.
- Webhooks: HMAC-SHA256 over the raw body with the app secret before parsing; routed only to a
  live connection of this store by the account id the event names; provider ids unique per
  connection; receipts monotonic.
- Delivery states: queued, sending, accepted, delivered and read only when reported, failed,
  unknown. Unknown outcomes are never resent automatically (no provider idempotency key);
  bounded retries only for requests that never reached Meta and for rate limits. No cross-channel
  failover.
- Messaging rules are enforced on the server (`policy.reply_policy`): 24-hour windows; Human
  Agent tag only when Meta approved it for the app; WhatsApp outside the window only with
  approved utility templates and recorded agreement; no marketing templates; no bulk sending.
- Meta's pricing category is displayed only; no conversation billing or credit purchasing.

### D-095 · Participants are not customers; association only by proof (2026-11-05, Phase 14)

- `Participant` (channel identity) and `identity.User` (customer account) stay separate. No
  account is created from a message; nothing is merged by name, phone or email.
- Verified association: a signed-in customer chats as themselves; a guest chat joins an account
  only when the visitor confirms it while holding both credentials. External participants are
  never associated in this phase.

### D-096 · Conversation privacy, rendering and retention (2026-11-05, Phase 14)

- All message text is plain text, rendered escaped; control characters are removed; files are
  validated from their bytes and served only through checked views with `nosniff`, a sandbox CSP
  and `private, no-store`.
- Internal notes live in their own table and are published only to staff groups.
- Retention defaults: closed conversations 365 days after closing, guest website conversations
  180 days after the last message: text, product snapshots, provider ids, files and notes are
  removed; rows, counts and order links stay. Expired guest credentials cleared; delivery
  attempts kept 90 days; tokens erased at disconnect. Configurable per deployment. This is
  data minimisation, not a legal compliance claim.

### D-097 · Orders from conversations through the assisted engine; development simulator (2026-11-05, Phase 14)

- No social order engine: `place_assisted_order` (D-077) unchanged, COD only, explicit
  `customer_request_attested`, reviewed totals, idempotency, no account. Requires
  `messages.manage` and `orders.manage`.
- `OrderSource` gains `instagram` and `live_chat` (migration `orders.0006`); Messenger stays
  `facebook`. Provenance: `AssistedOrder` (creator, attestation, reference) plus
  `ConversationOrderLink` and a timeline event.
- Development simulator: a connection flagged `is_simulated`, labelled in every view, whose
  adapter answers locally; `simulate_inbound` feeds Meta-format payloads into the post-signature
  processing. `MATECART_CHANNEL_SIMULATOR` is refused by production settings.

### D-098 · Inbox alerts, unread semantics and operational metrics (2026-11-05, Phase 14)

- Alerts use the Phase 13 engine (three kinds, topic `conversations`); messages are never
  stored as notifications. `emit` gained `staff_users` (assignee-only alerts) and
  `exclude_staff` (someone already viewing).
- Unread is per staff member (`ConversationReadState`); reading never changes anyone else's
  count. Visitor read receipts (website) and provider receipts are separate and shown only when
  reported.
- Metrics come from stored rows over store-local days; first response counts human replies
  only; a linked order is a reference, not attribution; paid linked value needs `reports.view`.

### D-099 · Delivery integrity: stored template values and a policy check at send time (2026-11-05, Phase 14.1)

- **Context.** WhatsApp template values travelled only as Celery task arguments. When the
  broker was down at queue time, the sweeper re-queued the message without them and the
  template was sent with no values. Separately, the messaging policy was checked only when
  staff sent; a message waiting in the queue (broker outage, retries) could be delivered after
  the window closed, after an opt-out, after the conversation was closed or after the channel
  was switched off.
- **Stored values.** `Message.template_parameters` (`communications.0003`, additive, JSON list)
  holds the ordered values while the message is `queued` or `sending`. Every delivery path
  (first run, provider retry, sweeper recovery) reads them from the row; tasks carry only the
  message id. They are cleared when the message settles (`accepted`, `failed`, `unknown`) and
  by retention, and are never serialised, logged or audited. Tasks queued before the upgrade
  still carry the values; they are used and stored on first use. A template whose stored values
  no longer match its parameter count fails (`template_parameters_invalid`); it is never sent
  without them.
- **Policy at send time.** Under the delivery claim lock, `policy.delivery_check` runs the
  rules again: `reply_policy` for free-form messages (window, Human Agent approval and its
  7-day limit, closed conversation, connection state), and for templates an open conversation,
  a usable connection, no opt-out, a recorded opt-in, a template that is still `APPROVED` and
  `UTILITY`, and exactly the values it needs. The Messenger tag actually used is recorded.
- **Outcome.** A refusal is permanent for that message: `failed` with the reason as
  `error_code` (for example `window_closed`, `opted_out`, `template_unavailable`), a
  `DeliveryAttempt` with outcome `blocked`, no provider call and no retry. Temporary provider
  failures keep their bounded retries; `unknown` is still never resent automatically. Closing a
  conversation therefore also stops replies still waiting in the queue; staff can reopen it and
  send again.
- **Limit.** The check and the provider call are not atomic: an opt-out that commits during
  the call itself cannot stop that call. Locks are never held across provider calls.

### D-100 · Website chat: every message sequence names its conversation (2026-11-05, Phase 14.1)

- **Context.** Sequences restart at 1 in every conversation, but the widget kept one message
  list and one `lastSeq` across conversations and `sync` sent only `after_seq`. After a closed
  conversation and a new one, threads could mix, a reconnection could skip the new
  conversation's first messages, and read or delivery receipts meant for the old thread were
  applied to the new one.
- **Contract (additive).** Visitor messages carry `conversation`. `sync`, `read` and
  `delivered` frames name the conversation; the `sync` reply and the status event name it too.
  `GET /api/chat/config/` returns `conversation_id` and accepts `conversation` with
  `after_seq`.
- **Server.** The visitor's current conversation is the active one (where the next message
  goes, which may be an older conversation staff reopened), otherwise the latest. `after_seq`
  is honoured only for the conversation it names; any other name returns the current
  conversation's recent history from the start. Receipts apply only to the visitor's own
  conversation they name. Frames that name no conversation (older clients) keep the previous
  behaviour.
- **Widget.** One conversation per thread: it keeps that conversation's id with `lastSeq`. A
  message or status from another conversation is never merged; the widget asks which
  conversation is current (socket `sync`, or the HTTP history when the socket is down, where
  only the newest answer counts) and then shows only that conversation. Frames from a replaced
  socket are ignored. Each store reply counts once as unread.
- **Unchanged.** Conversation lifecycle, guest and customer credentials, membership, Origin
  checks, and the closed conversation's history in the inbox and under retention.

### D-101 · Provider-independent AI layer with durable, reviewable requests (2026-11-05, Phase 15)

- **Decision.** A `commerce.ai` app inside the commerce API (no separate AI service). Business
  features build a `GenerationRequest`; providers implement `AIProvider` (`list_models`,
  `generate`) and declare per-model capabilities (text, JSON object, strict JSON schema,
  vision). A feature whose capability the chosen model lacks is refused before any call; there
  is never a silent fallback to another model.
- **Requests** are durable rows processed by Celery (`ai.run_request`), claimed under a row
  lock, with provider-reported token usage, safe error codes and the validated result. Prompts
  are never stored. Only documented rate-limit refusals are retried (at most 3 attempts);
  timeouts and 5xx are not, because a resend could be processed twice.
- **Review.** Results are drafts visible only to the requester. Applying is an explicit action
  through the owning service (catalogue, media, marketing) with overwrite confirmation and an
  `ai.draft.applied` audit event; support replies only fill the composer.
- **Test provider.** Deterministic "[Test]" fixtures for development and tests
  (`MATECART_AI_TEST_PROVIDER`), labelled everywhere, refused in production.
- **Module.** `ai_assistant` is now available (off by default). Off stops new requests and fails
  queued ones.

### D-102 · GroqCloud first, from documentation, with strict output validation (2026-11-05, Phase 15)

- Fixed endpoint `https://api.groq.com/openai/v1`; `max_completion_tokens`, `temperature` and
  `response_format` only. Strict `json_schema` on models documented to support it (gpt-oss),
  JSON mode otherwise; MateCart validates every answer against the same schema either way.
- An explicit registry of documented models (console.groq.com/docs, 2026-11-05). Vision models
  are preview models today; the dashboard marks them. Unknown listed models are not selectable.
- Verification lists models with the stored key; a chosen model that disappears turns the
  status to "model unavailable" without changing the choice.
- The API key is entered in the dashboard, encrypted with `MATECART_ENCRYPTION_KEY`, shown only
  as its last four characters, needs a recent sign-in to change and is never audited.

### D-103 · One SSRF-safe outbound HTTPS client (2026-11-05, Phase 15)

`commerce.core.outbound` serves GroqCloud, WooCommerce, Shopify and imported images: HTTPS host
names on port 443, every resolved address public, the socket pinned to the vetted address (TLS
still verifies the host name), redirects off unless allowed and re-validated per hop, credentials
dropped across hosts, explicit timeouts and size limits, coded errors. Tests replace its
`transport` seam; nothing in the test suite reaches the internet.

### D-104 · AI context: authorised, minimal and separated from instructions (2026-11-05, Phase 15)

- The worker re-checks the requester's membership and feature permission, then reads only that
  feature's records. Store and customer data go in one delimited block that the trusted system
  message declares untrusted; delimiter sequences inside data are neutralised.
- Support excerpts: last 12 messages, e-mail addresses, phone numbers and digit runs masked,
  notes never read; order and shipment facts only with `orders.manage`, produced by the server
  and shown apart from customer claims and AI interpretation. Support features require a
  settings manager's confirmation that customers are told (D-104 consent switch).
- Insights send only aggregates from the existing report services filtered by permission;
  cost prices and margins are never sent. Images are re-encoded (≤1280 px JPEG, metadata
  dropped) and sent inline, never as links.
- Retention: results 30 days, metadata 400 days.

### D-105 · Data transfer jobs: preview before write, item-level idempotency (2026-11-05, Phase 15)

A `commerce.transfers` app with `DataTransferJob`, `DataTransferFile`, `ImportItem` and
`ExternalSourceMapping`. Staging and classification write no business data (the preview);
approval is explicit; execution is per item in its own transaction through the domain services,
claimed under a lock so a duplicate task run is harmless; progress comes from item rows;
cancellation stops between items; resume processes pending items only; the sweep resumes jobs
with a stale heartbeat. Absent records are never deleted. CSV output is formula-safe.

### D-106 · One normalised record shape for files and migrations (2026-11-05, Phase 15)

CSV, JSON Lines, WooCommerce and Shopify all become the same product, category, customer, order
and coupon dictionaries before validation, which reuses the catalogue's own SKU, barcode and
ISBN normalisers, `Decimal` money at the currency's minor unit and preset product-type rules.
HTML becomes plain text. Currency conversion is never invented: a source in another currency
blocks product migration until an approved policy exists (Q12).

### D-107 · Read-only migration sources; documented credentials; simulator (2026-11-05, Phase 15)

WooCommerce REST v3 with a Read consumer key; Shopify Admin GraphQL 2026-10 with an existing
custom-app token or Dev Dashboard client credentials (no passwords, nothing installed in the
source, nothing written). Shopify bulk operations above 1,000 products. A labelled development
simulator (`MATECART_MIGRATION_SIMULATOR`) allows local acceptance; production refuses it.
Credentials are encrypted, never returned, erased on disconnect.

### D-108 · Customers and orders migrate as read-only history; reversal is compensation (2026-11-05, Phase 15)

- Imported customers become `HistoricalCustomer` rows (no account, login, password or hash;
  contact details not verified; the source's marketing flag is not consent). Imported orders
  become `HistoricalOrder` rows with source statuses and totals as recorded; they never pass
  through order placement, so no payment, COD collection, shipment, reservation, notification,
  coupon redemption or loyalty points is created and reports exclude them. Account activation
  for imported customers is an owner decision (Q11).
- Coupons are imported switched off; unrepresentable restrictions are listed.
- "Reverse import" removes only what the job created and nothing used since (sold products kept,
  stocked products archived, unused ones deleted), never a global rollback.
- Customer and order payloads in staged items are cleared when the job finishes.

### D-109 · Exports and sensitive data (2026-11-05, Phase 15)

Products, categories and inventory need `catalog.manage`; customers need `customers.view` +
`store.settings`; order history needs `orders.manage` + `reports.view` and excludes addresses
and payment details. Sensitive exports need a recent sign-in to create and download, and every
download is audited. Files are private, tenant-scoped, `no-store`, and expire after 7 days.

### D-110 · Related products are deterministic; AI is not needed (2026-11-05, Phase 15)

Related products are ranked from shared categories, collections, brand and tags, with merchant
pins and hides (`ProductRecommendation`), and only published, priced, sellable products are
shown. No visitor profiles or other customers' purchases are used. AI ranking is not built:
the deterministic list is explainable and keeps working without a provider. The storefront
falls back to the category listing if the list is unavailable.

### D-111 · One cohesive Control domain with immutable operational identities (2026-11-05, Phase 16)

Phase 16 uses one `control.platform` Django app for the interdependent client, store,
infrastructure, operations, monitoring and alert records. It avoids artificial app boundaries
while keeping accounts separate. Every operational primary key is UUIDv7. A StoreRegistration's
immutable UUID is the value installed later as `MATECART_TENANT_ID`; Control and Commerce have no
database foreign keys. Important historical relationships use `PROTECT` and are not automatically
deleted. Phase 17 may consume these records but cannot change their identity semantics.

### D-112 · Fixed Console roles and an append-only central audit (2026-11-05, Phase 16)

Console authorization is a code-owned role matrix: Platform Owner, Platform Administrator,
Operations Manager, Support Operator and Read-only Auditor. The backend checks permissions on
every operational endpoint. Sensitive changes also require the existing recent-authentication
state; an administrator cannot change their own role, and row locks preserve at least one active
Platform Owner. `ConsoleAuditEvent` is written in the business transaction and is immutable both
in the ORM and by PostgreSQL UPDATE/DELETE triggers. Audit changes are explicit, safe metadata,
never credentials or copied Store business data.

### D-113 · Deployment-scoped telemetry, PostgreSQL observations and honest health (2026-11-05, Phase 16)

Each deployment gets independently rotatable telemetry credentials; only a SHA-256 digest and
last four characters are stored, and the raw value is returned once. Reports must bind the Store
and Deployment, pass freshness, size, metric and rate limits, and use a unique report UUID for
replay protection. Control stores bounded observations and aggregated visitor totals only—never
customer, order, payment, session, message or prompt data. Freshness plus reported Store/API state
produces Healthy, Degraded, Unavailable, Stale or Unknown. Registration alone is never health.
Reported evidence remains labelled; applied configuration changes only after authoritative future
acknowledgment.

### D-114 · An isolated Control worker evaluates operations but never deploys (2026-11-05, Phase 16)

Control gets its own Celery worker and Beat on the existing Control Redis, separate from every
Commerce deployment. Idempotent schedules evaluate stale heartbeats, backup failure/overdue and
certificate expiry, deduplicate alerts and apply bounded retention. They do not SSH, run remote
Docker, change DNS, issue certificates, restore backups or apply configuration. Those execution
channels remain Phase 17; Phase 16 records truthful pending requests and observed acknowledgments.

## Open questions for the owner

| #   | Question                                                                                                                                                                                                      | Status                                                                                                                | Needed by                    |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| Q1  | Does the **Optimus Custom** font licence allow redistribution and web embedding?                                                                                                                              | Open. Not relevant to Phase 02.                                                                                       | Phase 05                     |
| Q2  | Is **Redis 8** licensing (RSALv2 / SSPLv1 / AGPLv3) acceptable for client deployments, or should we switch to **Valkey** (BSD-3, drop-in)?                                                                    | Open. Redis stays as adopted; nothing in Phase 02 depends on it. Reviewed with infrastructure.                        | Phase 17                     |
| Q3  | Initial market: Bangladesh?                                                                                                                                                                                   | **Resolved (Phase 02 brief):** Bangladesh is the first market; data structures are international (D-031).             | —                            |
| Q4  | Merchant dashboard placement                                                                                                                                                                                  | **Resolved:** `/manage` on the storefront domain in commerce-web (D-044).                                             | —                            |
| Q5  | Branching and first commit.                                                                                                                                                                                   | **Resolved:** Phase 01 committed on `main` (`8f3e34b`).                                                               | —                            |
| Q6  | Customer login methods                                                                                                                                                                                        | **Resolved:** email/password, phone OTP and Google OIDC; real provider gates apply (D-043).                           | —                            |
| Q7  | Customer versus merchant identity                                                                                                                                                                             | **Resolved:** separate accounts, credentials, sessions and permissions, even for matching emails (D-040).             | —                            |
| Q8  | One shared NetaMate Meta app for every store, or a Meta app per store? Messenger and Instagram webhooks are per app, so a shared app needs a central webhook router, which conflicts with store independence. | Open. Phase 14 works per deployment (each store's own app and webhook URL); a router would be a Phase 16/17 decision. | Before live Meta use         |
| Q9  | Should NetaMate apply for Meta's Human Agent feature (7-day replies) and app review for the messaging permissions?                                                                                            | Open. Disabled until approved (`META_HUMAN_AGENT_APPROVED`).                                                          | Before live Meta use         |
| Q10 | Are the default retention periods (closed conversations 365 days, guest chats 180 days) right for your clients?                                                                                               | Open. Defaults set conservatively and configurable (D-096).                                                           | Phase 18                     |
| Q11 | Should customers imported from WooCommerce or Shopify be invited to activate a MateCart account (e-mail invitation and password setup), or stay as read-only history?                                         | Open. Phase 15 keeps them as read-only history; no accounts or messages are created (D-108).                          | Before migrating live stores |
| Q12 | When a source store sells in another currency, should MateCart support an owner-approved conversion policy (fixed rate, rounding), or require a store in the same currency?                                   | Open. Product migration is blocked on a currency mismatch; no rate is invented (D-106).                               | Before such a migration      |
| Q13 | Which GroqCloud models (and whether preview vision models) should NetaMate recommend to clients, and should Zero Data Retention be required on client Groq accounts?                                          | Open. Merchants choose from the documented registry; preview models are labelled (D-102).                             | Before live AI use           |
