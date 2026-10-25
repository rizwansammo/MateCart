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

---

## Open questions for the owner (none block Phase 01)

| #   | Question                                                                                                                                                                              | Why it matters                                                                                                 | Needed by       |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | --------------- |
| Q1  | Does the **Optimus Custom** font licence allow redistribution and web embedding?                                                                                                      | It is the MateCART wordmark font in the prototype. Unconfirmed licences must not ship in a commercial product. | Phase 05        |
| Q2  | Is **Redis 8** licensing (RSALv2 / SSPLv1 / AGPLv3 choice) acceptable for client deployments, or should we use **Valkey** (BSD-3, drop-in compatible)?                                | NetaMate deploys and operates Redis for clients. This is a legal review, not a technical blocker.              | Phase 17        |
| Q3  | Confirm the initial market is **Bangladesh** (BDT, bKash/Nagad/Rocket, COD, Pathao/Steadfast/RedX/Paperfly, English and Bangla), as the prototype implies. Any other markets planned? | Drives currency, tax, address, phone and payment modelling in Phases 02, 04 and 07–09.                         | Phase 02        |
| Q4  | Merchant dashboard placement: same `commerce-web` app under a path (for example `/admin`) or a separate subdomain?                                                                    | Affects routing, cookies and session boundaries (Phase 03) and proxy configuration.                            | Phase 03        |
| Q5  | Branching: `main` has no commits yet. Recommended: commit the Phase 01 foundation as the root commit on `main`, then use `phase/NN-*` feature branches and pull requests.             | Assistants never commit; the owner chooses the history.                                                        | Before Phase 02 |
| Q6  | Customer login identity: phone + OTP, email + password, or both? Separate staff identities per store?                                                                                 | Shapes the custom user model, which must exist before `django.contrib.auth` is installed (D-010).              | Phase 02/03     |
