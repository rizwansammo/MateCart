"""Deployment Agent API (/api/agent/v1/, D-119).

Every request except enrollment is HMAC-signed by the Agent (method, path, timestamp, single-use
nonce, body digest). Every successful response is signed back with the same secret and bound to
the request nonce, so an Agent never acts on an unsigned or replayed response. No endpoint
accepts or returns business records, and none can run arbitrary commands.
"""

import uuid
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.views import APIView

from control.core import ratelimit
from control.core.http import client_ip

from .deployment import (
    AgentRequest,
    authenticate_agent,
    begin_rotation,
    canonical_json,
    command_envelope,
    enroll_agent,
    lease_next_command,
    record_command_event,
    record_heartbeat,
    response_signature,
    verify_backup_command,
)
from .models import BackupObservation, Deployment
from .serializers import (
    AGENT_BACKUP_TYPES,
    BACKUP_RESULTS,
    HEALTH_STATES,
    PlatformSerializer,
    TelemetrySerializer,
)
from .services import record_observation, validate_metrics

MAX_AGENT_BODY = 65536
AGENT_REPORT_WINDOW = timedelta(hours=24)


def _json(status_code: int, data: Any, signer: AgentRequest | None = None) -> HttpResponse:
    body = canonical_json(data)
    response = HttpResponse(body, status=status_code, content_type="application/json")
    response["Cache-Control"] = "no-store"
    if signer is not None:
        response["X-MateCart-Signature"] = response_signature(
            signer.secret, signer.nonce, status_code, body
        )
    return response


class AgentChallenge(BaseAuthentication):
    """Signature checks run in each view (they need the raw body); this class only makes
    authentication failures answer 401 with a challenge instead of 403."""

    def authenticate(self, request: Request) -> None:
        return None

    def authenticate_header(self, request: Request) -> str:
        return "MateCart-Agent-HMAC"


class AgentView(APIView):
    authentication_classes = (AgentChallenge,)
    permission_classes = (AllowAny,)
    serializer_class: type[serializers.BaseSerializer[Any]] = PlatformSerializer

    def agent_request(self, request: Request) -> AgentRequest:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_AGENT_BODY:
            raise ValidationError({"payload": "Agent payload is too large."})
        ratelimit.consume("agent", client_ip(request), limit=600, window=60)
        return authenticate_agent(
            method=request.method or "",
            path=request.path,
            headers=request.headers,
            body=request.body,
        )


class EnrollSerializer(serializers.Serializer[dict[str, Any]]):
    token = serializers.CharField(max_length=256)
    agent_version = serializers.CharField(max_length=40)
    host_facts = serializers.DictField(required=False)


class AgentEnrollView(AgentView):
    serializer_class = EnrollSerializer

    def post(self, request: Request) -> HttpResponse:
        ratelimit.consume("agent-enroll", client_ip(request), limit=10, window=600)
        serializer = EnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        agent, secret = enroll_agent(data["token"], data["agent_version"], data.get("host_facts"))
        return _json(
            201,
            {
                "agent_id": agent.pk,
                "server_id": agent.server_id,
                "key_id": agent.key_id,
                "secret": secret,
                "credential_expires_at": agent.credential_expires_at,
                "message": "Store this credential on the Server only. It is not shown again.",
            },
        )


class AgentHeartbeatView(AgentView):
    def post(self, request: Request) -> HttpResponse:
        signed = self.agent_request(request)
        data = request.data if isinstance(request.data, dict) else {}
        return _json(200, record_heartbeat(signed.agent, data), signed)


class AgentRotateView(AgentView):
    def post(self, request: Request) -> HttpResponse:
        signed = self.agent_request(request)
        secret = begin_rotation(signed.agent)
        return _json(200, {"secret": secret, "key_id": signed.agent.key_id}, signed)


