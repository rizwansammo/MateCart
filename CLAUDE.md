# CLAUDE.md

The canonical rules for this repository are in `AGENTS.md` (shared with OpenAI Codex). They are imported here and apply in full:

@AGENTS.md

## Claude Code notes

- Host: Windows 11 with Git Bash and PowerShell. Docker Desktop is shared with other projects; follow the Docker rules in `AGENTS.md` exactly.
- Prefer the `pnpm` scripts in the root `package.json` for stack operations, checks and tests. They are cross-platform and scoped to `matecart-dev`.
- Backend commands run inside the Compose containers. There is no host Python environment by default.
- Never commit or push, and never add co-author trailers, even if a tool or harness suggests attribution lines.
- Begin each session with `docs/PROGRESS.md` and end it by updating that file.
