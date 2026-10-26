# Security controls and deployment follow-up

Implemented scope: Phase 03 and its Phase 03.1 audit corrections, both accepted and
closed (`5d6152a`, CI run 40237897132: 4/4 jobs, 463 tests), and the Phase 04 product
and inventory engine (implemented locally; owner review pending). This is engineering
guidance, not a claim that the application has completed the final Phase 18
security audit. Live Google, SMS and external SMTP acceptance remain outstanding
until genuine providers are configured.

## Trust boundaries

- Commerce serves only the configured tenant. Customers and merchant staff are
  separate account rows, even for the same email, with distinct credentials and
  sessions. No public staff/administrator registration exists.
- Console identities, database, Redis and keys are separate. Merchant credentials
  cannot authenticate there. Commerce never requires control-api availability.
- Every new auth/invitation/membership model inherits `TenantOwnedModel` and every
  tenant-owned FK has its PostgreSQL composite ownership constraint. Scoped base
  managers, active-context refresh and tenant-qualified writes are retained.
- Request authentication code must never construct manager-free QuerySets, use
  raw SQL or enter `unscoped_access`. An AST guard covers identity/staff sources;
  it complements behavioral tests, not a complete static security proof.
- `NoLoginBackend` still refuses Django's generic login path. Explicit realm
  services and DRF authentication classes are the only browser sign-in boundary.

## Browser credentials

| Realm    | HttpOnly cookie | Path           | SameSite | Absolute / idle defaults |
| -------- | --------------- | -------------- | -------- | ------------------------ |
| Customer | `mc_customer`   | `/`            | Lax      | 30 days / 14 days        |
| Staff    | `mc_staff`      | `/api/manage`  | Lax      | 12 hours / 2 hours       |
| Console  | `mc_console`    | `/api/console` | Strict   | 8 hours / 30 minutes     |

Cookies are host-only, Secure in deployments. Local HTTP overrides Secure only in
Compose. Customer/staff pending MFA expires in ten minutes; console pending MFA
or enrollment expires in fifteen. Password success alone cannot access protected
MFA-required endpoints. Backend permissions/membership status are checked on every
request; hiding a frontend control is never authorization.

Session tokens are opaque random values, stored only as SHA-256 hashes. Sign-in
and pending-to-active transitions issue fresh credentials; security changes rotate
the active token without extending absolute expiry. Activation uses row locks and
rejects stale/reused pending sessions. Password resets revoke all sessions;
password/MFA changes revoke other sessions. Suspensions revoke staff access.

All unsafe requests, including anonymous login, require Django CSRF validation.
CSRF cookies are separately named per backend and readable only to echo the token;
they are not authentication credentials. Responses and browser requests for auth
data are no-store. Cross-origin POSTs and missing CSRF are negative-tested. No
tokens are kept in localStorage, sessionStorage or URL fragments.

## Secrets, challenges and abuse controls

- Argon2 is the first deployed password hasher. Minimum length, similarity,
  common-password and numeric checks apply. Tests use a fast test-only hasher.
- OTPs are cryptographically random, HMAC hashed with purpose binding, ten-minute
  expiry, five attempts, single use. Resend rotates the challenge code under a
  row lock, respects sixty seconds and five sends. Phone completion uses a
  separate high-entropy proof after OTP verification.
- Login, reset, OTP delivery/verification, step-up and TOTP enrollment have scoped
  Redis limits. Account keys are hashed and tenant-namespaced. Rate-limit unit
  tests use isolated in-memory counters; the running stack uses independent
  password-protected Redis instances and was checked separately.
- TOTP seeds are Fernet encrypted with a separate master key. Confirmation is
  required before activation; accepted time steps cannot be replayed. Recovery
  codes are HMAC hashed, transactionally single-use, and replaced on regeneration.
- Email reset/invitation/activation tokens are random, hashed, expiring and
  single-use. Owner invitations are provisioning-only; only Owner manages staff.
  Clients cannot assign Owner, change tenant, or invent permissions.
- Every Owner team mutation requires both `staff.manage` and a sign-in or password
  confirmation within ten minutes. This is enforced by the backend before the
  service is called; the frontend dialog is guidance, not the security boundary.
- Google state and browser binding, PKCE and nonce prevent callback substitution;
  signed ID token validation and stable subjects prevent email-based impersonation.
  Linking requires current account control plus verification of the new method.
  State remains unused when expiry or browser binding fails and is consumed only
  after those checks while the row is locked. Expected token/JWKS transport,
  decoding, key and signature failures return one generic result and issue no
  session or identity. Unrelated programmer errors are not suppressed.

## Audit and logging

Commerce security operations use append-only AuditEvent rows with non-sensitive
operational metadata. Their writes share transactions with changes where the
service performs the mutation; the existing PostgreSQL immutability trigger stays
enabled. Profile edits record the operation, not the name's content. Console
activation/sign-in uses payload-free operational logs with opaque administrator
IDs. A full central operational audit/retention system remains Phase 16/18.

Do not log passwords, OTPs, session cookies, recovery codes, TOTP seeds, OAuth
codes/state, activation/reset tokens or personal contact details. Django access
logging strips query values and Next request/browser-to-terminal logging is
disabled. Invitation/activation inspection uses POST bodies. Provider failures
are logged without exception strings, which can contain credentials or recipients.
Never enable request-body/header logging on these routes. Configure production
reverse proxies/APM/CDN to omit sensitive query strings as well; local application
filters cannot control those external logs.