class AgentObservationSerializer(TelemetrySerializer):
    worker_status = serializers.ChoiceField(choices=HEALTH_STATES, required=False, allow_blank=True)
    database_status = serializers.ChoiceField(
        choices=HEALTH_STATES, required=False, allow_blank=True
    )
    configuration_revision = serializers.IntegerField(
        min_value=0, max_value=1_000_000, required=False, allow_null=True
    )
    domains = serializers.ListField(child=serializers.DictField(), required=False, max_length=20)


class AgentObservationView(AgentView):
    serializer_class = AgentObservationSerializer

    def post(self, request: Request) -> HttpResponse:
        signed = self.agent_request(request)
        serializer = AgentObservationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        now = timezone.now()
        # Buffered reports from a disconnected Agent stay valid history for 24 hours; only a
        # strictly newer report becomes authoritative (D-115).
        if not now - AGENT_REPORT_WINDOW <= data["observed_at"] <= now + timedelta(minutes=2):
            raise ValidationError({"observed_at": "The report is outside the accepted window."})
        validate_metrics(data)
        try:
            with transaction.atomic():
                deployment = (
                    Deployment.objects.select_for_update()
                    .select_related("store")
                    .filter(pk=data["deployment_id"], server_id=signed.agent.server_id)
                    .first()
                )
                if deployment is None:
                    raise ValidationError(
                        {"deployment_id": "This Agent does not manage that Deployment."}
                    )
                observation = record_observation(deployment, data, now, agent=signed.agent)
        except IntegrityError as exc:
            raise ValidationError({"report_id": "This report was already received."}) from exc
        return _json(202, {"observation_id": observation.pk}, signed)


class AgentBackupSerializer(serializers.Serializer[dict[str, Any]]):
    deployment_id = serializers.UUIDField()
    report_id = serializers.UUIDField()
    backup_type = serializers.ChoiceField(choices=AGENT_BACKUP_TYPES)
    status = serializers.ChoiceField(choices=BACKUP_RESULTS)
    started_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField()
    size_bytes = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    sha256 = serializers.RegexField(r"^[0-9a-f]{64}$", required=False, allow_blank=True)
    artifact_name = serializers.RegexField(
        r"^[A-Za-z0-9._-]{1,200}$", required=False, allow_blank=True
    )
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class AgentBackupView(AgentView):
    serializer_class = AgentBackupSerializer

    def post(self, request: Request) -> HttpResponse:
        signed = self.agent_request(request)
        serializer = AgentBackupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            deployment = (
                Deployment.objects.select_for_update()
                .filter(pk=data["deployment_id"], server_id=signed.agent.server_id)
                .first()
            )
            if deployment is None:
                raise ValidationError(
                    {"deployment_id": "This Agent does not manage that Deployment."}
                )
            existing = BackupObservation.objects.filter(
                deployment=deployment, report_id=data["report_id"]
            ).first()
            if existing is None:
                existing = BackupObservation.objects.create(
                    deployment=deployment,
                    report_id=data["report_id"],
                    backup_type=data["backup_type"],
                    observed_at=data["finished_at"],
                    backup_at=data["finished_at"] if data["status"] == "successful" else None,
                    status=data["status"],
                    source="agent",
                    size_bytes=data.get("size_bytes"),
                    sha256=data.get("sha256", ""),
                    artifact_name=data.get("artifact_name", ""),
                    notes=data.get("notes", ""),
                )
                verify_backup_command(deployment, data["status"], data.get("sha256", ""))
        return _json(202, {"backup_observation_id": existing.pk}, signed)


class AgentCommandLeaseView(AgentView):
    def post(self, request: Request) -> HttpResponse:
        signed = self.agent_request(request)
        command = lease_next_command(signed.agent)
        return _json(200, {"command": command_envelope(command) if command else None}, signed)


class AgentCommandEventView(AgentView):
    def post(self, request: Request, pk: uuid.UUID) -> HttpResponse:
        signed = self.agent_request(request)
        data = request.data if isinstance(request.data, dict) else {}
        command = record_command_event(signed.agent, pk, data)
        return _json(
            200,
            {
                "command_id": command.pk,
                "status": command.status,
                "last_event_sequence": command.last_event_sequence,
            },
            signed,
        )
