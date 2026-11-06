"""Phase 17 local deployment acceptance (D-124). Run from the repository root:

    python services/deploy-agent/acceptance/run.py          (pnpm deploy:acceptance)
    python services/deploy-agent/acceptance/run.py --down   (removes only matecart-dev-acc-*)

Uses only disposable acc-* services of the matecart-dev Compose project, loopback ports, local
images (matecart-dev/*:acceptance-*), Caddy's internal test CA and generated secrets kept in the
gitignored .acceptance/ folder. No real VPS, SSH, DNS, ACME, registry or owner data is touched.

What runs for real: production images and entrypoints, the production Store template, migrations
with separated database roles, tenant provisioning, two isolated Stores, the shared reverse proxy
(HTTPS, WebSocket upgrade, unknown-host refusal), the Agent's release/update/rollback/backup code
paths against those containers, a pg_restore smoke test into a throwaway database, and the signed
Agent protocol against a disposable Control API. What is simulated: images are local (no GHCR pull),
TLS certificates come from Caddy's internal CA (not a public CA), and Console approvals for these
commands are exercised by the Control test-suite rather than through a browser.
"""

import argparse
import json
import secrets
import socket
import ssl
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "deploy-agent"))

from matecart_agent import backup, manifest, operations, render  # noqa: E402
from matecart_agent.agent import Agent  # noqa: E402
from matecart_agent.config import AgentConfig, save  # noqa: E402
from matecart_agent.control import ControlClient, ControlRejected, ControlUnavailable  # noqa: E402
from matecart_agent.runner import SubprocessRunner  # noqa: E402

ACC = REPO / ".acceptance"
PROFILE = ["--profile", "deploy-acceptance"]
STORES = {
    "acc-store-a": {
        "prefix": "ACC_STORE_A_",
        "short": "a",
        "domain": "store-a.matecart.test",
        "name": "Acceptance Store A",
        "preset": "general",
    },
    "acc-store-b": {
        "prefix": "ACC_STORE_B_",
        "short": "b",
        "domain": "store-b.matecart.test",
        "name": "Acceptance Store B",
        "preset": "books",
    },
}
IMAGES = {  # release version -> local image tag
    "1.0.0": "acceptance-1",
    "1.1.0": "acceptance-2",
    "1.1.1": "acceptance-2",
    "1.2.0": "acceptance-broken",
}
results: list[dict[str, Any]] = []


def check(name: str, ok: bool, detail: str = "", kind: str = "container") -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail[:200], "method": kind})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail[:160] if detail else ''}")


def compose(
    *args: str, stdin: bytes | None = None, timeout: int = 600
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", "compose", *PROFILE, *args],
        cwd=REPO,
        input=stdin,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def shell(service: str, script: str) -> Any:
    result = compose("exec", "-T", service, "python", "manage.py", "shell", stdin=script.encode())
    out = result.stdout.decode().strip().splitlines()
    if result.returncode != 0 or not out:
        raise RuntimeError(f"{service} shell failed: {result.stderr.decode()[-400:]}")
    return json.loads(out[-1])


def ps_rows(service: str) -> list[dict[str, Any]]:
    text = compose("ps", "--format", "json", service).stdout.decode().strip()
    if text.startswith("["):
        parsed = json.loads(text)
        return [item for item in parsed if isinstance(item, dict)]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def dev_states(services: list[str]) -> dict[str, str]:
    """State of development-stack containers (never modified by acceptance)."""
    states = {}
    for service in services:
        rows = subprocess.run(
            ["docker", "compose", "ps", "--all", "--format", "{{.State}}", service],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout.split()
        states[service] = rows[0] if rows else "missing"
    return states


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- setup


def build_variant_images() -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "--target",
            "prod",
            "--build-arg",
            "MATECART_VERSION=1.1.0-acceptance",
            "--build-arg",
            f"MATECART_GIT_SHA={sha}",
            "-t",
            "matecart-dev/commerce-api:acceptance-2",
            "services/commerce-api",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "docker",
            "tag",
            "matecart-dev/commerce-web:acceptance-1",
            "matecart-dev/commerce-web:acceptance-2",
        ],
        check=True,
    )
    # A release whose migrations succeed but whose API never starts (a realistic bad update).
    broken = (
        b"FROM matecart-dev/commerce-api:acceptance-2\n"
        b"USER root\n"
        b'RUN printf \'#!/bin/sh\\nif [ "$1" = api ]; then echo simulated-broken-release >&2; '
        b'exit 1; fi\\nexec /usr/local/bin/matecart-entrypoint "$@"\\n\' '
        b"> /usr/local/bin/broken-entrypoint && chmod 0755 /usr/local/bin/broken-entrypoint\n"
        b"USER matecart\n"
        b'ENTRYPOINT ["/usr/local/bin/broken-entrypoint"]\n'
    )
    subprocess.run(
        ["docker", "build", "-q", "-t", "matecart-dev/commerce-api:acceptance-broken", "-"],
        input=broken,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "docker",
            "tag",
            "matecart-dev/commerce-web:acceptance-1",
            "matecart-dev/commerce-web:acceptance-broken",
        ],
        check=True,
    )


