# Authentication and account security

Phase 03 implements three separate account realms. The full storefront, merchant
operations dashboard and platform management features remain in their later phases.
Security implementation details and deployment requirements: [SECURITY.md](SECURITY.md).

## Local review

```bash
pnpm install --frozen-lockfile
pnpm setup:env
pnpm stack:up
pnpm api:provision
pnpm dev
```

| Interface                                   | URL                                                                               |
| ------------------------------------------- | --------------------------------------------------------------------------------- |
| Customer registration / sign-in             | http://127.0.0.1:3400/account/register / http://127.0.0.1:3400/account/sign-in    |
| Customer profile and linked sign-in methods | http://127.0.0.1:3400/account                                                     |
| Merchant sign-in                            | http://127.0.0.1:3400/manage/sign-in                                              |
| Merchant team / account security            | http://127.0.0.1:3400/manage/team / http://127.0.0.1:3400/manage/account/security |
| NetaMate Console sign-in                    | http://127.0.0.1:3410/sign-in                                                     |
| Console account security                    | http://127.0.0.1:3410/account/security                                            |
| Local testing inbox (Mailpit)               | http://127.0.0.1:18025                                                            |

Use `127.0.0.1` consistently: cookies, email links and trusted origins are configured
for these URLs. Every development email is captured by Mailpit. SMTP is not
published on the host. Mailpit contains sensitive local verification links and
codes; keep its UI on loopback and never share its contents or screenshots.

Customer registration asks for a name, email and password; verification happens
inline with a six-digit code. Forgotten-password screens send single-use links.
Profile changes and sign-in methods do not create delivery addresses. The delivery
recipient and authentication phone are different concepts (Phases 07 and 10).

There is no public merchant or console administrator registration. NetaMate
personnel provision accounts with commands that email links and never print them:

```bash
docker compose exec commerce-api python manage.py invite_owner --email owner@example.test
docker compose exec control-api python manage.py create_admin --email ops@example.test --name "NetaMate Operator"
```

The first command requires a provisioned store and refuses once an Owner exists.
The owner chooses their password from the invitation email. The owner can invite,
change roles, suspend/reactivate staff and revoke/resend invitations through Team.
Only Owner can manage staff. Owner cannot be assigned by an invitation or role edit.
Console administrators choose a password from their activation link and must finish
MFA enrollment before accessing even the minimal console shell.

## MFA user experience

Merchant MFA is optional. Once enabled, password sign-in yields a restricted
pending session until a configured factor or recovery code is verified. Console
MFA is mandatory and its last configured method cannot be removed.

Account Security offers:

1. Authenticator App: introduction, QR shown by default, deliberate secret-key
   reveal/copy, six-digit confirmation, recovery-code save confirmation.
2. Email codes: masked destination, paste-friendly code input, expiration guidance,
   resend cooldown and clear errors.
3. Ten backup codes shown once, Copy All / Download, acknowledgment before Finish;
   single use, regeneration invalidates previous codes.

Password reauthentication precedes sensitive settings. TOTP replacement preserves
the current device until the replacement is verified. Security changes rotate the
current cookie credential and revoke other sessions where applicable. Codes and
setup keys live only in transient React memory, never localStorage or analytics.

## Configuration boundaries

Compose maps the root example's service-specific keys to backend settings:

