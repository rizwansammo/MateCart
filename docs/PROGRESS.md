# Progress and handoff

**Read this first in every session.** Update it at the end of every session and every phase (see AGENTS.md §7).

Last updated: 2026-10-25 · by Claude Code (Opus 5.5) · end of Phase 01 implementation.

## Project status board

| Item                          | Status                                                                                                |
| ----------------------------- | ----------------------------------------------------------------------------------------------------- |
| Active phase                  | **Phase 01 — Project Foundation**                                                                     |
| Phase 01                      | **Complete (verified locally) — awaiting owner review and approval**                                  |
| Phases 02–19                  | Pending. Do not start Phase 02 without explicit owner approval.                                       |
| Public subscription / billing | Deferred                                                                                              |
| Initial deployment model      | Single tenant                                                                                         |
| Future architecture           | Multi-tenant ready / hybrid                                                                           |
| UI prototype (MateCart-UI)    | Strictly read-only (verified untouched: HEAD `9ff1361`, clean)                                        |
| Business model                | Private beta, manual client onboarding                                                                |
| Git                           | `main` has no commits. All Phase 01 work is uncommitted in the working tree, for the owner to commit. |

## Phase 01 quality gates

| #   | Gate                                           | Result | Evidence (2026-10-25)                                                                                                                                              |
| --- | ---------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Structure coherent and documented              | Pass   | README, ARCHITECTURE §2                                                                                                                                            |
| 2   | Local dependencies configured                  | Pass   | `pnpm install` (pnpm 11.18, lockfile verified); `uv.lock` per service, `uv sync --locked` in images                                                                |
| 3   | Dedicated Docker environment works             | Pass   | `pnpm stack:up`: all 6 `matecart-dev` containers healthy; full `down` → `up` cycle repeated                                                                        |
| 4   | Unrelated Docker applications untouched        | Pass   | Before/after snapshot of non-MateCart containers (IDs, states, created times), volumes and networks: identical. See note 1.                                        |
| 5   | Frontend apps run                              | Pass   | `pnpm dev`: both apps serve `/` and `/healthz` (200); pages render live API readiness server-side; visually checked in headless Edge                               |
| 6   | Backends start and answer health checks        | Pass   | `/api/health/live/` and `/api/health/ready/` return 200 on both APIs                                                                                               |
| 7   | Database and Redis connections verified        | Pass   | commerce readiness `database/cache/broker: ok`; control readiness `database: ok`; integration tests against real PostgreSQL and Redis                              |
| 8   | Celery configuration validated                 | Pass   | `pnpm api:celery-check`: real round-trip API → Redis → worker → result returned `'pong'`; worker healthcheck uses `celery inspect ping`; config tests              |
| 9   | Test, lint, type-check and build commands work | Pass   | See verification log                                                                                                                                               |
| 10  | No secrets committed                           | Pass   | Nothing committed. All 136 commit-candidate files scanned: none of the generated secrets present, no credential patterns. `.env` and `.env.local` are git-ignored. |
| 11  | Architecture and roadmap docs exist            | Pass   | `docs/`                                                                                                                                                            |
| 12  | Claude/Codex handoff ready                     | Pass   | `AGENTS.md` (canonical), `CLAUDE.md` (imports it), this file                                                                                                       |
| 13  | Known issues and open decisions documented     | Pass   | Below, and DECISIONS "Open questions"                                                                                                                              |

## Verification log (final run, 2026-10-25, after a full `pnpm stack:down` → `pnpm stack:up`)

| Command                         | Result                                                                                       |
| ------------------------------- | -------------------------------------------------------------------------------------------- |
| `pnpm format:check`             | exit 0: all files use Prettier style                                                         |
| `pnpm lint`                     | exit 0: ESLint, 0 problems (24 files linted)                                                 |
| `pnpm typecheck`                | exit 0: 4 packages; Next typed routes generated                                              |
| `pnpm test`                     | exit 0: Vitest 6/6 passed (shared-contracts)                                                 |
| `pnpm build`                    | exit 0: both Next.js apps built (Next 16.4.3, Turbopack)                                     |
| `pnpm api:lint`                 | exit 0: ruff check, ruff format (34 + 28 files), mypy strict (no issues, 34 + 28 files)      |
| `pnpm api:test`                 | exit 0: commerce-api **27 passed**, control-api **24 passed**                                |
| `pnpm api:celery-check`         | exit 0: `Celery round-trip OK … returned 'pong'`                                             |
| `pnpm health`                   | exit 0: 6/6 OK (both APIs live and ready, both web apps)                                     |
| `docker compose config --quiet` | valid                                                                                        |
| actionlint (CI workflow)        | no findings. Action references verified to exist on GitHub.                                  |
| Data persistence                | after removing all containers, recreated APIs report "No migrations to apply" (volumes kept) |

Backend tests cover: liveness and readiness (including a 503 path that must not leak failure details), the error envelope (404, 500, validation, API exceptions), default-deny permissions, request id generation, reuse and rejection, log filter and JSON formatter, Celery task and config, migrations in sync, OpenAPI drift, production `check --deploy`, and refusal of weak secret keys and empty allowed hosts.

**Not verified:** the GitHub Actions workflow has not run on GitHub. Assistants do not push. The first run happens when the owner pushes or opens a pull request. Every step it runs passed locally (frontend directly, backend inside the containers).

## What Phase 01 delivered

