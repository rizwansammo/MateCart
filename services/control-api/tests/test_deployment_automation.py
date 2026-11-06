"""Phase 17: releases, Agent enrollment and signing, command lifecycle and verification."""

import copy
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from control.accounts.models import AdminRole
from control.platform.deployment import (
    canonical_json,
    reconcile_commands,
    rollback_allowed,
    signature_base,
    validate_manifest,
    verify_domain,
)
from control.platform.models import (
    AgentCommand,
    BackupObservation,
    ConsoleAuditEvent,
    Deployment,
    DeploymentAgent,
    DomainRecord,
    OperationalAlert,
    OperationalRequest,
    ReleaseRecord,
)

from .test_platform_integrity import infrastructure, signed_in

pytestmark = pytest.mark.django_db

# Cross-implementation vector: services/deploy-agent/tests/test_signing.py asserts the same value.
VECTOR_SECRET = "a" * 64
VECTOR_BODY = b'{"hello":"agent"}'
VECTOR_SIGNATURE = "520ac1074c63f0340cce3c335e6d2ade304ee18108f363ad0d93c6f7d001c710"


def manifest(version: str = "1.0.0", fingerprint: str = "1" * 64, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "matecart.release/v1",
        "version": version,
        "git_commit": "f" * 40,
        "created_at": "2026-11-06T00:00:00Z",
        "images": {
            name: {
                "repository": f"ghcr.io/rizwansammo/matecart-{name}",
                "digest": "sha256:" + hashlib.sha256(f"{name}{version}".encode()).hexdigest(),
                "tag": version,
            }
            for name in ("commerce-api", "commerce-web", "control-api", "platform-console")
        },
        "database": {
            "commerce": {"migration_fingerprint": fingerprint, "rollback_compatible_with": []},
            "control": {"migration_fingerprint": "2" * 64, "rollback_compatible_with": []},
        },
    }
    data.update(extra)
    return data


class AgentClient:
    """Signs requests exactly like the Deployment Agent (matecart_agent.signing)."""

    def __init__(self, key_id: str, secret: str) -> None:
        self.key_id = key_id
        self.secret = secret
        self.client = APIClient()

    def post(
        self,
        path: str,
        data: Any = None,
        *,
        nonce: str | None = None,
        timestamp: int | None = None,
    ) -> Any:
        body = json.dumps(data or {}).encode()
        stamp = str(timestamp if timestamp is not None else int(time.time()))
        used_nonce = nonce or secrets.token_hex(16)
        message = signature_base("POST", path, stamp, used_nonce, body)
        signature = hmac.new(self.secret.encode(), message, hashlib.sha256).hexdigest()
        response = self.client.post(
            path,
            body,
            content_type="application/json",
            HTTP_X_MATECART_AGENT_KEY=self.key_id,
            HTTP_X_MATECART_TIMESTAMP=stamp,
            HTTP_X_MATECART_NONCE=used_nonce,
            HTTP_X_MATECART_SIGNATURE=signature,
        )
        response.nonce = used_nonce  # type: ignore[attr-defined]
        return response

    def verify(self, response: Any) -> bool:
        digest = hashlib.sha256(response.content).hexdigest()
        message = "\n".join(["response", response.nonce, str(response.status_code), digest])
        expected = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, response.get("X-MateCart-Signature", ""))


def enrolled(deployment: Deployment, owner_api: APIClient | None = None) -> AgentClient:
    api = owner_api or signed_in()[0]
    issued = api.post(f"/api/console/servers/{deployment.server_id}/agent/", {}, format="json")
    assert issued.status_code == 201, issued.data
    response = APIClient().post(
        "/api/agent/v1/enroll/",
        {"token": issued.data["token"], "agent_version": "1.0.0", "host_facts": {"os": "Ubuntu"}},
        format="json",
    )
    assert response.status_code == 201, response.content
    body = json.loads(response.content)
    return AgentClient(body["key_id"], body["secret"])


def register(api: APIClient, version: str, **kwargs: Any) -> None:
    response = api.post(
        "/api/console/releases/", {"manifest": manifest(version, **kwargs)}, format="json"
    )
    assert response.status_code == 201, response.data


def observe(
    agent: AgentClient,
    deployment: Deployment,
    *,
    version: str,
    status: str = "healthy",
    when: Any = None,
    **extra: Any,
) -> Any:
    return agent.post(
        "/api/agent/v1/observations/",
        {
            "deployment_id": str(deployment.pk),
            "store_id": str(deployment.store_id),
            "report_id": str(uuid.uuid4()),
            "observed_at": (when or timezone.now()).isoformat(),
            "store_status": status,
            "api_status": status,
            "worker_status": status,
            "database_status": status,
            "application_version": version,
            **extra,
        },
    )


