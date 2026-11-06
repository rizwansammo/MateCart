# Deployment automation

Phase 17. This document covers production images, releases, Store deployments, the installer, the
Deployment Agent, Console integration, the proxy and TLS, updates, backups, monitoring and local
acceptance. Decisions: D-116 to D-125. **No real VPS, SSH connection, DNS change, public
certificate or GHCR publication was made in Phase 17.** Verification levels are listed in
[Verification status](#verification-status).

## 1. Production images

| Image (`ghcr.io/<owner>/…`) | Source                                        | Roles / process                                                              |
| --------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------- |
| `matecart-commerce-api`     | `services/commerce-api/Dockerfile`, `prod`    | `api` (uvicorn ASGI, HTTP + WebSocket), `worker`, `beat`, `migrate`, `check` |
| `matecart-control-api`      | `services/control-api/Dockerfile`, `prod`     | `api` (gunicorn WSGI), `worker`, `beat`, `migrate`, `check`                  |
| `matecart-commerce-web`     | `apps/web.Dockerfile`, `APP=commerce-web`     | Next.js standalone `server.js` on port 3000                                  |
| `matecart-platform-console` | `apps/web.Dockerfile`, `APP=platform-console` | Next.js standalone `server.js` on port 3000                                  |

- Python images: locked runtime dependencies only (`uv sync --locked --no-dev`), source owned by
  root, runtime UID 10001, tests removed, `DJANGO_SETTINGS_MODULE=config.settings.production`.
  The entrypoint accepts only the listed roles (anything else exits 64).
- Next.js images: `MATECART_NEXT_OUTPUT=standalone` is set only inside the image build, so local
  `pnpm build` is unchanged. Runtime URLs (`COMMERCE_API_INTERNAL_URL`, `STOREFRONT_PUBLIC_URL`,
  `CONTROL_API_INTERNAL_URL`) are read at request time; nothing is baked into `NEXT_PUBLIC_*`.
- No `.env` file, key or credential is copied into any image (`.dockerignore`, and checked by
  `release.yml` and the acceptance run). Images carry OCI version/revision labels.
- Build locally (not published): `pnpm images:build`.

## 2. Releases and GHCR (`.github/workflows/release.yml`)

Owner-triggered only (Actions → Release images → Run workflow). It never runs on push or pull
request, never creates tags or GitHub Releases and never tags `latest`.

1. Inputs: `version` (MAJOR.MINOR.PATCH, immutable), `publish` (default **false**),
   `rollback_compatible_with` (optional older versions whose code tolerates this schema).
2. `build` job (contents: read): builds all four images, checks they run as non-root and contain no
   secret-like files. Nothing is pushed.
3. `publish` job (only with `publish: true`): runs in the `ghcr-release` environment. **Before the
   first publication the owner creates that environment with required reviewers.** It refuses an
   existing version, pushes version tags with the job token (`packages: write` only in this job),
   and produces `release.json`, `release.sha256.txt` and `digests.json` as a workflow artifact.
4. Register the release in the Console (Deployments → Releases → paste `release.json`). Control
   validates it again and stores its SHA-256.

`release.json` (`matecart.release/v1`) lists the four images by repository and **sha256 digest**,
the commit, and migration fingerprints for Commerce and Control. Deployments use digests only.

## 3. Dedicated Store deployment

Template: `services/deploy-agent/templates/store-compose.yaml`, rendered by the Agent to
`/opt/matecart/stores/<handle>/`:

| File                  | Contents                                                                                | Mode |
| --------------------- | --------------------------------------------------------------------------------------- | ---- |
| `compose.yaml`        | the template                                                                            | 0600 |
| `deploy.env`          | project name, edge alias, two image references (no secrets)                             | 0600 |
| `app.env`             | Django settings, generated secrets, runtime DB/Redis URLs, tenant, provider credentials | 0600 |
| `migrate.env`         | schema-owner `DATABASE_URL` (migrate job only)                                          | 0600 |
| `db.env`, `redis.env` | PostgreSQL bootstrap and role passwords, Redis password                                 | 0600 |
| `web.env`             | storefront runtime URLs (no secrets)                                                    | 0600 |
| `store.json`          | Store ID, handle, project (prevents overwriting another Store)                          | 0600 |
| `release.json`        | the running release manifest (and `release.previous.json`)                              | 0600 |

Services: `db` (PostgreSQL 18), `redis` (Redis 8), `migrate` (one-shot, `jobs` profile), `api`,
`worker`, `beat`, `web`. Networks per Store: `data` (internal: db/redis/app), `egress` (provider
access) and `edge` (internal; shared only with the host proxy). Volumes per Store: database, Redis,
media. Every name is prefixed `matecart-store-<handle>`.

- **Tenant:** `MATECART_TENANT_ID` is the Console Store Registration ID from the command payload.
  The migrate job provisions it with `provision_tenant --if-missing`; readiness fails (and the
  deployment is not declared healthy) if the database holds another tenant.
- **Startup integrity:** the migrate job runs `check`, `migrate`, `apply_runtime_grants`,
  `provision_tenant` and `check_deployment_ready`; app services start only after it succeeds and are
  healthy only when `/api/health/ready/` passes (database, cache, broker, tenant).
- **Production settings** refuse the AI test provider, migration and channel simulators, test
  providers, insecure cookies and non-https public URLs. Mailpit is not part of production; a real
  `EMAIL_URL` must be added to `app.env` before go-live (kept across re-renders).
- **Database roles (D-118):** bootstrap superuser (init only), `matecart_owner` (schema owner,
  migrations and backups), `matecart_app` (runtime, data only, no DDL).

## 4. Installer and host bootstrap

Supported hosts: Ubuntu 22.04/24.04 or Debian 12/13 with Docker Engine and the Compose v2 plugin
installed from Docker's repository, systemd and Python 3.11+. Preflight minimums (2 CPUs, about
3.8 GB RAM plus 1.5 GB per extra Store, 20 GB free disk, free ports 80/443) are conservative
starting points, **not measured production capacity**; capacity planning is Phase 18 work.