def control_env() -> None:
    folder = ACC / "control"
    if (folder / "app.env").exists():
        return
    db_password, redis_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    write(
        folder / "db.env",
        f"POSTGRES_DB=control\nPOSTGRES_USER=control\nPOSTGRES_PASSWORD={db_password}\n",
    )
    write(folder / "redis.env", f"REDIS_PASSWORD={redis_password}\n")
    write(
        folder / "app.env",
        "\n".join(
            [
                "DJANGO_SETTINGS_MODULE=config.settings.production",
                f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}",
                f"MATECART_ENCRYPTION_KEY={secrets.token_urlsafe(48)}",
                f"DATABASE_URL=postgres://control:{db_password}@acc-control-db:5432/control",
                f"REDIS_URL=redis://:{redis_password}@acc-control-redis:6379/0",
                f"CELERY_BROKER_URL=redis://:{redis_password}@acc-control-redis:6379/1",
                "DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost",
                "MATECART_SECURE_COOKIES=true",
                "MATECART_CONSOLE_PUBLIC_BASE_URL=https://console.acceptance.test",
                "EMAIL_URL=smtp://127.0.0.1:25",
                # Loopback-only acceptance: the Agent talks plain HTTP to 127.0.0.1 (never in production).
                "DJANGO_SECURE_SSL_REDIRECT=false",
                "DJANGO_LOG_FORMAT=json",
            ]
        )
        + "\n",
    )


def agent_config() -> AgentConfig:
    services = {
        handle: {
            "variable_prefix": info["prefix"],
            "services": {
                name: f"acc-{info['short']}-{name}"
                for name in ("db", "redis", "migrate", "api", "worker", "beat", "web")
            },
        }
        for handle, info in STORES.items()
    }
    return AgentConfig(
        control_url="http://127.0.0.1:18410",
        allow_insecure_http=True,
        root=str(ACC),
        state_dir=str(ACC / "agent"),
        backup_root=str(ACC / "backups"),
        proxy_https_port=18443,
        min_free_disk_gb=2,
        acceptance={
            "project": "matecart-dev",
            "project_directory": str(REPO),
            "compose_file": str(REPO / "compose.yaml"),
            "profiles": ["deploy-acceptance"],
            "proxy_service": "acc-proxy",
            "stores": services,
        },
    ).validate()


def local_release(
    version: str, fingerprint: str, compatible: list[str] | None = None
) -> dict[str, Any]:
    digests = {name: "sha256:" + secrets.token_hex(32) for name in manifest.IMAGES}
    data = {
        "schema": manifest.SCHEMA,
        "version": version,
        "git_commit": "0" * 40,
        "created_at": "2026-11-06T00:00:00Z",
        "images": {
            name: {
                "repository": f"ghcr.io/acceptance/matecart-{name}",
                "digest": digests[name],
                "tag": version,
            }
            for name in manifest.IMAGES
        },
        "database": {
            "commerce": {
                "migration_fingerprint": fingerprint,
                "rollback_compatible_with": compatible or [],
            },
            "control": {"migration_fingerprint": "c" * 64, "rollback_compatible_with": []},
        },
    }
    return manifest.validate(data)


