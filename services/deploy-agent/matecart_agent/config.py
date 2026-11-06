"""Agent configuration and the on-host layout.

/etc/matecart/agent.json        Agent identity and Control URL (0600, root:matecart-agent)
/opt/matecart/stores/<handle>/  one Store: compose.yaml, env files, release.json, store.json
/opt/matecart/proxy/            the host's shared Caddy proxy (compose.yaml, Caddyfile, sites/)
/var/lib/matecart/agent/        journal (commands, outbox)
/var/lib/matecart/backups/      local backups per Store handle (0700)
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import validation
from .compose import LOGICAL_SERVICES, ComposeProject, ProxyProject

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass
class AgentConfig:
    control_url: str
    root: str = "/opt/matecart"
    state_dir: str = "/var/lib/matecart/agent"
    backup_root: str = "/var/lib/matecart/backups"
    registry_owner: str = ""
    agent_id: str = ""
    server_id: str = ""
    key_id: str = ""
    secret: str = ""
    pending_secret: str = ""
    allow_insecure_http: bool = False
    min_free_disk_gb: int = 5
    proxy_https_port: int = 443
    acme_email: str = ""
    # Local acceptance only (D-124): run Stores as acc-* services inside the matecart-dev
    # Compose project with locally built images. Refused unless Control is on loopback.
    acceptance: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> "AgentConfig":
        parsed = urlparse(self.control_url)
        if parsed.scheme != "https":
            insecure_ok = (
                parsed.scheme == "http"
                and self.allow_insecure_http
                and (parsed.hostname or "") in LOOPBACK_HOSTS
            )
            if not insecure_ok:
                raise validation.ValidationError(
                    "Control must be reached over https:// (plain http only on loopback for "
                    "local acceptance)."
                )
        if self.acceptance and (parsed.hostname or "") not in LOOPBACK_HOSTS:
            raise validation.ValidationError("Acceptance mode requires a loopback Control URL.")
        return self

    @property
    def stores_dir(self) -> Path:
        return Path(self.root) / "stores"

    @property
    def proxy_dir(self) -> Path:
        return Path(self.root) / "proxy"

    def store_dir(self, handle: str) -> Path:
        validation.handle(handle)
        return validation.within(self.stores_dir, self.stores_dir / handle)

    def compose_for(
        self, handle: str, interpolation: dict[str, str] | None = None
    ) -> ComposeProject:
        directory = self.store_dir(handle)
        if self.acceptance:
            stores = self.acceptance.get("stores", {})
            entry = stores.get(handle)
            if not isinstance(entry, dict):
                raise validation.ValidationError(f"Store {handle} is not an acceptance store.")
            prefix = str(entry["variable_prefix"])
            env = {f"{prefix}{key}": value for key, value in (interpolation or {}).items()}
            return ComposeProject(
                project=str(self.acceptance["project"]),
                directory=Path(self.acceptance["project_directory"]),
                files=(Path(self.acceptance["compose_file"]),),
                profiles=tuple(self.acceptance.get("profiles", [])),
                services={name: str(entry["services"][name]) for name in LOGICAL_SERVICES},
                interpolation=env,
            )
        return ComposeProject(
            project=f"matecart-store-{handle}",
            directory=directory,
            files=(directory / "compose.yaml",),
            env_files=(directory / "deploy.env",),
        )

    def proxy(self) -> ProxyProject:
        if self.acceptance:
            return ProxyProject(
                project=str(self.acceptance["project"]),
                directory=Path(self.acceptance["project_directory"]),
                files=(Path(self.acceptance["compose_file"]),),
                service=str(self.acceptance["proxy_service"]),
                profiles=tuple(self.acceptance.get("profiles", [])),
            )
        return ProxyProject(
            project="matecart-proxy",
            directory=self.proxy_dir,
            files=(self.proxy_dir / "compose.yaml",),
        )


def load(path: Path) -> AgentConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {key: value for key, value in data.items() if key in AgentConfig.__dataclass_fields__}
    return AgentConfig(**known).validate()


def write_private(path: Path, content: str) -> None:
    """Atomically replace ``path`` with a 0600 file (never a partially written secret)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def save(config: AgentConfig, path: Path) -> None:
    write_private(path, json.dumps(asdict(config), indent=2, sort_keys=True) + "\n")