| Command                                                                            | Changes the host?                                                                                                                        |
| ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `sudo installer/bootstrap.sh preflight`                                            | No                                                                                                                                       |
| `sudo installer/bootstrap.sh install --dry-run`                                    | No (prints plan)                                                                                                                         |
| `sudo installer/bootstrap.sh install`                                              | Creates `matecart-agent` user, `/opt/matecart`, `/etc/matecart`, `/var/lib/matecart`, the Agent files and the systemd unit (not started) |
| `python3 -m matecart_agent preflight`                                              | No                                                                                                                                       |
| `python3 -m matecart_agent plan-store --store s.json --release r.json`             | No (secrets masked)                                                                                                                      |
| `python3 -m matecart_agent install-store … --confirm-host $(hostname) [--dry-run]` | Renders one Store directory                                                                                                              |

The bootstrap never installs Docker, opens firewall ports, removes data, touches other containers
or asks for passwords. Re-running is idempotent; a Store directory that belongs to another Store ID
is refused.

**SSH path (D-125, not executed in Phase 17):** `python3 -m matecart_agent ssh-plan --host H
--user U --host-key-file key.pub --fingerprint SHA256:… --identity id_ed25519 --mode preflight`
prints the exact `ssh` invocation (pinned known_hosts, `StrictHostKeyChecking=yes`, `BatchMode`,
`sudo -n /bin/bash -s -- <mode>`). Only `--execute` would connect. An unknown or mismatching host
key is refused; trust-on-first-use is never used.

## 5. Deployment Agent

- Runs as the `matecart-agent` systemd service (`installer/matecart-agent.service`, hardened unit).
  It needs the Docker socket through the docker group, which is root-equivalent on that host; the
  account runs nothing but the Agent, which exposes no port and accepts no shell input.
