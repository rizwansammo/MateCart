# Architecture

Status: Phase 02 (database and tenant architecture). Sections marked **(planned)** describe intent that later phases must design and verify. The schema and domain blueprint are in [DATA_MODEL.md](DATA_MODEL.md).

## 1. System overview

```text
                       ┌──────────────────────── one client store deployment (× N) ─────────────────────────┐
 customers ──HTTPS──►  │  reverse proxy ──► commerce-web (Next.js)  storefront + merchant dashboard           │
 merchants ──HTTPS──►  │        │  /api/* ─► commerce-api (Django/DRF) ──► PostgreSQL (store's own DB)        │
                       │        │                    │  └──► Redis (cache, Celery broker) ◄── commerce-worker  │
                       │        │                    └──► payment / courier / mail / AI providers (Phases 08+) │
                       └────────────────────────────────────────────────────────────────────────────────────┘
                                         ▲  deployment agent / signed, scoped operations (planned, Phase 17)
                                         │  aggregate health and traffic metrics, no business data
 NetaMate staff ──HTTPS──► platform-console (Next.js) ──► control-api (Django/DRF) ──► PostgreSQL (console DB)
```

| Component          | Path                        | Responsibility                                                                       |
| ------------------ | --------------------------- | ------------------------------------------------------------------------------------ |
| `commerce-web`     | `apps/commerce-web`         | Customer storefront (Phase 06) and merchant dashboard (Phase 11), Next.js App Router |
| `platform-console` | `apps/platform-console`     | NetaMate's internal Central Platform Console UI (Phase 16)                           |
| `commerce-api`     | `services/commerce-api`     | Commerce domain API for a single store: catalogue, orders, payments and so on        |
| `commerce-worker`  | same image as commerce-api  | Celery worker: notifications, integrations, imports and other async work             |
| `control-api`      | `services/control-api`      | Console backend: client registry, servers, deployments, monitoring (Phase 16+)       |
| `shared-ui`        | `packages/shared-ui`        | Design tokens (Tailwind v4 theme) and shared React components                        |
| `shared-contracts` | `packages/shared-contracts` | Typed API contracts and runtime guards shared by the frontends                       |

## 2. Repository layout

```text
MateCart/
├── apps/
│   ├── commerce-web/          Next.js: storefront + merchant dashboard
│   └── platform-console/      Next.js: NetaMate internal console
├── services/
│   ├── commerce-api/          Django project "config" + apps commerce.{core,tenancy,stores,identity,audit}
│   └── control-api/           Django project "config" + apps control.{core,accounts}
├── packages/
│   ├── shared-ui/             theme.css tokens, Wordmark
│   └── shared-contracts/      health and error contracts + guards (+ Vitest)
├── scripts/                   setup-env.mjs, check-health.mjs (cross-platform Node)
├── docs/                      project documentation (this folder)
├── .github/workflows/ci.yml   CI quality gate (checks only)
├── compose.yaml               local dev stack "matecart-dev"
├── AGENTS.md / CLAUDE.md      assistant rules (AGENTS.md is canonical)
└── package.json               pnpm workspace root and command hub
```

`deployment/` (production Compose, installer, agent) and a top-level `tests/` (cross-service end-to-end tests) are created when their phases begin (17 and 18), not as empty folders now.

Each Django service is independently deployable with its own `pyproject.toml`, `uv.lock` and Dockerfile. The two share small foundation modules by copy (request id, logging, error envelope). Extracting a shared Python package is deferred until the duplication grows (DECISIONS D-016).

## 3. Deployment and tenancy model

### Deployment: dedicated single-tenant first

One **tenant = one client store = one commerce deployment**: its own `commerce-web`, `commerce-api`, workers, PostgreSQL database, Redis, secrets, media storage, domain and TLS, and provider credentials. Isolation between stores is primarily **physical**. Several deployments may share a VPS as separate Compose projects with separate volumes, networks and credentials, or run on the client's own VPS.

### Tenant strategy (implemented in Phase 02, hardened in 02.1 and 02.2; D-025, D-026, D-034, D-038)

The commerce core is tenant-aware even though each deployment holds one tenant, so a shared or hybrid mode stays possible without redesigning the domain.

**Runtime: strict single-tenant mode.**

