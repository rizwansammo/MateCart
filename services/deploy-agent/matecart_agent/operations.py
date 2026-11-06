"""The complete set of operations the Agent can execute (D-119, D-120, D-121).

Each operation validates its Control-built payload again, acts only on the Store directory and
Compose project derived from the validated handle, and returns an ``Outcome`` with a failure
category from Control's allowlist. Release operations follow the documented update lifecycle:

    validate manifest -> confirm target -> compatibility -> resources -> pre-update backup ->
    pull images by digest -> switch -> migrate -> start -> health -> report

A failed update restores the previous images automatically only when the previous release can run
on the current schema (no migration ran, or the new release declares the old one compatible).
Otherwise the Store is left as is and the outcome names the pre-update backup to restore; the
Agent never resets or restores a database by itself.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import backup, host, manifest, render, validation
from .compose import APP_SERVICES, DATA_SERVICES, ComposeProject
from .config import AgentConfig, write_private
from .runner import Runner

KINDS = frozenset(
    {
        "deploy_release",
        "update_release",
        "rollback_release",
        "apply_configuration",
        "configure_domains",
        "run_backup",
    }
)
THEME = re.compile(r"^[a-z][a-z-]{2,60}$")
MODULE = re.compile(r"^[a-z_]{2,40}$")

Progress = Callable[[str, str], None]


@dataclass
class Outcome:
    succeeded: bool
    stage: str
    message: str = ""
    failure_category: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_event(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "event": "succeeded" if self.succeeded else "failed",
            "stage": self.stage[:40],
            "message": self.message[:300],
            "evidence": self.evidence,
        }
        if not self.succeeded:
            data["failure_category"] = self.failure_category or "agent_error"
        return data


def fail(stage: str, category: str, message: str, **evidence: Any) -> Outcome:
    return Outcome(False, stage, message, category, evidence)


@dataclass
class Context:
    config: AgentConfig
    runner: Runner
    progress: Progress
    images_override: Callable[[dict[str, Any]], tuple[str, str]] | None = None

    def compose(self, handle: str, deploy_env: dict[str, str] | None = None) -> ComposeProject:
        values = (
            deploy_env
            if deploy_env is not None
            else render.read_env(self.config.store_dir(handle) / "deploy.env")
        )
        return self.config.compose_for(handle, values)

    def images(self, release: dict[str, Any]) -> tuple[str, str]:
        if self.images_override is not None:
            return self.images_override(release)
        return manifest.image(release, "commerce-api"), manifest.image(release, "commerce-web")


def validate_common(config: AgentConfig, payload: dict[str, Any]) -> tuple[str, str]:
    kind = str(payload.get("kind", ""))
    if kind not in KINDS:
        raise validation.ValidationError(f"Operation {kind[:40]!r} is not allowlisted.")
    if config.server_id and validation.identifier(payload.get("server_id")) != config.server_id:
        raise validation.ValidationError("The command targets another Server.")
    validation.identifier(payload.get("deployment_id"))
    validation.identifier(payload.get("store_id"))
    handle = validation.handle(payload.get("store_handle"))
    store_dir = config.store_dir(handle)
    marker = render.store_marker(store_dir)
    if marker is not None and marker.get("store_id") != payload["store_id"]:
        raise validation.ValidationError("The Store directory belongs to another Store ID.")
    if kind != "deploy_release" and marker is None:
        raise validation.ValidationError("This Store is not installed on the Server.")
    return kind, handle


def execute(context: Context, payload: dict[str, Any]) -> Outcome:
    try:
        kind, handle = validate_common(context.config, payload)
    except validation.ValidationError as exc:
        return fail("validate", "target_mismatch", str(exc))
    try:
        if kind in {"deploy_release", "update_release", "rollback_release"}:
            return release_operation(context, kind, handle, payload)
        if kind == "apply_configuration":
            return apply_configuration(context, handle, payload)
        if kind == "configure_domains":
            return configure_domains(context, handle, payload)
        return run_backup(context, handle, payload)
    except validation.ValidationError as exc:
        return fail("validate", "invalid_command", str(exc))


# ---------------------------------------------------------------- releases


def _current_release(store_dir: Path) -> dict[str, Any] | None:
    path = store_dir / "release.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _healthy(context: Context, compose: ComposeProject, expected: dict[str, str]) -> str:
    """Return '' when every service runs (healthy where checked) on the expected image."""
    rows = {row["Logical"]: row for row in compose.ps(context.runner) if row.get("Logical")}
    for service in (*DATA_SERVICES, *APP_SERVICES):
        row = rows.get(service)
        if row is None or str(row.get("State", "")).lower() != "running":
            return f"{service} is not running"
        health = str(row.get("Health", "")).lower()
        if health not in {"", "healthy"}:
            return f"{service} is {health}"
        want = expected.get(service)
        if want and str(row.get("Image", "")) != want:
            return f"{service} runs an unexpected image"
    if not compose.readiness(context.runner).ok:
        return "the API readiness check failed"
    return ""


def release_operation(context: Context, kind: str, handle: str, payload: dict[str, Any]) -> Outcome:
    config = context.config
    store_dir = config.store_dir(handle)
    # 1-3. Release identity and integrity.
    try:
        target = manifest.validate(payload.get("release"))
    except validation.ValidationError as exc:
        return fail("validate", "release_invalid", str(exc))
    if manifest.digest_of(target) != payload.get("release_sha256"):
        return fail("validate", "release_invalid", "The release manifest digest does not match.")
    try:
        api_image, web_image = context.images(target)
    except validation.ValidationError as exc:
        return fail("validate", "release_invalid", str(exc))
    current = _current_release(store_dir)
    if kind == "deploy_release" and current is not None:
        return fail("validate", "invalid_command", "A release is already installed; use update.")
    if kind != "deploy_release" and current is None:
        return fail("validate", "invalid_command", "No installed release to update.")
    if (
        kind == "rollback_release"
        and current is not None
        and not manifest.rollback_allowed(current, target)
    ):
        return fail(
            "compatibility",
            "rollback_refused",
            "Automatic rollback refused: the database schema changed. Restore the pre-update "
            "backup with an operator-run recovery instead.",
        )
    context.progress("compatibility", f"Target release {target['version']} accepted.")
    # 4. Resources.
    _, free_gb = host.disk(config.root if Path(config.root).exists() else ".")
    if free_gb is not None and free_gb < config.min_free_disk_gb:
        return fail("resources", "insufficient_resources", f"Only {free_gb} GB free on the Server.")
    # Prepare the Store directory (first install renders secrets on this Server only).
    spec = render.StoreSpec.from_payload(payload.get("store") or {}, payload.get("domains") or [])
    if spec.store_id != payload["store_id"] or spec.handle != handle:
        return fail("validate", "target_mismatch", "Store identity in the payload is inconsistent.")
    plan = render.plan_store(config, spec, api_image=api_image, web_image=web_image, dry_run=False)
    if kind == "deploy_release":
        render.write_plan(plan)
        context.progress("prepare", "Store directory rendered.")
    previous_env = render.read_env(store_dir / "deploy.env")
    new_env = {
        **render.read_env(store_dir / "deploy.env"),
        "MATECART_COMMERCE_API_IMAGE": api_image,
        "MATECART_COMMERCE_WEB_IMAGE": web_image,
    }
    staged = context.compose(handle, new_env)
    # 6. Backup prerequisite for an existing Store.
    backup_name = ""
    if kind != "deploy_release":
        result = backup.run_backup(
            context.runner,
            context.compose(handle),
            backup_root=Path(config.backup_root),
            handle=handle,
            include_media=False,
        )
        if result.status != "successful":
            return fail("backup", "backup_failed", "The pre-update backup failed; nothing changed.")
        backup_name = result.artifact_name
        context.progress("backup", f"Pre-update backup {backup_name} written.")
    # 7. Images must be available before anything changes.
    if not context.config.acceptance and not staged.pull(context.runner, ["api", "web"]).ok:
        return fail("pull", "image_unavailable", "A release image could not be pulled.")
    context.progress("pull", "Release images available.")
    # 8-10. Switch, migrate, start.
    render.write_deploy_env(store_dir, new_env)
    migrated = False
    if not staged.up(context.runner, list(DATA_SERVICES)).ok:
        problem = "PostgreSQL or Redis did not become healthy."
        return _recover(
            context,
            kind,
            handle,
            current,
            target,
            previous_env,
            False,
            problem,
            backup_name,
            "health_check_failed",
        )
    if kind != "rollback_release":
        migrated = True
        if not staged.migrate(context.runner).ok:
            return _recover(
                context,
                kind,
                handle,
                current,
                target,
                previous_env,
                migrated,
                "Migrations or tenant provisioning failed.",
                backup_name,
                "migration_failed",
            )
        context.progress("migrate", "Migrations applied and tenant provisioned.")
    started = staged.up(context.runner, list(APP_SERVICES), recreate=True)
    problem = "" if started.ok else "Application services did not become healthy."
    if not problem:
        problem = _healthy(
            context,
            staged,
            {"api": api_image, "worker": api_image, "beat": api_image, "web": web_image},
        )
    if problem:
        return _recover(
            context,
            kind,
            handle,
            current,
            target,
            previous_env,
            migrated,
            problem,
            backup_name,
            "health_check_failed",
        )
    # Success: record the running release.
    if current is not None:
        write_private(store_dir / "release.previous.json", json.dumps(current, indent=2))
    write_private(store_dir / "release.json", json.dumps(target, indent=2))
    _ensure_proxy(context, handle, payload.get("domains") or [])
    return Outcome(
        True,
        "verify",
        f"Release {target['version']} is running and ready.",
        evidence={
            "observed_version": target["version"],
            "images": {"api": api_image, "web": web_image},
            "pre_update_backup": backup_name,
        },
    )


def _recover(
    context: Context,
    kind: str,
    handle: str,
    current: dict[str, Any] | None,
    target: dict[str, Any],
    previous_env: dict[str, str],
    migrated: bool,
    problem: str,
    backup_name: str,
    category: str,
) -> Outcome:
    store_dir = context.config.store_dir(handle)
    if current is None:
        return fail("start", category, f"{problem} The new Store is not serving.")
    # The previous code can run only if the schema it expects is still compatible.
    compatible = not migrated or manifest.rollback_allowed(target, current)
    if not compatible:
        return fail(
            "recover",
            category,
            f"{problem} Automatic rollback refused after schema migration; restore backup "
            f"{backup_name or '(none)'} through an operator-approved recovery.",
            recovery_required=True,
            pre_update_backup=backup_name,
        )
    render.write_deploy_env(store_dir, previous_env)
    restored = context.compose(handle, previous_env)
    restored.up(context.runner, list(APP_SERVICES), recreate=True)
    still = _healthy(context, restored, {})
    context.progress("recover", still if still else "Previous release restored.")
    return fail(
        "recover",
        "rolled_back" if not still else category,
        f"{problem} Previous release {current['version']} "
        + ("restored and healthy." if not still else f"restore incomplete: {still}"),
        rolled_back=not still,
        observed_version=current["version"] if not still else "",
        pre_update_backup=backup_name,
    )


def recover_interrupted(context: Context, handle: str, kind: str) -> Outcome:
    """After an Agent restart mid-command: report the real state, never assume success."""
    store_dir = context.config.store_dir(handle)
    current = _current_release(store_dir)
    deploy_env = render.read_env(store_dir / "deploy.env")
    compose = context.compose(handle, deploy_env)
    problem = _healthy(context, compose, {})
    if kind in {"deploy_release", "update_release", "rollback_release"}:
        previous_env = render.read_env(store_dir / "deploy.previous.env")
        if problem and previous_env and current is not None:
            render.write_deploy_env(store_dir, previous_env)
            context.compose(handle, previous_env).up(
                context.runner, list(APP_SERVICES), recreate=True
            )
            problem = _healthy(context, context.compose(handle, previous_env), {})
            return fail(
                "recover",
                "interrupted",
                "The Agent stopped during the operation; the previous release was restored"
                + ("." if not problem else f" but {problem}."),
                rolled_back=not problem,
            )
    return fail(
        "recover",
        "interrupted",
        "The Agent stopped during the operation; the outcome was not confirmed"
        + ("." if not problem else f" ({problem})."),
    )


# ---------------------------------------------------------------- configuration and domains


def apply_configuration(context: Context, handle: str, payload: dict[str, Any]) -> Outcome:
    revision = payload.get("revision")
    preset = str(payload.get("preset", ""))
    theme = str(payload.get("theme", ""))
    modules = [str(item) for item in payload.get("modules", [])]
    if not isinstance(revision, int) or revision < 1 or preset not in validation.PRESETS:
        return fail("validate", "invalid_command", "Invalid configuration revision.")
    if not THEME.fullmatch(theme) or not all(MODULE.fullmatch(item) for item in modules):
        return fail("validate", "invalid_command", "Invalid theme or module identifier.")
    result = context.compose(handle).apply_configuration(
        context.runner, revision=revision, preset=preset, theme=theme, modules=modules
    )
    if not result.ok:
        return fail("apply", "configuration_failed", result.stderr.strip()[-300:])
    return Outcome(
        True,
        "apply",
        f"Configuration revision {revision} applied.",
        evidence={"configuration_revision": revision},
    )


def _ensure_proxy(context: Context, handle: str, domains: list[Any]) -> None:
    names = [validation.domain(item["domain"]) for item in domains if isinstance(item, dict)]
    modes = {str(item.get("tls_mode", "acme")) for item in domains if isinstance(item, dict)}
    mode = "internal" if modes == {"internal"} else "acme"
    config = context.config
    sites = config.proxy_dir / "sites"
    sites.mkdir(parents=True, exist_ok=True)
    site = validation.within(sites, sites / f"{handle}.caddy")
    block = render.site_block(handle, names, mode, config.acme_email)
    if not block and not site.exists():
        return  # nothing routed for this Store yet: leave the shared proxy untouched
    if block:
        site.write_text(block, encoding="utf-8", newline="\n")
    else:
        site.unlink(missing_ok=True)
    if not config.acceptance:
        installed = (
            sorted(
                path.name for path in config.stores_dir.iterdir() if (path / "store.json").exists()
            )
            if config.stores_dir.exists()
            else []
        )
        (config.proxy_dir / "Caddyfile").write_text(render.caddyfile(config.acme_email), "utf-8")
        (config.proxy_dir / "compose.yaml").write_text(render.proxy_compose(installed), "utf-8")
        config.proxy().up(context.runner)
    config.proxy().reload(context.runner)


def configure_domains(context: Context, handle: str, payload: dict[str, Any]) -> Outcome:
    domains = payload.get("domains") or []
    names = [validation.domain(item["domain"]) for item in domains if isinstance(item, dict)]
    if not names:
        return fail("validate", "domain_failed", "No verified domains were provided.")
    store_dir = context.config.store_dir(handle)
    store = json.loads((store_dir / "store.json").read_text(encoding="utf-8"))
    app = render.read_env(store_dir / "app.env")
    origins = ",".join(f"https://{name}" for name in names)
    app.update(
        {
            "DJANGO_ALLOWED_HOSTS": ",".join([*names, "api", "127.0.0.1", "localhost"]),
            "DJANGO_CSRF_TRUSTED_ORIGINS": origins,
            "MATECART_WEBSOCKET_ORIGINS": origins,
            "MATECART_PUBLIC_BASE_URL": f"https://{names[0]}",
        }
    )
    web = render.read_env(store_dir / "web.env")
    web["STOREFRONT_PUBLIC_URL"] = f"https://{names[0]}"
    write_private(store_dir / "app.env", render.format_env(app, "Store application settings"))
    write_private(store_dir / "web.env", render.format_env(web, "Storefront (no secrets)"))
    if store.get("store_id") != payload["store_id"]:
        return fail("validate", "target_mismatch", "Store identity mismatch.")
    compose = context.compose(handle)
    if not compose.up(context.runner, list(APP_SERVICES), recreate=True).ok:
        return fail("start", "domain_failed", "Services did not restart with the new domains.")
    _ensure_proxy(context, handle, domains)
    context.progress("proxy", f"Proxy configured for {len(names)} domain(s).")
    return Outcome(
        True,
        "proxy",
        "Domains configured; TLS is verified from observations.",
        evidence={"domains": names},
    )


def run_backup(context: Context, handle: str, payload: dict[str, Any]) -> Outcome:
    policy = payload.get("policy") or {}
    result = backup.run_backup(
        context.runner,
        context.compose(handle),
        backup_root=Path(context.config.backup_root),
        handle=handle,
        include_media=bool(policy.get("include_media", True)),
    )
    retention = int(policy.get("retention_days", 14))
    if result.status == "successful":
        backup.prune(Path(context.config.backup_root), handle, retention)
        return Outcome(
            True,
            "backup",
            f"Backup {result.artifact_name} written.",
            evidence={"backup": result.report(str(payload["deployment_id"]))},
        )
    return fail(
        "backup", "backup_failed", result.notes, backup=result.report(str(payload["deployment_id"]))
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
