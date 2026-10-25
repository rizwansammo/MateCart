# AGENTS.md — MateCart engineering rules (canonical)

This file is the single source of truth for how people and AI assistants (Claude Code, OpenAI Codex) work in this repository. `CLAUDE.md` imports it. If another document conflicts with this file, this file wins; fix the other document in the same change.

MateCart is a commercial, white-label e-commerce product by NetaMate Solutions. Treat every change as production work.

## 1. Start of every session

Read, in this order, before changing anything:

1. This file.
2. [docs/PROGRESS.md](docs/PROGRESS.md): active phase, what is done, what is next, known issues. **The handoff lives here.**
3. [docs/DECISIONS.md](docs/DECISIONS.md): decisions already made. Do not reverse one without the owner's approval.
4. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DATA_MODEL.md](docs/DATA_MODEL.md) and [docs/DEVELOPMENT_ROADMAP.md](docs/DEVELOPMENT_ROADMAP.md) for the area you will touch.

Then run `git status` and inspect the real state of the files, the Docker stack (`pnpm stack:ps`) and the tools. Never assume.

## 2. Phase discipline

- Work only on the active phase recorded in `docs/PROGRESS.md`. Never start the next phase without explicit approval from the owner.
- Never begin Phase 19 (production launch) before Phases 01–18 meet their acceptance criteria.
- Do not build deferred features: public self-service signup, subscription checkout, automated package purchasing, commercial billing, licence pricing.
- Missing information: if it is genuinely blocking (architecture change, tenancy strategy, stack deviation, destructive operation, other projects affected, real credentials, paid infrastructure, unresolved security decision), ask one concise question. Otherwise choose a conservative, well-established default, record it in `docs/DECISIONS.md` and continue.

## 3. Hard safety rules

### UI prototype is read-only

- `C:\Users\Rizwan\Desktop\MateCart-UI` (github.com/rizwansammo/MateCart-UI) is the approved, clickable UI prototype. It is **strictly read-only**: never edit, format, install into, build in, commit to or otherwise write to it.
- It is the visual and functional reference. Production code reimplements it cleanly. Do not copy its generated architecture (inline style strings, class components, `window.MC` globals, `@ts-nocheck`, mock data).
- The prototype's top bar (Storefront / Merchant / Design system / preset switcher) is a demo device. It must never appear in client stores.

### Git

- **AI assistants never run `git commit` or `git push`.** The repository owner makes all commits. Leave changes in the working tree and report them.
- If the owner explicitly asks an assistant to commit, **never add `Co-Authored-By` or any other co-author or attribution trailer.**
- Never force-push, rewrite history, reset destructively or push to `main`.
- Inspect `git status` before editing. Never overwrite unrelated local work.

### Docker (Docker Desktop is shared with MateERP, DeltaWave, HonriseSol and others)

- Only operate on the Compose project **`matecart-dev`** defined in the root `compose.yaml`. Use `pnpm stack:*` or `docker compose …` from the repository root.
- Never run global cleanup: `docker system prune`, `docker volume prune`, `docker image prune`, `docker network prune`, or `docker rm`, `stop`, `restart` on anything that is not `matecart-dev-*`.
- Never connect MateCart to another project's containers, databases, networks or volumes. Never change Docker Desktop's global settings without approval.
- New services: named volumes `matecart-dev-*`, ports published on `127.0.0.1` only, ports checked as free first (see `docs/DEVELOPMENT_SETUP.md`).
- `docker compose down -v` permanently deletes MateCart's local databases. Run it only with the owner's approval.

### Secrets and data

- Never commit `.env`, `.env.local`, keys, tokens, passwords, certificates or client data. Only `.env.example` files with placeholders are committed.
- Do not request real provider credentials (payments, couriers, email, AI, cloud, domains) unless the active phase needs them and the owner agrees.
- Never log secrets or personal data. Readiness checks and error responses must not expose internals.
- **Never print secret values**, including `.env` lines, in commands, logs or reports. Check secrets by presence, format or fingerprint comparison. If one is exposed, rotate it and recreate only the service that uses it (D-037).