| Backend variable                                     | Purpose                                                                                      |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`                                  | Django signing, different for each service                                                   |
| `MATECART_ENCRYPTION_KEY`                            | Separate encryption/HMAC master key; generated locally, backed up securely in deployments    |
| `MATECART_PUBLIC_BASE_URL`                           | Store origin for invitation and recovery links                                               |
| `MATECART_CONSOLE_PUBLIC_BASE_URL`                   | Console origin for activation and recovery links                                             |
| `DJANGO_CSRF_TRUSTED_ORIGINS`                        | Explicit browser origins allowed to submit cookie-authenticated requests                     |
| `MATECART_SECURE_COOKIES`                            | True in deployments; false only on local HTTP                                                |
| `MATECART_TRUSTED_PROXY_COUNT`                       | Number of trusted forwarding hops (local Next proxy: 1); deploy only behind verified proxies |
| `EMAIL_URL`, `DEFAULT_FROM_EMAIL`                    | Independent SMTP configuration for commerce and control; local Mailpit override              |
| `MATECART_SMS_PROVIDER`                              | Import path to a real provider adapter; empty disables phone sign-in                         |
| `GOOGLE_OIDC_CLIENT_ID`, `GOOGLE_OIDC_CLIENT_SECRET` | Commerce-only, server-side Google OAuth client                                               |

The frontend examples configure `COMMERCE_API_INTERNAL_URL` and
`CONTROL_API_INTERNAL_URL`. Next.js forwards `/api/*` to the matching backend.
The browser talks only to its own origin; there are no browser-visible provider
secrets or bearer tokens and no permissive CORS configuration.

Do not rotate the encryption key casually: TOTP seeds/PKCE material and keyed
challenge/recovery hashes depend on it. A managed rotation/re-enrollment procedure
is required before changing a deployed key. Keep existing local values when
running setup again; `pnpm setup:env` only adds missing keys.

## Google OIDC

The adapter uses PyJWT and cryptography for Google's signed RS256 ID tokens.
State, browser binding, nonce and PKCE are server generated. Issuer, audience,
expiry, issued-at, signature and subject are validated. The stable Google subject
is the identity key. A matching email never merges accounts: sign in to the
existing customer account and prove control before linking Google.

Configure an OAuth client of type Web application with the exact redirect URI:

```text
http://127.0.0.1:3400/api/account/google/callback/
```

For deployment use the store's HTTPS origin with the same callback path. Add the
client ID/secret to the root local `MATECART_GOOGLE_OIDC_*` variables; Compose maps
them to backend `GOOGLE_OIDC_*`. Recreate only commerce-api after changing these
values. No Google credentials were supplied or requested for this phase. The UI
shows Google unavailable until both values exist. Tests use signed tokens and a
controlled HTTP/JWKS transport; live Google exchange has not been verified.

## SMS and email delivery

`commerce.core.delivery.SmsProvider.send(to, body, tenant_id, purpose)` is the
provider boundary. Implement a genuine adapter before enabling it, and keep its
credentials server-side per deployment. Phone-only registration needs no email.
Tests use a controlled fake that exercises real challenge creation, hashing,
expiration, attempt limits, completion and account creation. Production settings
reject `tests.*` adapters. A provider failure produces a safe unavailable error;
it never becomes fabricated successful SMS delivery.

NetaMate Managed Masking SMS, approved senders and integrations belong to Phase 13.
Included monthly allowance is consumed before prepaid credits; allowance,
recharge, purchase and subscription accounting remain future billing scope. The
adapter receives tenant and purpose for attribution. Commerce does not call the
Central Console for sign-in or messaging.

Email templates cover verification, existing-account notices, resets, staff
invitations and console activation/codes. Delivery runs after commit and never
puts a code or token into Celery. Password recovery always returns the same public
acceptance response. A delivery acceptance is not a guarantee of mailbox receipt;
payload-free failures are logged for operators. Durable queues, retries and full
provider integrations belong to Phase 13. Live external SMTP/SMS were not tested.

## Verification tools

```bash
pnpm check
pnpm build
pnpm api:lint
pnpm api:test
pnpm api:schema
pnpm api:celery-check
pnpm health
docker compose config --quiet
python scripts/verify_auth_migrations.py
# With Next.js running; intentionally creates labelled local review accounts:
node scripts/auth-smoke.mjs --local
```

Stop `pnpm dev` while building/type-generating to avoid competing generated files
under `.next`. Both apps use Next's supported `--webpack` option because the local
Windows Turbopack CSS subprocess failed (D-046).

The live script retains clearly labelled `review-*@example.test` records and audit
history. It preserves any existing non-review merchant owner. The review owner is
`review-owner@example.test` when created on an empty development store. To try that
account, use merchant Forgot password and open the newest Mailpit link; no reusable
test passwords are stored in source, documentation or output. Other review records
use a run-specific suffix. These are test accounts, never production provisioning.
