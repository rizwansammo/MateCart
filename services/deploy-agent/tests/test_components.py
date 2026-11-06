"""Signing, validation, manifests, rendering, backups, SSH planning, preflight and the runner."""

import base64
import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from matecart_agent import backup, manifest, preflight, render, ssh, validation
from matecart_agent.config import AgentConfig
from matecart_agent.runner import CommandRefused, Completed, FakeRunner, redact
from matecart_agent.signing import response_valid, sign, signature_base

from .conftest import STORE_ID, release

# Same vector as services/control-api/tests/test_deployment_automation.py.
VECTOR = "520ac1074c63f0340cce3c335e6d2ade304ee18108f363ad0d93c6f7d001c710"


def test_signature_vector_matches_control() -> None:
    message = signature_base(
        "POST", "/api/agent/v1/heartbeat/", "1700000000", "0" * 32, b'{"hello":"agent"}'
    )
    assert sign("a" * 64, message) == VECTOR
    body = b"{}"
    digest = hashlib.sha256(body).hexdigest()
    signature = sign("s", f"response\nnonce\n200\n{digest}".encode())
    assert response_valid("s", "nonce", 200, body, signature)
    assert not response_valid("s", "other-nonce", 200, body, signature)


@pytest.mark.parametrize(
    "value",
    ["localhost", "1.2.3.4", "*.example.com", "exa mple.com", "-bad.com", "a.b/../c", "x" * 300],
)
def test_domain_validation_refuses_unsafe_names(value: str) -> None:
    with pytest.raises(validation.ValidationError):
        validation.domain(value)


def test_paths_and_env_values_cannot_escape(tmp_path: Path) -> None:
    with pytest.raises(validation.ValidationError):
        validation.within(tmp_path / "stores", tmp_path / "stores" / ".." / "etc")
    with pytest.raises(validation.ValidationError):
        validation.env_value("a\nINJECTED=1")
    with pytest.raises(validation.ValidationError):
        validation.handle("Bad_Handle")


def test_manifest_build_and_validation(tmp_path: Path) -> None:
    for service, app in (("commerce-api", "commerce"), ("control-api", "control")):
        folder = tmp_path / "services" / service / app / "orders" / "migrations"
        folder.mkdir(parents=True)
        (folder / "0001_initial.py").write_text("")
    digests = {name: "sha256:" + "b" * 64 for name in manifest.IMAGES}
    built = manifest.build(
        version="1.2.0",
        git_commit="c" * 40,
        owner="RizwanSammo",
        digests=digests,
        repo_root=tmp_path,
        rollback_compatible_with=["1.1.0"],
    )
    assert (
        built["images"]["commerce-api"]["repository"] == "ghcr.io/rizwansammo/matecart-commerce-api"
    )
    assert built["database"]["commerce"]["migration_count"] == 1
    assert manifest.rollback_allowed(built, {**release("1.1.0")})
    broken = json.loads(json.dumps(built))
    broken["images"]["web"] = broken["images"].pop("commerce-web")
    with pytest.raises(validation.ValidationError):
        manifest.validate(broken)
    path = tmp_path / "release.json"
    path.write_text(json.dumps(built))
    assert manifest.main(["validate", str(path)]) == 0


def spec(*domains: str) -> render.StoreSpec:
    return render.StoreSpec.from_payload(
        {
            "store_id": STORE_ID,
            "handle": "river-shop",
            "name": "River Shop",
            "preset": "general",
            "country": "bd",
            "currency": "bdt",
            "time_zone": "Asia/Dhaka",
            "language": "en",
            "languages": ["en", "bn"],
        },
        [{"domain": item} for item in domains],
    )


def test_render_dry_run_masks_secrets_and_real_render_preserves_them(config: AgentConfig) -> None:
    images = {
        "api_image": "ghcr.io/o/matecart-commerce-api@sha256:" + "a" * 64,
        "web_image": "ghcr.io/o/matecart-commerce-web@sha256:" + "b" * 64,
    }
    dry = render.plan_store(config, spec(), dry_run=True, **images)
    assert "<generated>" in dry.files["app.env"]
    assert not config.store_dir("river-shop").exists()
    plan = render.plan_store(config, spec("shop.example.test"), dry_run=False, **images)
    render.write_plan(plan)
    store_dir = config.store_dir("river-shop")
    first = render.read_env(store_dir / "app.env")
    for name in ("deploy.env", "web.env", "store.json", "compose.yaml"):
        text = (store_dir / name).read_text()
        assert first["DJANGO_SECRET_KEY"] not in text
    for name in ("app.env", "db.env", "redis.env", "migrate.env"):
        assert stat.S_IMODE((store_dir / name).stat().st_mode) == 0o600
    assert first["DJANGO_ALLOWED_HOSTS"].startswith("shop.example.test,")
    again = render.plan_store(config, spec("shop.example.test"), dry_run=False, **images)
    render.write_plan(again)
    second = render.read_env(store_dir / "app.env")
    assert second["DJANGO_SECRET_KEY"] == first["DJANGO_SECRET_KEY"]
    assert second["DATABASE_URL"] == first["DATABASE_URL"]


def test_two_stores_never_share_secrets_or_projects(config: AgentConfig) -> None:
    images = {"api_image": "a", "web_image": "b"}
    first = render.plan_store(config, spec(), dry_run=False, **images)
    other = render.StoreSpec.from_payload(
        {
            "store_id": "00000000-0000-0000-0000-0000000000aa",
            "handle": "lake-books",
            "name": "Lake",
            "preset": "books",
            "country": "BD",
            "currency": "BDT",
            "time_zone": "Asia/Dhaka",
            "language": "en",
        },
        [],
    )
    second = render.plan_store(config, other, dry_run=False, **images)
    a, b = (render.read_env_text(plan.files["app.env"]) for plan in (first, second))
    for key in (
        "DJANGO_SECRET_KEY",
        "MATECART_ENCRYPTION_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "MATECART_TENANT_ID",
    ):
        assert a[key] != b[key]
    assert "matecart-store-river-shop" in first.files["deploy.env"]
    assert "matecart-store-lake-books" in second.files["deploy.env"]