## 4. Architecture guardrails

Details and reasoning: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

- **Single-tenant-first, multi-tenant-ready.** Each client store is its own deployment (app, database, secrets, media, domain, credentials), serving exactly the tenant in `MATECART_TENANT_ID`. Code stays tenant-aware so a shared mode is possible later, but do not add pooled-mode machinery without an approved decision. A `tenant_id` column alone is not isolation.
- **Independent operation.** A store's storefront, checkout, dashboard and integrations must keep working when the NetaMate Central Console is down. `commerce-*` never calls `control-api` at runtime for anything a store needs to serve customers.
- **Data boundaries.** `control-api` never stores copies of client business data. Commerce data stays in the store's own database.
- **Routing.** All Django routes live under `/api/`. Next.js owns every other path.
- **Configuration** comes from environment variables only. Settings modules never contain secrets.

## 5. Engineering standards

### TypeScript and Next.js

- `strict` TypeScript (see `tsconfig.base.json`, including `noUncheckedIndexedAccess`). No `any`, `@ts-ignore` or `@ts-nocheck`.
- Validate untrusted data at boundaries (API responses, form input, env) with type guards or schemas. See `packages/shared-contracts`.
- Server-only modules start with `import 'server-only'`. Secrets never go into `NEXT_PUBLIC_*` variables.
- Styling: Tailwind utilities backed by the tokens in `@matecart/shared-ui/theme.css`. No hard-coded colours in components.
- Code moves into `packages/*` only when at least two apps use it.
- Next.js 16 differs from older releases (Turbopack by default, no `next lint`, typed routes, changed conventions). Check the installed version's guides in `apps/<app>/node_modules/next/dist/docs/` before relying on remembered APIs. `agentRules: false` in each `next.config.ts` stops `next dev` writing extra `AGENTS.md` files, so this file stays the only rulebook.

### Python and Django

- Python 3.13, Django 5.2 LTS, DRF. Type hints everywhere. `mypy` (strict) and `ruff check` + `ruff format` must pass.
- New endpoints are **denied by default** (DRF default permission). Anonymous access is an explicit, reviewed opt-in.
- Errors use the envelope `{"error": {"code", "message", "details"?}}`. The request id is returned in `X-Request-ID`.
- Celery tasks must be **idempotent**: tasks are acknowledged late and re-queued if a worker dies.
- Every model change ships with its migration. `makemigrations --check` must pass.
- API changes regenerate the committed OpenAPI schema (`pnpm api:schema`) and update `packages/shared-contracts`. A test fails when `openapi.yaml` is stale.
- **Tenant data rules** (ARCHITECTURE §3, DATA_MODEL §1):
  - Every business model extends `TenantOwnedModel`; extend `TenantOwnedModel.Meta.constraints`, never replace them.
  - Every FK from a tenant-owned model to another tenant-owned model gets a `TenantForeignKeyConstraint`. The `tenancy` system checks fail the build otherwise.
  - Business keys are unique per tenant, for example `UniqueConstraint(fields=['tenant', 'sku'])`.
  - Never take a tenant from request data. Never use raw SQL, querysets built directly with `QuerySet(model=...)` or `unscoped_access()` in request paths. `unscoped_access(reason)` is for reviewed platform maintenance only.
  - Reads are governed by the active tenant: the base manager is scoped, and `refresh_from_db()` refuses instances or querysets from another tenant (D-038). Keep `TenantOwnedModel.Meta.base_manager_name` when extending Meta (check E005).
  - Load rows through the scoped manager before changing them. Writes to existing rows are tenant-qualified in SQL (D-034), so a hand-built instance with someone else's primary key fails instead of writing; do not work around that. Upserts must use a conflict key that includes `tenant`.
  - Celery tasks touching tenant data use `base=TenantTask` and receive `tenant_id` explicitly.
  - Cache keys use `tenant_cache_key()`; uploaded files use `tenant_storage_path`.
  - Prove isolation with two-tenant tests for every new model and endpoint.
