# Progress and handoff

Updated: 2026-10-26 (Asia/Dhaka). **Phase 03 — Authentication & Security is complete locally and ready for independent owner review.** Formal acceptance requires the owner's
review, commit/push, GitHub CI and independent security audit. Phase 04 is pending.

## Accepted baseline and boundaries

| Item            | Recorded state                                                                                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 01        | Accepted: `8f3e34b2a2930a08084902d0d5beb9789d6eb4ca`; owner subsequently pushed and confirmed CI 4/4. Its earlier unrun-CI report was historically correct.                                 |
| Phase 02        | Accepted: `72f277e154dab40c586a48dcf5d3852e691993fc`; CI 4/4, 179 tests.                                                                                                                    |
| Correction 02.1 | Accepted: `ba513b5927cdfcadd7e8b98dcb08c71d834146f8`, run 40211394370, 4/4, 245 tests; forged-instance writes/deletes, audit sanitation, UUID policy and secret rotation.                   |
| Correction 02.2 | Accepted and Phase 02 closed: `aceae6fa4ed6cc186ba6a77bdd1869e818f0dceb`, run 40213680998, 4/4, 260 tests; active-context refresh and scoped base/relation managers, state-only migrations. |
| Current Git     | Baseline `aceae6f` on main; Phase 03 changes remain uncommitted/unstaged for owner. No commits/pushes or PR created. New Phase 03 CI has not run.                                           |
| Architecture    | Dedicated single-tenant deployment, tenant-aware modular monolith, future hybrid only if approved; commerce/control independent.                                                            |
| Product         | Private beta, manual onboarding. Public commercial signup/subscriptions/billing deferred. Public customer registration is authorized.                                                       |
| Prototype       | MateCart-UI strictly read-only; no edits/build/install.                                                                                                                                     |
| Roadmap         | All 19 main phases preserved; 02.1/02.2 are corrective subphases, not additional main phases.                                                                                               |

## Takeover and preservation

Codex inspected Claude's uncommitted work and all canonical documents before
continuation. A source-only recovery archive (no ignored credentials/dependencies)
was made in the local temporary directory:
`matecart-source-takeover-20261026-005755.zip`. The existing implementations and
migrations were retained, reviewed and completed. Existing local environment keys,
tenant identity, store configuration and persistent volumes were preserved.

## Implemented scope

- **Customer:** email/password registration and verification, login/logout, recovery
  and password management, profile name, phone-only OTP registration/sign-in,
  Google OIDC and verified linking/unlinking. No merge by matching email. Phone
  and Google have explicit provider gates; live external credentials are absent.
- **Merchant:** provisioning-only Owner invitation (`invite_owner`), single-use
  expiring staff invitation activation, resend/revoke, fixed tenant-scoped RBAC,
  role edits/suspend/reactivate. Separate customer/staff account/session realms;
  same storefront web app under `/manage`. Only Owner manages the team.
- **Console:** personnel-only `create_admin` activation, mandatory MFA with
  restricted initial enrollment, independent credentials/database/Redis/cookies,
  protected account shell. No public admin registration or full fleet console.
- **MFA:** TOTP encrypted at rest with replay prevention, verified email codes,
  single-use hashed recovery codes, regeneration, recent-auth checks and sensitive
  changes. Optional merchant policy, mandatory console policy with last-method
  protection. QR defaults visible; setup key reveal/copy is deliberate; guided
  verification, code copy/download and save acknowledgment; clear resend/expiry UI.
- **Security:** Argon2, opaque hashed cookie sessions, separate realm paths,
  Secure deployment cookies, CSRF including login, expiration/revocation/rotation,
  account/IP/tenant/operation limits, transactional tenant-owned audit, safe errors,
  no token persistence in browser storage, no-store authentication responses.
- **Delivery:** safe local Mailpit, independent console Redis, per-store SMS
  adapter attribution, transactional after-commit templates. Full asynchronous
  notification/retry infrastructure remains Phase 13; no fabricated SMS success.
- **Frontend:** runtime-validated shared contracts, accessible form/dialog/OTP
  controls, customer account pages, merchant team/security pages, console
  activation/enrollment/security. Minimal protected shells only. Approved visual
  tokens/typography and modest corners; no prototype demo switcher.

## Continuation review corrections