- **Enrollment:** Console → Server → Deployment Agent → Issue enrollment token (single use, one
  hour, recently authenticated `servers.manage`). On the Server:
  `printf '%s\n' "$TOKEN" | sudo -u matecart-agent python3 -m matecart_agent enroll --control-url https://console.example`
  then `systemctl enable --now matecart-agent`. The token is read from stdin, never argv.
- **Protocol** (`/api/agent/v1/`): enroll, heartbeat, rotate, observations, backups, commands/lease,
  commands/<id>/events. Every request is HMAC-SHA256 signed over method, path, timestamp (±5 min),
  a single-use nonce and the body digest; responses are signed back and bound to the nonce.
  Redirects are never followed. Credentials expire after 90 days and rotate automatically.
- **Revocation:** Console → Server → Revoke Agent. Requests fail immediately with 401; queued or
  running commands fail with `agent_revoked`. Re-enrollment needs a new token.
- **Allowlisted operations:** `deploy_release`, `update_release`, `rollback_release`,
  `apply_configuration`, `configure_domains`, `run_backup`. There is no shell, arbitrary Docker,
  SQL or filesystem operation. Process execution is limited to `docker` and `ssh` argv.
- **Console independence:** if Control is unreachable the Stores keep running; the Agent keeps
  observing and running scheduled backups, buffers up to 500 reports, never starts a command it
  could not claim, and flushes the buffer when Control returns. Interrupted commands are reported
  after restart as `interrupted` (with the previous release restored when that is safe).

## 6. Console integration

1. An operator with `operations.manage` and `deployments.manage` submits a request on the
   Deployment page (operation and release).
2. A **different** administrator approves it on Operations (recent authentication).
3. Moving it to **Pending Execution** (recent authentication) validates the Agent, release,
   rollback eligibility and domains and creates one scoped command. Another active command for the
   same Store is refused.
4. The Agent leases, verifies the payload digest, target Server and expiry, claims it, executes,
   streams progress and reports success or a categorized failure.
5. Control moves a success to **Awaiting verification** and marks it **Succeeded** only after a
   later authenticated observation proves it (version + healthy services, configuration revision,
   valid TLS, or a successful backup with digest). Otherwise it becomes Verification failed after
   15 minutes. The request becomes Completed or Failed with evidence; deployment failures raise the
   `deployment_failed` alert. Every step is audited (requester, approver, command, target, release,
   status, failure category, evidence); no secret is recorded.

## 7. Reverse proxy, domains and TLS

- One Caddy proxy per Server (`/opt/matecart/proxy`, ports 80/443). Each Store has a site file
  (`sites/<handle>.caddy`) for its verified domains: `/api/*` → `store-<handle>-api:8000`
  (HTTP and WebSocket), everything else → `store-<handle>-web:3000`, HSTS, 30 MB request limit,
  `X-Forwarded-For/Proto` replaced. Unknown hosts get 421 on HTTP and no certificate on HTTPS.
- **Domain workflow:** add the domain in the Console, create the TXT record
  `_matecart-verify.<domain>` with the shown token and point A/AAAA at the Server, then **Check
  DNS**. Only domains with verified ownership and ready DNS can be configured. Then request
  **Configure verified domains**.
- **TLS:** production uses Caddy's ACME issuance and automatic renewal (set the ACME account
  email in the Agent config). The Agent observes expiry over a local TLS handshake and reports
  renewal state (`ok`, `renewal_due`, `renewal_failed`), which drives the existing certificate
  alerts plus `certificate_renewal_failed`.

## 8. Updates and rollback

Update lifecycle (Agent): validate the manifest and its digest → confirm Server/Store identity →
compatibility → disk space → **pre-update database backup** (mandatory) → pull images by digest
(failure changes nothing) → switch images → migrate job → start and health-check → record the
release → report. On failure:

- if no migration ran, or the new release declares the previous one compatible, the previous
  images are restored automatically and the result is `rolled_back`;
- otherwise automatic rollback is refused and the result names the pre-update backup; recovery is
  an operator-approved restore. **Databases are never reset or restored automatically.**

