"""Local Store backups (D-123): database (pg_dump custom format) and media (tar).

Artifacts live in <backup_root>/<handle>/<timestamp>/ (directories 0700, files 0600) with a
manifest of SHA-256 digests. Only integrity metadata is reported to Control; the artifacts and any
destination credentials never leave the Server. Retention deletes only directories this module
created for that Store (named by timestamp, containing its manifest).
"""

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import validation
from .compose import ComposeProject
from .runner import Runner

STAMP = re.compile(r"^\d{8}T\d{12}Z$")


@dataclass(frozen=True)
class BackupResult:
    report_id: str
    status: str
    backup_type: str
    started_at: str
    finished_at: str
    directory: Path | None
    size_bytes: int
    sha256: str
    artifact_name: str
    notes: str = ""

    def report(self, deployment_id: str) -> dict[str, Any]:
        return {
            "deployment_id": deployment_id,
            "report_id": self.report_id,
            "backup_type": self.backup_type,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "artifact_name": self.artifact_name,
            "notes": self.notes,
        }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def store_root(backup_root: Path, handle: str) -> Path:
    validation.handle(handle)
    root = validation.within(backup_root, backup_root / handle)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def run_backup(
    runner: Runner,
    compose: ComposeProject,
    *,
    backup_root: Path,
    handle: str,
    include_media: bool,
    now: datetime | None = None,
) -> BackupResult:
    started = now or datetime.now(UTC)
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    root = store_root(backup_root, handle)
    target = root / stamp
    target.mkdir(mode=0o700)
    backup_type = "full" if include_media else "database"
    artifacts: dict[str, str] = {}
    notes = ""
    status = "successful"
    dump = target / "database.dump"
    result = compose.dump_database(runner, dump)
    if not result.ok or not dump.exists() or dump.stat().st_size == 0:
        status, notes = "failed", "Database dump failed."
    else:
        artifacts["database.dump"] = file_sha256(dump)
    if status == "successful" and include_media:
        media = target / "media.tar"
        result = compose.archive_media(runner, media)
        if not result.ok or not media.exists():
            status, notes = "failed", "Media archive failed."
        else:
            artifacts["media.tar"] = file_sha256(media)
    finished = datetime.now(UTC)
    manifest = {
        "store_handle": handle,
        "created_at": started.isoformat(),
        "status": status,
        "artifacts": artifacts,
        "format": {"database": "pg_dump custom (restore with pg_restore)", "media": "tar"},
    }
    manifest_path = target / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    manifest_path.chmod(0o600)
    size = sum(path.stat().st_size for path in target.iterdir() if path.is_file())
    if status != "successful":
        # Keep nothing that could be mistaken for a usable backup.
        shutil.rmtree(target)
    return BackupResult(
        report_id=str(uuid.uuid4()),
        status=status,
        backup_type=backup_type,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        directory=target if status == "successful" else None,
        size_bytes=size if status == "successful" else 0,
        sha256=file_sha256(manifest_path) if status == "successful" else "",
        artifact_name=f"{handle}-{stamp}" if status == "successful" else "",
        notes=notes,
    )


def verify(directory: Path) -> bool:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    return all(
        file_sha256(directory / name) == digest for name, digest in manifest["artifacts"].items()
    )


def prune(backup_root: Path, handle: str, retention_days: int, now: datetime | None = None) -> int:
    """Delete this Store's successful backups past retention; the newest is always kept."""
    if not 1 <= retention_days <= 365:
        raise validation.ValidationError("Retention must be 1-365 days.")
    root = store_root(backup_root, handle)
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and STAMP.fullmatch(path.name) and (path / "manifest.json").exists()
    )
    removed = 0
    for path in candidates[:-1]:
        created = datetime.strptime(path.name, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
        if created < cutoff:
            shutil.rmtree(validation.within(root, path))
            removed += 1
    return removed


def latest(backup_root: Path, handle: str) -> Path | None:
    root = store_root(backup_root, handle)
    found = sorted(path for path in root.iterdir() if path.is_dir() and STAMP.fullmatch(path.name))
    return found[-1] if found else None
