import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from matecart_agent import manifest, render
from matecart_agent.config import AgentConfig
from matecart_agent.runner import Completed, FakeRunner

SERVER_ID = str(uuid.UUID(int=1))
STORE_ID = str(uuid.UUID(int=2))
DEPLOYMENT_ID = str(uuid.UUID(int=3))
OWNER = "rizwansammo"


def release(
    version: str, fingerprint: str = "1" * 64, compatible: list[str] | None = None
) -> dict[str, Any]:
    data = {
        "schema": "matecart.release/v1",
        "version": version,
        "git_commit": "a" * 40,
        "created_at": "2026-11-06T00:00:00Z",
        "images": {
            name: {
                "repository": f"ghcr.io/{OWNER}/matecart-{name}",
                "digest": "sha256:" + hashlib.sha256(f"{name}{version}".encode()).hexdigest(),
                "tag": version,
            }
            for name in manifest.IMAGES
        },
        "database": {
            "commerce": {
                "migration_fingerprint": fingerprint,
                "rollback_compatible_with": compatible or [],
            },
            "control": {"migration_fingerprint": "2" * 64, "rollback_compatible_with": []},
        },
    }
    return manifest.validate(data)


def payload(kind: str, target: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "command_id": str(uuid.uuid4()),
        "kind": kind,
        "deployment_id": DEPLOYMENT_ID,
        "server_id": SERVER_ID,
        "store_id": STORE_ID,
        "store_handle": "river-shop",
        "environment": "production",
        "issued_at": "2026-11-06T00:00:00+00:00",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    if target is not None:
        data.update(
            {
                "release": target,
                "release_sha256": manifest.digest_of(target),
                "store": {
                    "store_id": STORE_ID,
                    "handle": "river-shop",
                    "name": "River Shop",
                    "preset": "general",
                    "country": "BD",
                    "currency": "BDT",
                    "time_zone": "Asia/Dhaka",
                    "language": "en",
                    "languages": ["en", "bn"],
                },
                "domains": [],
            }
        )
    data.update(extra)
    return data


@dataclass
class FakeDocker:
    """Simulates the Compose project of one Store: which images run and whether they are healthy."""

    config: AgentConfig
    running: dict[str, str] = field(default_factory=dict)
    fail: set[str] = field(default_factory=set)  # "pull", "migrate", "up_app", "health", "dump"
    unhealthy_images: set[str] = field(default_factory=set)
    log: list[str] = field(default_factory=list)

    def env(self) -> dict[str, str]:
        return render.read_env(self.config.store_dir("river-shop") / "deploy.env")

    def __call__(self, argv: tuple[str, ...]) -> Completed | None:
        if argv[:2] != ("docker", "compose"):
            return Completed(argv, 0, "27.0.0", "")
        action = next(item for item in argv if item in ACTIONS)
        self.log.append(action)
        env = self.env()
        if action == "pull":
            return Completed(argv, 1 if "pull" in self.fail else 0, "", "")
        if action == "run":
            return Completed(argv, 1 if "migrate" in self.fail else 0, "", "")
        if action == "up":
            tail = argv[argv.index("up") + 1 :]
            services = tuple(x for x in tail if not x.startswith("-") and not x.isdigit())
            for service in services:
                image = env.get(
                    "MATECART_COMMERCE_WEB_IMAGE"
                    if service == "web"
                    else "MATECART_COMMERCE_API_IMAGE",
                    "",
                )
                self.running[service] = image if service not in {"db", "redis"} else service
            app_failed = "up_app" in self.fail and "api" in services
            bad = (
                env.get("MATECART_COMMERCE_API_IMAGE") in self.unhealthy_images
                and "api" in services
            )
            return Completed(argv, 1 if app_failed or bad else 0, "", "")
        if action == "ps":
            rows = [
                {
                    "Service": name,
                    "State": "running",
                    "Health": "unhealthy"
                    if name == "api" and image in self.unhealthy_images
                    else "healthy",
                    "Image": image,
                }
                for name, image in self.running.items()
            ]
            return Completed(argv, 0, json.dumps(rows), "")
        if action == "exec":
            if "check_deployment_ready" in argv:
                bad = self.running.get("api") in self.unhealthy_images or "health" in self.fail
                return Completed(argv, 1 if bad else 0, "", "")
            if "pg_dump" in " ".join(argv) and "dump" in self.fail:
                return Completed(argv, 1, "", "pg_dump: error password=hunter2")
            return Completed(argv, 0, "", "")
        return None


ACTIONS = ("pull", "run", "up", "ps", "exec")


@pytest.fixture
def config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        control_url="https://control.example.test",
        root=str(tmp_path / "opt"),
        state_dir=str(tmp_path / "state"),
        backup_root=str(tmp_path / "backups"),
        server_id=SERVER_ID,
        key_id="k" * 24,
        secret="s" * 64,
        min_free_disk_gb=0,
    )


@pytest.fixture
def docker(config: AgentConfig) -> FakeDocker:
    return FakeDocker(config)


@pytest.fixture
def runner(docker: FakeDocker) -> FakeRunner:
    return FakeRunner(responder=docker)


Progress = Callable[[str, str], None]
