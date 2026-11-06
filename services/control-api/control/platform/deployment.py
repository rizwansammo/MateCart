"""Phase 17 deployment automation: releases, Agent identity and the command lifecycle.

Integrity rules (docs/DEPLOYMENT_AUTOMATION.md, D-119 to D-123):

- Approval is not execution: a command exists only for an approved executable request that an
  authorized operator moved to Pending Execution.
- Delivery is not completion: the Agent must claim a command (``started``) before acting, and
  Control refuses expired, cancelled or foreign commands.
- An Agent acknowledgment is not proof of health: release, configuration and domain commands end
  in ``awaiting_verification`` and succeed only when a later authenticated observation shows the
  expected version/revision/TLS state.
- Commands are allowlisted kinds with Control-built payloads. There is no free-form shell, Docker,
  SQL or filesystem operation anywhere in this protocol.
"""

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.core.cache import caches
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.utils.encoders import JSONEncoder

from control.accounts.models import AdminUser
from control.core.security import decrypt, encrypt, hash_token, new_token

from .models import (
    ACTIVE_COMMAND_STATUSES,
    AgentCommand,
    AgentEnrollment,
    AgentStatus,
    BackupPolicy,
    CommandKind,
    CommandStatus,
    Deployment,
    DeploymentAgent,
    DomainRecord,
    MonitoringObservation,
    OperationalRequest,
    ReleaseRecord,
    Server,
    StoreConfigurationRevision,
    StoreStatus,
)
from .services import apply_alert_condition, audit

ENROLLMENT_LIFETIME = timedelta(hours=1)
CREDENTIAL_LIFETIME = timedelta(days=90)
ROTATE_BEFORE = timedelta(days=30)
SIGNATURE_WINDOW_SECONDS = 300
NONCE_TTL_SECONDS = 900
COMMAND_DELIVERY_WINDOW = timedelta(minutes=30)
COMMAND_SILENCE_TIMEOUT = timedelta(minutes=60)
VERIFICATION_TIMEOUT = timedelta(minutes=15)
AGENT_STALE_AFTER = timedelta(minutes=5)
MAX_PROGRESS_EVENTS = 50

EXECUTABLE_KINDS = frozenset(CommandKind.values)
RELEASE_KINDS = frozenset(
    {CommandKind.DEPLOY_RELEASE, CommandKind.UPDATE_RELEASE, CommandKind.ROLLBACK_RELEASE}
)
VERIFIED_KINDS = RELEASE_KINDS | {
    CommandKind.APPLY_CONFIGURATION,
    CommandKind.CONFIGURE_DOMAINS,
    CommandKind.RUN_BACKUP,
}
FAILURE_CATEGORIES = frozenset(
    {
        "invalid_command",
        "expired",
        "target_mismatch",
        "release_invalid",
        "image_unavailable",
        "insufficient_resources",
        "backup_failed",
        "migration_failed",
        "health_check_failed",
        "rollback_refused",
        "rolled_back",
        "interrupted",
        "configuration_failed",
        "domain_failed",
        "agent_error",
        "agent_timeout",
        "agent_revoked",
        "verification_timeout",
        "evidence_mismatch",
    }
)

VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,31})?$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9-]{0,38}/matecart-[a-z-]{3,40}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DOMAIN_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
REQUIRED_IMAGES = ("commerce-api", "commerce-web", "control-api", "platform-console")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, cls=JSONEncoder
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- release manifests (D-121)


def validate_manifest(data: Any) -> dict[str, Any]:
    """Validate a ``matecart.release/v1`` manifest; return it unchanged when valid."""
    if not isinstance(data, dict):
        raise ValidationError({"manifest": "The release manifest must be a JSON object."})
    if data.get("schema") != "matecart.release/v1":
        raise ValidationError({"manifest": "Unsupported release manifest schema."})
    version = data.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValidationError({"manifest": "The release version must be MAJOR.MINOR.PATCH."})
    if not isinstance(data.get("git_commit"), str) or not GIT_SHA_PATTERN.fullmatch(
        data["git_commit"]
    ):
        raise ValidationError({"manifest": "git_commit must be a full commit SHA."})
    images = data.get("images")
    if not isinstance(images, dict) or set(images) != set(REQUIRED_IMAGES):
        raise ValidationError({"manifest": "The manifest must list exactly the four images."})
    for name, image in images.items():
        if not isinstance(image, dict):
            raise ValidationError({"manifest": f"Image {name} is malformed."})
        repository = image.get("repository")
        digest = image.get("digest")
        if not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository):
            raise ValidationError({"manifest": f"Image {name} has an unsupported repository."})
        if not repository.endswith(f"/matecart-{name}"):
            raise ValidationError({"manifest": f"Image {name} points to another repository."})
        if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
            raise ValidationError({"manifest": f"Image {name} needs an immutable sha256 digest."})
    database = data.get("database")
    if not isinstance(database, dict):
        raise ValidationError({"manifest": "The manifest must describe database migrations."})
    for service in ("commerce", "control"):
        entry = database.get(service)
        if not isinstance(entry, dict) or not FINGERPRINT_PATTERN.fullmatch(
            str(entry.get("migration_fingerprint", ""))
        ):
            raise ValidationError({"manifest": f"Missing {service} migration fingerprint."})
        compatible = entry.get("rollback_compatible_with", [])
        if not isinstance(compatible, list) or not all(
            isinstance(item, str) and VERSION_PATTERN.fullmatch(item) for item in compatible
        ):
            raise ValidationError({"manifest": "rollback_compatible_with must list versions."})
    return data


