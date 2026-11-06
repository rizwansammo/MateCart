"""Release manifests (matecart.release/v1, D-121): built in CI, validated on the Agent.

    python -m matecart_agent.manifest build --version 1.2.0 --git-commit <sha> \\
        --digests digests.json --owner <github-owner> --repo-root . > release.json
    python -m matecart_agent.manifest validate release.json
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import validation
from .signing import canonical_json, sha256_hex

SCHEMA = "matecart.release/v1"
IMAGES = ("commerce-api", "commerce-web", "control-api", "platform-console")


def migration_names(app_root: Path) -> list[str]:
    return sorted(
        f"{path.parent.parent.name}.{path.stem}"
        for path in app_root.glob("**/migrations/[0-9][0-9][0-9][0-9]_*.py")
    )


def fingerprint(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def build(
    *,
    version: str,
    git_commit: str,
    owner: str,
    digests: dict[str, str],
    repo_root: Path,
    rollback_compatible_with: list[str],
) -> dict[str, Any]:
    commerce = migration_names(repo_root / "services" / "commerce-api" / "commerce")
    control = migration_names(repo_root / "services" / "control-api" / "control")
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "version": version,
        "git_commit": git_commit,
        "created_at": created,
        "images": {
            name: {
                "repository": f"ghcr.io/{owner.lower()}/matecart-{name}",
                "digest": digests[name],
                "tag": version,
            }
            for name in IMAGES
        },
        "database": {
            "commerce": {
                "migration_fingerprint": fingerprint(commerce),
                "migration_count": len(commerce),
                "rollback_compatible_with": rollback_compatible_with,
            },
            "control": {
                "migration_fingerprint": fingerprint(control),
                "migration_count": len(control),
                "rollback_compatible_with": [],
            },
        },
    }
    return validate(manifest)


def validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise validation.ValidationError("Unsupported release manifest schema.")
    validation.version(data.get("version"))
    commit = str(data.get("git_commit", ""))
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise validation.ValidationError("git_commit must be a full commit SHA.")
    images = data.get("images")
    if not isinstance(images, dict) or set(images) != set(IMAGES):
        raise validation.ValidationError("The manifest must list exactly the four images.")
    for name, entry in images.items():
        if not isinstance(entry, dict):
            raise validation.ValidationError(f"Image {name} is malformed.")
        validation.image_reference(entry.get("repository"), entry.get("digest"))
        if not str(entry["repository"]).endswith(f"/matecart-{name}"):
            raise validation.ValidationError(f"Image {name} points to another repository.")
    database = data.get("database")
    if not isinstance(database, dict):
        raise validation.ValidationError("The manifest must describe database migrations.")
    for service in ("commerce", "control"):
        entry = database.get(service)
        if not isinstance(entry, dict) or not validation.SHA256.fullmatch(
            str(entry.get("migration_fingerprint", ""))
        ):
            raise validation.ValidationError(f"Missing {service} migration fingerprint.")
        for item in entry.get("rollback_compatible_with", []):
            validation.version(item)
    return data


def digest_of(manifest: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(manifest))


def image(manifest: dict[str, Any], name: str) -> str:
    entry = manifest["images"][name]
    return validation.image_reference(entry["repository"], entry["digest"])


def rollback_allowed(current: dict[str, Any], target: dict[str, Any]) -> bool:
    """Same rule as Control: unchanged schema, or explicitly declared compatible (D-121)."""
    current_db = current["database"]["commerce"]
    target_db = target["database"]["commerce"]
    if current_db["migration_fingerprint"] == target_db["migration_fingerprint"]:
        return True
    return str(target["version"]) in current_db.get("rollback_compatible_with", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matecart_agent.manifest")
    sub = parser.add_subparsers(dest="action", required=True)
    build_cmd = sub.add_parser("build")
    build_cmd.add_argument("--version", required=True)
    build_cmd.add_argument("--git-commit", required=True)
    build_cmd.add_argument("--owner", required=True)
    build_cmd.add_argument("--digests", required=True, type=Path)
    build_cmd.add_argument("--repo-root", default=Path(), type=Path)
    build_cmd.add_argument("--rollback-compatible-with", default="")
    check = sub.add_parser("validate")
    check.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "build":
            digests = json.loads(args.digests.read_text(encoding="utf-8"))
            compatible = [item for item in args.rollback_compatible_with.split(",") if item]
            result = build(
                version=args.version,
                git_commit=args.git_commit,
                owner=args.owner,
                digests=digests,
                repo_root=args.repo_root,
                rollback_compatible_with=compatible,
            )
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        else:
            data = validate(json.loads(args.path.read_text(encoding="utf-8")))
            sys.stdout.write(f"valid {data['version']} sha256={digest_of(data)}\n")
    except (validation.ValidationError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"manifest error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
