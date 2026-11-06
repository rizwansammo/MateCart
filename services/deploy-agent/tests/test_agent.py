"""Agent loop: claims, duplicates, expiry, offline buffering and revocation."""

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from matecart_agent.agent import Agent
from matecart_agent.config import AgentConfig
from matecart_agent.control import ControlRejected, ControlUnavailable, Response
from matecart_agent.runner import FakeRunner
from matecart_agent.signing import canonical_json, sha256_hex

from .conftest import DEPLOYMENT_ID, STORE_ID, payload


@dataclass
class FakeControl:
    commands: list[dict[str, Any]] = field(default_factory=list)
    posts: list[tuple[str, Any]] = field(default_factory=list)
    offline: bool = False
    revoked: bool = False
    reject_start: bool = False

    def post(self, path: str, data: Any = None) -> Response:
        if self.offline:
            raise ControlUnavailable("network down")
        if self.revoked:
            raise ControlRejected(401, "revoked")
        if self.reject_start and path.endswith("/events/") and data.get("event") == "started":
            raise ControlRejected(400, "expired")
        self.posts.append((path, data))
        if path.endswith("heartbeat/"):
            return Response(
                200,
                {
                    "deployments": [
                        {
                            "deployment_id": DEPLOYMENT_ID,
                            "store_id": STORE_ID,
                            "store_handle": "river-shop",
                            "backup_policy": None,
                        }
                    ]
                },
            )
        if path.endswith("lease/"):
            return Response(200, {"command": self.commands.pop(0) if self.commands else None})
        return Response(200, {})


def envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "command_id": data["command_id"],
        "kind": data["kind"],
        "payload": data,
        "payload_sha256": sha256_hex(canonical_json(data)),
        "last_event_sequence": 0,
    }


def make_agent(config: AgentConfig, runner: FakeRunner, control: FakeControl) -> Agent:
    agent = Agent(config, config_path=None, runner=runner, client=control)  # type: ignore[arg-type]
    agent.heartbeat()
    return agent


def events(control: FakeControl) -> list[str]:
    return [data["event"] for path, data in control.posts if path.endswith("/events/")]


def test_command_is_claimed_before_execution_and_never_run_twice(
    config: AgentConfig, runner: FakeRunner
) -> None:
    control = FakeControl()
    agent = make_agent(config, runner, control)
    command = envelope(payload("run_backup", policy={"include_media": False}))
    # run_backup needs an installed Store; it fails cleanly, still claimed first.
    assert agent.handle(command) == "failed"
    assert events(control)[0] == "started"
    calls = len(runner.calls)
    assert agent.handle(command) == "duplicate"
    assert len(runner.calls) == calls


def test_unclaimable_command_does_not_run(config: AgentConfig, runner: FakeRunner) -> None:
    control = FakeControl(reject_start=True)
    agent = make_agent(config, runner, control)
    with pytest.raises(ControlRejected):
        agent.handle(envelope(payload("run_backup")))
    assert runner.calls == []


def test_tampered_expired_or_foreign_commands_are_refused(
    config: AgentConfig, runner: FakeRunner
) -> None:
    control = FakeControl()
    agent = make_agent(config, runner, control)
    tampered = envelope(payload("run_backup"))
    tampered["payload"]["kind"] = "deploy_release"
    assert agent.handle(tampered) == "refused"
    expired = envelope(payload("run_backup", expires_at="2000-01-01T00:00:00+00:00"))
    assert agent.handle(expired) == "refused"
    foreign = envelope(payload("run_backup", deployment_id=str(uuid.uuid4())))
    assert agent.handle(foreign) == "refused"
    assert runner.calls == []
    failures = [data for path, data in control.posts if data and data.get("event") == "failed"]
    assert all(item["failure_category"] == "invalid_command" for item in failures)


def test_offline_reports_are_buffered_and_flushed_in_order(
    config: AgentConfig, runner: FakeRunner
) -> None:
    control = FakeControl()
    agent = make_agent(config, runner, control)
    control.offline = True
    agent._deliver("observation", "/api/agent/v1/observations/", {"n": 1})
    agent._deliver("event", "/api/agent/v1/commands/x/events/", {"n": 2})
    agent.tick()  # Control down: no exception, Stores untouched, reports kept
    assert len(agent.journal.outbox()) == 2
    control.offline = False
    assert agent.flush_outbox() == 2
    assert [data["n"] for _, data in control.posts[-2:]] == [1, 2]
    assert agent.journal.outbox() == []


def test_revoked_credentials_stop_command_processing(
    config: AgentConfig, runner: FakeRunner
) -> None:
    control = FakeControl()
    agent = make_agent(config, runner, control)
    control.revoked = True
    with pytest.raises(ControlRejected):
        agent.tick()
    assert runner.calls == []


def test_interrupted_commands_are_reported_after_restart(
    config: AgentConfig, runner: FakeRunner
) -> None:
    control = FakeControl()
    agent = make_agent(config, runner, control)
    agent.journal.start("cmd-1", "run_backup", "river-shop", 1)
    restarted = make_agent(config, runner, control)
    restarted.recover()
    final = [data for path, data in control.posts if path.endswith("cmd-1/events/")]
    assert final[-1]["event"] == "failed"
    assert final[-1]["failure_category"] == "interrupted"
    assert restarted.journal.unfinished() == []