def register_release(manifest: Any, actor: AdminUser, notes: str = "") -> ReleaseRecord:
    clean = validate_manifest(manifest)
    digest = sha256_hex(canonical_json(clean))
    try:
        with transaction.atomic():
            release = ReleaseRecord.objects.create(
                version=clean["version"],
                manifest=clean,
                manifest_sha256=digest,
                registered_by=actor,
                notes=notes[:500],
            )
            audit(
                actor,
                "release.registered",
                release,
                {"version": release.version, "manifest_sha256": digest},
            )
    except IntegrityError as exc:
        raise ValidationError({"version": "This release version is already registered."}) from exc
    return release


def rollback_allowed(current: dict[str, Any], target: dict[str, Any]) -> bool:
    """Automatic rollback only when the target code can run on the current schema (D-121).

    The schema is unchanged between the releases, or the current release explicitly declares its
    migrations additive for that older version. Anything else needs a database restore.
    """
    current_db = current["database"]["commerce"]
    target_db = target["database"]["commerce"]
    if current_db["migration_fingerprint"] == target_db["migration_fingerprint"]:
        return True
    return target["version"] in current_db.get("rollback_compatible_with", [])


# ---------------------------------------------------------------- Agent identity (D-119)


def issue_enrollment(server: Server, actor: AdminUser) -> tuple[AgentEnrollment, str]:
    raw = new_token()
    with transaction.atomic():
        enrollment = AgentEnrollment.objects.create(
            server=server,
            token_hash=hash_token(raw),
            last_four=raw[-4:],
            created_by=actor,
            expires_at=timezone.now() + ENROLLMENT_LIFETIME,
        )
        audit(actor, "agent.enrollment.issued", enrollment, {"server_id": str(server.pk)})
    return enrollment, raw


def _clean_facts(value: Any) -> dict[str, Any]:
    allowed = {
        "hostname": 253,
        "os": 120,
        "kernel": 120,
        "architecture": 40,
        "docker_version": 40,
        "compose_version": 40,
        "python_version": 40,
    }
    facts: dict[str, Any] = {}
    if not isinstance(value, dict):
        return facts
    for key, limit in allowed.items():
        item = value.get(key)
        if isinstance(item, str):
            facts[key] = item[:limit]
    for key in ("cpu_count", "ram_mb", "disk_gb"):
        item = value.get(key)
        if isinstance(item, int) and 0 <= item <= 10_000_000:
            facts[key] = item
    return facts


def enroll_agent(token: str, agent_version: str, host_facts: Any) -> tuple[DeploymentAgent, str]:
    if not token or len(token) > 256:
        raise AuthenticationFailed("Invalid or expired enrollment token.")
    now = timezone.now()
    secret = secrets.token_hex(32)
    with transaction.atomic():
        enrollment = (
            AgentEnrollment.objects.select_for_update()
            .select_related("server")
            .filter(token_hash=hash_token(token))
            .first()
        )
        if enrollment is None or enrollment.used_at is not None or enrollment.expires_at <= now:
            raise AuthenticationFailed("Invalid or expired enrollment token.")
        if DeploymentAgent.objects.filter(
            server=enrollment.server, status=AgentStatus.ACTIVE
        ).exists():
            raise ValidationError(
                {"token": "This Server already has an active Agent. Revoke it before re-enrolling."}
            )
        agent = DeploymentAgent.objects.create(
            server=enrollment.server,
            key_id=secrets.token_hex(12),
            secret_encrypted=encrypt(secret),
            credential_expires_at=now + CREDENTIAL_LIFETIME,
            agent_version=agent_version[:40],
            host_facts=_clean_facts(host_facts),
            last_seen_at=now,
        )
        enrollment.used_at = now
        enrollment.agent = agent
        enrollment.save(update_fields=["used_at", "agent"])
        audit(
            None,
            "agent.enrolled",
            agent,
            {"server_id": str(agent.server_id), "agent_version": agent.agent_version},
        )
    return agent, secret


def revoke_agent(agent: DeploymentAgent, actor: AdminUser) -> DeploymentAgent:
    now = timezone.now()
    with transaction.atomic():
        locked = DeploymentAgent.objects.select_for_update().get(pk=agent.pk)
        if locked.status == AgentStatus.REVOKED:
            return locked
        locked.status = AgentStatus.REVOKED
        locked.revoked_at = now
        locked.revoked_by = actor
        locked.pending_secret_encrypted = ""
        locked.save(
            update_fields=[
                "status",
                "revoked_at",
                "revoked_by",
                "pending_secret_encrypted",
                "updated_at",
            ]
        )
        for command in AgentCommand.objects.select_for_update().filter(
            agent=locked, status__in=ACTIVE_COMMAND_STATUSES
        ):
            _finish_failed(command, "agent_revoked", "The Agent credential was revoked.", now)
        audit(actor, "agent.revoked", locked, {"server_id": str(locked.server_id)})
    return locked