def test_proxy_site_and_compose_rendering() -> None:
    block = render.site_block(
        "river-shop", ["shop.example.test", "www.shop.example.test"], "internal", ""
    )
    assert block.startswith("# Store river-shop")
    assert "tls internal" in block
    assert "handle /api/*" in block
    with pytest.raises(validation.ValidationError):
        render.site_block("river-shop", ["shop.example.test}\n:80"], "acme", "")
    compose = render.proxy_compose(["river-shop", "lake-books"])
    assert "matecart-store-lake-books-edge:\n    external: true" in compose
    assert "__" not in compose
    assert "email ops@example.test" in render.caddyfile("ops@example.test")
    with pytest.raises(validation.ValidationError):
        render.caddyfile("bad email}")


def test_backup_integrity_isolation_and_retention(config: AgentConfig) -> None:
    runner = FakeRunner()
    compose = config.compose_for("river-shop", {})
    root = Path(config.backup_root)
    result = backup.run_backup(
        runner, compose, backup_root=root, handle="river-shop", include_media=True
    )
    assert result.status == "successful"
    assert result.directory is not None
    assert backup.verify(result.directory)
    assert stat.S_IMODE(result.directory.stat().st_mode) == 0o700
    assert result.directory.parent == root / "river-shop"
    (result.directory / "database.dump").write_bytes(b"tampered")
    assert not backup.verify(result.directory)
    old = datetime.now(UTC) - timedelta(days=40)
    backup.run_backup(
        runner, compose, backup_root=root, handle="river-shop", include_media=False, now=old
    )
    other = backup.run_backup(
        runner, compose, backup_root=root, handle="lake-books", include_media=False, now=old
    )
    assert backup.prune(root, "river-shop", 14) == 1
    assert other.directory is not None
    assert other.directory.exists()


def test_failed_backup_leaves_no_artifact(config: AgentConfig) -> None:
    runner = FakeRunner(responder=lambda argv: Completed(argv, 1, "", "error password=secret"))
    result = backup.run_backup(
        runner,
        config.compose_for("river-shop", {}),
        backup_root=Path(config.backup_root),
        handle="river-shop",
        include_media=False,
    )
    assert result.status == "failed"
    assert result.directory is None
    assert list((Path(config.backup_root) / "river-shop").iterdir()) == []


def test_ssh_plan_requires_a_pinned_matching_host_key(tmp_path: Path) -> None:
    key = "ssh-ed25519 " + base64.b64encode(b"k" * 51).decode()
    good = ssh.Target("203.0.113.5", "deploy", 22, key, ssh.fingerprint(key))
    argv = ssh.ssh_argv(
        good, known_hosts=tmp_path / "kh", identity=tmp_path / "id", mode="preflight"
    )
    assert "StrictHostKeyChecking=yes" in argv
    assert "BatchMode=yes" in argv
    assert argv[-6:] == ["sudo", "-n", "/bin/bash", "-s", "--", "preflight"]
    with pytest.raises(validation.ValidationError):
        ssh.ssh_argv(
            ssh.Target("203.0.113.5", "deploy", 22, key, "SHA256:wrong"),
            known_hosts=tmp_path / "kh",
            identity=tmp_path / "id",
            mode="preflight",
        )
    with pytest.raises(validation.ValidationError):
        ssh.ssh_argv(
            ssh.Target("-oProxyCommand=x", "deploy", 22, key, ssh.fingerprint(key)),
            known_hosts=tmp_path / "kh",
            identity=tmp_path / "id",
            mode="preflight",
        )
    with pytest.raises(validation.ValidationError):
        ssh.ssh_argv(good, known_hosts=tmp_path / "kh", identity=tmp_path / "id", mode="rm -rf")
    assert ssh.known_hosts_line(good).startswith("203.0.113.5 ssh-ed25519 ")


def test_preflight_reports_missing_docker_and_unsupported_os(config: AgentConfig) -> None:
    runner = FakeRunner(responder=lambda argv: Completed(argv, 127, "", "not installed"))
    report = preflight.run(
        runner,
        root=config.root,
        os_release={"ID": "centos", "VERSION_ID": "7"},
        proxy_installed=True,
    )
    names = {item.name: item.ok for item in report.checks}
    assert not report.ok
    assert names["os"] is False
    assert names["docker"] is False
    assert names["compose"] is False
    ok_runner = FakeRunner(
        responder=lambda argv: Completed(
            argv, 0, '{"version":"v2.40.0"}' if "compose" in argv else '"28.0.0"', ""
        )
    )
    report = preflight.run(
        ok_runner,
        root=config.root,
        min_cpus=1,
        min_ram_mb=1,
        min_free_disk_gb=0,
        proxy_installed=True,
        os_release={"ID": "ubuntu", "VERSION_ID": "24.04"},
    )
    checks = {item.name: item.ok for item in report.checks}
    assert checks["os"]
    assert checks["docker"]
    assert checks["compose"]


def test_runner_allowlist_and_redaction() -> None:
    runner = FakeRunner()
    with pytest.raises(CommandRefused):
        runner.run(["bash", "-c", "id"])
    with pytest.raises(CommandRefused):
        runner.run(["docker", "compose", "exec", "api", "sh\nrm"])
    text = redact("postgres://user:pw@db/x password=abc redis://:secret@redis:6379")
    assert "pw@" not in text
    assert "abc" not in text
    assert "secret@" not in text