def run_operation(
    requester: APIClient,
    approver: APIClient,
    deployment: Deployment,
    kind: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
    created = requester.post(
        "/api/console/operations/",
        {
            "kind": kind,
            "title": f"{kind} for review",
            "status": "submitted",
            "deployment": str(deployment.pk),
            "parameters": parameters or {},
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    url = f"/api/console/operations/{created.data['id']}/"
    assert approver.patch(url, {"status": "approved"}, format="json").status_code == 200
    return approver.patch(url, {"status": "pending_execution"}, format="json")


def execute(agent: AgentClient, *, version: str | None, succeed: bool = True) -> dict[str, Any]:
    leased = agent.post("/api/agent/v1/commands/lease/")
    assert leased.status_code == 200
    assert agent.verify(leased)
    command = json.loads(leased.content)["command"]
    base = f"/api/agent/v1/commands/{command['command_id']}/events/"
    assert agent.post(base, {"sequence": 1, "event": "started"}).status_code == 200
    assert (
        agent.post(base, {"sequence": 2, "event": "progress", "stage": "pull"}).status_code == 200
    )
    final: dict[str, Any] = {"sequence": 3, "event": "succeeded" if succeed else "failed"}
    if succeed:
        final["evidence"] = {"observed_version": version}
    else:
        final.update({"failure_category": "health_check_failed", "message": "API unhealthy"})
    assert agent.post(base, final).status_code == 200
    return dict(command)


def installed(version: str = "1.0.0") -> tuple[Deployment, AgentClient, APIClient, APIClient]:
    requester, _ = signed_in(AdminRole.PLATFORM_ADMIN)
    approver, _ = signed_in(AdminRole.OPERATIONS_MANAGER)
    _, _, server, deployment = infrastructure()
    server.public_ip = "203.0.113.10"
    server.save(update_fields=["public_ip"])
    register(requester, version)
    agent = enrolled(deployment, requester)
    assert (
        run_operation(
            requester, approver, deployment, "deploy_release", {"version": version}
        ).status_code
        == 200
    )
    execute(agent, version=version)
    assert observe(agent, deployment, version=version).status_code == 202
    deployment.refresh_from_db()
    assert deployment.verified_version == version
    return deployment, agent, requester, approver


# ---------------------------------------------------------------- releases


def test_manifest_validation_and_rollback_rules() -> None:
    assert validate_manifest(manifest())["version"] == "1.0.0"
    broken = manifest()
    broken["images"]["commerce-api"]["digest"] = "latest"
    with pytest.raises(Exception, match="immutable"):
        validate_manifest(broken)
    foreign = manifest()
    foreign["images"]["commerce-web"]["repository"] = "ghcr.io/rizwansammo/matecart-control-api"
    with pytest.raises(Exception, match="another repository"):
        validate_manifest(foreign)
    with pytest.raises(Exception, match="Unsupported"):
        validate_manifest({**manifest(), "schema": "other"})

    old = manifest("1.0.0", "1" * 64)
    same_schema = manifest("1.1.0", "1" * 64)
    new_schema = manifest("1.2.0", "3" * 64)
    declared = copy.deepcopy(new_schema)
    declared["database"]["commerce"]["rollback_compatible_with"] = ["1.0.0"]
    assert rollback_allowed(same_schema, old)
    assert not rollback_allowed(new_schema, old)
    assert rollback_allowed(declared, old)


def test_release_registration_requires_permission_and_is_unique() -> None:
    owner, _ = signed_in()
    auditor, _ = signed_in(AdminRole.READ_ONLY_AUDITOR)
    denied = auditor.post("/api/console/releases/", {"manifest": manifest()}, format="json")
    assert denied.status_code == 403
    register(owner, "1.0.0")
    again = owner.post("/api/console/releases/", {"manifest": manifest()}, format="json")
    assert again.status_code == 400
    listed = auditor.get("/api/console/releases/").data["results"][0]
    assert listed["images"]["commerce-api"].startswith("ghcr.io/rizwansammo/matecart-commerce-api@")
    assert ConsoleAuditEvent.objects.filter(action="release.registered").count() == 1


# ---------------------------------------------------------------- enrollment and signing


def test_signature_vector_matches_the_agent_implementation() -> None:
    message = signature_base(
        "POST", "/api/agent/v1/heartbeat/", "1700000000", "0" * 32, VECTOR_BODY
    )
    digest = hmac.new(VECTOR_SECRET.encode(), message, hashlib.sha256).hexdigest()
    assert digest == VECTOR_SIGNATURE


def test_enrollment_is_single_use_scoped_and_revocable() -> None:
    owner, _ = signed_in()
    _, _, _, deployment = infrastructure()
    auditor, _ = signed_in(AdminRole.READ_ONLY_AUDITOR)
    url = f"/api/console/servers/{deployment.server_id}/agent/"
    assert auditor.post(url, {}, format="json").status_code == 403
    stale, _ = signed_in(recent=False)
    assert stale.post(url, {}, format="json").status_code == 403

    issued = owner.post(url, {}, format="json")
    token = issued.data["token"]
    enroll = {"token": token, "agent_version": "1.0.0"}
    assert (
        APIClient()
        .post("/api/agent/v1/enroll/", {**enroll, "token": "x" * 43}, format="json")
        .status_code
        == 401
    )
    first = APIClient().post("/api/agent/v1/enroll/", enroll, format="json")
    assert first.status_code == 201
    assert APIClient().post("/api/agent/v1/enroll/", enroll, format="json").status_code == 401
    body = json.loads(first.content)
    agent = AgentClient(body["key_id"], body["secret"])

    heartbeat = agent.post("/api/agent/v1/heartbeat/", {"agent_version": "1.0.0"})
    assert heartbeat.status_code == 200
    assert agent.verify(heartbeat)
    data = json.loads(heartbeat.content)
    assert [item["deployment_id"] for item in data["deployments"]] == [str(deployment.pk)]
    # Only this Server's Deployments are visible to its Agent.
    _, _, _, other = infrastructure()
    assert str(other.pk) not in heartbeat.content.decode()
    stored = DeploymentAgent.objects.get(key_id=body["key_id"])
    assert body["secret"] not in stored.secret_encrypted

    # A second Agent for the same Server needs the first one revoked.
    second = owner.post(url, {}, format="json").data["token"]
    refused = APIClient().post(
        "/api/agent/v1/enroll/", {"token": second, "agent_version": "1.0.0"}, format="json"
    )
    assert refused.status_code == 400

    assert owner.patch(url, {"action": "revoke"}, format="json").status_code == 200
    assert agent.post("/api/agent/v1/heartbeat/").status_code == 401
    reconnect = APIClient().post(
        "/api/agent/v1/enroll/", {"token": second, "agent_version": "1.0.1"}, format="json"
    )
    assert reconnect.status_code == 201
    assert ConsoleAuditEvent.objects.filter(action="agent.revoked").exists()


def test_signed_requests_reject_tampering_replay_and_stale_timestamps() -> None:
    _, _, _, deployment = infrastructure()
    agent = enrolled(deployment)
    nonce = secrets.token_hex(16)
    assert agent.post("/api/agent/v1/heartbeat/", nonce=nonce).status_code == 200
    assert agent.post("/api/agent/v1/heartbeat/", nonce=nonce).status_code == 401
    old = int(time.time()) - 3600
    assert agent.post("/api/agent/v1/heartbeat/", timestamp=old).status_code == 401
    forged = AgentClient(agent.key_id, "b" * 64)
    assert forged.post("/api/agent/v1/heartbeat/").status_code == 401
    unknown = AgentClient("0" * 24, agent.secret)
    assert unknown.post("/api/agent/v1/heartbeat/").status_code == 401


def test_credential_rotation_promotes_on_first_use() -> None:
    _, _, _, deployment = infrastructure()
    agent = enrolled(deployment)
    rotated = agent.post("/api/agent/v1/rotate/")
    assert rotated.status_code == 200
    assert agent.verify(rotated)
    new = AgentClient(agent.key_id, json.loads(rotated.content)["secret"])
    assert agent.post("/api/agent/v1/heartbeat/").status_code == 200  # old still valid
    assert new.post("/api/agent/v1/heartbeat/").status_code == 200  # promotes
    assert agent.post("/api/agent/v1/heartbeat/").status_code == 401  # old retired


def test_agent_observations_are_bound_to_its_server() -> None:
    _, _, _, deployment = infrastructure()
    _, _, _, foreign = infrastructure()
    agent = enrolled(deployment)
    assert observe(agent, foreign, version="1.0.0").status_code == 400
    accepted = observe(agent, deployment, version="1.0.0")
    assert accepted.status_code == 202
    assert agent.verify(accepted)
    deployment.refresh_from_db()
    assert deployment.observed_version == "1.0.0"


# ---------------------------------------------------------------- command lifecycle


def test_approved_update_is_delivered_once_and_verified_by_telemetry() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    register(requester, "1.1.0")
    # The requester cannot approve, and approval alone creates nothing.
    created = requester.post(
        "/api/console/operations/",
        {
            "kind": "update_release",
            "title": "Update",
            "status": "submitted",
            "deployment": str(deployment.pk),
            "parameters": {"version": "1.1.0"},
        },
        format="json",
    )
    url = f"/api/console/operations/{created.data['id']}/"
    assert requester.patch(url, {"status": "approved"}, format="json").status_code == 400
    assert approver.patch(url, {"status": "approved"}, format="json").status_code == 200
    assert not AgentCommand.objects.filter(operation_id=created.data["id"]).exists()
    assert approver.patch(url, {"status": "pending_execution"}, format="json").status_code == 200
    command = AgentCommand.objects.get(operation_id=created.data["id"])
    assert command.status == "queued"
    assert command.target_version == "1.1.0"
    assert command.payload["release"]["version"] == "1.1.0"
    assert "secret" not in json.dumps(command.payload).lower()

    first = json.loads(agent.post("/api/agent/v1/commands/lease/").content)["command"]
    again = json.loads(agent.post("/api/agent/v1/commands/lease/").content)["command"]
    assert first["command_id"] == again["command_id"]
    assert first["payload_sha256"] == hashlib.sha256(canonical_json(first["payload"])).hexdigest()
    events = f"/api/agent/v1/commands/{first['command_id']}/events/"
    assert agent.post(events, {"sequence": 1, "event": "started"}).status_code == 200
    duplicate = agent.post(events, {"sequence": 1, "event": "started"})
    assert duplicate.status_code == 200
    assert (
        agent.post(
            events, {"sequence": 2, "event": "succeeded", "evidence": {"observed_version": "1.1.0"}}
        ).status_code
        == 200
    )
    command.refresh_from_db()
    assert command.status == "awaiting_verification"
    # Approval and acknowledgment are not completion.
    assert OperationalRequest.objects.get(pk=created.data["id"]).status == "pending_execution"
    # An unhealthy fresh report does not verify.
    observe(agent, deployment, version="1.1.0", status="degraded")
    command.refresh_from_db()
    assert command.status == "awaiting_verification"
    observe(agent, deployment, version="1.1.0", when=timezone.now() + timedelta(seconds=1))
    command.refresh_from_db()
    deployment.refresh_from_db()
    assert command.status == "succeeded"
    assert deployment.verified_version == "1.1.0"
    assert deployment.rollback_version == "1.0.0"
    operation = OperationalRequest.objects.get(pk=created.data["id"])
    assert operation.status == "completed"
    assert "Verified 1.1.0" in operation.completed_evidence
    actions = set(
        ConsoleAuditEvent.objects.filter(resource_id=command.pk).values_list("action", flat=True)
    )
    assert {
        "deployment.command.created",
        "deployment.command.started",
        "deployment.command.reported_success",
        "deployment.command.verified",
    } <= actions


def test_failed_update_marks_request_and_raises_deployment_alert() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    register(requester, "1.1.0")
    run_operation(requester, approver, deployment, "update_release", {"version": "1.1.0"})
    command = execute(agent, version=None, succeed=False)
    row = AgentCommand.objects.get(pk=command["command_id"])
    assert row.status == "failed"
    assert row.failure_category == "health_check_failed"
    assert row.operation.status == "failed"
    deployment.refresh_from_db()
    assert deployment.verified_version == "1.0.0"
    assert deployment.state == "update_failed"
    assert OperationalAlert.objects.get(category="deployment_failed").status == "open"


def test_mismatched_evidence_never_counts_as_success() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    register(requester, "1.1.0")
    run_operation(requester, approver, deployment, "update_release", {"version": "1.1.0"})
    command = execute(agent, version="1.0.0")
    row = AgentCommand.objects.get(pk=command["command_id"])
    assert row.status == "failed"
    assert row.failure_category == "evidence_mismatch"


def test_only_one_active_operation_per_store_and_no_manual_completion() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    register(requester, "1.1.0")
    assert (
        run_operation(
            requester, approver, deployment, "update_release", {"version": "1.1.0"}
        ).status_code
        == 200
    )
    second = run_operation(requester, approver, deployment, "run_backup")
    assert second.status_code == 400
    operation = AgentCommand.objects.get(deployment=deployment, status="queued").operation
    manual = approver.patch(
        f"/api/console/operations/{operation.pk}/",
        {"status": "completed", "completed_evidence": "trust me"},
        format="json",
    )
    assert manual.status_code == 400
    # A queued command can be cancelled; a delivered one cannot.
    agent.post("/api/agent/v1/commands/lease/")
    cancel = approver.patch(
        f"/api/console/operations/{operation.pk}/", {"status": "cancelled"}, format="json"
    )
    assert cancel.status_code == 400


def test_support_operator_cannot_request_deployment_operations() -> None:
    _, _, _, deployment = infrastructure()
    support, _ = signed_in(AdminRole.SUPPORT_OPERATOR)
    response = support.post(
        "/api/console/operations/",
        {"kind": "run_backup", "title": "Backup", "deployment": str(deployment.pk)},
        format="json",
    )
    assert response.status_code == 403
    sneaky = support.post(
        "/api/console/operations/",
        {"kind": "note", "title": "Pre-approved", "status": "approved"},
        format="json",
    )
    assert sneaky.status_code == 400


def test_expired_commands_cannot_start_and_reconcile_times_out() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    run_operation(requester, approver, deployment, "run_backup")
    command = AgentCommand.objects.get(deployment=deployment, status="queued")
    AgentCommand.objects.filter(pk=command.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    leased = json.loads(agent.post("/api/agent/v1/commands/lease/").content)
    assert leased["command"] is None
    command.refresh_from_db()
    assert command.status == "expired"
    assert command.operation.status == "failed"

    run_operation(requester, approver, deployment, "run_backup")
    running = execute(agent, version=None)
    row = AgentCommand.objects.get(pk=running["command_id"])
    assert row.status == "awaiting_verification"
    AgentCommand.objects.filter(pk=row.pk).update(
        finished_at=timezone.now() - timedelta(minutes=30)
    )
    reconcile_commands()
    reconcile_commands()
    row.refresh_from_db()
    assert row.status == "verification_failed"


def test_backup_reports_are_idempotent_and_verify_backup_commands() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    run_operation(requester, approver, deployment, "run_backup")
    command = execute(agent, version=None)
    report = {
        "deployment_id": str(deployment.pk),
        "report_id": str(uuid.uuid4()),
        "backup_type": "full",
        "status": "successful",
        "started_at": timezone.now().isoformat(),
        "finished_at": timezone.now().isoformat(),
        "size_bytes": 1024,
        "sha256": "c" * 64,
        "artifact_name": "store-20261106T020000Z.tar",
    }
    assert agent.post("/api/agent/v1/backups/", report).status_code == 202
    assert agent.post("/api/agent/v1/backups/", report).status_code == 202
    assert BackupObservation.objects.filter(deployment=deployment, source="agent").count() == 1
    assert AgentCommand.objects.get(pk=command["command_id"]).status == "succeeded"


def test_rollback_is_limited_to_verified_previous_release_and_schema_compatibility() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    register(requester, "1.1.0", fingerprint="9" * 64)  # schema changed
    run_operation(requester, approver, deployment, "update_release", {"version": "1.1.0"})
    execute(agent, version="1.1.0")
    observe(agent, deployment, version="1.1.0", when=timezone.now() + timedelta(seconds=1))
    deployment.refresh_from_db()
    assert deployment.rollback_version == "1.0.0"
    refused = run_operation(
        requester, approver, deployment, "rollback_release", {"version": "1.0.0"}
    )
    assert refused.status_code == 400
    assert "Restore the pre-update backup" in json.dumps(refused.data)
    wrong = requester.post(
        "/api/console/operations/",
        {
            "kind": "rollback_release",
            "title": "Rollback",
            "deployment": str(deployment.pk),
            "parameters": {"version": "0.9.0"},
        },
        format="json",
    )
    assert wrong.status_code == 400


def test_compatible_rollback_is_dispatched() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    register(requester, "1.1.0")  # same schema fingerprint
    run_operation(requester, approver, deployment, "update_release", {"version": "1.1.0"})
    execute(agent, version="1.1.0")
    observe(agent, deployment, version="1.1.0", when=timezone.now() + timedelta(seconds=1))
    response = run_operation(
        requester, approver, deployment, "rollback_release", {"version": "1.0.0"}
    )
    assert response.status_code == 200
    command = AgentCommand.objects.get(deployment=deployment, status="queued")
    assert command.payload["previous_release"]["version"] == "1.1.0"


def test_configuration_is_applied_only_from_agent_acknowledgment() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    store = deployment.store
    patch = requester.patch(
        f"/api/console/stores/{store.pk}/",
        {"desired_modules": [*store.desired_modules, "live_chat"]},
        format="json",
    )
    assert patch.status_code == 200
    store.refresh_from_db()
    run_operation(requester, approver, deployment, "apply_configuration")
    execute(agent, version=None)
    store.refresh_from_db()
    assert store.applied_revision < store.desired_revision
    observe(
        agent,
        deployment,
        version="1.0.0",
        when=timezone.now() + timedelta(seconds=1),
        configuration_revision=store.desired_revision,
    )
    store.refresh_from_db()
    assert store.applied_revision == store.desired_revision
    assert "live_chat" in store.applied_modules


def test_domain_verification_and_tls_observation() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    domain = DomainRecord.objects.create(
        store=deployment.store, deployment=deployment, domain="shop.example.test"
    )
    early = requester.post(
        "/api/console/operations/",
        {"kind": "configure_domains", "title": "Domains", "deployment": str(deployment.pk)},
        format="json",
    )
    assert early.status_code == 400

    records: dict[tuple[str, str], list[str]] = {}

    def resolver(name: str, record_type: str) -> list[str]:
        return records.get((name, record_type), [])

    checked = verify_domain(domain, approver_user(), resolver)
    assert checked.verification_state == "failed"
    records[("_matecart-verify.shop.example.test", "TXT")] = [checked.verification_token]
    records[("shop.example.test", "A")] = ["203.0.113.10"]
    checked = verify_domain(domain, approver_user(), resolver)
    assert checked.verification_state == "verified"
    assert checked.dns_state == "ready"

    assert run_operation(requester, approver, deployment, "configure_domains").status_code == 200
    execute(agent, version=None)
    expires = timezone.now() + timedelta(days=80)
    observe(
        agent,
        deployment,
        version="1.0.0",
        when=timezone.now() + timedelta(seconds=1),
        domains=[
            {
                "domain": "shop.example.test",
                "tls_state": "valid",
                "renewal_state": "ok",
                "certificate_expires_at": expires.isoformat(),
            }
        ],
    )
    command = AgentCommand.objects.get(deployment=deployment, kind="configure_domains")
    assert command.status == "succeeded"
    domain.refresh_from_db()
    assert domain.tls_state == "valid"
    assert domain.configured

    observe(
        agent,
        deployment,
        version="1.0.0",
        when=timezone.now() + timedelta(seconds=2),
        domains=[
            {"domain": "shop.example.test", "tls_state": "valid", "renewal_state": "renewal_failed"}
        ],
    )
    assert OperationalAlert.objects.get(category="certificate_renewal_failed").status == "open"


def approver_user() -> Any:
    from control.accounts.models import AdminUser

    return AdminUser.objects.filter(role=AdminRole.OPERATIONS_MANAGER).first()


def test_agent_disconnection_alert_and_worker_signals() -> None:
    deployment, agent, _, _ = installed("1.0.0")
    DeploymentAgent.objects.update(last_seen_at=timezone.now() - timedelta(minutes=10))
    reconcile_commands()
    reconcile_commands()
    alert = OperationalAlert.objects.get(category="agent_disconnected")
    assert alert.status == "open"
    assert alert.server_id == deployment.server_id
    agent.post("/api/agent/v1/heartbeat/")
    reconcile_commands()
    alert.refresh_from_db()
    assert alert.status == "resolved"

    observe(
        agent,
        deployment,
        version="1.0.0",
        when=timezone.now() + timedelta(seconds=1),
        worker_status="unavailable",
    )
    assert OperationalAlert.objects.get(category="worker_unavailable").status == "open"


def test_revocation_cancels_in_flight_commands() -> None:
    deployment, agent, requester, approver = installed("1.0.0")
    run_operation(requester, approver, deployment, "run_backup")
    agent.post("/api/agent/v1/commands/lease/")
    owner, _ = signed_in()
    owner.patch(
        f"/api/console/servers/{deployment.server_id}/agent/", {"action": "revoke"}, format="json"
    )
    command = AgentCommand.objects.get(deployment=deployment, kind="run_backup")
    assert command.status == "failed"
    assert command.failure_category == "agent_revoked"
    assert ReleaseRecord.objects.count() == 1
