"""The Agent loop: heartbeat, command receipt and execution, observations, local backups.

Console independence (D-119): Stores keep running whatever happens here. When Control is
unreachable the Agent keeps observing and running scheduled backups, buffers reports, and never
starts a command it could not claim, so no success is ever reported without Control's knowledge.
"""

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import backup, host, operations, render, validation
from .config import AgentConfig, save
from .control import ControlClient, ControlRejected, ControlUnavailable
from .journal import Journal
from .runner import Runner
from .signing import canonical_json, sha256_hex

logger = logging.getLogger("matecart_agent")


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        config_path: Path | None,
        runner: Runner,
        client: ControlClient | None = None,
        context_factory: Any = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.runner = runner
        self.client = client or ControlClient(config)
        self.journal = Journal(Path(config.state_dir))
        self.deployments: list[dict[str, Any]] = []
        self.context_factory = context_factory or (
            lambda progress: operations.Context(config, runner, progress)
        )
        self.recovered = False

    # ---------------------------------------------------------------- transport helpers
    def _deliver(self, kind: str, path: str, body: dict[str, Any]) -> bool:
        """Send now, or keep in the outbox when Control is unreachable."""
        try:
            self.client.post(path, body)
            return True
        except ControlUnavailable:
            self.journal.enqueue(kind, path, body)
            return False
        except ControlRejected as exc:
            if exc.status in {401, 403}:
                raise
            logger.warning("Control rejected a %s report: %s", kind, exc)
            return False

    def flush_outbox(self) -> int:
        sent = 0
        for item in self.journal.outbox():
            try:
                self.client.post(item["path"], item["body"])
            except ControlUnavailable:
                break
            except ControlRejected as exc:
                if exc.status in {401, 403}:
                    raise
                logger.warning("Dropping a rejected buffered %s report: %s", item["kind"], exc)
            self.journal.drop_first()
            sent += 1
        return sent

    def _persist_config(self) -> None:
        if self.config_path is not None:
            save(self.config, self.config_path)

    # ---------------------------------------------------------------- loop
    def heartbeat(self) -> dict[str, Any]:
        response = self.client.post(
            "/api/agent/v1/heartbeat/",
            {
                "agent_version": host.facts(self.config.root).get("agent_version"),
                "host_facts": host.facts(self.config.root),
                "buffered_reports": len(self.journal.outbox()),
            },
        )
        self._persist_config()
        data = response.data if isinstance(response.data, dict) else {}
        self.deployments = [item for item in data.get("deployments", []) if isinstance(item, dict)]
        if data.get("rotate") and not self.config.pending_secret:
            rotated = self.client.post("/api/agent/v1/rotate/")
            if isinstance(rotated.data, dict) and rotated.data.get("secret"):
                self.config.pending_secret = str(rotated.data["secret"])
                self._persist_config()
        return data

    def tick(self) -> None:
        try:
            self.flush_outbox()
            self.heartbeat()
            if not self.recovered:
                self.recover()
                self.recovered = True
            self.poll_command()
        except ControlUnavailable as exc:
            logger.warning("Control unavailable; Stores keep running: %s", exc)
        self.observe_all()
        self.scheduled_backups()

    def run_forever(self, interval: float = 30) -> None:
        while True:
            started = time.monotonic()
            try:
                self.tick()
            except ControlRejected as exc:
                if exc.status in {401, 403}:
                    logger.error("Agent credential refused (revoked or expired); idling: %s", exc)
                    time.sleep(300)
                    continue
                logger.exception("Agent tick failed")
            except Exception:
                logger.exception("Agent tick failed")
            time.sleep(max(1.0, interval - (time.monotonic() - started)))

    # ---------------------------------------------------------------- commands
    def _managed(self, deployment_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.deployments if str(item.get("deployment_id")) == deployment_id),
            None,
        )

    def _event(self, command_id: str, sequence: int, data: dict[str, Any]) -> None:
        self._deliver(
            "event", f"/api/agent/v1/commands/{command_id}/events/", {"sequence": sequence, **data}
        )

    def poll_command(self) -> str | None:
        response = self.client.post("/api/agent/v1/commands/lease/")
        envelope = response.data.get("command") if isinstance(response.data, dict) else None
        if not isinstance(envelope, dict):
            return None
        return self.handle(envelope)

    def handle(self, envelope: dict[str, Any]) -> str:
        command_id = validation.identifier(envelope.get("command_id"))
        payload = envelope.get("payload")
        previous = self.journal.command(command_id)
        if previous is not None:
            # Duplicate delivery: never execute twice; re-send the recorded outcome if finished.
            if previous.get("state") == "finished":
                self._event(command_id, int(previous["sequence"]), previous["result"])
            return "duplicate"
        sequence = int(envelope.get("last_event_sequence", 0)) + 1
        problem = ""
        if not isinstance(payload, dict) or str(payload.get("command_id")) != command_id:
            problem = "The command payload is malformed."
        elif sha256_hex(canonical_json(payload)) != envelope.get("payload_sha256"):
            problem = "The command payload digest does not match."
        elif self._managed(str(payload.get("deployment_id"))) is None:
            problem = "The command targets a Deployment this Agent does not manage."
        else:
            expires = datetime.fromisoformat(str(payload.get("expires_at")))
            if expires <= datetime.now(UTC):
                problem = "The command expired before it started."
        if problem or not isinstance(payload, dict):
            # Claim then fail, so Control records the refusal instead of re-delivering forever.
            self.client.post(
                f"/api/agent/v1/commands/{command_id}/events/",
                {"sequence": sequence, "event": "started"},
            )
            self._event(
                command_id,
                sequence + 1,
                {
                    "event": "failed",
                    "stage": "validate",
                    "failure_category": "invalid_command",
                    "message": problem,
                },
            )
            return "refused"
        handle = validation.handle(payload.get("store_handle"))
        # Claim before acting: if Control cannot confirm, nothing runs.
        self.client.post(
            f"/api/agent/v1/commands/{command_id}/events/",
            {"sequence": sequence, "event": "started"},
        )
        self.journal.start(command_id, str(payload["kind"]), handle, sequence)
        counter = {"sequence": sequence}

        def progress(stage: str, message: str) -> None:
            counter["sequence"] += 1
            self.journal.stage(command_id, stage, counter["sequence"])
            self._event(
                command_id,
                counter["sequence"],
                {"event": "progress", "stage": stage, "message": message},
            )

        outcome = operations.execute(self.context_factory(progress), payload)
        final = outcome.as_event()
        counter["sequence"] += 1
        self.journal.finish(command_id, final, counter["sequence"])
        if outcome.succeeded and payload.get("kind") == "apply_configuration":
            self.journal.set_store_state(
                handle, configuration_revision=outcome.evidence.get("configuration_revision")
            )
        self._event(command_id, counter["sequence"], final)
        backup_report = outcome.evidence.get("backup")
        if isinstance(backup_report, dict) and backup_report.get("report_id"):
            self._deliver("backup", "/api/agent/v1/backups/", backup_report)
        # Fresh evidence for Control's independent verification.
        self.observe(self._managed(str(payload["deployment_id"])) or {})
        return "succeeded" if outcome.succeeded else "failed"

    def recover(self) -> None:
        for command_id, entry in self.journal.unfinished():
            context = self.context_factory(lambda stage, message: None)
            outcome = operations.recover_interrupted(
                context, str(entry["handle"]), str(entry["kind"])
            )
            sequence = int(entry.get("sequence", 1)) + 1
            final = outcome.as_event()
            self.journal.finish(command_id, final, sequence)
            self._event(command_id, sequence, final)

    # ---------------------------------------------------------------- observations
    def observe_all(self) -> None:
        for deployment in self.deployments:
            try:
                self.observe(deployment)
            except Exception:
                logger.exception("Observation failed for %s", deployment.get("deployment_id"))

    def observe(self, deployment: dict[str, Any]) -> dict[str, Any] | None:
        handle = str(deployment.get("store_handle", ""))
        try:
            store_dir = self.config.store_dir(handle)
        except validation.ValidationError:
            return None
        if not (store_dir / "store.json").exists():
            return None  # not installed here yet: report nothing rather than "unavailable"
        compose = self.config.compose_for(handle, render.read_env(store_dir / "deploy.env"))
        rows = {row["Logical"]: row for row in compose.ps(self.runner) if row.get("Logical")}

        def state(service: str) -> str:
            row = rows.get(service)
            if row is None or str(row.get("State", "")).lower() != "running":
                return "unavailable"
            health = str(row.get("Health", "")).lower()
            return {"healthy": "healthy", "": "healthy", "starting": "degraded"}.get(
                health, "unavailable"
            )

        release_path = store_dir / "release.json"
        version = ""
        if release_path.exists():
            version = str(json.loads(release_path.read_text(encoding="utf-8")).get("version", ""))
        metrics = host.metrics(self.config.root)
        report: dict[str, Any] = {
            "deployment_id": str(deployment["deployment_id"]),
            "store_id": str(deployment["store_id"]),
            "report_id": str(uuid.uuid4()),
            "observed_at": datetime.now(UTC).isoformat(),
            "store_status": state("web"),
            "api_status": state("api"),
            "worker_status": state("worker"),
            "database_status": state("db"),
            "application_version": version,
            **{key: value for key, value in metrics.items() if value is not None},
        }
        revision = self.journal.store_state(handle).get("configuration_revision")
        if isinstance(revision, int):
            report["configuration_revision"] = revision
        domains = self._domains(store_dir)
        if domains:
            report["domains"] = domains
        self._deliver("observation", "/api/agent/v1/observations/", report)
        return report

    def _domains(self, store_dir: Path) -> list[dict[str, Any]]:
        site = self.config.proxy_dir / "sites" / f"{store_dir.name}.caddy"
        if not site.exists():
            return []
        header = site.read_text(encoding="utf-8").splitlines()
        names = next((line for line in header if line.endswith("{") and "." in line), "")
        cafile = None
        if "tls internal" in site.read_text(encoding="utf-8"):
            candidate = self.config.proxy_dir / "data/caddy/pki/authorities/local/root.crt"
            cafile = str(candidate) if candidate.exists() else None
        results = []
        for name in names.rstrip("{").replace(",", " ").split():
            results.append(
                host.certificate_state(name, port=self.config.proxy_https_port, cafile=cafile)
            )
        return results

    # ---------------------------------------------------------------- scheduled backups
    def scheduled_backups(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for deployment in self.deployments:
            policy = deployment.get("backup_policy")
            if not isinstance(policy, dict) or not policy.get("enabled"):
                continue
            handle = str(deployment.get("store_handle", ""))
            try:
                store_dir = self.config.store_dir(handle)
            except validation.ValidationError:
                continue
            if not (store_dir / "store.json").exists():
                continue
            due = current.replace(
                hour=int(policy["schedule_hour_utc"]),
                minute=int(policy["schedule_minute"]),
                second=0,
                microsecond=0,
            )
            last = self.journal.store_state(handle).get("last_scheduled_backup", "")
            if current < due or last == due.date().isoformat():
                continue
            compose = self.config.compose_for(handle, render.read_env(store_dir / "deploy.env"))
            result = backup.run_backup(
                self.runner,
                compose,
                backup_root=Path(self.config.backup_root),
                handle=handle,
                include_media=bool(policy.get("include_media", True)),
            )
            if result.status == "successful":
                backup.prune(
                    Path(self.config.backup_root), handle, int(policy.get("retention_days", 14))
                )
            self.journal.set_store_state(handle, last_scheduled_backup=due.date().isoformat())
            self._deliver(
                "backup", "/api/agent/v1/backups/", result.report(str(deployment["deployment_id"]))
            )
