# Progress and handoff

Updated: 2026-10-26 (Asia/Dhaka). **Phase 03.1 — Authentication Security & Final
Closeout is complete locally and ready for the owner's GitHub review.** Phase 03
was committed and its CI passed; formal acceptance remains pending review of this
corrective diff and its post-push CI. Phase 04 is pending and has not started.

## Accepted baseline and boundaries

| Item            | Recorded state                                                                                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 01        | Accepted: `8f3e34b2a2930a08084902d0d5beb9789d6eb4ca`; owner subsequently pushed and confirmed CI 4/4. Its earlier unrun-CI report was historically correct.                                 |
| Phase 02        | Accepted: `72f277e154dab40c586a48dcf5d3852e691993fc`; CI 4/4, 179 tests.                                                                                                                    |
| Correction 02.1 | Accepted: `ba513b5927cdfcadd7e8b98dcb08c71d834146f8`, run 40211394370, 4/4, 245 tests; forged-instance writes/deletes, audit sanitation, UUID policy and secret rotation.                   |
| Correction 02.2 | Accepted and Phase 02 closed: `aceae6fa4ed6cc186ba6a77bdd1869e818f0dceb`, run 40213680998, 4/4, 260 tests; active-context refresh and scoped base/relation managers, state-only migrations. |
| Phase 03        | Implemented and pushed: `f2c18d0b533d1bf94b486e452c75cb2f4db63d89`; run 40230926210, 4/4, 449 automated tests. Independent review then requested the focused 03.1 corrections below.        |
| Current Git     | Baseline `f2c18d0` on `main`; only Phase 03.1 changes are uncommitted/unstaged for the owner. No commit, push or PR was created by the assistant.                                           |
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

Phase 03.1 handoff (Codex → Claude Code, 2026-10-26): Codex stopped at a usage
limit after implementing the 03.1 corrections. Claude Code took over the
uncommitted 14-file worktree on `f2c18d0` without reset or reimplementation,
saved a recovery snapshot outside the repository (`phase-03.1-codex.patch` plus
file copies, verified to reverse-apply), reviewed every change and reran all
gates. The only addition was one smoke assertion that a freshly signed-in
non-Owner's direct team mutation is refused with `permission_denied`.

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

## Phase 03.1 independent audit corrections

The independent source review confirmed three narrow security gaps. The existing
authentication architecture, schema and business policies were retained.

1. **Merchant team step-up:** every invitation create/resend/revoke, role change,
   suspension and reactivation now requires both `staff.manage` and the existing
   ten-minute recent-authentication policy at the backend API boundary. Direct API
   calls cannot bypass the password-confirmation dialog. Wrong passwords do not
   renew the window; fresh sign-in or successful reauthentication does. Customer,
   non-Owner, CSRF and tenant boundaries remain independently enforced.
2. **Google state lifecycle:** an authorization row is locked, checked for unused
   state, expiry and browser binding, and only then marked used. A wrong or missing
   binding no longer invalidates the legitimate browser's flow. Correct callbacks
   remain exactly once under concurrent attempts.
3. **Google provider failures:** expected token/JWKS transport, decoding, payload,
   key and JWT validation failures are normalized to the existing generic
   `google_failed` result. No session or external identity is issued. Exception
   handling is deliberately narrow so unrelated programmer errors remain visible.

No model or migration changed. `openapi.yaml` changed only to document the recent
password-confirmation requirement on the six team mutation operations.

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

Phase 03's pushed baseline passed **449 automated tests** (356 commerce + 57
control + 17 contracts + 16 shared UI + 3 Node) in GitHub Actions run 40230926210.
Phase 03.1 local verification passes **463 automated tests** (370 commerce + 57
control + 17 contracts + 16 shared UI + 3 Node), plus 12 live smoke checks.