- Important changes write an `AuditEvent` with `record_audit_event()` inside the same `transaction.atomic()` block. Build `changes` with `field_changes(..., fields=[...])`, an explicit allowlist. Never record secrets, and record personal data only when the audit need is documented. Sanitisation is a safety net, not a licence (D-035).
- Money is `Decimal` with an ISO 4217 currency, never float. Countries, currencies, time zones and phones use ISO, IANA and E.164 identifiers (`commerce.core.standards`, `commerce.core.contact`).
- Catalogue, pricing, inventory and media changes go through their `services` modules (D-051 to D-058). Amounts respect the currency's minor unit; cost prices need `catalog.costs` and never enter audit `changes`.
- Stock changes only through `commerce.inventory.services` operations. Never write `StockLevel` quantities directly and never update or delete `StockMovement` rows (append-only, trigger-enforced); corrections are reversals or adjustments. Keep the lock order (variant, levels by primary key, batch levels, serials).
- Uploaded files are validated from their bytes in `commerce.media.services`, stored through `tenant_storage_path` and served only through checked API views, never directly from `MEDIA_ROOT`.
- Users: `identity.User` (store customers and staff) and `accounts.AdminUser` (console) are the `AUTH_USER_MODEL`s. Never use Django's global groups and permissions for store roles.
- Authentication: keep customer/staff/console cookie realms separate. Merchant routes are `/manage` in commerce-web. Merchant MFA is optional; console MFA is mandatory. Do not bypass pending MFA/enrollment, CSRF, recent-authentication or membership checks. Never merge Google accounts by email. See `docs/AUTHENTICATION.md` and `docs/SECURITY.md`.
- Never log authentication query strings or provider exception payloads; they can contain action tokens or credentials. Auth responses are no-store. Keep Google/SMS unavailable until genuine providers are configured; test fakes are never live providers.

### Tests

- Every feature ships with tests. Backend tests run against real PostgreSQL and Redis (Compose locally, service containers in CI).
- Never claim a test passed unless you ran it and saw it pass.

### Dependencies

- Before adding or upgrading a dependency, verify compatibility (peer dependencies, Python classifiers, supported versions). Record significant choices in `docs/DECISIONS.md`.
- pnpm denies dependency install scripts by default (`allowBuilds` in `pnpm-workspace.yaml`). Read a script before allowing it.
- Python dependencies: edit `pyproject.toml`, run `docker compose run --rm --no-deps <service> uv lock`, then `pnpm stack:rebuild`.

## 6. Definition of done

A change is done only when all of these hold:

- `pnpm check` and `pnpm build` pass.
- With the stack up: `pnpm api:lint`, `pnpm api:test` and `pnpm health` pass.
- Relevant docs are updated: `docs/PROGRESS.md` always; `docs/DECISIONS.md` for new decisions; `docs/FEATURE_CHECKLIST.md` for feature status.
- The report states what ran, what passed, what failed and what was not verified.

## 7. Handoff protocol (end of every session and every phase)

Update `docs/PROGRESS.md` with:

- active phase and its status;
- completed and pending tasks;
- tests and checks run, with actual results;
- files and services added or changed;
- known issues and decisions needing the owner;
- the exact next step for the next assistant.

Keep it factual and current, so the next assistant (Claude Code or Codex) can continue without this conversation.

At the end of every phase, leave the app running on localhost for the owner to review: run `pnpm stack:up` and `pnpm dev`, confirm http://127.0.0.1:3400 and http://127.0.0.1:3410 respond, and share the URLs.

At the end of every phase, also give the owner a ready-to-use commit message for that phase's changes. Use an imperative subject of about 72 characters, then a body wrapped at 72 columns and grouped by area. Describe what was built and leave out the phase number. Do not run `git commit`, and never add co-author or attribution trailers.
