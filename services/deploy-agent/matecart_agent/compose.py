"""Allowlisted Docker Compose invocations for one Store project (D-119).

Every argv is assembled here from validated values. There is no generic "run this" method: the
public methods are the complete list of Docker operations the Agent can perform.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import validation
from .runner import Completed, Runner

LOGICAL_SERVICES = ("db", "redis", "migrate", "api", "worker", "beat", "web")
APP_SERVICES = ("api", "worker", "beat", "web")
DATA_SERVICES = ("db", "redis")
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


@dataclass(frozen=True)
class ComposeProject:
    """``services`` maps logical names to real Compose service names (identity in production;
    prefixed acc-* services inside the matecart-dev project for local acceptance)."""

    project: str
    directory: Path
    files: tuple[Path, ...]
    env_files: tuple[Path, ...] = ()
    profiles: tuple[str, ...] = ()
    services: Mapping[str, str] = field(default_factory=lambda: {s: s for s in LOGICAL_SERVICES})
    interpolation: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not PROJECT_NAME.fullmatch(self.project):
            raise validation.ValidationError("Invalid Compose project name.")
        missing = set(LOGICAL_SERVICES) - set(self.services)
        if missing:
            raise validation.ValidationError(f"Service map is missing {sorted(missing)}.")

    def _base(self) -> list[str]:
        argv = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--project-directory",
            str(self.directory),
        ]
        for item in self.files:
            argv += ["--file", str(item)]
        for item in self.env_files:
            argv += ["--env-file", str(item)]
        for profile in self.profiles:
            argv += ["--profile", profile]
        return argv

    def _names(self, logical: Sequence[str]) -> list[str]:
        return [self.services[name] for name in logical]

    def _run(self, runner: Runner, argv: list[str], **kwargs: Any) -> Completed:
        env = dict(self.interpolation) if self.interpolation else None
        return runner.run(argv, env=env, **kwargs)

    def pull(self, runner: Runner, logical: Sequence[str]) -> Completed:
        return self._run(runner, [*self._base(), "pull", "--quiet", *self._names(logical)])

    def up(self, runner: Runner, logical: Sequence[str], *, recreate: bool = False) -> Completed:
        argv = [*self._base(), "up", "--detach", "--wait", "--wait-timeout", "300"]
        if recreate:
            argv.append("--force-recreate")
        return self._run(runner, [*argv, *self._names(logical)], timeout=420)

    def migrate(self, runner: Runner) -> Completed:
        """The one-shot migrate job (migrations, runtime grants, idempotent provisioning)."""
        return self._run(
            runner,
            [*self._base(), "run", "--rm", "--no-deps", self.services["migrate"]],
            timeout=1200,
        )

    def ps(self, runner: Runner) -> list[dict[str, Any]]:
        result = self._run(runner, [*self._base(), "ps", "--all", "--format", "json"], timeout=60)
        if not result.ok:
            return []
        rows: list[dict[str, Any]] = []
        text = result.stdout.strip()
        if text.startswith("["):
            parsed = json.loads(text)
            rows = [item for item in parsed if isinstance(item, dict)]
        else:
            for line in text.splitlines():
                if line.strip():
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
        reverse = {real: logical for logical, real in self.services.items()}
        for row in rows:
            row["Logical"] = reverse.get(str(row.get("Service", "")), "")
        return rows

    def readiness(self, runner: Runner) -> Completed:
        return self._run(
            runner,
            [
                *self._base(),
                "exec",
                "-T",
                self.services["api"],
                "python",
                "manage.py",
                "check_deployment_ready",
            ],
            timeout=120,
        )

    def apply_configuration(
        self, runner: Runner, *, revision: int, preset: str, theme: str, modules: Sequence[str]
    ) -> Completed:
        return self._run(
            runner,
            [
                *self._base(),
                "exec",
                "-T",
                self.services["api"],
                "python",
                "manage.py",
                "apply_platform_configuration",
                "--revision",
                str(revision),
                "--preset",
                preset,
                "--theme",
                theme,
                "--modules",
                ",".join(modules),
            ],
            timeout=300,
        )

    def dump_database(self, runner: Runner, target: Path) -> Completed:
        # pg_dump runs inside the Store's own db container as the schema owner; the custom format
        # is streamed to a 0600 file on the host. Credentials come from that container's env.
        return self._run(
            runner,
            [
                *self._base(),
                "exec",
                "-T",
                self.services["db"],
                "sh",
                "-c",
                'exec pg_dump --format=custom --no-owner --username "$MATECART_DB_OWNER" '
                '"$POSTGRES_DB"',
            ],
            timeout=3600,
            stdout_path=str(target),
        )

    def archive_media(self, runner: Runner, target: Path) -> Completed:
        return self._run(
            runner,
            [
                *self._base(),
                "exec",
                "-T",
                self.services["api"],
                "tar",
                "-C",
                "/var/lib/matecart/media",
                "-cf",
                "-",
                ".",
            ],
            timeout=3600,
            stdout_path=str(target),
        )


@dataclass(frozen=True)
class ProxyProject:
    project: str
    directory: Path
    files: tuple[Path, ...]
    service: str = "proxy"
    profiles: tuple[str, ...] = ()

    def _base(self) -> list[str]:
        argv = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--project-directory",
            str(self.directory),
        ]
        for item in self.files:
            argv += ["--file", str(item)]
        for profile in self.profiles:
            argv += ["--profile", profile]
        return argv

    def up(self, runner: Runner) -> Completed:
        return runner.run(
            [*self._base(), "up", "--detach", "--wait", "--wait-timeout", "120", self.service],
            timeout=300,
        )

    def reload(self, runner: Runner) -> Completed:
        return runner.run(
            [
                *self._base(),
                "exec",
                "-T",
                self.service,
                "caddy",
                "reload",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            timeout=120,
        )
