# Central Platform Console

Status: Phase 16.1 integrity corrections implemented locally; owner browser acceptance and GitHub
CI remain pending. This document describes the Control-side management plane; remote deployment
execution remains Phase 17.

## Boundary

The NetaMate Console is `platform-console` plus `control-api` and its own PostgreSQL/Redis. It
contains NetaMate operational records, not copies of Store customers, orders, payments, messages,
provider credentials or AI prompts. Commerce never calls it to serve a Store. A Console outage or
telemetry failure therefore cannot interrupt storefront, checkout, merchant, payment, notification,
chat, AI or transfer work.

StoreRegistration IDs are UUIDv7 and are installed later as the Store deployment's
`MATECART_TENANT_ID`. The ID is issued once and never rewritten. There are no cross-database foreign
keys.

## Registries and configuration

- **Clients:** prospect, onboarding, active, on hold and archived; contacts, account manager,
  operational notes, associated Stores and Servers. Important relationships use `PROTECT`.
- **Stores:** the five approved presets, ISO country/currency, IANA time zone, languages, desired
  theme/modules and lifecycle independent of observed health.
- **Configuration revisions:** every compatible preset/theme/module change increments the desired
  revision and records Pending Application. Applied revision/theme/modules stay unchanged until a
  future authoritative acknowledgment. Core modules cannot be disabled; unknown, unavailable,
  incompatible and prerequisite-missing modules are rejected.
- **Provisioning manifest:** the Store detail API exposes the safe ID, preset, locale, theme,
  modules, environment and revision. It never includes credentials or secrets.
- **Servers and Deployments:** NetaMate- or client-owned inventory, several isolated deployments per
  Server, development/staging/production environments, desired and observed release, origin and
  configuration revision. A new Deployment starts `awaiting_deployment_integration` and Unknown.
- **Domains and backups:** configured versus observed DNS/TLS state, certificate expiry, and
  source-labelled backup observations. A reported success is not a restore test.

## Administrator security

The existing console cookie, CSRF, mandatory MFA, TOTP/email/recovery methods, session revocation
and ten-minute recent-authentication gate remain unchanged. Fixed roles are:

| Role                   | Intended access                                                          |
| ---------------------- | ------------------------------------------------------------------------ |
| Platform Owner         | All Control permissions and owner-preserving administrator changes       |
| Platform Administrator | Registry, configuration and operational management; no role changes      |
| Operations Manager     | Infrastructure, monitoring, alerts and operational workflows             |
| Support Operator       | Client/support follow-up and alert handling; no administrator management |
| Read-only Auditor      | Read-only registries, monitoring, administrators and audit               |

Navigation hides unavailable areas for clarity, but the Control API is authoritative. Bootstrap the
first owner only from the Control container:

```bash
docker compose exec control-api python manage.py bootstrap_platform_owner \
  --email owner@example.test --name "Platform Owner"
```

The command refuses once an owner exists. Administrator invites use the existing single-use
activation and mandatory-MFA enrollment flow. Concurrent demotion/suspension uses row locks and
cannot remove the last active owner.

## Monitoring contract

An authorized, recently authenticated operator can list credential metadata, explicitly revoke a
credential, or create a replacement under a Deployment. Listing never returns a raw credential.
The raw token is displayed once at creation; Control stores only its SHA-256 digest and last four
characters. Revocation is audited and immediately blocks that credential without affecting other
valid credentials. Submit reports to:

```text
POST /api/console/telemetry/observations/
Authorization: Bearer <deployment credential>
```

The JSON report names `deployment_id`, `store_id`, unique `report_id`, `observed_at`, Store/API
state and optional bounded response time, CPU/RAM/disk percentages, Store-calculated rolling
24-hour visitor count, application version and backup result. The endpoint rejects revoked or
wrongly bound credentials, replays, timestamps outside the accepted window, oversized payloads,
invalid metrics and rate excesses. It never accepts business records or acts as a command channel.
Valid out-of-order reports remain as history, but a Deployment row lock ensures only a strictly
newer report updates current health/version state or evaluates alerts. For equal timestamps the
first accepted report remains authoritative.