- `MATECART_TENANCY_MODE=single` is the only accepted mode. Anything else stops startup.
- `MATECART_TENANT_ID` (a UUID) names the one tenant the deployment serves. It is the same opaque id the Central Console uses for the store registration. Production refuses to start without it.
- The tenant is resolved **from configuration only** (`tenancy.resolution`). Host, headers, query parameters, cookies and bodies are never consulted, so no client can choose or switch the tenant (tested with hostile `X-Tenant-ID`, `?tenant=` and `Host` values).
- Readiness reports `tenant: error` unless the configured tenant is provisioned **and is the only tenant** in the database. A misconfigured deployment fails closed; it never falls back to another tenant.
- Tenants are created only by `manage.py provision_tenant` (`tenancy.provisioning`). It requires the id to equal `MATECART_TENANT_ID`, refuses a second tenant, validates every input, and writes the tenant, its store settings and an audit event in one transaction.

**Enforcement layers.**

| Layer              | Mechanism                                                                                            | Guarantees                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------ | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Explicit context   | `tenant_context(tenant)` (contextvars); `TenantMiddleware` per request; `TenantTask` per Celery task | No implicit or default tenant. Switching tenants inside a context raises. Contexts never leak across threads or requests.                                                                                                                                                                                                                                                                                                                                      |
| Scoped reads       | `TenantOwnedModel.objects` (`TenantScopedManager`), also the model's **base manager**                | Every query is filtered to the active tenant. **Without a context it raises `TenantContextMissing`** instead of returning all rows. Because the scoped manager is also the base manager, Django's internal reads (refresh, deferred fields, forward relations, generic relations, foreign-key validation) obey the same rule. `refresh_from_db()` is decided by the active tenant, never by the instance's tenant or a caller's queryset (D-038).              |
| Guarded writes     | `TenantOwnedModel.save/delete/refresh_from_db`, `TenantQuerySet.update/bulk_create/bulk_update`      | New rows take the active tenant. Writing or deleting another tenant's row raises. Every statement that touches an existing row is **tenant-qualified in SQL** (`UPDATE … WHERE id = … AND tenant_id = …`; deletes lock and check the stored tenant), so an instance built in code with another tenant's primary key cannot reach that row (D-034). Upserts must conflict on a key that includes `tenant`. `tenant` never changes, even with privileged access. |
| Database integrity | `TenantForeignKeyConstraint`: composite FK `(tenant_id, x_id) → target (tenant_id, id)`              | PostgreSQL rejects cross-tenant references even from raw ids, bulk operations or SQL.                                                                                                                                                                                                                                                                                                                                                                          |
| Build-time rules   | `tenancy` system checks E001–E005                                                                    | A tenant-owned model without a non-null tenant FK, the `(tenant, id)` key, a tenant guard on each FK or M2M to tenant-owned models, or scoped default and base managers fails `manage.py check`, the tests and CI.                                                                                                                                                                                                                                             |
| Privileged access  | `unscoped_access(reason)`                                                                            | The only way to read across tenants. Requires a reason, cannot be entered inside a tenant context, and is logged at WARNING. Even here, updates and deletes are qualified by each row's stored tenant. For platform maintenance, never for requests.                                                                                                                                                                                                           |
| Failure handling   | `api_exception_handler`                                                                              | Any tenancy failure answers `503 tenant_unavailable` in the standard envelope; the detail is only logged.                                                                                                                                                                                                                                                                                                                                                      |

Outside the database: cache keys use `tenant_cache_key(...)` (`tenant:<id>:...`); files use `tenant_storage_path` (`tenants/<id>/<app>/<model>/<uuid7><ext>`, so client filenames never become paths); store-specific secrets belong in tenant-owned integration tables (Phase 08+), never in global settings. Search indexes, imports and exports, analytics and rate limits take the tenant from the active context when they are built (Phases 04, 12, 15).

**Why not PostgreSQL row-level security (RLS) now?** In a dedicated deployment every row belongs to the one tenant, so RLS would add migration and connection-handling complexity (per-transaction `SET`, pooler compatibility, bypass roles for migrations) without protecting anything extra. Application scoping plus composite foreign keys already fail closed and are testable. RLS is the planned defence-in-depth layer **if** a pooled mode is approved (DATA_MODEL §5, D-025).

| Option                                           | Dedicated (today)            | Future pooled                         | Verdict                 |
| ------------------------------------------------ | ---------------------------- | ------------------------------------- | ----------------------- |
| Convention only (`filter(tenant=...)` by hand)   | works                        | leaks on the first forgotten filter   | rejected                |
| Scoped managers and guarded writes (fail closed) | yes                          | yes                                   | **adopted**             |
| Composite tenant foreign keys                    | yes                          | yes                                   | **adopted**             |
| PostgreSQL RLS                                   | no added protection          | strong defence in depth               | planned for pooled mode |
| Schema-per-tenant                                | not applicable               | heavy migrations at scale             | not planned             |
| Database-per-tenant                              | **current deployment model** | needs fleet management in the console | current                 |

