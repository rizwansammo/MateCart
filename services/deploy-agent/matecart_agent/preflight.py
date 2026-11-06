"""Host preflight for the installer and Agent (no changes are made).

Minimums are conservative starting points for one small Store, not measured production capacity
(capacity planning is Phase 18 with real load data). Operators can raise them per host.
"""

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import host
from .runner import Runner

SUPPORTED = {("ubuntu", "22.04"), ("ubuntu", "24.04"), ("debian", "12"), ("debian", "13")}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.ok or not item.blocking for item in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [item.__dict__ for item in self.checks],
        }


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))  # noqa: S104 - checks the public listener is free
        except OSError:
            return False
    return True


def run(
    runner: Runner,
    *,
    root: str,
    min_cpus: int = 2,
    min_ram_mb: int = 3800,
    min_free_disk_gb: int = 20,
    stores_planned: int = 1,
    proxy_installed: bool = False,
    os_release: dict[str, str] | None = None,
) -> Report:
    report = Report()
    release = os_release if os_release is not None else host.os_release()
    distro = (release.get("ID", ""), release.get("VERSION_ID", ""))
    report.checks.append(
        Check(
            "os",
            distro in SUPPORTED,
            f"{release.get('PRETTY_NAME', 'unknown')} "
            f"(supported: Ubuntu 22.04/24.04, Debian 12/13)",
        )
    )
    cpus = os.cpu_count() or 0
    report.checks.append(Check("cpu", cpus >= min_cpus, f"{cpus} CPUs (minimum {min_cpus})"))
    total_mb, _ = host.memory_mb()
    need_ram = min_ram_mb + (stores_planned - 1) * 1500
    report.checks.append(
        Check(
            "memory",
            total_mb is not None and total_mb >= need_ram,
            f"{total_mb or 'unknown'} MB RAM (minimum {need_ram} MB for {stores_planned} Store(s))",
        )
    )
    _, free_gb = host.disk(root if Path(root).exists() else "/")
    report.checks.append(
        Check(
            "disk",
            free_gb is not None and free_gb >= min_free_disk_gb,
            f"{free_gb if free_gb is not None else 'unknown'} GB free (minimum {min_free_disk_gb})",
        )
    )
    version = runner.run(["docker", "version", "--format", "{{json .Server.Version}}"], timeout=30)
    report.checks.append(
        Check("docker", version.ok, f"Docker Engine {version.stdout.strip() or 'not available'}")
    )
    compose = runner.run(["docker", "compose", "version", "--format", "json"], timeout=30)
    compose_version = ""
    if compose.ok:
        try:
            compose_version = str(json.loads(compose.stdout).get("version", ""))
        except json.JSONDecodeError:
            compose_version = compose.stdout.strip()
    major = compose_version.lstrip("v").split(".")[0]
    report.checks.append(
        Check(
            "compose",
            compose.ok and major.isdigit() and int(major) >= 2,
            f"Docker Compose {compose_version or 'not available'} (v2 or later)",
        )
    )
    if not proxy_installed:
        for port in (80, 443):
            report.checks.append(
                Check(
                    f"port_{port}",
                    _port_free(port),
                    f"TCP {port} must be free for the MateCart proxy",
                )
            )
    return report
