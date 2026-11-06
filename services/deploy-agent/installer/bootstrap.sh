#!/bin/bash
# MateCart host bootstrap (Phase 17, D-125). Prepares a Linux VPS for the Deployment Agent.
#
#   sudo ./bootstrap.sh preflight         checks only; changes nothing
#   sudo ./bootstrap.sh install --dry-run prints every change it would make
#   sudo ./bootstrap.sh install           creates the service user, directories, Agent files
#                                         and the systemd unit (Agent stays stopped until enrolled)
#
# Over SSH the script and Agent bundle arrive on stdin (matecart_agent.ssh.bundle); locally, run it
# from an unpacked release next to matecart_agent/. It never installs or upgrades Docker, touches
# other containers, opens firewall ports, collects passwords or removes existing data.
set -euo pipefail
umask 027

MODE="${1:-preflight}"
DRY_RUN=0
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1

AGENT_USER="matecart-agent"
ROOT="/opt/matecart"
ETC="/etc/matecart"
STATE="/var/lib/matecart"
UNIT="/etc/systemd/system/matecart-agent.service"

log() { printf '[matecart-bootstrap] %s\n' "$*"; }
fail() { printf '[matecart-bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }
act() {
  if [ "$DRY_RUN" -eq 1 ]; then log "would run: $*"; else "$@"; fi
}

preflight() {
  local ok=1
  [ "$(id -u)" -eq 0 ] || fail "run with sudo (root is needed to create the service user)"
  . /etc/os-release
  case "${ID}:${VERSION_ID}" in
    ubuntu:22.04|ubuntu:24.04|debian:12|debian:13) log "OS ${PRETTY_NAME}: supported" ;;
    *) log "OS ${PRETTY_NAME}: NOT supported (Ubuntu 22.04/24.04, Debian 12/13)"; ok=0 ;;
  esac
  command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    && log "python3 $(python3 -c 'import platform; print(platform.python_version())'): ok" \
    || { log "python3 >= 3.11 is required"; ok=0; }
  if command -v docker >/dev/null && docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    log "Docker Engine $(docker version --format '{{.Server.Version}}'): ok"
  else
    log "Docker Engine is not installed or not running (install it from Docker's official repository)"; ok=0
  fi
  if docker compose version >/dev/null 2>&1; then
    log "$(docker compose version): ok"
  else
    log "Docker Compose v2 plugin is required"; ok=0
  fi
  command -v systemctl >/dev/null || { log "systemd is required"; ok=0; }
  log "CPUs: $(nproc); RAM: $(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo) MB; free disk on /: $(df -BG --output=avail / | tail -1 | tr -d ' ')"
  [ "$ok" -eq 1 ] || fail "preflight failed; nothing was changed"
  log "preflight passed"
}

extract_bundle() {
  local target="$1"
  if [ -d "$(dirname "$0")/../matecart_agent" ] && [ -t 0 ]; then
    act cp -R "$(dirname "$0")/.." "$target"
    return
  fi
  # Remaining stdin after the marker is the base64 tar produced by matecart_agent.ssh.bundle.
  local payload
  payload="$(mktemp)"
  sed -n '/^__MATECART_AGENT_BUNDLE__$/,$p' | tail -n +2 > "$payload"
  [ -s "$payload" ] || fail "no Agent bundle on stdin"
  act mkdir -p "$target"
  if [ "$DRY_RUN" -eq 1 ]; then log "would unpack the Agent bundle into $target"; else
    base64 -d "$payload" | tar -xz -C "$target" --no-same-owner
  fi
  rm -f "$payload"
}

install() {
  preflight
  local version
  version="$(date -u +%Y%m%dT%H%M%SZ)"
  if ! id "$AGENT_USER" >/dev/null 2>&1; then
    act useradd --system --home-dir "$STATE/agent" --shell /usr/sbin/nologin "$AGENT_USER"
  fi
  # Membership of the docker group is root-equivalent on this host. It is limited to this one
  # service user, which runs only the allowlisted Agent (docs/DEPLOYMENT_AUTOMATION.md).
  act usermod -aG docker "$AGENT_USER"
  act install -d -m 0750 -o root -g "$AGENT_USER" "$ROOT" "$ETC"
  act install -d -m 0750 -o "$AGENT_USER" -g "$AGENT_USER" "$ROOT/stores" "$ROOT/proxy"
  act install -d -m 0700 -o "$AGENT_USER" -g "$AGENT_USER" "$STATE/agent" "$STATE/backups"
  extract_bundle "$ROOT/agent/releases/$version"
  act chown -R root:"$AGENT_USER" "$ROOT/agent"
  act ln -sfn "$ROOT/agent/releases/$version" "$ROOT/agent/current"
  if [ -f "$ROOT/agent/current/installer/matecart-agent.service" ]; then
    act install -m 0644 "$ROOT/agent/current/installer/matecart-agent.service" "$UNIT"
  fi
  act systemctl daemon-reload
  log "installed. Enroll with: sudo -u $AGENT_USER python3 -m matecart_agent enroll ... (token on stdin)"
  log "then: systemctl enable --now matecart-agent"
}

case "$MODE" in
  preflight) preflight ;;
  install) install ;;
  *) fail "unknown mode '$MODE' (preflight, install)" ;;
esac
exit 0