### Data ownership boundaries

| Data                                                                | Owner                                                                                                          | Never                                 |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| Store business data (catalogue, orders, customers, staff, audit)    | the store's commerce database                                                                                  | copied in bulk to the console         |
| Store configuration                                                 | `stores.StoreSettings` in the store database; the console keeps the desired configuration it pushes (Phase 16) | read from the console at request time |
| NetaMate operations (clients, servers, deployments, administrators) | control-api database                                                                                           | stored in a store database            |
| Cross-service links                                                 | opaque UUIDs (store registration id = `MATECART_TENANT_ID`)                                                    | cross-database foreign keys           |

### Identity boundaries

- **Store identities** (`identity.User`, the `AUTH_USER_MODEL` of commerce-api) are per tenant, in two separate realms: `customer` and `staff`. Email and phone are each optional, at least one is required, both are normalised and unique per tenant and realm. Phase 03 can offer email, phone OTP, or both without migrating the user table.
- **NetaMate administrators** (`accounts.AdminUser`, the `AUTH_USER_MODEL` of control-api) exist only in the console database.
- Until Phase 03 both services use `NoLoginBackend`: nothing can sign in.

### Independence from the Central Console

- Stores never need `control-api` at runtime. Licensing and configuration must not depend on a continuously reachable central endpoint.
- **(planned)** The console manages stores through a deployment agent and SSH-based installer (Phase 17) with signed, narrowly scoped operations.
- **(planned)** Stores report aggregate operational metrics (health, request and error rates, rolling 24-hour visitors) to the console. They send no customer personal data and no business records.

## 4. Runtime topology (local development)

| Service            | Runs on           | URL / port (host, 127.0.0.1 only)             | Network                 |
| ------------------ | ----------------- | --------------------------------------------- | ----------------------- |
| `commerce-web`     | host (`pnpm dev`) | http://127.0.0.1:3400                         | —                       |
| `platform-console` | host (`pnpm dev`) | http://127.0.0.1:3410                         | —                       |
| `commerce-api`     | Docker            | http://127.0.0.1:8400                         | `matecart-dev-commerce` |
| `commerce-worker`  | Docker            | not published                                 | `matecart-dev-commerce` |
| `commerce-db`      | Docker            | 127.0.0.1:15440 (PostgreSQL 18)               | `matecart-dev-commerce` |
| `commerce-redis`   | Docker            | 127.0.0.1:16380 (Redis 8, password protected) | `matecart-dev-commerce` |
| `control-api`      | Docker            | http://127.0.0.1:8410                         | `matecart-dev-control`  |
| `control-db`       | Docker            | 127.0.0.1:15441 (PostgreSQL 18)               | `matecart-dev-control`  |

Commerce and control use separate networks, so `control-api` cannot reach the commerce database, as in production. Redis logical databases: `0` cache, `1` Celery broker, `2` Celery results.

## 5. Cross-cutting foundations

### Configuration

- Twelve-factor: all configuration through environment variables, read by `django-environ` in `config/settings/base.py`.
- Settings modules: `base` → `development` | `test` | `production`. `manage.py` and Celery default to development; WSGI and ASGI default to production.
- `production` refuses to start with a weak or placeholder `DJANGO_SECRET_KEY`, empty `DJANGO_ALLOWED_HOSTS` or a missing `MATECART_TENANT_ID`, and passes `manage.py check --deploy` (asserted by tests). An invalid tenant id or tenancy mode stops every environment at startup.
- Local secrets are generated by `pnpm setup:env` into git-ignored `.env` files.
- Next.js server-side configuration (`*_INTERNAL_URL`) is read in `server-only` modules and never exposed to the browser.

### HTTP API conventions

- All Django routes live under `/api/`; a reverse proxy can send `/api/*` to Django and everything else to Next.js.
- **Default deny:** DRF has no authentication classes yet and defaults to `IsAuthenticated`, and Django's only backend is `NoLoginBackend`, so any endpoint that does not explicitly opt in is refused. Phase 03 adds authentication.
- **Error envelope** (`ApiErrorBody` in shared-contracts):
  `{"error": {"code": "not_found", "message": "Not found.", "details": {...}}}`. `details` is present for validation errors. Tenancy failures return `503 tenant_unavailable`. Unhandled errors return a generic `server_error`; details are only logged.