1. Pending-session activation now locks and checks live state, preventing stale
   references from issuing multiple authenticated sessions.
2. MFA resend serializes challenge reuse/cooldown; console last-method removal
   serializes against the user row. Step-up, password changes and TOTP enrollment
   receive dedicated rate limits.
3. Password/MFA and customer linked-method changes rotate the current browser
   credential without extending absolute expiry. Other sessions are revoked.
4. Invitation/activation preview uses POST bodies instead of API URL tokens; the
   frontend now matches that contract. Django access log query values are removed;
   Next request/browser-to-terminal logging is disabled. Provider failures log no
   raw exception payloads. All auth responses are no-store.
5. Every new commerce auth model has two-tenant read/update/delete/refresh/no-context
   regression coverage; AST guard rejects raw/manager-free/unscoped auth paths.
   Original `_do_update`, forged-instance, deferred-field and audit trigger tests
   remain in the complete suite.
6. Local Turbopack CSS subprocess crashed on Windows. The installed Next 16 CLI
   documents the supported `--webpack` option; both app scripts now use it. Both
   production builds passed using Webpack. Do not build/typegen concurrently with
   the dev server because generated `.next` declarations can race.

## Models, migrations and changed areas

| Area                   | Files/modules                                                                                                                                                                                                               |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Commerce core/identity | `commerce/core/{security,totp,ratelimit,http,delivery,logging}.py`; `identity/{models,auth,customers,google,mfa,otp,passwords,sessions,serializers,schema,views,urls}.py`, templates/settings/urls and locked dependencies. |
| Staff                  | New `commerce/staff` app: models, role permissions, invitation/membership/sign-in services, serializers/views/urls, `invite_owner` command.                                                                                 |
| Control                | `control/accounts` models/auth/services/sessions/views/urls/schema, `create_admin`, templates; independent core security/delivery/http/rate-limit/TOTP modules, settings and lock.                                          |
| Migrations             | Commerce `identity.0003_authentication`, `staff.0001_initial`; control `accounts.0002_console_sign_in`. Additive authentication structures; no database reset/fake migration.                                               |
| New commerce models    | AuthSession, OtpChallenge, ActionToken, ExternalIdentity, OidcAuthorization, TotpDevice, RecoveryCode, StaffMembership, StaffInvitation.                                                                                    |
| New control models     | AdminSession, AdminOtpChallenge, AdminActionToken, AdminTotpDevice, AdminRecoveryCode; AdminUser activation/email-MFA fields.                                                                                               |
| Web                    | `apps/commerce-web/src/{app/account,app/manage,components/*-app.tsx,lib/auth-client.ts}`; console catchall, `/status`, ConsoleApp; configs and scripts.                                                                     |
| Shared                 | `shared-contracts/src/auth*`; shared-ui API client, controls, shells/pages, MFA, security settings, tests/Vitest setup, design token.                                                                                       |
| Infrastructure         | Compose Mailpit/control Redis, new env examples (real values ignored), package/uv locks; no deployments/publishing.                                                                                                         |
| Verification           | Expanded backend tests, frontend contract/component tests, `scripts/auth-smoke.mjs`, `scripts/verify_auth_migrations.py`.                                                                                                   |
| Documentation          | AGENTS, README, ARCHITECTURE, DATA_MODEL, DECISIONS, roadmap, checklist, setup; AUTHENTICATION and SECURITY guides; this canonical handoff.                                                                                 |

## Verification record

Only observed runs are reported. Final documented command runs completed successfully: **449 automated tests** (356 commerce + 57 control + 17 contracts + 16 shared UI + 3 Node), plus 11 separate live smoke checks.