Manual rollback is offered only to the last verified release and only when the schemas are
compatible (same migration fingerprint or declared `rollback_compatible_with`).

## 9. Backups

Policy per Deployment (Console): enabled, daily time (UTC), retention 1-365 days, include media.
The Agent writes `database.dump` (`pg_dump -Fc`, owner role) and `media.tar` to
`/var/lib/matecart/backups/<handle>/<timestamp>/` with `manifest.json` (SHA-256 per artifact),
prunes only that Store's expired backups (always keeping the newest), and reports status, size and
digest to Control. Failed backups leave no artifact. Only `local` storage exists until the owner
chooses an off-site provider. Restore with `pg_restore` into a new, empty database.

## 10. Monitoring

Agent observations reuse the Phase 16 pipeline (authoritative newest report, freshness, alerts) and
add worker and database status, configuration revision and domain TLS state. Unknown values stay
Not reported: the Agent does not report response time or visitors (no estimates). New alert
categories: `agent_disconnected` (no heartbeat for 5 minutes), `deployment_failed`,
`worker_unavailable`, `database_unavailable`, `certificate_renewal_failed`, using the existing
deduplication, recovery, silence and notification rules.

## 11. Local acceptance

```bash
pnpm deploy:acceptance        # builds local images, runs every check, writes .acceptance/report.json
pnpm deploy:acceptance:down   # removes only matecart-dev-acc-* containers and volumes
```

Requires Python 3.11+ on the host (the runner drives the real Agent code against Docker). Uses
loopback ports 18480/18443 (proxy) and 18410 (disposable Control). See the verification table.

## Verification status

| Area                                                             | Status                                                                                          |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Alert silence lifecycle                                          | Implemented; unit/integration tested (Control, real PostgreSQL)                                 |
| Production images                                                | Built locally and run in disposable containers; no secrets; non-root                            |
| GHCR workflow                                                    | Implemented; **not run**; publishing requires owner approval and the `ghcr-release` environment |
| Store template, roles, provisioning                              | Locally deployed in disposable containers (two Stores)                                          |
| Installer preflight / dry run                                    | Unit tested; bootstrap script **not run on a real Linux host**                                  |
| SSH bootstrap                                                    | Planning and host-key validation unit tested; **no SSH connection made**                        |
| Agent protocol                                                   | Unit tested and exercised over real HTTP against a disposable Control API                       |
| Agent operations (install/update/rollback/backup)                | Unit tested with a simulated runner and run against real local containers                       |
| Console approval → command → verification                        | Integration tested (Control, real PostgreSQL); browser flow is owner acceptance                 |
| Proxy, WebSocket, unknown hosts                                  | Locally verified through Caddy in disposable containers                                         |
| TLS                                                              | **Simulated** with Caddy's internal CA; public ACME issuance needs a real domain                |
| Domain ownership                                                 | Unit tested with a fake resolver; **no real DNS queried or changed**                            |
| Backups and restore                                              | Local backup and a disposable `pg_restore` smoke test; DR drills are Phase 18                   |
| Real VPS, public DNS/TLS, GHCR publication, remote Agent install | **Not done** (Phase 19 / owner-approved)                                                        |

## Future real deployment (owner-approved, Phase 19)

1. Choose the provider, Server size and region; create the Server; record it in the Console with
   its public IP and SSH host-key fingerprint.
2. Create the `ghcr-release` environment with required reviewers; run the release workflow with
   `publish: true`; register `release.json` in the Console.
3. Bootstrap the host (`ssh-plan` → review → `--execute`, or run `bootstrap.sh` on the host),
   enroll the Agent, set the ACME email in `/etc/matecart/agent.json`.
4. Add provider credentials (SMTP, payments, couriers) to the Store's `app.env` on the Server.
5. Register and verify the domain; request Install release, then Configure verified domains;
   approve each request with a second administrator.
6. Confirm verification in the Console, set the backup policy and test a restore (Phase 18 drill).
