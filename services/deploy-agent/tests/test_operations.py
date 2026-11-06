"""Release lifecycle against a simulated Compose project (no Docker needed)."""

import json
import stat
from pathlib import Path
from typing import Any

from matecart_agent import manifest, operations, render
from matecart_agent.config import AgentConfig
from matecart_agent.runner import FakeRunner

from .conftest import STORE_ID, FakeDocker, payload, release


def run(config: AgentConfig, runner: FakeRunner, data: dict[str, Any]) -> operations.Outcome:
    events: list[tuple[str, str]] = []
    context = operations.Context(config, runner, lambda stage, msg: events.append((stage, msg)))
    return operations.execute(context, data)


def install(config: AgentConfig, runner: FakeRunner, version: str = "1.0.0", **kw: Any) -> None:
    outcome = run(config, runner, payload("deploy_release", release(version, **kw)))
    assert outcome.succeeded, outcome


def test_first_install_renders_isolated_store_and_reports_the_running_version(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    outcome = run(config, runner, payload("deploy_release", release("1.0.0")))
    assert outcome.succeeded
    assert outcome.evidence["observed_version"] == "1.0.0"
    store_dir = config.store_dir("river-shop")
    app = render.read_env(store_dir / "app.env")
    assert app["MATECART_TENANT_ID"] == STORE_ID
    assert app["DATABASE_URL"].startswith("postgres://matecart_app:")
    migrate = render.read_env(store_dir / "migrate.env")
    assert migrate["DATABASE_URL"].startswith("postgres://matecart_owner:")
    assert stat.S_IMODE((store_dir / "app.env").stat().st_mode) == 0o600
    assert docker.log == ["pull", "up", "run", "up", "ps", "exec"]
    assert json.loads((store_dir / "release.json").read_text())["version"] == "1.0.0"
    # Production images are pulled by digest before anything changes.
    assert any("pull" in call for call in runner.calls)
    pull = next(call for call in runner.calls if "pull" in call)
    assert "--project-name" in pull
    assert "matecart-store-river-shop" in pull


def test_update_backs_up_pulls_migrates_and_records_previous_release(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    install(config, runner)
    docker.log.clear()
    outcome = run(config, runner, payload("update_release", release("1.1.0")))
    assert outcome.succeeded, outcome
    assert docker.log[:2] == ["exec", "pull"]  # pre-update backup, then pull
    assert "run" in docker.log  # migrate job
    store_dir = config.store_dir("river-shop")
    assert json.loads((store_dir / "release.previous.json").read_text())["version"] == "1.0.0"
    assert outcome.evidence["pre_update_backup"].startswith("river-shop-")


def test_unavailable_image_changes_nothing(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    install(config, runner)
    before = (config.store_dir("river-shop") / "deploy.env").read_text()
    docker.fail.add("pull")
    outcome = run(config, runner, payload("update_release", release("1.1.0")))
    assert not outcome.succeeded
    assert outcome.failure_category == "image_unavailable"
    assert (config.store_dir("river-shop") / "deploy.env").read_text() == before


def test_health_failure_restores_previous_release_when_schema_compatible(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    install(config, runner)
    target = release("1.1.0")
    docker.unhealthy_images.add(manifest.image(target, "commerce-api"))
    outcome = run(config, runner, payload("update_release", target))
    assert not outcome.succeeded
    assert outcome.failure_category == "rolled_back"
    assert outcome.evidence["rolled_back"] is True
    assert outcome.evidence["observed_version"] == "1.0.0"
    env = render.read_env(config.store_dir("river-shop") / "deploy.env")
    assert env["MATECART_COMMERCE_API_IMAGE"] == manifest.image(release("1.0.0"), "commerce-api")


def test_failure_after_incompatible_migration_refuses_automatic_rollback(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    install(config, runner)
    target = release("2.0.0", fingerprint="9" * 64)
    docker.unhealthy_images.add(manifest.image(target, "commerce-api"))
    outcome = run(config, runner, payload("update_release", target))
    assert not outcome.succeeded
    assert outcome.failure_category == "health_check_failed"
    assert outcome.evidence["recovery_required"] is True
    assert "restore backup" in outcome.message


def test_migration_failure_is_reported(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    install(config, runner)
    docker.fail.add("migrate")
    outcome = run(config, runner, payload("update_release", release("1.1.0")))
    assert not outcome.succeeded
    assert outcome.failure_category in {"rolled_back", "migration_failed"}


def test_rollback_rules(config: AgentConfig, runner: FakeRunner) -> None:
    install(config, runner, "1.0.0")
    changed = release("2.0.0", fingerprint="9" * 64)
    assert run(config, runner, payload("update_release", changed)).succeeded
    refused = run(config, runner, payload("rollback_release", release("1.0.0")))
    assert refused.failure_category == "rollback_refused"
    declared = release("3.0.0", fingerprint="8" * 64, compatible=["2.0.0"])
    assert run(config, runner, payload("update_release", declared)).succeeded
    allowed = run(config, runner, payload("rollback_release", changed))
    assert allowed.succeeded, allowed


def test_invalid_manifest_and_wrong_target_are_refused(
    config: AgentConfig, runner: FakeRunner
) -> None:
    data = payload("deploy_release", release("1.0.0"))
    data["release_sha256"] = "0" * 64
    assert run(config, runner, data).failure_category == "release_invalid"
    tampered = payload("deploy_release", release("1.0.0"))
    tampered["release"]["images"]["commerce-api"]["digest"] = "latest"
    assert run(config, runner, tampered).failure_category == "release_invalid"
    other = payload(
        "deploy_release", release("1.0.0"), server_id="00000000-0000-0000-0000-000000000009"
    )
    assert run(config, runner, other).failure_category == "target_mismatch"
    traversal = payload("deploy_release", release("1.0.0"), store_handle="../etc")
    assert run(config, runner, traversal).failure_category == "target_mismatch"
    unknown = payload("deploy_release", release("1.0.0"))
    unknown["kind"] = "shell"
    assert run(config, runner, unknown).failure_category == "target_mismatch"
    assert runner.calls == []


def test_existing_store_directory_of_another_store_is_never_overwritten(
    config: AgentConfig, runner: FakeRunner
) -> None:
    install(config, runner)
    other = payload(
        "deploy_release", release("1.0.0"), store_id="00000000-0000-0000-0000-000000000099"
    )
    other["store"]["store_id"] = other["store_id"]
    assert run(config, runner, other).failure_category == "target_mismatch"


def test_interrupted_update_restores_previous_release(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    install(config, runner)
    store_dir = config.store_dir("river-shop")
    broken = release("1.1.0")
    render.write_deploy_env(
        store_dir,
        {
            **render.read_env(store_dir / "deploy.env"),
            "MATECART_COMMERCE_API_IMAGE": manifest.image(broken, "commerce-api"),
        },
    )
    docker.unhealthy_images.add(manifest.image(broken, "commerce-api"))
    docker.running["api"] = manifest.image(broken, "commerce-api")
    context = operations.Context(config, runner, lambda stage, msg: None)
    outcome = operations.recover_interrupted(context, "river-shop", "update_release")
    assert outcome.failure_category == "interrupted"
    assert outcome.evidence["rolled_back"] is True


def test_configuration_and_backup_operations(
    config: AgentConfig, runner: FakeRunner, docker: FakeDocker
) -> None:
    install(config, runner)
    good = payload(
        "apply_configuration",
        revision=3,
        preset="general",
        theme="general-marketplace",
        modules=["catalog", "live_chat"],
    )
    outcome = run(config, runner, good)
    assert outcome.succeeded
    assert outcome.evidence["configuration_revision"] == 3
    call = runner.calls[-1]
    assert "apply_platform_configuration" in call
    assert "live_chat" in ",".join(call)
    bad = payload(
        "apply_configuration", revision=4, preset="general", theme="x; rm -rf /", modules=[]
    )
    assert run(config, runner, bad).failure_category == "invalid_command"

    backup_outcome = run(config, runner, payload("run_backup", policy={"include_media": True}))
    assert backup_outcome.succeeded
    report = backup_outcome.evidence["backup"]
    assert report["status"] == "successful"
    assert len(report["sha256"]) == 64
    docker.fail.add("dump")
    failed = run(config, runner, payload("run_backup", policy={"include_media": False}))
    assert failed.failure_category == "backup_failed"
    assert "hunter2" not in json.dumps(failed.evidence)


def test_configure_domains_renders_proxy_only_for_valid_names(
    config: AgentConfig, runner: FakeRunner
) -> None:
    install(config, runner)
    outcome = run(
        config,
        runner,
        payload("configure_domains", domains=[{"domain": "Shop.Example.TEST", "tls_mode": "acme"}]),
    )
    assert outcome.succeeded
    site = Path(config.root) / "proxy" / "sites" / "river-shop.caddy"
    text = site.read_text()
    assert "shop.example.test {" in text
    assert "store-river-shop-api:8000" in text
    app = render.read_env(config.store_dir("river-shop") / "app.env")
    assert app["MATECART_PUBLIC_BASE_URL"] == "https://shop.example.test"
    injected = run(
        config,
        runner,
        payload(
            "configure_domains",
            domains=[{"domain": "evil.test {\n respond 200", "tls_mode": "acme"}],
        ),
    )
    assert injected.failure_category == "invalid_command"
    compose_file = (Path(config.root) / "proxy" / "compose.yaml").read_text()
    assert "matecart-store-river-shop-edge" in compose_file
