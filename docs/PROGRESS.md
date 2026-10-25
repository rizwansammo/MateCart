# Progress and handoff

**Read this first in every session.** Update it at the end of every session and every phase (see AGENTS.md §7).

Last updated: 2026-10-25 · by Claude Code (Opus 5.5) · end of Phase 02.2 (final tenant read-isolation correction).

## Project status board

| Item                          | Status                                                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Active phase                  | **Phase 02.2 — Tenant read isolation** (final corrective subphase of Phase 02)                                                                          |
| Phase 01                      | **Complete — accepted.** `8f3e34b`; CI run 40201938872 passed 4/4.                                                                                      |
| Phase 02                      | **Complete — accepted.** `72f277e`; CI run 40207921499 passed 4/4.                                                                                      |
| Phase 02.1                    | **Complete — accepted.** `ba513b5`; CI run 40211394370 passed 4/4 (245 tests).                                                                          |
| Phase 02.2                    | **Complete (verified locally) — awaiting owner review.** Uncommitted in the working tree. Its GitHub CI has **not** run yet (runs on the owner's push). |
| Phase 02 closure              | Ready for formal closure once the owner accepts 02.2 and its CI passes.                                                                                 |
| Phases 03–19                  | Pending. Do not start Phase 03 without explicit owner approval.                                                                                         |
| Public subscription / billing | Deferred                                                                                                                                                |
| Deployment model              | Single tenant (strictly enforced at runtime, D-026)                                                                                                     |
| Future architecture           | Multi-tenant ready / hybrid (D-025, D-034, D-038, DATA_MODEL §5)                                                                                        |
| UI prototype (MateCart-UI)    | Strictly read-only (verified untouched: HEAD `9ff1361`, clean)                                                                                          |
| Business model                | Private beta, manual client onboarding                                                                                                                  |
| Repository                    | Public on GitHub by the owner's choice; secrets policy unchanged                                                                                        |

## Phase 02 record

- **Phase 02** (`72f277e`): tenant-aware data foundation: UUIDv7 keys, `Tenant`, fail-closed scoped managers, composite tenant foreign keys, tenancy system checks, strict single-tenant runtime and provisioning, `StoreSettings`, `identity.User` and `accounts.AdminUser`, append-only audit events, DATA_MODEL blueprint (D-023 to D-033).
- **Phase 02.1** (`ba513b5`): tenant-qualified SQL for writes to existing rows (forged-instance bypass), recursive audit sanitisation and field allowlists, identifier policy, rotation of an exposed local secret (D-034 to D-037). 179 → 245 tests.

## Phase 02.2 — tenant read isolation

### Finding — **reproduced and fixed** (high)

The review suspected that `TenantOwnedModel.refresh_from_db()` could read another tenant's row. Regression tests were written first and run against PostgreSQL **before** any change: 10 of 13 scenarios failed with "DID NOT RAISE", meaning data was actually read. With tenant A active and the row owned by tenant B:

| Path                                                                                                                                             | Before the fix             |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------- |
| A. Instance built with B's primary key and B's tenant id, `refresh_from_db()`                                                                    | read B's row               |
| B. Instance loaded under B, refreshed under A                                                                                                    | read B's current row       |
| C. `from_queryset` that is manager-free, built under `unscoped_access`, or filtered to B                                                         | read B's row               |
| D. `refresh_from_db()` with no tenant context                                                                                                    | read the row (fail-open)   |
| E. Deferred-field load with no tenant context                                                                                                    | read the field (fail-open) |
| H. `arefresh_from_db()` (delegates to the sync method)                                                                                           | read B's row               |
| I. Same attack on another model (`StoreSettings`)                                                                                                | read B's row               |
| J. **Relation traversal** on an instance built in code (`AuditEvent(actor_user_id=<B's user>).actor_user`), found during the related-path review | read B's user              |

Scenarios that already behaved correctly: same-tenant refresh (F), privileged refresh with a mismatched claim (G), same-tenant relation traversal.

**Root cause.** The read side still trusted untrusted input. `refresh_from_db()` took the tenant from the instance before the active context, never compared the two, passed a caller's queryset through unchanged, and fell back to an unfiltered read without a context. In addition, Django performs refreshes, deferred-field loads and forward relation reads through `_base_manager`, which Phase 02 had left unscoped.

**Fix (D-038).**

1. `TenantOwnedModel.Meta.base_manager_name = "objects"`: the scoped manager is also the base manager. Every Django-internal read is confined to the active tenant, fails closed without a context, and is unfiltered only inside `unscoped_access`. This covers refresh, deferred fields, forward foreign keys and one-to-ones, generic relations, foreign-key validation and deletion collection.
2. `refresh_from_db()` (and so `arefresh_from_db()`): the active tenant decides. An instance claiming another tenant raises `CrossTenantAccessError` before any query; a caller's `from_queryset` is always filtered to the active tenant; no context raises `TenantContextMissing`; under `unscoped_access` only the row stored under the instance's own tenant is read.
3. The write-path ownership checks from 02.1 use a manager-free `_stored_tenant_id()`, so the now-scoped base manager cannot hide another tenant's row from them. Without this, a forged `delete()` would silently have stopped being detected. It was caught in design and covered by the existing 02.1 tests.
4. New system check `tenancy.E005`: default and base managers of tenant-owned models must be `TenantScopedManager`s.

**Why it is compatible.** Legitimate behaviour is unchanged and tested: same-tenant refresh (all fields, selected fields, scoped `from_queryset`), deferred fields, relation traversal within a tenant, privileged maintenance, every Phase 02 and 02.1 test. Django's only internal caller of `refresh_from_db` is deferred-field loading, which is covered.

**Migration.** Django records `base_manager_name` in migration state, so three state-only `AlterModelOptions` migrations were added: `audit.0003`, `identity.0002`, `stores.0002`. `sqlmigrate` reports each as `(no-op)`, with no SQL. They were verified forward, backward and forward again on a temporary database, then recorded on the development database (three `django_migrations` rows; no table, column or data change). The alternatives that avoid a migration (custom metaclass, custom foreign-key class, patching `_meta`) were rejected as obscure and fragile.

### Related-path review (§5 of the brief)

| Path                                                                  | Result                                                                                                                                                                                                                                                                |
| --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `save()`, `_do_update()`                                              | unchanged guarantees: tenant-qualified UPDATE; cross-tenant detection now via `_stored_tenant_id()` (02.1 tests pass with the exact `CrossTenantAccessError`)                                                                                                         |
| `delete()`                                                            | unchanged guarantees: locked stored-tenant check via `_stored_tenant_id()`                                                                                                                                                                                            |
| `refresh_from_db()`, `arefresh_from_db()`                             | fixed (above)                                                                                                                                                                                                                                                         |
| Scoped querysets, `bulk_update`, `bulk_create(update_conflicts=True)` | unchanged; tests pass                                                                                                                                                                                                                                                 |
| `unscoped_access()`                                                   | still the only unfiltered read path; cannot be entered inside a tenant context; never a fallback for a missing context (tested)                                                                                                                                       |
| Relationships                                                         | forward foreign keys and one-to-ones, generic relations: scoped via the base manager (fixed, J). Reverse managers use the scoped default manager (none exist yet; all current reverse names are `"+"`). Many-to-many between tenant-owned models is forbidden (E003). |

### Files changed in Phase 02.2

- Code: `commerce/tenancy/models.py` (base manager, `refresh_from_db`, `_stored_tenant_id`), `commerce/tenancy/managers.py` (docstring), `commerce/tenancy/checks.py` (E005).
- Migrations (state-only): `audit/migrations/0003_alter_auditevent_options.py`, `identity/migrations/0002_alter_user_options.py`, `stores/migrations/0002_alter_storesettings_options.py`.
- Tests: new `tests/test_tenant_reads.py` (13); `tests/test_tenancy_checks.py` (+2). No existing test weakened or removed.
- Docs: AGENTS, ARCHITECTURE, DATA_MODEL, DECISIONS (D-038; status note on D-034), DEVELOPMENT_ROADMAP (02.2 subsection; 19 phases unchanged), DEVELOPMENT_SETUP, FEATURE_CHECKLIST, this file.

## Verification log (2026-10-25, final run)

| Check                                                       | Result                                                                                              |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Reproduction before the fix                                 | 10 of 13 read scenarios disclosed data (all "DID NOT RAISE")                                        |
| `pnpm api:test`                                             | commerce-api **220 passed** (205 + 13 read isolation + 2 manager checks); control-api **31 passed** |
| `pnpm check`                                                | Prettier, ESLint, typecheck clean; Vitest 6/6; node --test 3/3                                      |
| `pnpm build`                                                | both Next.js apps built                                                                             |
| `pnpm api:lint`                                             | ruff clean; mypy strict: no issues (77 + 35 files)                                                  |
| `manage.py check`, `makemigrations --check` (both services) | no issues; no changes detected                                                                      |
| Migrations on a temporary database                          | forward from empty; reversed the three 02.2 migrations; re-applied; dropped                         |
| `pnpm api:celery-check`                                     | round-trip OK (worker restarted to load the new code)                                               |
| `docker compose config --quiet`                             | valid                                                                                               |
| Dev data                                                    | tenant `dev-store` (active), its store settings and audit event intact; three MateCart volumes kept |
| `pnpm health`                                               | 6/6 OK                                                                                              |
| Docker isolation                                            | non-MateCart containers, volumes and networks identical before and after                            |
| Secrets                                                     | 15 changed files scanned: no local secret value; `.env` files untracked; nothing staged             |
| Totals                                                      | **260 passing tests** (220 + 31 + 6 + 3), up from 245                                               |

**Not verified:** the Phase 02.2 GitHub Actions run (happens on the owner's push). The workflow is unchanged.

## Known issues and limitations

1. **`dumpdata` / `loaddata` of tenant-owned data** now need a tenant context or `unscoped_access` (intended: fail closed).
2. **Raw SQL and querysets built directly with `QuerySet(model=...)`** bypass all managers. They are allowed only for ownership checks inside `tenancy`; application code must not use them (AGENTS.md).
3. **`_do_update` is a private Django hook** (D-034); re-run the forged-instance tests on every Django upgrade.
4. **Audit sanitisation is pattern-based**; allowlists remain the primary control (D-035).
5. Still open from earlier phases: nobody can sign in until Phase 03; customer and staff realms await confirmation (Q7); TRUNCATE on the audit table is restricted by database roles only in production (Phase 17); `phonenumbers` classifiers stop at Python 3.12.

## Decisions needing the owner

[DECISIONS.md → Open questions](DECISIONS.md#open-questions-for-the-owner): Q1 Optimus font licence (Phase 05) · Q2 Redis vs Valkey licensing (Phase 17) · Q4 dashboard placement (Phase 03) · Q6 customer login method (Phase 03) · **Q7 confirm separate customer and staff accounts** (Phase 03).

## Handoff: next steps

1. **Owner:** review Phase 02.2, commit, push and report the CI result. Phase 02 can then be formally closed.
2. **Owner:** approve Phase 03, ideally answering Q4, Q6 and Q7.
3. **Next assistant (Phase 03, Authentication & Security), once approved:**
   - Read AGENTS.md (tenant data and secret rules), this file, DECISIONS D-025 to D-038, ARCHITECTURE §3 and DATA_MODEL §3.
   - Baseline: `pnpm install && pnpm setup:env && pnpm stack:rebuild && pnpm api:provision && pnpm health --skip-web`.
   - Replace `NoLoginBackend` with tenant-aware backends (email and/or phone within the active tenant and realm), sessions or tokens, MFA, tenant-scoped RBAC (staff membership and roles as tenant-owned models with tenant foreign keys), password hashing (consider Argon2), rate limiting, CORS and CSRF. Note that Django's session user lookup (`get_user`) runs through the scoped managers, so it needs the request's tenant context (already set by `TenantMiddleware`).
