# Development setup

Local environment for Windows 11 (primary), macOS or Linux. All commands run from the repository root.

## Prerequisites

| Tool           | Version used              | Notes                                                                |
| -------------- | ------------------------- | -------------------------------------------------------------------- |
| Git            | 2.55                      | `.gitattributes` forces LF line endings, which containers need       |
| Node.js        | 24 LTS                    | see `.node-version`                                                  |
| pnpm           | 11.18.0                   | pinned in `package.json` (`corepack enable` or a standalone install) |
| Docker Desktop | Engine 29.7, Compose v5.5 | shared with other projects; follow the Docker rules in `AGENTS.md`   |
| Python / uv    | not required on host      | backend tooling runs inside containers                               |

## First-time setup

```bash
pnpm install          # workspace dependencies (install scripts denied by default)
pnpm setup:env        # creates .env and apps/*/.env.local with random local secrets
pnpm stack:up         # builds and starts matecart-dev, waits until every service is healthy
pnpm api:provision    # creates the local dev store (tenant MATECART_TENANT_ID from .env)
pnpm dev              # starts commerce-web (3400) and platform-console (3410) on the host
pnpm health           # in a second terminal: checks all six services
```

Open http://127.0.0.1:3400 (commerce web) and http://127.0.0.1:3410 (platform console). Each shows a Phase 01 status page with its API's live readiness.

## Services, ports and URLs

| Service             | URL / address (127.0.0.1 only)                 | Health                                    |
| ------------------- | ---------------------------------------------- | ----------------------------------------- |
| commerce-web        | http://127.0.0.1:3400                          | `/healthz`                                |
| platform-console    | http://127.0.0.1:3410                          | `/healthz`                                |
| commerce-api        | http://127.0.0.1:8400                          | `/api/health/live/`, `/api/health/ready/` |
| commerce-api schema | http://127.0.0.1:8400/api/schema/              | development only                          |
| control-api         | http://127.0.0.1:8410                          | `/api/health/live/`, `/api/health/ready/` |
| commerce-db         | `127.0.0.1:15440`, db/user `matecart_commerce` | password in `.env`                        |
| control-db          | `127.0.0.1:15441`, db/user `matecart_control`  | password in `.env`                        |
| commerce-redis      | `127.0.0.1:16380` (password in `.env`)         | db 0 cache, 1 broker, 2 results           |
| commerce-worker     | not published                                  | `celery inspect ping` healthcheck         |

API and database ports can be changed in `.env`. The Next.js ports live in `apps/*/package.json` (and in `scripts/check-health.mjs`).

## Command reference

| Task                                            | Command                                                            |
| ----------------------------------------------- | ------------------------------------------------------------------ |
| Install dependencies                            | `pnpm install`                                                     |
| Create local env files                          | `pnpm setup:env`                                                   |
| Start the Docker stack                          | `pnpm stack:up`                                                    |
| Stop and remove MateCart containers (data kept) | `pnpm stack:down`                                                  |
| Container status                                | `pnpm stack:ps`                                                    |
| Follow MateCart logs                            | `pnpm stack:logs` (or `docker compose logs -f commerce-api`)       |
| Rebuild MateCart images and restart             | `pnpm stack:rebuild`                                               |
| Run both web apps                               | `pnpm dev` (`pnpm dev:web`, `pnpm dev:console`)                    |
| Frontend checks                                 | `pnpm check` (format, lint, typecheck, unit tests)                 |
| Frontend production builds                      | `pnpm build`                                                       |
| Backend tests                                   | `pnpm api:test` (`api:test:commerce`, `api:test:control`)          |
| Backend lint and types                          | `pnpm api:lint` (ruff, ruff format, mypy strict)                   |
| Format backend code                             | `pnpm api:format`                                                  |
| Regenerate OpenAPI schemas                      | `pnpm api:schema`                                                  |
| Celery round-trip check                         | `pnpm api:celery-check`                                            |
| Provision the local dev store (idempotent)      | `pnpm api:provision`                                               |
| Health of everything                            | `pnpm health` (`pnpm health --skip-web` for the Docker stack only) |

Every Docker command is scoped to the Compose project `matecart-dev`. None of them touch other projects.

## After pulling changes

Run `pnpm install`, then `pnpm setup:env` (it **adds** new keys such as `MATECART_TENANT_ID` to an existing `.env` and never changes existing values), then `pnpm stack:rebuild`. Migrations apply automatically when the API containers start.

