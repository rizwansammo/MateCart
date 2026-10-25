# MateCart

White-label e-commerce platform by **NetaMate Solutions**. Each client gets an independently deployed, branded online store with a storefront and a merchant dashboard. NetaMate operates the fleet through the internal Central Platform Console.

> **Status:** Phase 01 accepted. Phase 02 (Database & Tenant Architecture) is complete locally and awaiting owner review. See [docs/PROGRESS.md](docs/PROGRESS.md).

## Repository

| Path                        | What it is                                                    |
| --------------------------- | ------------------------------------------------------------- |
| `apps/commerce-web`         | Next.js: customer storefront and merchant dashboard           |
| `apps/platform-console`     | Next.js: NetaMate's internal Central Platform Console         |
| `services/commerce-api`     | Django + DRF + Celery: commerce API for one store deployment  |
| `services/control-api`      | Django + DRF: Central Console backend                         |
| `packages/shared-ui`        | Design tokens (Tailwind v4 theme) and shared React components |
| `packages/shared-contracts` | Typed API contracts with runtime guards                       |
| `scripts/`                  | Cross-platform setup and health scripts                       |
| `compose.yaml`              | Isolated local Docker stack (`matecart-dev`)                  |
| `docs/`                     | Project documentation                                         |

Stack: Next.js 16 · React 19 · TypeScript 6 (strict) · Tailwind CSS 4 · Django 5.2 LTS · DRF · PostgreSQL 18 · Redis 8 · Celery 5 · Docker Compose · GitHub Actions. Reasoning: [docs/DECISIONS.md](docs/DECISIONS.md).

## Quick start

Requires Node.js 24, pnpm 11 and Docker Desktop.

```bash
pnpm install
pnpm setup:env      # local .env files with generated secrets (never committed)
pnpm stack:up       # PostgreSQL, Redis, commerce-api, commerce-worker, control-api
pnpm api:provision  # create the local dev store (one tenant per deployment)
pnpm dev            # commerce-web on :3400, platform-console on :3410
pnpm health         # verify every service
```

Everyday commands, ports and troubleshooting: [docs/DEVELOPMENT_SETUP.md](docs/DEVELOPMENT_SETUP.md).

## Documentation

| Document                                                   | Purpose                                                                   |
| ---------------------------------------------------------- | ------------------------------------------------------------------------- |
| [AGENTS.md](AGENTS.md)                                     | **Canonical rules** for developers and AI assistants (Claude Code, Codex) |
| [docs/PROGRESS.md](docs/PROGRESS.md)                       | Current phase, verification results, handoff                              |
| [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)         | Product vision, presets, business model                                   |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)               | System design, tenancy model, conventions                                 |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md)                   | Database conventions, implemented schema, domain blueprint                |
| [docs/DEVELOPMENT_ROADMAP.md](docs/DEVELOPMENT_ROADMAP.md) | The 19 phases and their status                                            |
| [docs/FEATURE_CHECKLIST.md](docs/FEATURE_CHECKLIST.md)     | Long-term feature scope and status                                        |
| [docs/DECISIONS.md](docs/DECISIONS.md)                     | Decision log and open questions                                           |
| [docs/DEVELOPMENT_SETUP.md](docs/DEVELOPMENT_SETUP.md)     | Local environment and commands                                            |

## Ground rules

- The UI prototype ([MateCart-UI](https://github.com/rizwansammo/MateCart-UI)) is the approved design reference and is **strictly read-only**.
- The local Docker Desktop is shared with other projects. MateCart only ever touches its own `matecart-dev` Compose project.
- No secrets in Git. Real credentials are never needed for local development.
