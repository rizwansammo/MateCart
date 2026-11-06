"""Console endpoints for Phase 17: Agents, releases, commands, domain verification, backups."""

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from control.accounts.permissions import require_permission
from control.accounts.sessions import require_recent_authentication

from .deployment import (
    AGENT_STALE_AFTER,
    issue_enrollment,
    register_release,
    revoke_agent,
    verify_domain,
)
from .models import (
    AgentCommand,
    AgentEnrollment,
    AgentStatus,
    BackupPolicy,
    Deployment,
    DeploymentAgent,
    DomainRecord,
    ReleaseRecord,
    Server,
)
from .services import audit
from .views import ConsolePlatformView, get_or_404


def agent_row(agent: DeploymentAgent | None) -> dict[str, Any] | None:
    if agent is None:
        return None
    now = timezone.now()
    connected = (
        agent.status == AgentStatus.ACTIVE
        and agent.last_seen_at is not None
        and agent.last_seen_at >= now - AGENT_STALE_AFTER
    )
    return {
        "id": agent.pk,
        "server_id": agent.server_id,
        "status": agent.status,
        "connection": "connected" if connected else "disconnected",
        "agent_version": agent.agent_version,
        "host_facts": agent.host_facts,
        "enrolled_at": agent.enrolled_at,
        "last_seen_at": agent.last_seen_at,
        "credential_expires_at": agent.credential_expires_at,
        "revoked_at": agent.revoked_at,
    }


def command_row(item: AgentCommand) -> dict[str, Any]:
    return {
        "id": item.pk,
        "kind": item.kind,
        "status": item.status,
        "deployment_id": item.deployment_id,
        "operation_id": item.operation_id,
        "target_version": item.target_version,
        "previous_version": item.previous_version,
        "target_revision": item.target_revision,
        "created_at": item.created_at,
        "expires_at": item.expires_at,
        "delivered_at": item.delivered_at,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "verified_at": item.verified_at,
        "progress": item.progress,
        "failure_category": item.failure_category,
        "failure_detail": item.failure_detail,
    }


def release_row(item: ReleaseRecord) -> dict[str, Any]:
    database = item.manifest.get("database", {})
    return {
        "id": item.pk,
        "version": item.version,
        "status": item.status,
        "git_commit": item.manifest.get("git_commit", ""),
        "created_at": item.created_at,
        "manifest_sha256": item.manifest_sha256,
        "images": {
            name: f"{image['repository']}@{image['digest']}"
            for name, image in item.manifest.get("images", {}).items()
        },
        "rollback_compatible_with": database.get("commerce", {}).get(
            "rollback_compatible_with", []
        ),
        "notes": item.notes,
    }