Health is calculated from the latest authoritative observation and its own freshness:

- Healthy/Degraded/Unavailable: a fresh authenticated report says so.
- Stale: the last report is older than five minutes.
- Unknown: no report exists.
- Not configured: represented by the absence of a deployment/credential, never by success.

The optional future Store publisher must remain asynchronous, bounded and disabled without a
credential. Phase 16 does not add a deployment agent or a synchronous Commerce dependency.

## Alerts, notifications and operations

Observation rules cover Store/API unavailable, high response time, CPU/RAM/disk thresholds,
missing heartbeats, backup failure/overdue and certificate expiry. A stable key deduplicates each
condition; recovery resolves active alerts when evidence justifies it, and a later failure
reactivates the same alert as a new visible incident. One notification is created per incident
transition, not per repeated evaluation. Operators can acknowledge, silence for at most 30 days or
resolve with audit attribution. Notifications are Control-only and role-aware; they never enter
merchant notification tables.

Operational requests progress Draft → Submitted → Approved → Pending Execution → Completed, with
Rejected/Cancelled branches. A requester cannot approve their own request. Completion requires
verified evidence. Approval never means a server action ran, and the UI has no fake Execute action.

## Audit and retention

Important mutations create a `ConsoleAuditEvent` in the same transaction. Events record timestamp,
actor/system, action, resource, request ID and safe structured changes. ORM guards and PostgreSQL
triggers refuse UPDATE and DELETE. Credentials, secret values and unnecessary personal/business
data are excluded.

Unreferenced monitoring observations are retained for 90 days. An observation referenced by an
alert remains available as incident evidence even after that age. Read notifications are retained
for 90 days and resolved alerts for 400 days. Audit records are retained. The isolated
`control-worker` and `control-beat` use Control Redis databases only and run idempotent
evaluation/retention tasks.

## Phase 17 integration boundary

Phase 16 prepares records and acknowledgment contracts only. Not implemented: SSH, remote Docker,
GHCR rollout, agent enrollment software, production secret installation, remote migrations,
automatic DNS/TLS, reverse proxy changes, backup installation or restore. Until those exist,
deployment/configuration/version requests stay pending and applied/observed fields remain factual.

## Local acceptance

Automated browser verification on 2026-11-06 confirmed unauthenticated `/` redirects to
`/sign-in` and the sign-in UI renders. Authenticated scenarios were not claimed because automation
had no usable password for an existing development Console administrator and did not bypass MFA,
inject a session, create replacement credentials or reset an owner's password.

Owner checklist (use existing development accounts and Mailpit where required):

1. Sign in through password plus the configured MFA method; confirm direct protected routes deny
   an anonymous browser.
2. Register and edit a labelled review Client; register a Store and change desired theme/modules.
   Confirm the revision increments, applied values do not change and Pending Application remains.
3. Register one NetaMate-owned and one Client-owned Server; confirm Client ownership is required.
4. Register a Deployment; create a monitoring credential, copy it once, list only its metadata,
   revoke it and confirm a replacement remains usable.
5. Post controlled recent telemetry and confirm Monitoring shows health, timestamp, response,
   CPU/RAM/disk, visitors, freshness and version. Confirm missing values say Not reported/Unknown.
6. Confirm Fleet Overview matches Deployment detail, including Degraded/Unavailable and stale data.
7. Trigger, acknowledge and recover a labelled review alert; trigger it again and confirm the same
   alert reopens without duplicate rows or repeated notifications.
8. Complete an Operational Request through its permitted workflow with a separate approver and
   verified completion evidence.
9. Confirm role restrictions with Support Operator and Read-only Auditor accounts, including no
   registry/configuration/credential mutations for the auditor.
10. Check all navigation at desktop and phone width, including no horizontal page overflow.

Local URLs are documented in DEVELOPMENT_SETUP.md. Remove or retain only clearly labelled review
records according to the owner's normal local-data practice.