Mailpit is development-only, loopback-bound, with no forwarding configured. It
intentionally holds test email contents in memory; treat access to its inbox as
access to the development accounts. Console/store use distinct sender settings
and keys, sharing only this local testing inbox.

## Catalogue, inventory and media (Phase 04)

- **Permissions.** Merchant catalogue, pricing, inventory and media APIs sit under
  `/api/manage/` with the staff cookie, CSRF on every unsafe method, and
  `catalog.manage` (Owner, Manager, Content editor). Customers' sessions never
  authenticate there; Order handlers get 403. Cost prices need `catalog.costs`
  (Owner, Manager): they are omitted from responses and refused on write otherwise,
  and audit events record only that a cost changed (D-053).
- **Public exposure.** `/api/catalog/` is anonymous and GET-only. It returns active
  products and active categories/collections, selling prices and an `in_stock` flag
  only: no drafts, archived items, costs, stock quantities, publication problems,
  staff or audit data (tested field by field, D-058).
- **Tenant boundaries.** All 27 Phase 04 models are tenant-owned with composite
  tenant foreign keys; references in requests resolve through scoped managers, so
  another store's id is "not found". Two-tenant tests cover reads, writes, refresh,
  missing context, deferred fields, relation traversal and every one of the 45 tenant
  foreign keys at the PostgreSQL level; an AST test forbids raw SQL, `extra`,
  cursors, manager-free querysets and `unscoped_access` in the four new apps.
- **Media (D-055).** Types are detected from the bytes (JPEG/PNG/WebP fully
  decoded; PDF by signature); SVG, HTML and other types are refused. Size (10 MB),
  dimension (8000 px) and pixel (25 MP) limits stop oversized files and
  decompression bombs; the Content-Length check runs before CSRF or form parsing.
  Client filenames never reach the filesystem; storage paths are generated under
  the active tenant and fail closed without one. Responses carry `nosniff`,
  `Content-Security-Policy: default-src 'none'; sandbox`, a generic filename, and
  PDFs download as attachments. Public files are served only while something
  published uses them. Uploads are rate limited per store.
- **Inventory integrity (D-054).** Stock changes only through locked service
  operations; PostgreSQL CHECKs refuse negative stock and overselling even if a code
  path forgot a rule, and a trigger keeps the ledger append-only below the ORM.
  Retries with an idempotency key apply once; reusing a key for a different request
  answers `409 idempotency_conflict` (request fingerprint, D-059). Expired batch stock
  is never advertised, judged purchasable or reserved (D-060). Reconciliation proves
  levels equal the ledger.

## Appearance and content (Phase 05)

- Appearance and content need `content.manage`; renaming the store and module switches need
  `store.settings`; customers and order handlers are refused. CSRF covers every mutation.
- Brand colours are `#RRGGBB` only and must keep button text readable (4.5:1); the browser
  ignores anything else, and theme values are generated from definitions, never merchant
  CSS. Status colours cannot be re-themed.
- Content is plain text: HTML-like input is refused (including translations), links are
  internal paths only, and the storefront must render text escaped.
- Logos, favicons and section images come only from the validated, tenant-namespaced media
  pipeline (no SVG); they are public only while assigned to branding or an enabled section.
- The public storefront API returns only render-ready data: no staff, drafts, module
  overrides or audit history. Planned modules cannot be enabled before their phase.
- Two-tenant tests cover the four new models, cross-tenant media references (API and
  PostgreSQL composite keys) and isolation of module overrides.

## Limits and scheduled follow-up

- Phase 04 public catalogue and media routes have no dedicated rate limits or CDN
  caching yet (Phases 12/17). Uploaded files are not malware-scanned; only images
  and PDFs are accepted and PDFs are served as downloads. Image resizing/thumbnails
  and a CDN are deferred. Public media may stay in shared caches for up to five
  minutes after a product is unpublished.
- Phase 04.1: reservations are not assigned to batches, so sellable batch stock
  subtracts each location's whole reservation (conservative), and batches that expire
  after a reservation can leave it uncovered until Phase 08 allocation handles it.
  Keys recorded before 04.1 are verified from the ledger without the batch
  manufacture date.

- External Google, SMS and SMTP delivery need real configuration and live provider
  acceptance tests before enabling for clients; none were claimed here.
- Email is delivered once per committed callback, with a safe error signal. This
  is not an exactly-once distributed delivery guarantee. The Phase 13 notification
  engine must add durable retry/deduplication and delivery monitoring.
- Public recovery messages avoid account enumeration; this is not a claim of
  constant total network latency. SMTP latency and infrastructure should receive
  the independent audit's attention.
- Auth cookie path separation is defense in depth within one trusted web app,
  not a separate browser-origin security sandbox. A future XSS can still submit
  same-origin requests. Maintain output escaping and dependency hygiene; Phase 18
  must validate CSP, XSS, CSRF, open redirects and session behavior end to end.
- Expired session/challenge/invitation retention and key-rotation operations need
  explicit production policies (13/17/18). Protect database backups containing
  encrypted seeds and retain encryption keys securely; never blindly regenerate.
- Phase 17: separate migration/runtime PostgreSQL roles, remove runtime schema/DDL
  and trigger-bypass privileges, secure proxy trust, secret provisioning, TLS and
  logging. Compose's database owner is for local development only.
- Phase 18: final Django-version compatibility and private `_do_update()` review,
  tenant bypass audit, race/load tests, dependency vulnerabilities, recovery and
  backup/restore verification. Existing Phase 02.1/02.2 tests stay mandatory.
- Pooled hosting and PostgreSQL RLS remain unimplemented and require a separately
  approved architecture review. Current defenses are for dedicated deployments.