- **Request ids:** every response carries `X-Request-ID`. A well-formed incoming id (8–64 characters of `[A-Za-z0-9._-]`) is reused for cross-hop correlation; anything else is replaced. The id is attached to every log line.
- **Contracts:** drf-spectacular generates OpenAPI 3 schemas, committed at `services/<service>/openapi.yaml`. A test fails when they are stale (`pnpm api:schema` regenerates them). TypeScript types in `packages/shared-contracts` mirror them by hand until code generation is introduced (DECISIONS D-013).

### Health endpoints

| Service                        | Liveness                | Readiness                                                                       |
| ------------------------------ | ----------------------- | ------------------------------------------------------------------------------- |
| commerce-api                   | `GET /api/health/live/` | `GET /api/health/ready/` checks database, cache, broker and tenant (200 or 503) |
| control-api                    | `GET /api/health/live/` | `GET /api/health/ready/` checks database                                        |
| commerce-web, platform-console | `GET /healthz`          | —                                                                               |

Liveness never touches dependencies. Readiness reports `ok` or `error` per check and never failure details. Responses are `Cache-Control: no-store`. **(planned)** In production, readiness should be reachable only from the proxy or orchestrator, not the public internet (Phase 17).

### Logging

stdout only. JSON lines (`timestamp`, `level`, `service`, `logger`, `message`, `request_id`, `exception`) by default, or a readable console format in development (`DJANGO_LOG_FORMAT=console`). Django's own loggers are routed through the same handler.

### Asynchronous work (Celery)

Redis broker, JSON-only serialisation, `acks_late` + `reject_on_worker_lost` + prefetch 1, so tasks survive worker crashes. **Every task must be idempotent.** Tasks that touch tenant data use `base=TenantTask` and receive an explicit `tenant_id`, which the worker verifies before running. Time limits: 240 s soft and 300 s hard by default. Visibility timeout 3600 s (must exceed the longest task or ETA). `manage.py check_celery` performs a real broker → worker → result round-trip.

### Frontend foundations

- Next.js 16 App Router with Turbopack, React 19, strict TypeScript with typed routes, Tailwind CSS v4.
- Design tokens live in `@matecart/shared-ui/theme.css` (core tokens from the prototype design system v1.0). Importing the theme also registers the package's components as Tailwind sources. Font CSS variables come from `next/font` (self-hosted at build time, no runtime calls to Google).
- Baseline security headers on every route (`nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY`, `Permissions-Policy`) and no `X-Powered-By`. **(planned)** A nonce-based Content Security Policy is designed once payment and analytics scripts are known (Phases 08 and 12).
- Foundation pages are noindex developer status pages. They are replaced in Phases 06, 11 and 16.

## 6. Security baseline

- Secrets only in environment variables, git-ignored locally. CI uses throwaway values for ephemeral service containers.
- Containers run as a non-root user (uid 10001). Dev ports bind to 127.0.0.1. Redis requires a password.
- DRF defaults to deny, JSON-only parsers and renderers, no browsable API.
- Production settings: HTTPS redirect (health paths exempt), secure cookies, HSTS (initial 3600 s; includeSubDomains and preload decided per domain in Phase 17), `X-Frame-Options: DENY`.
- Supply chain: pnpm 24-hour minimum release age, exotic transitive dependencies blocked, install scripts denied by default. Locked Python dependencies (`uv sync --locked`). Third-party GitHub Actions pinned to commit SHAs.

## 7. Planned architecture (not built yet)

| Topic                                                                              | Phase       |
| ---------------------------------------------------------------------------------- | ----------- |
| Login flows (email, phone OTP), sessions, tenant-scoped RBAC, MFA, console sign-in | 03          |
| Catalogue and inventory with preset-specific attributes                            | 04          |
| Preset and theme system, white-label branding                                      | 05          |
| Production reverse proxy, production images, GHCR releases, installer, agent, TLS  | 17          |
| Rate limiting, bot protection, CDN, horizontal scaling validation                  | 12, 17, 18  |
| AI provider abstraction (GroqCloud first)                                          | 15          |
| Store → console metrics channel, alerting                                          | 12, 13, 16  |
| Row-level security and verified domain → tenant resolution (pooled mode)           | if approved |