## Tenancy in development

The local commerce stack serves one store, like production: `MATECART_TENANT_ID` in `.env`. Until `pnpm api:provision` has created it, commerce readiness reports `tenant: error` (HTTP 503) and tenant data is unreachable. That is the fail-closed behaviour, not a bug. Inside `docker compose exec commerce-api python manage.py shell`, wrap tenant data access in `tenant_context(...)`:

```python
from commerce.tenancy.context import tenant_context
from commerce.tenancy.resolution import get_deployment_tenant

with tenant_context(get_deployment_tenant()):
    ...  # User.objects, StoreSettings.objects, AuditEvent.objects
```

## Backend workflows

```bash
# Django management commands
docker compose exec commerce-api python manage.py shell
docker compose exec commerce-api python manage.py makemigrations
docker compose exec commerce-api python manage.py migrate

# Add or upgrade a Python dependency (edit pyproject.toml first, or let uv edit it)
docker compose run --rm --no-deps commerce-api uv add --no-sync "package>=x.y"
docker compose run --rm --no-deps commerce-api uv lock
pnpm stack:rebuild

# Run a single test
docker compose run --rm commerce-api pytest tests/test_health.py -k readiness
```

Source code is bind-mounted, so Django's dev server reloads automatically. The Celery worker does not reload. Restart it with `docker compose restart commerce-worker`.

Tests use the real PostgreSQL and Redis containers. pytest-django creates and drops a `test_*` database automatically.

### Optional: host Python environment for IDE support

The backend does not need host Python. For editor autocompletion only, install uv (https://docs.astral.sh/uv/) and run `uv sync` inside `services/commerce-api` and `services/control-api`. This creates a git-ignored `.venv` that you point your editor at.

## Docker safety (summary of AGENTS.md)

- Use only `pnpm stack:*` or `docker compose …` from this repository root (project `matecart-dev`).
- Never run `docker system prune`, `docker volume prune` or any other global cleanup, and never touch `mateerp-*`, `deltawave-*`, `honrisesol-*` or other projects' resources.
- **Reset MateCart's local data** (destructive, MateCart only, ask the owner first): `docker compose down -v` deletes the `matecart-dev-*` volumes.
- Containers use `restart: unless-stopped`. They come back after a Docker Desktop restart unless you ran `pnpm stack:down`.

## Troubleshooting

| Symptom                                                                                       | Fix                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Readiness shows `"tenant": "error"`                                                           | Run `pnpm api:provision`. If it says the id does not match, the database was provisioned for a different `MATECART_TENANT_ID`.                                                                       |
| A local secret was printed or shared                                                          | Replace its value in `.env` with a new random 64-hex value (never print it), then recreate only the service that uses it, for example `docker compose up -d --no-deps --force-recreate control-api`. |
| `TenantContextMissing` in a shell or script                                                   | Wrap the code in `tenant_context(...)` (see Tenancy in development).                                                                                                                                 |
| `required variable … is missing a value`                                                      | Run `pnpm setup:env` to create `.env` or add newly introduced keys to it.                                                                                                                            |
| `bind: An attempt was made to access a socket in a way forbidden` or `address already in use` | Port taken or reserved by Windows. Check with `netstat -ano \| findstr :PORT` and `netsh interface ipv4 show excludedportrange protocol=tcp`, then change the port in `.env`.                        |
| Container exits with `/bin/sh^M` or similar                                                   | CRLF line endings. Keep `.gitattributes`; re-checkout with `git add --renormalize .`                                                                                                                 |
| Web page shows "The API did not respond"                                                      | `pnpm stack:up`, then `pnpm health`.                                                                                                                                                                 |
| `ERR_PNPM_IGNORED_BUILDS`                                                                     | A new dependency wants an install script. Review it, then set it in `allowBuilds` in `pnpm-workspace.yaml`.                                                                                          |
| `ERR_PNPM_NO_MATURE_MATCHING_VERSION`                                                         | The version is younger than 24 hours (pnpm's `minimumReleaseAge`). Use an older version or wait.                                                                                                     |
| `openapi.yaml is stale` test failure                                                          | `pnpm api:schema`, review the diff, update `packages/shared-contracts`.                                                                                                                              |
| Celery check times out                                                                        | `docker compose logs commerce-worker`; `docker compose restart commerce-worker`.                                                                                                                     |
