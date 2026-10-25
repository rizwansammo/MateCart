# Progress and handoff

**Read this first in every session.** Update it at the end of every session and every phase (see AGENTS.md §7).

Last updated: 2026-10-25 · by Claude Code (Opus 5.5) · end of Phase 02.1 (security hardening and audit fixes).

## Project status board

| Item                          | Status                                                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Active phase                  | **Phase 02.1 — Security hardening & audit fixes** (corrective subphase of Phase 02)                                                                     |
| Phase 01                      | **Complete — accepted.** `8f3e34b`; GitHub Actions run 40201938872 passed 4/4.                                                                          |
| Phase 02                      | **Complete — accepted.** `72f277e`; GitHub Actions run 40207921499 passed 4/4 (179 tests).                                                              |
| Phase 02.1                    | **Complete (verified locally) — awaiting owner review.** Uncommitted in the working tree. Its GitHub CI has **not** run yet (runs on the owner's push). |
| Phase 02 closure              | Phase 02 can be formally closed once the owner accepts 02.1 and its CI passes.                                                                          |
| Phases 03–19                  | Pending. Do not start Phase 03 without explicit owner approval.                                                                                         |
| Public subscription / billing | Deferred                                                                                                                                                |
| Deployment model              | Single tenant (strictly enforced at runtime, D-026)                                                                                                     |
| Future architecture           | Multi-tenant ready / hybrid (D-025, D-034, DATA_MODEL §5)                                                                                               |
| UI prototype (MateCart-UI)    | Strictly read-only (verified untouched: HEAD `9ff1361`, clean)                                                                                          |
| Business model                | Private beta, manual client onboarding                                                                                                                  |
| Repository                    | Public on GitHub by the owner's choice; secrets policy unchanged                                                                                        |

## Phase 01 and 02 record

- **Phase 01** (`8f3e34b`): monorepo, Next.js apps, Django services, isolated Docker stack, CI, docs.
- **Phase 02** (`72f277e`): tenant-aware data foundation: UUIDv7 keys, `Tenant`, fail-closed scoped managers, composite tenant foreign keys, tenancy system checks, strict single-tenant runtime and provisioning, `StoreSettings`, `identity.User` and `accounts.AdminUser`, append-only audit events, DATA_MODEL blueprint (D-023 to D-033). Verified locally and by CI: 142 commerce, 31 control and 6 contract tests, both builds.

## Phase 02.1 — findings and fixes

An independent review of Phase 02 raised four findings. Each was investigated against PostgreSQL before any change.

### Finding 01 — tenant isolation bypass through forged instances (high) — **confirmed and fixed**

- **Root cause:** Django targets existing rows by primary key alone. Phase 02 checked the tenant id on the Python instance, which an instance built in code can set to anything.
- **Reproduced** (tenant A active; tenant B owns the row): `save(force_update=True)` overwrote B's row and **moved it into tenant A**; `save()` and `save(update_fields=...)` on an instance marked as stored updated it; `delete()` deleted it; `refresh_from_db()` read it; `bulk_create(update_conflicts=True)` overwrote it; under `unscoped_access`, `bulk_update` changed and `delete` deleted it despite a wrong tenant claim.
- **Not reproducible (already safe):** `save()` and `save(update_fields=...)` on a _new_ instance (Django inserts because the primary key has a default; the duplicate key fails), and `get_or_create` / `update_or_create` (scoped lookup, then a failing insert).
- **Fix (D-034), no schema change:** tenant-qualified SQL for every statement that touches an existing row. `_do_update` adds `AND tenant_id = …` to the UPDATE of every `save()` path and raises `CrossTenantAccessError` when the key belongs to another tenant; `delete()` locks the row and checks its stored tenant first; `refresh_from_db()` reads through a tenant-qualified queryset; `bulk_update` runs one qualified UPDATE per tenant; upserts must conflict on a key that includes `tenant`.
- **Why it is safe:** legitimate saves, deletes, refreshes, deferred loading and "update without loading" of one's own rows keep working (tested). Qualification happens in the same statement or under a row lock, so there is no check-then-write race. The fail-closed managers, immutable ownership and composite foreign keys are unchanged.

### Finding 02 — sensitive data in audit payloads (high) — **confirmed and fixed**

- **Root cause:** redaction inspected top-level keys only, and provisioning passed the whole store profile (`asdict(profile)`) into the audit trail.
- **Reproduced:** an API key nested under `integration.credentials` was stored; provisioning stored the owner's contact email and phone.
- **Fix (D-035):** `field_changes()` requires an explicit field allowlist; provisioning records only operational facts (no contact details or legal name). `sanitize_audit_value()` sanitises every payload recursively: sensitive keys in any naming style at any depth, secret-looking values under any key (private keys, password hashes, JWTs, bearer credentials, card numbers), unsupported objects reduced to type markers, depth and length caps, no mutation of the input. Provisioning errors no longer echo database messages. Audit immutability (ORM and trigger) is unchanged.
- The existing development audit event (written before the fix) was checked: its contact fields are empty, so no personal data needs handling.

### Finding 03 — UUID version consistency (low) — **resolved by policy and a small fix**

- `setup:env` generated `MATECART_TENANT_ID` as UUIDv4 while the docs implied every id was UUIDv7.
- **Policy (D-036):** keys MateCart generates are UUIDv7. Externally assigned ids (`MATECART_TENANT_ID` = `Tenant.id`, future console store registration ids) may be any RFC 9562 UUID; nil and max UUIDs are now rejected at startup. New ids generated outside the database are UUIDv7 (`scripts/uuidv7.mjs`, used by `setup:env`, tested with `node --test` in `pnpm test`).
- The existing development tenant id (UUIDv4) was **kept**: no reset, no re-provisioning, no key rewrite.

### Finding 04 — exposed local development secret (medium) — **rotated**

- The full local `MATECART_CONTROL_SECRET_KEY` was printed in the Phase 02 transcript. It was never committed (verified).
- Rotated in `.env` by a script that prints no values (all other lines verified unchanged). Only `control-api` was recreated. Fingerprint comparison confirmed it uses the new key and that no container holds the old value. Readiness 200.
- **Transcript review:** the only full secret printed was this key. Phase 01 output showed masked 8-character prefixes of the local secrets (32 of 256 bits); that is not a usable exposure, so they were not rotated (D-037). The printed tenant id is an identifier, not a secret. Celery and log output mask passwords.
- New rule in AGENTS.md: never print secret values; compare fingerprints instead.

## Files changed in Phase 02.1

- Code: `commerce/tenancy/models.py`, `commerce/tenancy/managers.py`, `commerce/tenancy/provisioning.py`, `commerce/audit/services.py`, `config/settings/base.py` (tenant id validation), `scripts/setup-env.mjs`, new `scripts/uuidv7.mjs`, `package.json` (`pnpm test` also runs script tests).
- Tests: new `tests/test_forged_instances.py` and `tests/test_audit_sanitization.py`, new `scripts/uuidv7.test.mjs`; updated `tests/test_audit.py` (allowlist argument) and `tests/test_project.py` (nil and max UUID cases). No existing test was weakened or removed.
- Docs: AGENTS, ARCHITECTURE, DATA_MODEL, DECISIONS (D-034 to D-037; status notes on D-023, D-025, D-029), DEVELOPMENT_ROADMAP (02.1 subsection; 19 phases unchanged), DEVELOPMENT_SETUP, FEATURE_CHECKLIST, this file.
- Local only (not in Git): `.env` (rotated key). No migrations, no schema changes, no database or volume changes.

## Verification log (2026-10-25, final run)

| Check                                       | Result                                                                                                                                       |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Reproduction before fixes                   | 7 of 11 forged-instance scenarios failed (bypass) and the audit probe stored a nested secret and the contact email; after the fixes all pass |
| `pnpm api:test`                             | commerce-api **205 passed** (142 + 63 new: 11 forged-instance, 50 audit sanitisation, 2 tenant-id validation); control-api **31 passed**     |
| `pnpm check`                                | Prettier, ESLint, typecheck clean; Vitest 6/6; **node --test 3/3**                                                                           |
| `pnpm build`                                | both Next.js apps built                                                                                                                      |
| `pnpm api:lint`                             | ruff clean; **mypy strict: no issues (76 + 35 files)**                                                                                       |
| `manage.py check`, `makemigrations --check` | no issues; no changes detected (no schema changes in 02.1)                                                                                   |
| `pnpm api:celery-check`                     | round-trip OK                                                                                                                                |
| `docker compose config --quiet`             | valid                                                                                                                                        |
| Secret rotation                             | control-api recreated alone, uses the new key, old value present in no container, readiness 200                                              |
| Dev data                                    | tenant `dev-store`, its store settings and audit event intact; all three MateCart volumes kept                                               |
| `pnpm health`                               | 6/6 OK                                                                                                                                       |
| Docker isolation                            | non-MateCart containers, volumes and networks identical before and after                                                                     |
| Secrets in commit candidates                | 20 files scanned: no local secret value, no credential patterns; `.env` and `.env.local` untracked; nothing staged                           |
| Totals                                      | **245 passing tests** (205 + 31 + 6 + 3), up from 179                                                                                        |

**Not verified:** the Phase 02.1 GitHub Actions run (happens on the owner's push). The workflow is unchanged; every step it runs passed locally. The new script tests run in CI through `pnpm test`.

## Known issues and limitations

1. **`_do_update` is a private Django hook** (D-034). The forged-instance tests fail if a Django upgrade bypasses it; re-run them on every Django upgrade.
2. **Raw SQL is not tenant-scoped** (unchanged). Composite foreign keys still protect references; raw SQL on tenant data needs review (AGENTS.md).
3. **Audit sanitisation is pattern-based.** The allowlist is the primary control; sanitisation is a safety net that errs towards redaction.
4. **Rotation does not erase history.** The old control key remains in past transcripts; it is no longer used anywhere.
5. Still open from Phase 02: nobody can sign in until Phase 03; customer and staff realms await confirmation (Q7); TRUNCATE on the audit table is blocked only by database roles in production (Phase 17); `phonenumbers` classifiers stop at Python 3.12.

## Decisions needing the owner

[DECISIONS.md → Open questions](DECISIONS.md#open-questions-for-the-owner): Q1 Optimus font licence (Phase 05) · Q2 Redis vs Valkey licensing (Phase 17) · Q4 dashboard placement (Phase 03) · Q6 customer login method (Phase 03) · **Q7 confirm separate customer and staff accounts** (Phase 03).

## Handoff: next steps

1. **Owner:** review Phase 02.1, commit, push and report the CI result. Phase 02 can then be closed.
2. **Owner:** approve Phase 03, ideally answering Q4, Q6 and Q7.
3. **Next assistant (Phase 03, Authentication & Security), once approved:**
   - Read AGENTS.md (tenant data and secret rules), this file, DECISIONS D-025 to D-037, ARCHITECTURE §3 and DATA_MODEL §3.
   - Baseline: `pnpm install && pnpm setup:env && pnpm stack:rebuild && pnpm api:provision && pnpm health --skip-web`.
   - Replace `NoLoginBackend` with tenant-aware backends (email and/or phone within the active tenant and realm), sessions or tokens, MFA, tenant-scoped RBAC (staff membership and roles as tenant-owned models with tenant foreign keys), password hashing (consider Argon2), rate limiting, CORS and CSRF. Audit sign-in and permission changes with allowlisted payloads.