| Check                       | Actual observation                                                                                                                                                                                                                                                                                                                            |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Commerce PostgreSQL suite   | 356 passed via `pnpm api:test`; strict Mypy (114 files), Ruff lint and formatting passed via `pnpm api:lint`.                                                                                                                                                                                                                                 |
| Control PostgreSQL suite    | 57 passed via `pnpm api:test`; strict Mypy (52 files), Ruff lint and formatting passed via `pnpm api:lint`.                                                                                                                                                                                                                                   |
| Contracts                   | 17 passed.                                                                                                                                                                                                                                                                                                                                    |
| Shared React UI             | 16 passed, including POST invitation preview, QR/key visibility, paste/cooldown and recovery save acknowledgment.                                                                                                                                                                                                                             |
| Node scripts                | 3 passed.                                                                                                                                                                                                                                                                                                                                     |
| `pnpm check`                | Passed: Prettier, ESLint, strict TypeScript/route generation, all TS/UI/Node tests.                                                                                                                                                                                                                                                           |
| `pnpm build`                | Passed for both applications using Webpack, including strict TypeScript and page generation.                                                                                                                                                                                                                                                  |
| Migration round-trip        | Both services: fresh migrate, reverse auth migrations, reapply, drift/system checks passed on temporary PostgreSQL DBs. Only temporary DBs removed.                                                                                                                                                                                           |
| Live Next/API/Mailpit smoke | 11 checks passed: registration/verification/recovery/logout, CSRF, realm denial, owner/staff activation/RBAC/replay, QR+TOTP enrollment/token rotation, console restricted enrollment/email MFA/last-method guard and sign-in second step. Passed again through the rebuilt running APIs and Next.js proxies after final changes.             |
| Browser                     | Desktop customer sign-in, required-field focus, registration, 390px mobile, anonymous `/manage` redirect checked. Anonymous console root redirected to its separate sign-in page. MFA components tested in React and via live endpoints; no real external provider/browser OAuth exchange.                                                    |
| Runtime                     | `pnpm stack:up` rebuilt/recreated only MateCart app processes; all eight MateCart services healthy. `pnpm dev` remains running on 3400/3410; web health endpoints responded.                                                                                                                                                                  |
| Celery and health           | `pnpm api:celery-check` returned pong after rebuild. `pnpm health` passed all six endpoint checks. First cold Next compilation exceeded its five-second health timeout; after warm-up the exact command passed.                                                                                                                               |
| System/schema/Compose       | Both Django system checks and `makemigrations --check --dry-run`: passed/no changes. `pnpm api:schema`: both schemas validated without warnings, drift tests green. `docker compose config --quiet`: passed.                                                                                                                                  |
| Preservation/secrets        | Existing tenant/store configuration verified, database volumes retained; other five project containers kept the same IDs/exited states, volumes/networks unchanged. Prototype HEAD `9ff1361`, clean. Actual local-secret scan: zero matches in reviewable source; zero tracked real env files, zero staged files. `git diff --check`: passed. |
| GitHub CI                   | Phase 02.2 accepted baseline only. New Phase 03 CI not run; owner must commit/push.                                                                                                                                                                                                                                                           |

## Local review data and integrations

Mailpit at `http://127.0.0.1:18025` contains local test messages only. Live smoke
verification created labelled `review-*@example.test` accounts and audit history;
these remain in development. The review owner is `review-owner@example.test`.
Use merchant Forgot password and the newest local inbox link to try that account.
Random test passwords/codes were never printed or saved to source. The script
preserves a non-review owner if one already exists. No real email, SMS or Google
provider has been configured or claimed tested.

## Remaining scope, limits and next step

- **Next step:** owner independently reviews the uncommitted Phase 03 source/UI, commits and pushes, verifies GitHub CI, and requests the independent security audit. No unresolved local implementation/check blocker remains; formal acceptance is pending those external review steps.
- Real Google/SMS/SMTP require configuration and provider acceptance before client
  use. Delivery acceptance is not guaranteed mailbox receipt. Durable retry/outbox,
  retention and monitoring belong to Phase 13; see SECURITY.md for limits.
- Key rotation requires preserving/decrypting existing seeds; do not regenerate
  deployed encryption keys casually. Production DB runtime privileges/proxy logging
  hardening is Phase 17; final Django/tenant/security audit is Phase 18.
- Q4 `/manage`, Q6 all three customer methods, Q7 separate realms are resolved.
  Font license (05) and Redis licensing review (17) remain deferred decisions.
- Customer address book/defaults (10), checkout recipient selection/snapshots (07/08),
  managed masking SMS/notifications (13), allowance/prepaid/recharge billing (future)
  remain planned. Full merchant operations (11), console fleet management (16),
  production deployment (17/19) are not implemented here.
- Owner commits and pushes. No co-author trailers. Phase 04 requires explicit
  authorization after Phase 03 review, CI and independent security acceptance.
