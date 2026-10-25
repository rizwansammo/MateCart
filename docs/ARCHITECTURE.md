# Architecture

Status: Phase 01 foundation. Sections marked **(planned)** describe intent that later phases must design and verify.

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
│   ├── commerce-api/          Django project "config" + app package "commerce" (+ Celery)
│   └── control-api/           Django project "config" + app package "control"
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

### Initial: dedicated single-tenant deployments

One **tenant = one client store = one commerce deployment**: its own `commerce-web`, `commerce-api`, workers, PostgreSQL database, Redis, secrets, media storage, domain and TLS, and provider credentials. Isolation is physical. Several deployments may share a VPS as separate Compose projects with separate volumes, networks and credentials, or run on the client's own VPS.

### Multi-tenant readiness (rules for Phase 02 onward)

The domain must not assume it can only ever serve one store. Phase 02 chooses the concrete mechanism. These requirements are fixed:

1. **Identity.** Business data belongs to a store. The store/tenant identity is explicit in the domain model, even though one deployment holds one store today.
2. **One choke point.** Data access goes through tenant-scoped managers or services, never ad-hoc unscoped queries. Defence in depth (for example PostgreSQL row-level security) is evaluated in Phase 02.
3. **Authorization.** Every API checks that the authenticated principal belongs to the store it is acting on (Phase 03).
4. **Everything else is scoped too:** cache keys (prefixes), Celery tasks (carry tenant context explicitly), media and file paths, imports and exports, search indexes, rate limits, logs and metrics labels.
5. **Configuration and secrets** are per store, never global constants.
6. **Storing a `tenant_id` is not isolation.** Isolation is enforced scoping plus tests that prove cross-tenant access fails (verified in Phase 18).

Options Phase 02 will weigh for a future shared mode: shared schema with enforced tenant scoping (plus RLS), schema-per-tenant, or keeping database-per-tenant with a fleet manager. A hybrid mode (dedicated for large clients, shared for small ones) must stay possible.

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

## 5. Cross-cutting foundations (implemented in Phase 01)

### Configuration

- Twelve-factor: all configuration through environment variables, read by `django-environ` in `config/settings/base.py`.
- Settings modules: `base` → `development` | `test` | `production`. `manage.py` and Celery default to development; WSGI and ASGI default to production.
- `production` refuses to start with a weak or placeholder `DJANGO_SECRET_KEY` or empty `DJANGO_ALLOWED_HOSTS`, and passes `manage.py check --deploy` (asserted by tests).
- Local secrets are generated by `pnpm setup:env` into git-ignored `.env` files.
- Next.js server-side configuration (`*_INTERNAL_URL`) is read in `server-only` modules and never exposed to the browser.

### HTTP API conventions

- All Django routes live under `/api/`; a reverse proxy can send `/api/*` to Django and everything else to Next.js.
- **Default deny:** DRF has no authentication backends yet and defaults to `IsAuthenticated`, so any endpoint that does not explicitly opt in is refused. Phase 03 adds authentication.
- **Error envelope** (`ApiErrorBody` in shared-contracts):
  `{"error": {"code": "not_found", "message": "Not found.", "details": {...}}}`. `details` is present for validation errors. Unhandled errors return a generic `server_error`; details are only logged.
- **Request ids:** every response carries `X-Request-ID`. A well-formed incoming id (8–64 characters of `[A-Za-z0-9._-]`) is reused for cross-hop correlation; anything else is replaced. The id is attached to every log line.
- **Contracts:** drf-spectacular generates OpenAPI 3 schemas, committed at `services/<service>/openapi.yaml`. A test fails when they are stale (`pnpm api:schema` regenerates them). TypeScript types in `packages/shared-contracts` mirror them by hand until code generation is introduced (DECISIONS D-013).

### Health endpoints

| Service                        | Liveness                | Readiness                                                               |
| ------------------------------ | ----------------------- | ----------------------------------------------------------------------- |
| commerce-api                   | `GET /api/health/live/` | `GET /api/health/ready/` checks database, cache and broker (200 or 503) |
| control-api                    | `GET /api/health/live/` | `GET /api/health/ready/` checks database                                |
| commerce-web, platform-console | `GET /healthz`          | —                                                                       |

Liveness never touches dependencies. Readiness reports `ok` or `error` per check and never failure details. Responses are `Cache-Control: no-store`. **(planned)** In production, readiness should be reachable only from the proxy or orchestrator, not the public internet (Phase 17).

### Logging

stdout only. JSON lines (`timestamp`, `level`, `service`, `logger`, `message`, `request_id`, `exception`) by default, or a readable console format in development (`DJANGO_LOG_FORMAT=console`). Django's own loggers are routed through the same handler.

### Asynchronous work (Celery)

Redis broker, JSON-only serialisation, `acks_late` + `reject_on_worker_lost` + prefetch 1, so tasks survive worker crashes. **Every task must be idempotent.** Time limits: 240 s soft and 300 s hard by default. Visibility timeout 3600 s (must exceed the longest task or ETA). `manage.py check_celery` performs a real broker → worker → result round-trip.

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

| Topic                                                                             | Phase      |
| --------------------------------------------------------------------------------- | ---------- |
| Domain model, tenant scoping mechanism, auditing                                  | 02         |
| Custom user model; customer, merchant and central admin auth, RBAC, MFA, sessions | 03         |
| Catalogue and inventory with preset-specific attributes                           | 04         |
| Preset and theme system, white-label branding                                     | 05         |
| Production reverse proxy, production images, GHCR releases, installer, agent, TLS | 17         |
| Rate limiting, bot protection, CDN, horizontal scaling validation                 | 12, 17, 18 |
| AI provider abstraction (GroqCloud first)                                         | 15         |
| Store → console metrics channel, alerting                                         | 12, 13, 16 |