def local_images(release: dict[str, Any]) -> tuple[str, str]:
    tag = IMAGES[release["version"]]
    return f"matecart-dev/commerce-api:{tag}", f"matecart-dev/commerce-web:{tag}"


def command(kind: str, target: dict[str, Any], /, **extra: Any) -> dict[str, Any]:
    """The same payload shape control.platform.deployment.build_payload produces."""
    data = {
        "command_id": str(uuid.uuid4()),
        "kind": kind,
        "deployment_id": target["deployment_id"],
        "server_id": target["server_id"],
        "store_id": target["store_id"],
        "store_handle": target["handle"],
        "environment": "production",
        "issued_at": "2026-11-06T00:00:00+00:00",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    data.update(extra)
    return data


def release_command(kind: str, store: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    info = STORES[store["handle"]]
    return command(
        kind,
        store,
        release=release,
        release_sha256=manifest.digest_of(release),
        store={
            "store_id": store["store_id"],
            "handle": store["handle"],
            "name": info["name"],
            "preset": info["preset"],
            "country": "BD",
            "currency": "BDT",
            "time_zone": "Asia/Dhaka",
            "language": "en",
            "languages": ["en"],
        },
        domains=[{"domain": info["domain"], "tls_mode": "internal"}],
    )


# ---------------------------------------------------------------- network probes


def https(domain: str, path: str, *, ws: bool = False, origin: str = "") -> tuple[int, str]:
    cafile = ACC / "proxy" / "data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"
    context = ssl.create_default_context(cafile=str(cafile))
    with (
        socket.create_connection(("127.0.0.1", 18443), timeout=15) as raw,
        context.wrap_socket(raw, server_hostname=domain) as tls,
    ):
        headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {domain}",
            "Connection: close",
            "User-Agent: matecart-acceptance",
        ]
        if ws:
            headers += [
                "Upgrade: websocket",
                "Connection: Upgrade",
                "Sec-WebSocket-Version: 13",
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
                f"Origin: {origin}",
            ]
        tls.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
        data = b""
        tls.settimeout(15)
        try:
            while len(data) < 200_000:
                chunk = tls.recv(65536)
                if not chunk or ws:
                    data += chunk
                    break
                data += chunk
        except (TimeoutError, ssl.SSLError):
            pass
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1]) if head.startswith(b"HTTP/") else 0
    return status, body.decode("utf-8", "replace")


def http_status(host: str) -> int:
    with socket.create_connection(("127.0.0.1", 18480), timeout=10) as raw:
        raw.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        head = raw.recv(4096)
    return int(head.split(b" ")[1]) if head.startswith(b"HTTP/") else 0


# ---------------------------------------------------------------- acceptance