- **Monorepo** (pnpm workspaces): `apps/commerce-web`, `apps/platform-console`, `packages/shared-ui`, `packages/shared-contracts`, root tooling (strict `tsconfig.base.json`, ESLint flat config, Prettier with Tailwind plugin, `.editorconfig`, `.gitattributes` LF).
- **Frontends:** Next.js 16.4 App Router, React 19.3, Tailwind 4.3 with shared prototype tokens, self-hosted fonts (Geist, Geist Mono, Hind Siliguri), typed routes, baseline security headers, `/healthz`, server-only env access, noindex Phase 01 status pages showing their API's readiness.
- **Backends:** Django 5.2 LTS + DRF projects `commerce-api` (with Celery and Redis) and `control-api`; settings `base/development/test/production`; health endpoints; request id middleware; JSON or console logging; error envelope; default-deny DRF; OpenAPI schemas (`openapi.yaml`) with drift test; `check_celery` command; non-root dev images with uv.
- **Docker:** `compose.yaml` (project `matecart-dev`) with healthchecks, isolated networks and named volumes.
- **Scripts:** `pnpm setup:env` (generates local secrets), `pnpm health`.
- **CI:** `.github/workflows/ci.yml` (checks only, no deployment).
- **Docs:** README, AGENTS, CLAUDE, and `docs/` (PROJECT_CONTEXT, ARCHITECTURE, DEVELOPMENT_ROADMAP, FEATURE_CHECKLIST, DECISIONS, PROGRESS, DEVELOPMENT_SETUP).

### Docker resources created (all MateCart-only)

- Containers: `matecart-dev-{commerce-db,commerce-redis,commerce-api,commerce-worker,control-db,control-api}-1`
- Volumes: `matecart-dev-commerce-db-data`, `matecart-dev-commerce-redis-data`, `matecart-dev-control-db-data`
- Networks: `matecart-dev-commerce`, `matecart-dev-control`
- Images built: `matecart-dev/commerce-api:dev`, `matecart-dev/control-api:dev`
- Public images pulled: `python:3.13.17-slim-trixie`, `redis:8.10.2-alpine`. `postgres:18.6-alpine3.24` was already present and is used read-only. A temporary `rhysd/actionlint` image was pulled for workflow linting and removed afterwards.
- Throwaway containers (auto-removed): `matecart-dev-uv-lock` (generated the `uv.lock` files), `matecart-dev-actionlint`.

### Ports (127.0.0.1 only)

| Service          | Port  |
| ---------------- | ----- |
| commerce-web     | 3400  |
| platform-console | 3410  |
| commerce-api     | 8400  |
| control-api      | 8410  |
| commerce-db      | 15440 |
| control-db       | 15441 |
| commerce-redis   | 16380 |

Details: DEVELOPMENT_SETUP.md.

## Known issues and limitations

1. **Docker default `bridge` network ID changed** between the first read-only inspection and the first MateCart command. The engine's event log starts afresh at that point, which indicates a Docker engine restart (likely Docker Desktop Resource Saver, since all containers were idle). No MateCart command can cause this. Other projects' own networks, containers and volumes are unchanged.
2. **CI not yet executed on GitHub** (see above).
3. **ESLint 9 is end-of-life** but required by the Next.js 16.4 ESLint plugin set (D-005).
4. **TypeScript contracts are hand-mirrored** from `openapi.yaml` until a code generator is chosen (D-013).
5. **Frontends run on the host, not in Docker**, by design (D-008). Production frontend images arrive in Phase 17.
6. **`control-api` has no Redis or Celery yet.** They are added when the console gets async work (Phase 16).
7. **Production settings are a skeleton**, not deployment-verified (Phase 17). The readiness endpoint should be made internal-only then.
8. In development (`DEBUG=True`), unknown URLs show Django's HTML debug 404. The JSON 404 envelope applies with `DEBUG=False` and is covered by tests.
9. MateCart containers use `restart: unless-stopped`: if left running, they start again with Docker Desktop. Run `pnpm stack:down` to stop them.
10. Python editor intellisense needs the optional host venv (DEVELOPMENT_SETUP.md).
11. Next.js 16.4 writes `apps/*/AGENTS.md` when `next dev` runs under an AI agent. Disabled with `agentRules: false`; Next removed the generated files itself.

## Decisions needing the owner

See [DECISIONS.md → Open questions](DECISIONS.md#open-questions-for-the-owner-none-block-phase-01). Q1 Optimus font licence · Q2 Redis vs Valkey licensing · Q3 confirm Bangladesh market · Q4 dashboard placement · Q5 first commit and branching · Q6 customer login identity. None block Phase 01. Q3, Q5 and Q6 are best answered before or early in Phase 02.

## Handoff: next steps

1. **Owner:** review Phase 01 and commit it. Recommendation: root commit on `main`, then `phase/02-*` feature branches with pull requests, so CI runs on GitHub for the first time.
2. **Owner:** approve the start of Phase 02, ideally with answers to Q3 and Q6.
3. **Next assistant (Phase 02, Database & Tenant Architecture), once approved:**
   - Read AGENTS.md, this file, DECISIONS (especially D-010) and ARCHITECTURE §3.
   - `pnpm install && pnpm setup:env && pnpm stack:up && pnpm health --skip-web` to confirm the baseline.
   - Design the tenant scoping mechanism, base models and managers, auditing, and the store configuration model. Coordinate the custom user model with Phase 03 before installing `django.contrib.auth`.
   - Keep updating this file, DECISIONS and FEATURE_CHECKLIST.