| Check                       | Actual observation                                                                                                                                                                                                                                                                                                                                                                              |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Commerce PostgreSQL suite   | 370 passed via `pnpm api:test` (14 new 03.1 regression tests); the 90 focused staff/OIDC/CSRF tests pass. Against the pre-fix `staff/views.py` and `identity/google.py` (throwaway copy), 7 of them fail, proving they detect the original defects. Strict Mypy (114 files), Ruff lint and formatting passed via `pnpm api:lint`.                                                               |
| Control PostgreSQL suite    | 57 passed via `pnpm api:test`; strict Mypy (52 files), Ruff lint and formatting passed via `pnpm api:lint`.                                                                                                                                                                                                                                                                                     |
| Contracts                   | 17 passed.                                                                                                                                                                                                                                                                                                                                                                                      |
| Shared React UI             | 16 passed, including POST invitation preview, QR/key visibility, paste/cooldown and recovery save acknowledgment.                                                                                                                                                                                                                                                                               |
| Node scripts                | 3 passed.                                                                                                                                                                                                                                                                                                                                                                                       |
| `pnpm check`                | Passed: Prettier, ESLint, strict TypeScript/route generation, all TS/UI/Node tests.                                                                                                                                                                                                                                                                                                             |
| `pnpm build`                | Passed for both applications using Webpack, including strict TypeScript and page generation.                                                                                                                                                                                                                                                                                                    |
| Migration round-trip        | `python scripts/verify_auth_migrations.py`: both services fresh migrate, reverse auth migrations, reapply, drift/system checks passed on temporary PostgreSQL DBs; only temporary DBs removed. Dev DBs: no unapplied migrations, no drift. Phase 03.1 has no model or migration changes.                                                                                                        |
| Live Next/API/Mailpit smoke | `node scripts/auth-smoke.mjs --local`: 12 checks passed through the Next.js proxies, adding an aged Owner session, direct team-mutation denial (`reauthentication_required`), password confirmation and successful retry, plus non-Owner direct mutation denial (`permission_denied`), to the existing registration/recovery, CSRF, realm, RBAC, merchant MFA and mandatory console MFA checks. |
| Browser                     | Phase 03: desktop customer sign-in, required-field focus, registration, 390px mobile, anonymous `/manage` redirect, console sign-in redirect. Phase 03.1 changed no UI; HTTP 200 confirmed for customer/merchant sign-in, `/manage/team`, console sign-in and Mailpit. No real external provider/browser OAuth exchange.                                                                        |
| Runtime                     | All eight `matecart-dev` services already up and healthy (no rebuild needed: source is bind-mounted). `pnpm dev` started after `pnpm build` and left running on 3400/3410.                                                                                                                                                                                                                      |
| Celery and health           | `pnpm api:celery-check` returned pong. `pnpm health` passed all six endpoint checks after Next warm-up.                                                                                                                                                                                                                                                                                         |
| System/schema/Compose       | Both Django system checks and `makemigrations --check --dry-run`: passed/no changes. `pnpm api:schema`: both schemas validated without warnings; regenerated `openapi.yaml` files byte-identical to the worktree. `docker compose config --quiet`: passed.                                                                                                                                      |
| Preservation/secrets        | Single `dev-store` tenant unchanged; users 7→9 and memberships 4→5, all new rows labelled smoke-review accounts (no non-review users exist). Other five project containers kept the same IDs/exited states; volumes/networks unchanged. Prototype HEAD `9ff1361`, clean. No `.env`/secret changes, zero staged files. `git diff --check`: passed.                                               |
| GitHub CI                   | Phase 03 run 40230926210 passed 4/4 with 449 tests. Phase 03.1 has not been committed or pushed; its CI and independent final review remain pending.                                                                                                                                                                                                                                            |

## Local review data and integrations

Mailpit at `http://127.0.0.1:18025` contains local test messages only. Live smoke
verification created labelled `review-*@example.test` accounts and audit history;
these remain in development. The review owner is `review-owner@example.test`.
Use merchant Forgot password and the newest local inbox link to try that account.
Random test passwords/codes were never printed or saved to source. The script
preserves a non-review owner if one already exists. No real email, SMS or Google
provider has been configured or claimed tested.

## Remaining scope, limits and next step

- **Next step:** owner reviews the uncommitted Phase 03.1 correction, commits and
  pushes it, verifies GitHub CI, and asks the independent reviewer to inspect the
  new diff. No local implementation/check blocker remains. Do not start Phase 04
  until the owner accepts Phase 03.1 and explicitly authorizes it.
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
  authorization after Phase 03.1 review, CI and independent security acceptance.