CONTROL_FIXTURE = r"""
import json
from django.utils import timezone
from control.accounts.models import AdminUser
from control.platform.models import Client, Deployment, Server, StoreRegistration
from control.platform.deployment import issue_enrollment
owner = AdminUser.objects.filter(email="acceptance-owner@acceptance.test").first() or AdminUser.objects.create_user(email="acceptance-owner@acceptance.test", password=None, role="platform_owner", activated_at=timezone.now())
client = Client.objects.get_or_create(display_name="Acceptance Client", defaults={"country": "BD", "status": "active"})[0]
server = Server.objects.get_or_create(name="Acceptance local host", defaults={"ownership": "netamate", "public_ip": "127.0.0.1", "environment": "staging"})[0]
stores = {}
for handle, name, preset, theme in (("acc-store-a", "Acceptance Store A", "general", "general-marketplace"), ("acc-store-b", "Acceptance Store B", "books", "books-literary")):
    store = StoreRegistration.objects.get_or_create(handle=handle, defaults=dict(client=client, name=name, preset=preset, country="BD", default_currency="BDT", time_zone="Asia/Dhaka", default_language="en", supported_languages=["en"], desired_theme=theme, desired_modules=["catalog", "inventory", "content_pages", "customer_accounts"], status="ready_for_deployment"))[0]
    deployment = Deployment.objects.get_or_create(store=store, environment="production", defaults={"server": server, "reference": "ACC-" + handle})[0]
    stores[handle] = {"store_id": str(store.pk), "deployment_id": str(deployment.pk), "server_id": str(server.pk), "handle": handle}
_, token = issue_enrollment(server, owner)
print(json.dumps({"stores": stores, "token": token, "server_id": str(server.pk), "owner": str(owner.pk)}))
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--down", action="store_true", help="remove acc-* containers and volumes")
    args = parser.parse_args()
    if args.down:
        # Name the acc-* services explicitly: `compose rm` without service names would also stop
        # and remove the development stack's containers (same matecart-dev project).
        services = [
            name
            for name in compose("config", "--services").stdout.decode().split()
            if name.startswith("acc-")
        ]
        if services:
            compose("rm", "--stop", "--force", "--volumes", *services)
        names = subprocess.run(
            ["docker", "volume", "ls", "-q", "--filter", "name=matecart-dev-acc-"],
            capture_output=True,
            text=True,
        ).stdout.split()
        acc = [name for name in names if name.startswith("matecart-dev-acc-")]
        if acc:
            subprocess.run(["docker", "volume", "rm", *acc], check=False)
        print(f"Removed {len(services)} acc-* service(s) and {len(acc)} acc volume(s).")
        return 0
    if not all(state == "running" for state in dev_states(["commerce-db", "control-db"]).values()):
        print(
            "Start the development stack first (pnpm stack:up); acceptance verifies it is untouched."
        )
        return 2

    dev_tenants_before = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "commerce-db",
            "psql",
            "-U",
            "matecart_commerce",
            "-d",
            "matecart_commerce",
            "-tAc",
            "select count(*) from tenancy_tenant",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # ---- Test A: production images
    build_variant_images()
    for image in ("commerce-api", "control-api", "commerce-web", "platform-console"):
        ref = f"matecart-dev/{image}:acceptance-1"
        user = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Config.User}}", ref],
            capture_output=True,
            text=True,
        ).stdout.strip()
        check(f"A image {image} runs as non-root user", user not in {"", "root", "0"}, user)
        found = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "sh",
                ref,
                "-c",
                "find / -xdev \\( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \\) "
                "-not -path '/proc/*' -not -path '/usr/*' -not -path '/etc/ssl/*' 2>/dev/null",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
        check(f"A image {image} contains no secret files", found == "", found)
    entry = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "matecart-dev/commerce-api:acceptance-1",
            "shell",
        ],
        capture_output=True,
        text=True,
    )
    check("A entrypoint refuses unknown roles", entry.returncode == 64, entry.stderr.strip())

    # ---- environment
    for folder in ("proxy/sites", "proxy/data", "agent", "backups", "stores"):
        (ACC / folder).mkdir(parents=True, exist_ok=True)
    write(ACC / "proxy" / "Caddyfile", render.caddyfile(""))
    control_env()
    up = compose("up", "--detach", "--wait", "acc-control-db", "acc-control-redis", "acc-proxy")
    check(
        "environment control db/redis and proxy healthy",
        up.returncode == 0,
        up.stderr.decode()[-200:],
    )
    migrated = compose("run", "--rm", "--no-deps", "acc-control-api", "migrate", timeout=900)
    check(
        "A control-api migrate entrypoint",
        migrated.returncode == 0,
        migrated.stderr.decode()[-200:],
    )
    up = compose("up", "--detach", "--wait", "acc-control-api")
    check("A control-api production server healthy", up.returncode == 0, up.stderr.decode()[-200:])
    fixture = shell("acc-control-api", CONTROL_FIXTURE)

    # ---- Test D: enrollment and signed protocol over real HTTP
    config = agent_config()
    config_path = ACC / "agent" / "agent.json"
    client = ControlClient(config)
    try:
        client.enroll("x" * 43, {})
        check("D invalid enrollment refused", False)
    except ControlRejected as exc:
        check("D invalid enrollment refused", exc.status == 401, str(exc.status), "real-http")
    enrolled = client.enroll(fixture["token"], {"hostname": "acceptance"})
    config.agent_id, config.server_id = str(enrolled["agent_id"]), str(enrolled["server_id"])
    config.key_id, config.secret = str(enrolled["key_id"]), str(enrolled["secret"])
    save(config, config_path)
    check("D authorized enrollment", bool(config.key_id), config.agent_id, "real-http")
    try:
        client.enroll(fixture["token"], {})
        check("D enrollment token is single-use", False)
    except ControlRejected as exc:
        check("D enrollment token is single-use", exc.status == 401, str(exc.status), "real-http")
    runner = SubprocessRunner()
    agent = Agent(config, config_path=config_path, runner=runner, client=ControlClient(config))
    beat = agent.heartbeat()
    handles = sorted(item["store_handle"] for item in beat["deployments"])
    check(
        "D signed heartbeat lists only this Server's Deployments",
        handles == sorted(STORES),
        ",".join(handles),
        "real-http",
    )

    def context() -> operations.Context:
        return operations.Context(
            config,
            runner,
            lambda stage, msg: print(f"      {stage}: {msg}"),
            images_override=local_images,
        )

    stores = fixture["stores"]
    v100 = local_release("1.0.0", "1" * 64)
    # ---- Test B / C: install two isolated Stores through the Agent's release operation
    for handle in STORES:
        outcome = operations.execute(
            context(), release_command("deploy_release", stores[handle], v100)
        )
        check(
            f"B install {handle} (migrate, provision, start, health)",
            outcome.succeeded,
            outcome.message,
        )
    for handle, info in STORES.items():
        short, store = info["short"], stores[handle]
        tenants = compose(
            "exec",
            "-T",
            f"acc-{short}-db",
            "sh",
            "-c",
            'psql -U "$MATECART_DB_OWNER" -d "$POSTGRES_DB" -tAc "select id from tenancy_tenant"',
        )
        rows = tenants.stdout.decode().split()
        check(
            f"B {handle} tenant is the Console Store ID only",
            rows == [store["store_id"]],
            ",".join(rows),
        )
        ddl = compose(
            "exec",
            "-T",
            f"acc-{short}-db",
            "sh",
            "-c",
            'PGPASSWORD="$MATECART_DB_APP_PASSWORD" psql -h 127.0.0.1 -U "$MATECART_DB_APP_ROLE" '
            '-d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "create table acceptance_probe (id int)"',
        )
        check(
            f"D {handle} runtime role cannot run DDL",
            ddl.returncode != 0,
            ddl.stderr.decode()[-120:],
        )
        dml = compose(
            "exec",
            "-T",
            f"acc-{short}-db",
            "sh",
            "-c",
            'PGPASSWORD="$MATECART_DB_APP_PASSWORD" psql -h 127.0.0.1 -U "$MATECART_DB_APP_ROLE" '
            '-d "$POSTGRES_DB" -tAc "select count(*) from tenancy_tenant"',
        )
        check(f"D {handle} runtime role can read data", dml.stdout.decode().strip() == "1")
        status, body = https(info["domain"], "/api/storefront/config/")
        check(
            f"G {handle} API via HTTPS proxy",
            status == 200 and info["name"] in body,
            f"{status}",
            "container",
        )
        status, body = https(info["domain"], "/")
        check(
            f"B {handle} storefront via HTTPS proxy",
            status == 200 and info["name"] in body,
            str(status),
        )
        ws, _ = https(info["domain"], "/api/chat/ws/", ws=True, origin=f"https://{info['domain']}")
        # 101 = handshake completed by the Store's ASGI server; 403 = refused by the Store's own
        # consumer policy. Either proves the upgrade crossed the proxy (a proxy failure is 502).
        check(f"G {handle} WebSocket upgrade reaches the Store", ws in {101, 403}, str(ws))
        rows = ps_rows(f"acc-{short}-worker")
        health = str(rows[0].get("Health", "")) if rows else ""
        check(f"B {handle} worker healthy", health == "healthy", health)
    a_env = render.read_env(ACC / "stores" / "acc-store-a" / "app.env")
    b_env = render.read_env(ACC / "stores" / "acc-store-b" / "app.env")
    check(
        "C secrets differ between Stores",
        all(
            a_env[key] != b_env[key]
            for key in ("DJANGO_SECRET_KEY", "MATECART_ENCRYPTION_KEY", "DATABASE_URL")
        ),
    )
    cross = compose(
        "exec",
        "-T",
        "acc-a-api",
        "python",
        "-c",
        "import socket\nfor n in ('acc-b-db','acc-b-redis','store-acc-store-b-api','acc-b-api'):\n"
        "    try:\n        socket.gethostbyname(n); print('REACHABLE', n)\n"
        "    except OSError: pass",
    )
    check(
        "C Store A cannot resolve Store B services",
        "REACHABLE" not in cross.stdout.decode(),
        cross.stdout.decode().strip(),
    )
    unknown = http_status("unknown.matecart.test")
    check("G unknown Host header is refused", unknown == 421, str(unknown))
    try:
        https("unknown.matecart.test", "/")
        check("G unknown TLS name gets no certificate", False)
    except (ssl.SSLError, OSError) as exc:
        check("G unknown TLS name gets no certificate", True, type(exc).__name__)
    redirect = http_status(STORES["acc-store-a"]["domain"])
    check(
        "G HTTP redirects to HTTPS for a configured domain", redirect in {301, 308}, str(redirect)
    )

    # ---- Test G: TLS observation and monitoring over the real protocol
    target = next(item for item in beat["deployments"] if item["store_handle"] == "acc-store-a")
    report = agent.observe(target)
    domains = (report or {}).get("domains", [])
    check(
        "G TLS observed valid (Caddy internal test CA)",
        bool(domains) and domains[0]["tls_state"] == "valid",
        json.dumps(domains)[:150],
        "simulated-ca",
    )
    check(
        "E observation reports healthy services and version",
        bool(report)
        and report["api_status"] == "healthy"
        and report["application_version"] == "1.0.0",
        json.dumps(
            {
                k: (report or {}).get(k)
                for k in (
                    "store_status",
                    "api_status",
                    "worker_status",
                    "database_status",
                    "application_version",
                )
            }
        ),
    )
    seen = shell(
        "acc-control-api",
        "import json\nfrom control.platform.models import Deployment, DomainRecord\n"
        f"d = Deployment.objects.get(pk='{stores['acc-store-a']['deployment_id']}')\n"
        "print(json.dumps({'version': d.observed_version, 'observations': d.observations.count()}))",
    )
    check(
        "E Control recorded the Agent observation",
        seen["version"] == "1.0.0",
        json.dumps(seen),
        "real-http",
    )

    # ---- Test F: updates and rollback (Store A)
    a = stores["acc-store-a"]
    v110 = local_release("1.1.0", "2" * 64)  # schema fingerprint changes
    outcome = operations.execute(context(), release_command("update_release", a, v110))
    check("F compatible update to 1.1.0", outcome.succeeded, outcome.message)
    image = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.version"}}',
            "matecart-dev-acc-a-api-1",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    check("F observed running image version", image == "1.1.0-acceptance", image)
    v120 = local_release("1.2.0", "2" * 64)  # broken image, same schema
    outcome = operations.execute(context(), release_command("update_release", a, v120))
    check(
        "F failed update recovers the previous release",
        (not outcome.succeeded) and outcome.evidence.get("rolled_back") is True,
        outcome.failure_category,
    )
    status, _ = https(STORES["acc-store-a"]["domain"], "/api/health/ready/")
    check("F Store serving after recovery", status == 200, str(status))
    refused = operations.execute(context(), release_command("rollback_release", a, v100))
    check(
        "F unsafe rollback (schema changed) refused",
        refused.failure_category == "rollback_refused",
        refused.message,
    )
    v111 = local_release("1.1.1", "2" * 64)
    outcome = operations.execute(context(), release_command("update_release", a, v111))
    rollback = operations.execute(context(), release_command("rollback_release", a, v110))
    check(
        "F compatible rollback 1.1.1 -> 1.1.0",
        outcome.succeeded and rollback.succeeded,
        rollback.message,
    )

    # ---- Test H: backups and a disposable restore
    outcome = operations.execute(
        context(), command("run_backup", a, policy={"include_media": True, "retention_days": 14})
    )
    check("H backup completed", outcome.succeeded, outcome.message)
    latest = backup.latest(ACC / "backups", "acc-store-a")
    check(
        "H backup integrity manifest verifies",
        latest is not None and backup.verify(latest),
        str(latest),
    )
    check(
        "H backups are isolated per Store",
        latest is not None and latest.parent.name == "acc-store-a",
    )
    delivered = agent._deliver("backup", "/api/agent/v1/backups/", outcome.evidence["backup"])
    check("H backup status sent to Control", delivered, "", "real-http")
    compose("up", "--detach", "--wait", "acc-restore-db")
    dump = (latest / "database.dump").read_bytes() if latest else b""
    restored = compose(
        "exec",
        "-T",
        "acc-restore-db",
        "pg_restore",
        "-U",
        "restore_check",
        "-d",
        "restore_check",
        "--no-owner",
        "--no-privileges",
        stdin=dump,
        timeout=900,
    )
    count = (
        compose(
            "exec",
            "-T",
            "acc-restore-db",
            "psql",
            "-U",
            "restore_check",
            "-d",
            "restore_check",
            "-tAc",
            "select id from tenancy_tenant",
        )
        .stdout.decode()
        .split()
    )
    check(
        "H disposable restore contains Store A only",
        count == [a["store_id"]],
        f"rc={restored.returncode} {','.join(count)}",
    )
    compose("rm", "--stop", "--force", "acc-restore-db")

    # ---- Test D/E: disconnection, revocation and reconnection over real HTTP
    compose("stop", "acc-control-api")
    agent.tick()  # must not raise: Stores keep running, reports are buffered
    status, _ = https(STORES["acc-store-b"]["domain"], "/api/health/ready/")
    check("D Stores keep serving while Control is down", status == 200, str(status))
    check(
        "D reports buffered while disconnected",
        len(agent.journal.outbox()) > 0,
        str(len(agent.journal.outbox())),
    )
    compose("up", "--detach", "--wait", "acc-control-api")
    sent = agent.flush_outbox()
    check(
        "D reconnection flushes buffered reports",
        sent > 0 and agent.journal.outbox() == [],
        str(sent),
        "real-http",
    )
    shell(
        "acc-control-api",
        "import json\nfrom control.accounts.models import AdminUser\n"
        "from control.platform.models import DeploymentAgent\nfrom control.platform.deployment import revoke_agent\n"
        f"a = DeploymentAgent.objects.get(key_id='{config.key_id}')\n"
        "revoke_agent(a, AdminUser.objects.get(email='acceptance-owner@acceptance.test'))\nprint('{}')",
    )
    try:
        agent.heartbeat()
        check("D revoked credential refused", False)
    except ControlRejected as exc:
        check("D revoked credential refused", exc.status == 401, str(exc.status), "real-http")
    token = shell(
        "acc-control-api",
        "import json\nfrom control.accounts.models import AdminUser\nfrom control.platform.models import Server\n"
        "from control.platform.deployment import issue_enrollment\n"
        f"_, t = issue_enrollment(Server.objects.get(pk='{fixture['server_id']}'), "
        "AdminUser.objects.get(email='acceptance-owner@acceptance.test'))\nprint(json.dumps(t))",
    )
    again = ControlClient(config).enroll(token, {})
    config.key_id, config.secret = str(again["key_id"]), str(again["secret"])
    agent.client = ControlClient(config)
    check(
        "D re-enrolled Agent reconnects",
        bool(agent.heartbeat().get("deployments")),
        "",
        "real-http",
    )

    dev_tenants_after = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "commerce-db",
            "psql",
            "-U",
            "matecart_commerce",
            "-d",
            "matecart_commerce",
            "-tAc",
            "select count(*) from tenancy_tenant",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.strip()
    check(
        "H owner's development database unchanged",
        dev_tenants_before.isdigit() and dev_tenants_before == dev_tenants_after,
        f"tenants {dev_tenants_before or '?'} -> {dev_tenants_after or '?'}",
    )
    states = dev_states(["commerce-db", "commerce-api", "control-db", "control-api"])
    check(
        "H development stack containers untouched",
        all(state == "running" for state in states.values()),
        json.dumps(states),
    )

    passed = sum(1 for item in results if item["ok"])
    write(
        ACC / "report.json",
        json.dumps({"passed": passed, "total": len(results), "results": results}, indent=2),
    )
    print(f"\nAcceptance: {passed}/{len(results)} checks passed (report: .acceptance/report.json)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ControlUnavailable as exc:
        print(f"Control unavailable: {exc}")
        raise SystemExit(1) from exc