@dataclass(frozen=True)
class AgentRequest:
    agent: DeploymentAgent
    nonce: str
    secret: str


def signature_base(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    return "\n".join([method.upper(), path, timestamp, nonce, sha256_hex(body)]).encode()


def sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def authenticate_agent(
    *, method: str, path: str, headers: Any, body: bytes, now: float | None = None
) -> AgentRequest:
    """Verify an Agent request signature, timestamp window and single-use nonce."""
    key_id = str(headers.get("X-MateCart-Agent-Key", ""))
    timestamp = str(headers.get("X-MateCart-Timestamp", ""))
    nonce = str(headers.get("X-MateCart-Nonce", ""))
    signature = str(headers.get("X-MateCart-Signature", ""))
    failure = AuthenticationFailed("Agent authentication failed.")
    if not (key_id and timestamp.isdigit() and NONCE_PATTERN.fullmatch(nonce) and signature):
        raise failure
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > SIGNATURE_WINDOW_SECONDS:
        raise failure
    agent = DeploymentAgent.objects.filter(key_id=key_id[:32]).select_related("server").first()
    if agent is None or agent.status != AgentStatus.ACTIVE:
        raise failure
    if agent.credential_expires_at <= timezone.now():
        raise AuthenticationFailed("Agent credential expired; re-enroll the Agent.")
    message = signature_base(method, path, timestamp, nonce, body)
    secret = decrypt(agent.secret_encrypted)
    if not hmac.compare_digest(sign(secret, message), signature):
        pending = decrypt(agent.pending_secret_encrypted) if agent.pending_secret_encrypted else ""
        if not pending or not hmac.compare_digest(sign(pending, message), signature):
            raise failure
        _promote_pending_secret(agent)
        secret = pending
    if not caches["ratelimit"].add(f"agent-nonce:{agent.pk}:{nonce}", 1, NONCE_TTL_SECONDS):
        raise AuthenticationFailed("Replayed Agent request.")
    return AgentRequest(agent=agent, nonce=nonce, secret=secret)


def _promote_pending_secret(agent: DeploymentAgent) -> None:
    with transaction.atomic():
        locked = DeploymentAgent.objects.select_for_update().get(pk=agent.pk)
        if not locked.pending_secret_encrypted:
            return
        locked.secret_encrypted = locked.pending_secret_encrypted
        locked.pending_secret_encrypted = ""
        locked.credential_expires_at = timezone.now() + CREDENTIAL_LIFETIME
        locked.save(
            update_fields=[
                "secret_encrypted",
                "pending_secret_encrypted",
                "credential_expires_at",
                "updated_at",
            ]
        )
        audit(None, "agent.credential.rotated", locked, {"server_id": str(locked.server_id)})
    agent.secret_encrypted = locked.secret_encrypted
    agent.pending_secret_encrypted = ""
    agent.credential_expires_at = locked.credential_expires_at


def begin_rotation(agent: DeploymentAgent) -> str:
    """Issue a pending secret. It replaces the current one on its first verified use."""
    secret = secrets.token_hex(32)
    DeploymentAgent.objects.filter(pk=agent.pk).update(
        pending_secret_encrypted=encrypt(secret), updated_at=timezone.now()
    )
    return secret


def response_signature(secret: str, nonce: str, status_code: int, body: bytes) -> str:
    return sign(secret, "\n".join(["response", nonce, str(status_code), sha256_hex(body)]).encode())


def record_heartbeat(agent: DeploymentAgent, data: dict[str, Any]) -> dict[str, Any]:
    now = timezone.now()
    DeploymentAgent.objects.filter(pk=agent.pk).update(
        last_seen_at=now,
        agent_version=str(data.get("agent_version", agent.agent_version))[:40],
        host_facts=_clean_facts(data.get("host_facts")) or agent.host_facts,
        updated_at=now,
    )
    deployments = Deployment.objects.filter(server_id=agent.server_id).select_related("store")
    policies = {
        policy.deployment_id: policy
        for policy in BackupPolicy.objects.filter(deployment__server_id=agent.server_id)
    }
    return {
        "server_time": now,
        "agent_id": agent.pk,
        "server_id": agent.server_id,
        "credential_expires_at": agent.credential_expires_at,
        "rotate": agent.credential_expires_at - now < ROTATE_BEFORE,
        "deployments": [
            {
                "deployment_id": item.pk,
                "store_id": item.store_id,
                "store_handle": item.store.handle,
                "environment": item.environment,
                "backup_policy": backup_policy_payload(policies.get(item.pk)),
            }
            for item in deployments
        ],
    }


def backup_policy_payload(policy: BackupPolicy | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {
        "enabled": policy.enabled,
        "revision": policy.revision,
        "schedule_hour_utc": policy.schedule_hour_utc,
        "schedule_minute": policy.schedule_minute,
        "retention_days": policy.retention_days,
        "include_media": policy.include_media,
        "destination_kind": policy.destination_kind,
    }


# ---------------------------------------------------------------- operations to commands (D-120)


def validate_operation(kind: str, deployment: Deployment | None, parameters: Any) -> dict[str, Any]:
    """Validate an executable request when it is created. Returns clean parameters."""
    if kind not in EXECUTABLE_KINDS:
        return parameters if isinstance(parameters, dict) else {}
    if deployment is None:
        raise ValidationError({"deployment": "Executable operations target one Deployment."})
    params = parameters if isinstance(parameters, dict) else {}
    if kind in RELEASE_KINDS:
        version = str(params.get("version", ""))
        release = ReleaseRecord.objects.filter(version=version, status="available").first()
        if release is None:
            raise ValidationError({"parameters": "Choose a registered, available release."})
        if kind == CommandKind.ROLLBACK_RELEASE and version != deployment.rollback_version:
            raise ValidationError(
                {"parameters": "Rollback is only allowed to the last verified previous release."}
            )
        if kind == CommandKind.UPDATE_RELEASE and not deployment.verified_version:
            raise ValidationError({"parameters": "Install and verify a release before updating."})
        if kind == CommandKind.DEPLOY_RELEASE and deployment.verified_version:
            raise ValidationError(
                {"parameters": "This Deployment is installed; request an update."}
            )
        return {"version": version}
    if kind == CommandKind.APPLY_CONFIGURATION:
        store = deployment.store
        if store.desired_revision == store.applied_revision:
            raise ValidationError({"parameters": "The desired configuration is already applied."})
        return {"revision": store.desired_revision}
    if kind == CommandKind.CONFIGURE_DOMAINS:
        if not _deployable_domains(deployment):
            raise ValidationError(
                {"parameters": "Verify domain ownership and DNS readiness first."}
            )
        return {}
    return {}


def _deployable_domains(deployment: Deployment) -> list[DomainRecord]:
    return list(
        DomainRecord.objects.filter(
            deployment=deployment, verification_state="verified", dns_state="ready"
        ).order_by("domain")
    )


def _store_manifest(deployment: Deployment) -> dict[str, Any]:
    store = deployment.store
    return {
        "store_id": str(store.pk),
        "handle": store.handle,
        "name": store.name,
        "preset": store.preset,
        "country": store.country,
        "currency": store.default_currency,
        "time_zone": store.time_zone,
        "language": store.default_language,
        "languages": list(store.supported_languages),
    }


def build_payload(
    command_id: str, kind: str, deployment: Deployment, params: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (payload, command fields). Payloads are built only from validated Control data."""
    now = timezone.now()
    base: dict[str, Any] = {
        "command_id": command_id,
        "kind": kind,
        "deployment_id": str(deployment.pk),
        "server_id": str(deployment.server_id),
        "store_id": str(deployment.store_id),
        "store_handle": deployment.store.handle,
        "environment": deployment.environment,
        "issued_at": now.isoformat(),
        "expires_at": (now + COMMAND_DELIVERY_WINDOW).isoformat(),
    }
    fields: dict[str, Any] = {}
    if kind in RELEASE_KINDS:
        release = ReleaseRecord.objects.get(version=params["version"], status="available")
        current = ReleaseRecord.objects.filter(version=deployment.verified_version).first()
        if kind == CommandKind.ROLLBACK_RELEASE and (
            current is None or not rollback_allowed(current.manifest, release.manifest)
        ):
            raise ValidationError(
                {
                    "parameters": (
                        "Automatic rollback is refused: the database schema changed since "
                        f"{release.version}. Restore the pre-update backup instead."
                    )
                }
            )
        base.update(
            {
                "release": release.manifest,
                "release_sha256": release.manifest_sha256,
                "previous_release": current.manifest if current else None,
                "store": _store_manifest(deployment),
                "domains": [
                    {"domain": item.domain, "tls_mode": item.tls_mode}
                    for item in _deployable_domains(deployment)
                ],
            }
        )
        fields = {
            "target_version": release.version,
            "previous_version": deployment.verified_version,
        }
    elif kind == CommandKind.APPLY_CONFIGURATION:
        revision = StoreConfigurationRevision.objects.get(
            store=deployment.store, revision=params["revision"]
        )
        base.update(
            {
                "revision": revision.revision,
                "preset": revision.preset,
                "theme": revision.theme,
                "modules": list(revision.modules),
                "languages": list(revision.languages),
            }
        )
        fields = {"target_revision": revision.revision}
    elif kind == CommandKind.CONFIGURE_DOMAINS:
        base["domains"] = [
            {"domain": item.domain, "tls_mode": item.tls_mode}
            for item in _deployable_domains(deployment)
        ]
    elif kind == CommandKind.RUN_BACKUP:
        policy = BackupPolicy.objects.filter(deployment=deployment).first()
        base["policy"] = backup_policy_payload(policy) or {
            "enabled": True,
            "revision": 0,
            "retention_days": 14,
            "include_media": True,
            "destination_kind": "local",
        }
    return base, fields


def dispatch_operation(operation: OperationalRequest, actor: AdminUser) -> AgentCommand:
    """Create the scoped command for an approved executable request (inside the caller's
    transaction, which holds the request row lock)."""
    if operation.deployment_id is None:
        raise ValidationError({"deployment": "Executable operations target one Deployment."})
    deployment = (
        Deployment.objects.select_for_update()
        .select_related("store", "server")
        .filter(pk=operation.deployment_id)
        .first()
    )
    if deployment is None:
        raise ValidationError({"deployment": "Executable operations target one Deployment."})
    agent = DeploymentAgent.objects.filter(
        server_id=deployment.server_id, status=AgentStatus.ACTIVE
    ).first()
    if agent is None:
        raise ValidationError({"status": "No active Deployment Agent is enrolled on this Server."})
    params = validate_operation(operation.kind, deployment, operation.parameters)
    if AgentCommand.objects.filter(
        deployment=deployment, status__in=ACTIVE_COMMAND_STATUSES
    ).exists():
        raise ValidationError(
            {"status": "Another deployment operation is still active for this Store."}
        )
    from control.core.ids import uuid7

    command_id = uuid7()
    payload, fields = build_payload(str(command_id), operation.kind, deployment, params)
    try:
        with transaction.atomic():
            command = AgentCommand.objects.create(
                id=command_id,
                agent=agent,
                deployment=deployment,
                operation=operation,
                kind=operation.kind,
                payload=payload,
                payload_sha256=sha256_hex(canonical_json(payload)),
                expires_at=parse_datetime(payload["expires_at"]) or timezone.now(),
                **fields,
            )
    except IntegrityError as exc:
        raise ValidationError(
            {"status": "Another deployment operation is still active for this Store."}
        ) from exc
    if operation.kind in RELEASE_KINDS:
        deployment.desired_version = command.target_version
        deployment.state = "update_pending"
        deployment.save(update_fields=["desired_version", "state", "updated_at"])
    audit(
        actor,
        "deployment.command.created",
        command,
        {
            "kind": command.kind,
            "operation_id": str(operation.pk),
            "requested_by": str(operation.requested_by_id),
            "approved_by": str(operation.decided_by_id or ""),
            "deployment_id": str(deployment.pk),
            "server_id": str(deployment.server_id),
            "store_id": str(deployment.store_id),
            "target_version": command.target_version,
        },
    )
    return command


def cancel_command(command: AgentCommand, actor: AdminUser) -> None:
    if command.status != CommandStatus.QUEUED:
        raise ValidationError(
            {"status": "Only a command that has not been delivered to the Agent can be cancelled."}
        )
    command.status = CommandStatus.CANCELLED
    command.finished_at = timezone.now()
    command.save(update_fields=["status", "finished_at", "updated_at"])
    _restore_deployment_state(command)
    audit(actor, "deployment.command.cancelled", command, {"kind": command.kind})


def lease_next_command(agent: DeploymentAgent) -> AgentCommand | None:
    """Deliver the oldest queued (or re-deliver the in-flight) command for this Agent.

    Re-delivery returns the same command id, so a lost response never creates a second
    execution: the Agent's journal and the ``started`` claim are both keyed by that id.
    """
    now = timezone.now()
    with transaction.atomic():
        for command in (
            AgentCommand.objects.select_for_update()
            .filter(agent=agent, status__in=[CommandStatus.QUEUED, CommandStatus.DELIVERED])
            .order_by("created_at")
        ):
            if command.expires_at <= now:
                _finish_failed(command, "expired", "The command expired before it started.", now)
                command.status = CommandStatus.EXPIRED
                command.save(update_fields=["status", "updated_at"])
                continue
            command.status = CommandStatus.DELIVERED
            command.delivered_at = command.delivered_at or now
            command.delivery_count += 1
            command.save(update_fields=["status", "delivered_at", "delivery_count", "updated_at"])
            return command
        return AgentCommand.objects.filter(agent=agent, status=CommandStatus.RUNNING).first()


def command_envelope(command: AgentCommand) -> dict[str, Any]:
    return {
        "command_id": command.pk,
        "kind": command.kind,
        "status": command.status,
        "payload": command.payload,
        "payload_sha256": command.payload_sha256,
        "last_event_sequence": command.last_event_sequence,
    }


def _bounded_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    encoded = canonical_json(value)
    if len(encoded) > 8192:
        return {"truncated": True}
    return value


def record_command_event(
    agent: DeploymentAgent, command_id: Any, data: dict[str, Any]
) -> AgentCommand:
    now = timezone.now()
    event = str(data.get("event", ""))
    sequence = data.get("sequence")
    if event not in {"started", "progress", "succeeded", "failed"}:
        raise ValidationError({"event": "Unknown command event."})
    if not isinstance(sequence, int) or not 1 <= sequence <= 10_000:
        raise ValidationError({"sequence": "Events need a positive sequence number."})
    with transaction.atomic():
        command = (
            AgentCommand.objects.select_for_update()
            .select_related("deployment", "operation")
            .filter(pk=command_id, agent=agent)
            .first()
        )
        if command is None:
            raise ValidationError({"command": "Unknown command for this Agent."})
        if sequence <= command.last_event_sequence:
            return command  # duplicate delivery of an already recorded event (idempotent)
        terminal = command.status not in ACTIVE_COMMAND_STATUSES
        if terminal or command.status == CommandStatus.AWAITING_VERIFICATION:
            raise ValidationError({"status": "This command is already finalised."})
        stage = str(data.get("stage", ""))[:40]
        message = str(data.get("message", ""))[:300]
        if event == "started":
            if command.status != CommandStatus.DELIVERED:
                raise ValidationError({"status": "Only a delivered command can start."})
            if command.expires_at <= now:
                command.status = CommandStatus.EXPIRED
                command.finished_at = now
                command.failure_category = "expired"
                command.save(update_fields=["status", "finished_at", "failure_category"])
                _fail_operation(command, "The command expired before it started.")
                raise ValidationError({"status": "The command expired before it started."})
            command.status = CommandStatus.RUNNING
            command.started_at = now
            _append_progress(command, sequence, "started", stage, message, now)
            command.last_event_sequence = sequence
            command.save()
            audit(None, "deployment.command.started", command, {"kind": command.kind})
            return command
        if command.status != CommandStatus.RUNNING:
            raise ValidationError({"status": "The command has not been started."})
        _append_progress(command, sequence, event, stage, message, now)
        command.last_event_sequence = sequence
        if event == "progress":
            command.save()
            return command
        evidence = _bounded_evidence(data.get("evidence"))
        command.result = evidence
        command.finished_at = now
        if event == "failed":
            category = str(data.get("failure_category", "agent_error"))
            if category not in FAILURE_CATEGORIES:
                category = "agent_error"
            command.save()
            _finish_failed(command, category, message, now)
            return command
        if command.kind in RELEASE_KINDS and evidence.get("observed_version") != (
            command.target_version
        ):
            command.save()
            _finish_failed(
                command,
                "evidence_mismatch",
                "The reported version does not match the requested release.",
                now,
            )
            return command
        command.status = CommandStatus.AWAITING_VERIFICATION
        command.save()
        audit(
            None,
            "deployment.command.reported_success",
            command,
            {"kind": command.kind, "target_version": command.target_version},
        )
        return command


def _append_progress(
    command: AgentCommand, sequence: int, event: str, stage: str, message: str, now: datetime
) -> None:
    progress = list(command.progress or [])
    progress.append(
        {"sequence": sequence, "event": event, "stage": stage, "message": message, "at": now}
    )
    command.progress = json.loads(canonical_json(progress[-MAX_PROGRESS_EVENTS:]))


def _fail_operation(command: AgentCommand, detail: str) -> None:
    operation = command.operation
    operation.status = "failed"
    operation.completed_evidence = detail[:500]
    operation.save(update_fields=["status", "completed_evidence", "updated_at"])


def _restore_deployment_state(command: AgentCommand) -> None:
    if command.kind not in RELEASE_KINDS:
        return
    deployment = command.deployment
    deployment.desired_version = deployment.verified_version
    deployment.state = "active" if deployment.verified_version else "awaiting_installation"
    deployment.save(update_fields=["desired_version", "state", "updated_at"])


def _finish_failed(command: AgentCommand, category: str, detail: str, now: datetime) -> None:
    command.status = (
        CommandStatus.VERIFICATION_FAILED
        if category in {"verification_timeout", "evidence_mismatch"}
        and command.status == CommandStatus.AWAITING_VERIFICATION
        else CommandStatus.FAILED
    )
    command.failure_category = category
    command.failure_detail = detail[:300]
    command.finished_at = command.finished_at or now
    command.save(
        update_fields=["status", "failure_category", "failure_detail", "finished_at", "updated_at"]
    )
    _fail_operation(command, f"Failed ({category}): {detail}")
    if command.kind in RELEASE_KINDS:
        deployment = command.deployment
        deployment.state = "update_failed"
        deployment.desired_version = deployment.verified_version
        deployment.save(update_fields=["state", "desired_version", "updated_at"])
    audit(
        None,
        "deployment.command.failed",
        command,
        {"kind": command.kind, "failure_category": category},
    )
    _deployment_failure_alert(command.deployment, True, now, category)


def _deployment_failure_alert(
    deployment: Deployment, failed: bool, now: datetime, category: str = ""
) -> None:
    apply_alert_condition(
        key=f"deployment:{deployment.pk}:deployment_failed:",
        active=failed,
        now=now,
        deployment=deployment,
        category="deployment_failed",
        severity="critical",
        title="A deployment operation failed",
        evidence={"source": "agent", "failure_category": category},
    )


def _succeed(command: AgentCommand, evidence: str, now: datetime) -> None:
    command.status = CommandStatus.SUCCEEDED
    command.verified_at = now
    command.save(update_fields=["status", "verified_at", "updated_at"])
    operation = command.operation
    operation.status = "completed"
    operation.completed_evidence = evidence[:500]
    operation.save(update_fields=["status", "completed_evidence", "updated_at"])
    audit(
        None,
        "deployment.command.verified",
        command,
        {"kind": command.kind, "evidence": evidence[:200]},
    )
    _deployment_failure_alert(command.deployment, False, now)


def verify_commands(observation: MonitoringObservation) -> None:
    """Complete awaiting commands only from fresh, authoritative observations (D-120)."""
    now = timezone.now()
    for command in AgentCommand.objects.select_for_update().filter(
        deployment_id=observation.deployment_id,
        status=CommandStatus.AWAITING_VERIFICATION,
    ):
        if command.finished_at is None or observation.observed_at < command.finished_at:
            continue
        deployment = Deployment.objects.select_for_update().get(pk=command.deployment_id)
        healthy = (
            observation.store_status == "healthy"
            and observation.api_status == "healthy"
            and observation.worker_status != "unavailable"
            and observation.database_status != "unavailable"
        )
        if command.kind in RELEASE_KINDS:
            if observation.application_version != command.target_version:
                _finish_failed(
                    command,
                    "evidence_mismatch",
                    "Telemetry reports a different version than the requested release.",
                    now,
                )
                continue
            if not healthy:
                continue  # keep waiting until healthy or the verification timeout
            if command.kind == CommandKind.UPDATE_RELEASE:
                deployment.rollback_version = deployment.verified_version
            elif command.kind == CommandKind.ROLLBACK_RELEASE:
                deployment.rollback_version = ""
            deployment.verified_version = command.target_version
            deployment.verified_at = observation.observed_at
            deployment.desired_version = command.target_version
            deployment.state = "active"
            deployment.save(
                update_fields=[
                    "rollback_version",
                    "verified_version",
                    "verified_at",
                    "desired_version",
                    "state",
                    "updated_at",
                ]
            )
            store = deployment.store
            if store.status in {StoreStatus.READY, StoreStatus.PENDING, StoreStatus.REGISTERED}:
                store.status = StoreStatus.ACTIVE
                store.save(update_fields=["status", "updated_at"])
            _succeed(
                command,
                f"Verified {command.target_version} healthy by observation "
                f"{observation.pk} at {observation.observed_at.isoformat()}.",
                now,
            )
        elif command.kind == CommandKind.APPLY_CONFIGURATION:
            reported = observation.configuration_revision
            if reported is None or command.target_revision is None:
                continue
            if reported < command.target_revision:
                continue
            _mark_configuration_applied(deployment, command.target_revision, observation)
            _succeed(
                command,
                f"Store reported configuration revision {reported} at "
                f"{observation.observed_at.isoformat()}.",
                now,
            )
        elif command.kind == CommandKind.CONFIGURE_DOMAINS:
            expected = {item["domain"] for item in command.payload.get("domains", [])}
            valid = set(
                DomainRecord.objects.filter(
                    deployment=deployment, domain__in=expected, tls_state="valid"
                ).values_list("domain", flat=True)
            )
            if expected and expected <= valid:
                _succeed(
                    command,
                    f"Valid TLS observed for {len(valid)} domain(s) at "
                    f"{observation.observed_at.isoformat()}.",
                    now,
                )


def _mark_configuration_applied(
    deployment: Deployment, revision_number: int, observation: MonitoringObservation
) -> None:
    store = deployment.store
    revision = StoreConfigurationRevision.objects.filter(
        store=store, revision=revision_number
    ).first()
    if revision is None or store.applied_revision >= revision_number:
        return
    store.applied_revision = revision_number
    store.applied_theme = revision.theme
    store.applied_modules = revision.modules
    store.applied_at = observation.observed_at
    store.save(
        update_fields=[
            "applied_revision",
            "applied_theme",
            "applied_modules",
            "applied_at",
            "updated_at",
        ]
    )
    StoreConfigurationRevision.objects.filter(store=store, revision__lte=revision_number).exclude(
        status="applied"
    ).update(
        status="applied",
        applied_at=observation.observed_at,
        evidence=f"Acknowledged by Agent observation {observation.pk}.",
    )
    deployment.configuration_revision = revision_number
    deployment.save(update_fields=["configuration_revision", "updated_at"])


def verify_backup_command(deployment: Deployment, backup_status: str, sha256: str) -> None:
    now = timezone.now()
    command = (
        AgentCommand.objects.select_for_update()
        .filter(
            deployment=deployment,
            kind=CommandKind.RUN_BACKUP,
            status=CommandStatus.AWAITING_VERIFICATION,
        )
        .first()
    )
    if command is None:
        return
    if backup_status == "successful" and sha256:
        _succeed(command, f"Backup reported with SHA-256 {sha256[:16]}…", now)
    else:
        _finish_failed(command, "backup_failed", "The Agent reported a failed backup.", now)


def reconcile_commands(now: datetime | None = None) -> None:
    """Scheduled, idempotent: expire, time out and stop-wait commands; Agent connectivity alerts."""
    current = now or timezone.now()
    for command_id in AgentCommand.objects.filter(status__in=ACTIVE_COMMAND_STATUSES).values_list(
        "pk", flat=True
    ):
        with transaction.atomic():
            command = (
                AgentCommand.objects.select_for_update()
                .select_related("deployment", "operation")
                .filter(pk=command_id, status__in=ACTIVE_COMMAND_STATUSES)
                .first()
            )
            if command is None:
                continue
            if (
                command.status in {CommandStatus.QUEUED, CommandStatus.DELIVERED}
                and command.expires_at <= current
            ):
                _finish_failed(
                    command, "expired", "The command expired before it started.", current
                )
                command.status = CommandStatus.EXPIRED
                command.save(update_fields=["status", "updated_at"])
            elif (
                command.status == CommandStatus.RUNNING
                and command.updated_at < current - COMMAND_SILENCE_TIMEOUT
            ):
                _finish_failed(
                    command,
                    "agent_timeout",
                    "The Agent stopped reporting progress for this command.",
                    current,
                )
            elif (
                command.status == CommandStatus.AWAITING_VERIFICATION
                and command.finished_at is not None
                and command.finished_at < current - VERIFICATION_TIMEOUT
            ):
                _finish_failed(
                    command,
                    "verification_timeout",
                    "No healthy observation confirmed the result in time.",
                    current,
                )
    for agent in DeploymentAgent.objects.filter(status=AgentStatus.ACTIVE).select_related("server"):
        stale = agent.last_seen_at is None or agent.last_seen_at < current - AGENT_STALE_AFTER
        apply_alert_condition(
            key=f"server:{agent.server_id}:agent_disconnected",
            active=stale,
            now=current,
            deployment=None,
            server=agent.server,
            category="agent_disconnected",
            severity="critical",
            title=f"Deployment Agent on {agent.server.name} is not connected",
            evidence={
                "source": "control_evaluation",
                "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else "",
            },
        )


# ---------------------------------------------------------------- domains (D-122)


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or not all(DOMAIN_LABEL.fullmatch(label) for label in labels)
        or labels[-1].isdigit()
    ):
        raise ValidationError(
            {"domain": "Enter a fully qualified domain name (no IP or wildcard)."}
        )
    return domain


def resolve_records(name: str, record_type: str) -> list[str]:  # pragma: no cover - network
    import dns.exception
    import dns.resolver

    try:
        answer = dns.resolver.resolve(name, record_type, lifetime=5.0)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException:
        raise
    if record_type == "TXT":
        return [b"".join(item.strings).decode("ascii", "replace") for item in answer]
    return [item.to_text() for item in answer]


def verify_domain(domain: DomainRecord, actor: AdminUser, resolver: Any = None) -> DomainRecord:
    """Check the ownership TXT token and that A/AAAA records point at the Server's address.

    Only DNS is queried; Control never connects to the domain itself (no SSRF surface).
    """
    lookup = resolver or resolve_records
    now = timezone.now()
    with transaction.atomic():
        record = (
            DomainRecord.objects.select_for_update(of=("self",))
            .select_related("deployment__server")
            .get(pk=domain.pk)
        )
        if not record.verification_token:
            record.verification_token = secrets.token_hex(16)
        try:
            txt = lookup(f"_matecart-verify.{record.domain}", "TXT")
            addresses = lookup(record.domain, "A") + lookup(record.domain, "AAAA")
        except Exception:
            record.verification_detail = "DNS lookup failed; try again later."
            record.verification_checked_at = now
            record.save()
            return record
        owned = record.verification_token in txt
        server_ip = (
            record.deployment.server.public_ip
            if record.deployment and record.deployment.server
            else None
        )
        points_here = bool(server_ip) and str(server_ip) in addresses
        record.verification_state = "verified" if owned else "failed"
        record.dns_state = "ready" if points_here else ("mismatch" if addresses else "missing")
        record.verification_checked_at = now
        record.last_verified_at = now if owned else record.last_verified_at
        record.verification_detail = (
            "Ownership verified." if owned else "TXT record not found or does not match."
        )
        if owned and not points_here:
            record.verification_detail = "Ownership verified; A/AAAA do not point at the Server."
        record.save()
        audit(
            actor,
            "domain.verification.checked",
            record,
            {
                "verification_state": record.verification_state,
                "dns_state": record.dns_state,
            },
        )
    return record


def apply_domain_observations(deployment: Deployment, items: Any) -> None:
    if not isinstance(items, list):
        return
    now = timezone.now()
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("domain", "")).lower()
        record = DomainRecord.objects.filter(deployment=deployment, domain=name).first()
        if record is None:
            continue
        tls_state = str(item.get("tls_state", "unknown"))
        if tls_state not in {"valid", "invalid", "missing", "unknown"}:
            tls_state = "unknown"
        renewal = str(item.get("renewal_state", "unknown"))
        if renewal not in {"ok", "renewal_due", "renewal_failed", "unknown"}:
            renewal = "unknown"
        expires = parse_datetime(str(item.get("certificate_expires_at") or ""))
        record.tls_state = tls_state
        record.renewal_state = renewal
        record.certificate_expires_at = expires
        record.configured = tls_state == "valid"
        record.save(
            update_fields=[
                "tls_state",
                "renewal_state",
                "certificate_expires_at",
                "configured",
                "updated_at",
            ]
        )
        apply_alert_condition(
            key=f"deployment:{deployment.pk}:certificate_renewal_failed:{record.pk}",
            active=renewal == "renewal_failed" if renewal != "unknown" else None,
            now=now,
            deployment=deployment,
            category="certificate_renewal_failed",
            severity="critical",
            title=f"TLS certificate renewal failing for {record.domain}",
            evidence={"source": "agent"},
        )
