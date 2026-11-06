"""Command line for the Agent and the Store installer.

    matecart-agent preflight [--root DIR] [--stores N]
    matecart-agent plan-store --store store.json --release release.json [--domain D ...]
    matecart-agent install-store --store ... --release ... --confirm-host HOSTNAME [--dry-run]
    matecart-agent enroll --config agent.json --control-url URL   (token read from stdin)
    matecart-agent run --config agent.json [--once]
    matecart-agent ssh-plan --host H --user U --host-key-file F --fingerprint SHA256:... \\
        --identity KEY --mode preflight|install [--execute]

``plan-store`` and ``install-store --dry-run`` never write files or start containers, and mask
every secret. ``install-store`` without ``--dry-run`` is for future authorized hosts only and
requires --confirm-host to equal this machine's hostname (explicit target identity).
"""

import argparse
import json
import logging
import socket
import sys
from pathlib import Path
from typing import Any

from . import VERSION, host, manifest, operations, preflight, render, ssh, validation
from .agent import Agent
from .config import AgentConfig, load, save
from .control import ControlClient, ControlRejected, ControlUnavailable
from .runner import SubprocessRunner


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _print(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")


def cmd_preflight(args: argparse.Namespace) -> int:
    report = preflight.run(SubprocessRunner(), root=args.root, stores_planned=args.stores)
    _print(report.as_dict())
    return 0 if report.ok else 2


def _store_plan(args: argparse.Namespace, *, dry_run: bool) -> tuple[AgentConfig, render.Plan]:
    config = AgentConfig(control_url="https://control.invalid", root=args.root)
    release = manifest.validate(_json(args.release))
    store = _json(args.store)
    domains = [{"domain": item, "tls_mode": args.tls_mode} for item in args.domain]
    spec = render.StoreSpec.from_payload(store, domains)
    plan = render.plan_store(
        config,
        spec,
        api_image=manifest.image(release, "commerce-api"),
        web_image=manifest.image(release, "commerce-web"),
        dry_run=dry_run,
    )
    return config, plan


def _masked(plan: render.Plan) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for name, content in plan.files.items():
        if name in render.PUBLIC_FILES or name.startswith("postgres-init/"):
            files[name] = f"{len(content)} bytes"
        else:
            keys = [line.split("=", 1)[0] for line in content.splitlines() if "=" in line]
            files[name] = {"mode": "0600", "keys": keys}
    return {"store_dir": str(plan.store_dir), "actions": plan.actions, "files": files}


def cmd_plan_store(args: argparse.Namespace) -> int:
    _, plan = _store_plan(args, dry_run=True)
    _print({"dry_run": True, **_masked(plan)})
    return 0


def cmd_install_store(args: argparse.Namespace) -> int:
    if args.confirm_host != socket.gethostname():
        sys.stderr.write("--confirm-host does not match this machine; refusing to install.\n")
        return 2
    report = preflight.run(SubprocessRunner(), root=args.root)
    if not report.ok:
        _print(report.as_dict())
        return 2
    config, plan = _store_plan(args, dry_run=args.dry_run)
    if args.dry_run:
        _print({"dry_run": True, **_masked(plan)})
        return 0
    render.write_plan(plan)
    _print({"written": str(plan.store_dir), "next": "deploy through an approved Console request"})
    del config
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    token = sys.stdin.readline().strip()
    config = AgentConfig(
        control_url=args.control_url,
        root=args.root,
        state_dir=args.state_dir,
        backup_root=args.backup_root,
        allow_insecure_http=args.allow_insecure_http,
    ).validate()
    try:
        data = ControlClient(config).enroll(token, host.facts(config.root))
    except (ControlRejected, ControlUnavailable) as exc:
        sys.stderr.write(f"Enrollment failed: {exc}\n")
        return 1
    config.agent_id = str(data["agent_id"])
    config.server_id = str(data["server_id"])
    config.key_id = str(data["key_id"])
    config.secret = str(data["secret"])
    save(config, args.config)
    _print({"enrolled": True, "agent_id": config.agent_id, "server_id": config.server_id})
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load(args.config)
    agent = Agent(config, config_path=args.config, runner=SubprocessRunner())
    if args.once:
        agent.tick()
        return 0
    agent.run_forever(args.interval)
    return 0


def cmd_ssh_plan(args: argparse.Namespace) -> int:
    target = ssh.Target(
        host=args.host,
        user=args.user,
        port=args.port,
        host_key=args.host_key_file.read_text(encoding="utf-8").strip(),
        expected_fingerprint=args.fingerprint,
    ).validate()
    known_hosts = Path(args.known_hosts)
    argv = ssh.ssh_argv(target, known_hosts=known_hosts, identity=args.identity, mode=args.mode)
    if not args.execute:
        _print({"execute": False, "known_hosts_line": ssh.known_hosts_line(target), "argv": argv})
        return 0
    known_hosts.write_text(ssh.known_hosts_line(target), encoding="utf-8")
    known_hosts.chmod(0o600)
    payload = ssh.bundle(Path(__file__).resolve().parent.parent)
    result = SubprocessRunner().run(argv, stdin=payload, timeout=1800)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="matecart-agent", description=f"MateCart Agent {VERSION}")
    sub = root.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--root", default="/opt/matecart")
    pre.add_argument("--stores", type=int, default=1)
    pre.set_defaults(func=cmd_preflight)

    for name, func in (("plan-store", cmd_plan_store), ("install-store", cmd_install_store)):
        item = sub.add_parser(name)
        item.add_argument("--store", type=Path, required=True)
        item.add_argument("--release", type=Path, required=True)
        item.add_argument("--domain", action="append", default=[])
        item.add_argument("--tls-mode", choices=["acme", "internal"], default="acme")
        item.add_argument("--root", default="/opt/matecart")
        if name == "install-store":
            item.add_argument("--confirm-host", required=True)
            item.add_argument("--dry-run", action="store_true")
        item.set_defaults(func=func)

    enroll = sub.add_parser("enroll")
    enroll.add_argument("--config", type=Path, default=Path("/etc/matecart/agent.json"))
    enroll.add_argument("--control-url", required=True)
    enroll.add_argument("--root", default="/opt/matecart")
    enroll.add_argument("--state-dir", default="/var/lib/matecart/agent")
    enroll.add_argument("--backup-root", default="/var/lib/matecart/backups")
    enroll.add_argument("--allow-insecure-http", action="store_true")
    enroll.set_defaults(func=cmd_enroll)

    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, default=Path("/etc/matecart/agent.json"))
    run.add_argument("--once", action="store_true")
    run.add_argument("--interval", type=float, default=30)
    run.set_defaults(func=cmd_run)

    plan = sub.add_parser("ssh-plan")
    plan.add_argument("--host", required=True)
    plan.add_argument("--user", required=True)
    plan.add_argument("--port", type=int, default=22)
    plan.add_argument("--host-key-file", type=Path, required=True)
    plan.add_argument("--fingerprint", required=True)
    plan.add_argument("--identity", type=Path, required=True)
    plan.add_argument("--known-hosts", default="known_hosts.matecart")
    plan.add_argument("--mode", choices=sorted(ssh.MODES), default="preflight")
    plan.add_argument("--execute", action="store_true")
    plan.set_defaults(func=cmd_ssh_plan)
    return root


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (validation.ValidationError, render.StoreConflict) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


__all__ = ["main", "operations"]
