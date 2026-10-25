# Progress and handoff

**Read this first in every session.** Update it at the end of every session and every phase (see AGENTS.md §7).

Last updated: 2026-10-25 · by Claude Code (Opus 5.5) · end of Phase 02 implementation.

## Project status board

| Item                          | Status                                                                                                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Active phase                  | **Phase 02 — Database & Tenant Architecture**                                                                                                                                   |
| Phase 01                      | **Complete — accepted by the owner.** Baseline commit `8f3e34b` on `main`; GitHub Actions run 40201938872 **passed (4/4 jobs)**.                                                |
| Phase 02                      | **Complete (verified locally) — awaiting owner review.** Uncommitted in the working tree for the owner to commit. Its GitHub CI has **not** run yet (runs on the owner's push). |
| Phases 03–19                  | Pending. Do not start Phase 03 without explicit owner approval.                                                                                                                 |
| Public subscription / billing | Deferred                                                                                                                                                                        |
| Deployment model              | Single tenant (strictly enforced at runtime, D-026)                                                                                                                             |
| Future architecture           | Multi-tenant ready / hybrid (D-025, DATA_MODEL §5)                                                                                                                              |
| UI prototype (MateCart-UI)    | Strictly read-only (verified untouched: HEAD `9ff1361`, clean)                                                                                                                  |
| Business model                | Private beta, manual client onboarding                                                                                                                                          |
| Repository                    | Public on GitHub by owner's choice; secrets policy unchanged                                                                                                                    |

## Phase 01 record

Phase 01 was verified locally on 2026-10-25 (51 backend tests, 6 contract tests, both builds). Its handoff noted that GitHub CI had not yet run; that was accurate at the time. The owner then committed and pushed `8f3e34b`, and CI passed 4/4. Phase 01 is accepted.

## Phase 02 — what was delivered

### Architecture decisions (D-023 to D-033 in DECISIONS.md)

- **Tenant strategy:** tenant-aware commerce core; strict single-tenant runtime (`MATECART_TENANCY_MODE=single`, `MATECART_TENANT_ID`), tenant resolved from configuration only, explicit atomic provisioning, readiness fails closed.
- **Isolation:** fail-closed scoped managers, guarded writes, immutable ownership, PostgreSQL composite tenant foreign keys, build-time system checks, logged `unscoped_access`, tenant-verified Celery tasks, tenant cache keys and storage paths. RLS deferred to a future pooled mode, with documented reasons.
- **Database ownership:** each store's commerce database holds only its own business data; control-api holds NetaMate data only; opaque UUID references, no cross-database keys.
- **Identity:** `identity.User` (per tenant; `customer` and `staff` realms; email and/or phone, normalised and unique per tenant and realm) and `accounts.AdminUser` (console only). Login flows wait for Phase 03 (`NoLoginBackend`).
- **Conventions:** UUIDv7 keys, timestamps, per-tenant uniqueness, `CHECK`-backed choices, ISO/IANA/E.164 data, `Decimal` money, no global soft delete, append-only audit events in the same transaction.

### New modules and models

| Service      | App                 | Contents                                                                                                                                                                                                                                                                          |
| ------------ | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| commerce-api | `commerce.core`     | `ids.uuid7`, `UUIDModel`, `TimestampedModel`, `standards` (country/currency/time zone/language validation via Babel and zoneinfo), `contact` (email and E.164 phone normalisation), `TenantUnavailable` error mapping, `tenant` readiness check                                   |
| commerce-api | `commerce.tenancy`  | `Tenant`, `TenantOwnedModel`, `TenantScopedManager`/`TenantQuerySet`, `TenantForeignKeyConstraint`, `context` (`tenant_context`, `unscoped_access`), `TenantMiddleware`, `resolution`, `TenantTask`, `keys`, system checks E001–E004, `provisioning` + `provision_tenant` command |
| commerce-api | `commerce.stores`   | `StoreSettings`, `StorePreset`                                                                                                                                                                                                                                                    |
| commerce-api | `commerce.identity` | `User` (`AUTH_USER_MODEL`), `UserManager`, `AccountType`, `NoLoginBackend`                                                                                                                                                                                                        |
| commerce-api | `commerce.audit`    | `AuditEvent` (append-only), `record_audit_event`, `field_changes`, redaction                                                                                                                                                                                                      |
| control-api  | `control.core`      | `ids.uuid7`, `UUIDModel`, `TimestampedModel`                                                                                                                                                                                                                                      |
| control-api  | `control.accounts`  | `AdminUser` (`AUTH_USER_MODEL`), `AdminUserManager`, `NoLoginBackend`                                                                                                                                                                                                             |

### Migrations

| Migration                         | Content                                                                                                                      |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `tenancy.0001_initial`            | `tenancy_tenant` (handle format, status `CHECK`s)                                                                            |
| `stores.0001_initial`             | `stores_storesettings` (one per tenant, `(tenant, id)` key, preset/country/currency/language/phone `CHECK`s)                 |
| `identity.0001_initial`           | `identity_user` (`(tenant, id)` key, partial unique email/phone per tenant and realm, identifier and normalisation `CHECK`s) |
| `audit.0001_initial`              | `audit_auditevent` (composite tenant FK to `identity_user`, action and actor `CHECK`s, tenant-prefixed indexes)              |
| `audit.0002_append_only`          | Trigger refusing UPDATE and DELETE (`RunSQL` with reverse)                                                                   |
| `accounts.0001_initial` (control) | `accounts_adminuser` (unique lowercase email `CHECK`)                                                                        |
| Django `auth.0001`–`0012`         | Installed now that the custom user models exist (D-010 fulfilled)                                                            |

### Other changes

- `compose.yaml`: `MATECART_TENANCY_MODE` and `MATECART_TENANT_ID` for commerce-api and commerce-worker.
- `.env.example`: `MATECART_TENANT_ID=__GENERATE_UUID__`. `scripts/setup-env.mjs` now **adds** missing keys to existing env files, never changing existing values.
- `package.json`: `pnpm api:provision` (idempotent dev store).
- Dependencies (commerce): `babel` 2.18.0, `phonenumbers` 9.0.41, `tzdata` 2026.5 (lockfile regenerated in a container).
- Ruff: generated migrations excluded; `RUF012` relaxed only for Django `Meta` lists in `models.py` and tests.
- OpenAPI: commerce readiness gained `tenant` (`openapi.yaml` regenerated; shared-contracts unchanged because checks are a map).
- Docs: new [DATA_MODEL.md](DATA_MODEL.md); ARCHITECTURE §3 rewritten (final tenancy strategy); DECISIONS D-023–D-033 and updated questions; ROADMAP, FEATURE_CHECKLIST, DEVELOPMENT_SETUP, README and AGENTS (tenant data rules) updated.

## Verification log (2026-10-25, final run)

| Check                                                                         | Result                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Migrations on a **temporary** commerce database                               | forward from empty OK; `makemigrations --check` no changes; reversed to zero (no tables left); re-applied OK; database dropped                                                                              |
| Migrations on a **temporary** control database                                | forward OK; no drift; `accounts` zero and back OK; database dropped                                                                                                                                         |
| Migrations on development databases                                           | applied additively at container start (tenancy, stores, identity, audit 0001–0002, auth 0001–0012, accounts 0001); no reset, volumes kept                                                                   |
| `sqlmigrate` review                                                           | composite FK `(tenant_id, actor_user_id) → identity_user (tenant_id, id)`, partial unique indexes, all `CHECK`s as designed                                                                                 |
| `pnpm api:test`                                                               | commerce-api **142 passed**; control-api **31 passed**                                                                                                                                                      |
| Two-tenant isolation tests                                                    | 22 in `test_tenant_isolation.py` + 5 context + 4 system-check tests: all passed                                                                                                                             |
| `pnpm api:lint`                                                               | ruff check and format clean; **mypy strict: no issues (74 + 35 files)**                                                                                                                                     |
| `manage.py check`                                                             | no issues (includes tenancy checks E001–E004)                                                                                                                                                               |
| `pnpm format:check`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` | all exit 0 (Vitest 6/6; both Next.js builds)                                                                                                                                                                |
| `pnpm api:celery-check`                                                       | real round-trip OK (`'pong'`)                                                                                                                                                                               |
| `docker compose config --quiet`                                               | valid                                                                                                                                                                                                       |
| Provisioning on the dev stack                                                 | readiness `tenant: error` (503) before; `pnpm api:provision` created tenant `dev-store` with the `.env` UUID, store settings and a `tenant.provisioned` audit event; rerun "Nothing changed"; readiness 200 |
| `pnpm health`                                                                 | 6/6 OK; commerce-web status page shows `tenant ok`                                                                                                                                                          |
| Docker isolation                                                              | non-MateCart containers, volumes and networks identical before and after                                                                                                                                    |
| Secrets                                                                       | 86 commit candidates scanned: no generated secret, no credential patterns; `.env` and `.env.local` ignored                                                                                                  |

Commerce test breakdown: isolation 22, conventions 26, identity 16, provisioning 14, store settings 14, audit 9, project 9, request id 9, errors 6, health 5, context 5, checks 4, Celery 3.

**Not verified:** the Phase 02 GitHub Actions run (happens on the owner's push). The CI workflow itself is unchanged; every step it runs passed locally.

## Known issues and limitations

1. **Nobody can sign in yet** by design (`NoLoginBackend` in both services) until Phase 03.
2. **Customer and staff realms** (D-027, Q7) are a deliberate choice awaiting the owner's confirmation. Reversible until the first production launch.
3. **Raw SQL is not tenant-scoped.** Scoping covers the ORM; composite foreign keys protect references. Raw SQL on tenant data must be reviewed (AGENTS.md).
4. **No production consumers yet** for `TenantTask`, `tenant_cache_key` and `tenant_storage_path`; they are verified by tests and will be used from Phase 04.
5. `phonenumbers` declares Python classifiers only up to 3.12; it passes all tests on 3.13 (D-031).
6. **TRUNCATE on the audit table** is not blocked (test-database flushes need it). Production will use a runtime database role that does not own tables (Phase 17).
7. While working on Windows, inline edit scripts briefly produced CRLF line endings and one cp1252-encoded file. Both were caught and fixed; a final scan confirmed every file is LF and valid UTF-8.
8. Phase 01 limitations that still apply: ESLint 9 (D-005), hand-mirrored TypeScript contracts (D-013), frontends run on the host in development (D-008).

## Decisions needing the owner

[DECISIONS.md → Open questions](DECISIONS.md#open-questions-for-the-owner): Q1 Optimus font licence (Phase 05) · Q2 Redis vs Valkey licensing (Phase 17) · Q4 dashboard placement (Phase 03) · Q6 customer login method (Phase 03; no longer blocks the schema) · **Q7 confirm separate customer and staff accounts** (Phase 03). Q3 and Q5 are resolved.

## Handoff: next steps

1. **Owner:** review Phase 02, commit it, push, and report the CI result.
2. **Owner:** approve Phase 03, ideally answering Q4, Q6 and Q7.
3. **Next assistant (Phase 03, Authentication & Security), once approved:**
   - Read AGENTS.md (tenant data rules), this file, DECISIONS D-025–D-029, ARCHITECTURE §3 and DATA_MODEL §3.
   - Baseline: `pnpm install && pnpm setup:env && pnpm stack:rebuild && pnpm api:provision && pnpm health --skip-web`.
   - Replace `NoLoginBackend` with tenant-aware backends that resolve email or phone within the active tenant and realm; design sessions or tokens, MFA, tenant-scoped RBAC (staff membership and roles as tenant-owned models with tenant foreign keys), password hashing (consider Argon2), rate limiting, CORS and CSRF.
   - Keep every new model on `TenantOwnedModel` and prove isolation with two-tenant tests.