def backup_policy_row(policy: BackupPolicy | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {
        "enabled": policy.enabled,
        "schedule_hour_utc": policy.schedule_hour_utc,
        "schedule_minute": policy.schedule_minute,
        "retention_days": policy.retention_days,
        "include_media": policy.include_media,
        "destination_kind": policy.destination_kind,
        "destination_label": policy.destination_label,
        "revision": policy.revision,
    }


def deployment_automation(deployment: Deployment) -> dict[str, Any]:
    """Agent, readiness, commands and policy for the Deployment detail screen."""
    agent = DeploymentAgent.objects.filter(
        server_id=deployment.server_id, status=AgentStatus.ACTIVE
    ).first()
    agent_info = agent_row(agent)
    domains = DomainRecord.objects.filter(deployment=deployment)
    policy = BackupPolicy.objects.filter(deployment=deployment).first()
    checks = {
        "agent_connected": bool(agent_info and agent_info["connection"] == "connected"),
        "release_available": ReleaseRecord.objects.filter(status="available").exists(),
        "domain_verified": domains.filter(
            verification_state="verified", dns_state="ready"
        ).exists(),
        "backup_policy": policy is not None and policy.enabled,
    }
    return {
        "agent": agent_info,
        "readiness": {"ready": all(checks.values()), "checks": checks},
        "rollback_eligible": bool(deployment.rollback_version),
        "commands": [command_row(item) for item in deployment.commands.all()[:25]],
        "backup_policy": backup_policy_row(policy),
    }


class ServerAgentView(ConsolePlatformView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "servers.read")
        server = get_or_404(Server, pk)
        return Response(
            {
                "agent": agent_row(
                    DeploymentAgent.objects.filter(server=server, status=AgentStatus.ACTIVE).first()
                ),
                "history": [
                    agent_row(item) for item in DeploymentAgent.objects.filter(server=server)[:20]
                ],
                "enrollments": [
                    {
                        "id": item.pk,
                        "last_four": item.last_four,
                        "created_at": item.created_at,
                        "expires_at": item.expires_at,
                        "used_at": item.used_at,
                    }
                    for item in AgentEnrollment.objects.filter(server=server)[:20]
                ],
            }
        )

    def post(self, request: Request, pk: uuid.UUID) -> Response:
        """Issue a single-use, one-hour enrollment token (shown once)."""
        require_permission(self.admin, "servers.manage")
        require_recent_authentication(self.session)
        server = get_or_404(Server, pk)
        enrollment, token = issue_enrollment(server, self.admin)
        return Response(
            {
                "id": enrollment.pk,
                "token": token,
                "expires_at": enrollment.expires_at,
                "message": (
                    "Copy this enrollment token now. It is single-use and expires in 1 hour."
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "servers.manage")
        require_recent_authentication(self.session)
        if request.data.get("action") != "revoke":
            raise ValidationError({"action": "Choose revoke."})
        agent = DeploymentAgent.objects.filter(server_id=pk, status=AgentStatus.ACTIVE).first()
        if agent is None:
            raise NotFound()
        return Response(agent_row(revoke_agent(agent, self.admin)))


class ReleaseCollectionView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "deployments.read")
        rows = [release_row(item) for item in ReleaseRecord.objects.all()[:100]]
        return Response({"count": len(rows), "next": None, "previous": None, "results": rows})

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "deployments.manage")
        require_recent_authentication(self.session)
        release = register_release(
            request.data.get("manifest"), self.admin, str(request.data.get("notes", ""))
        )
        return Response(release_row(release), status=status.HTTP_201_CREATED)


class ReleaseDetailView(ConsolePlatformView):
    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "deployments.manage")
        require_recent_authentication(self.session)
        if request.data.get("status") != "withdrawn":
            raise ValidationError({"status": "A release can only be withdrawn."})
        with transaction.atomic():
            release = ReleaseRecord.objects.select_for_update().filter(pk=pk).first()
            if release is None:
                raise NotFound()
            release.status = "withdrawn"
            release.save(update_fields=["status", "updated_at"])
            audit(self.admin, "release.withdrawn", release, {"version": release.version})
        return Response(release_row(release))


class DeploymentCommandView(ConsolePlatformView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "deployments.read")
        deployment = get_or_404(Deployment, pk)
        return Response(deployment_automation(deployment))


class BackupPolicySerializer(serializers.Serializer[dict[str, Any]]):
    enabled = serializers.BooleanField()
    schedule_hour_utc = serializers.IntegerField(min_value=0, max_value=23)
    schedule_minute = serializers.IntegerField(min_value=0, max_value=59)
    retention_days = serializers.IntegerField(min_value=1, max_value=365)
    include_media = serializers.BooleanField()
    # Only local Server storage is available until the owner selects a backup provider.
    destination_kind = serializers.ChoiceField(choices=["local"])
    destination_label = serializers.CharField(max_length=100, required=False, allow_blank=True)


class BackupPolicyView(ConsolePlatformView):
    serializer_class = BackupPolicySerializer  # type: ignore[assignment]

    def put(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "deployments.manage")
        serializer = BackupPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            deployment = Deployment.objects.select_for_update().filter(pk=pk).first()
            if deployment is None:
                raise NotFound()
            policy = BackupPolicy.objects.filter(deployment=deployment).first()
            if policy is None:
                policy = BackupPolicy.objects.create(deployment=deployment, **data)
            else:
                for key, value in data.items():
                    setattr(policy, key, value)
                policy.revision += 1
                policy.save()
            audit(self.admin, "backup.policy.updated", policy, {"revision": policy.revision})
        return Response(backup_policy_row(policy))


class DomainVerifyView(ConsolePlatformView):
    def post(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "deployments.manage")
        require_recent_authentication(self.session)
        domain = get_or_404(DomainRecord, pk)
        if domain.deployment_id is None:
            raise ValidationError({"deployment": "Assign the domain to a Deployment first."})
        record = verify_domain(domain, self.admin)
        return Response(
            {
                "id": record.pk,
                "domain": record.domain,
                "verification_state": record.verification_state,
                "dns_state": record.dns_state,
                "verification_detail": record.verification_detail,
                "txt_record_name": f"_matecart-verify.{record.domain}",
                "txt_record_value": record.verification_token,
                "checked_at": record.verification_checked_at,
            }
        )
