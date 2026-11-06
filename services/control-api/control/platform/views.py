import uuid
from datetime import timedelta
from typing import Any, ClassVar, cast

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from control.accounts import services as account_services
from control.accounts.auth import ConsoleSessionAuthentication, CsrfProtectedAPIView
from control.accounts.models import AdminRole, AdminSession, AdminUser
from control.accounts.permissions import require_permission
from control.accounts.sessions import require_recent_authentication, revoke_all
from control.core import ratelimit
from control.core.http import client_ip

from .constants import capabilities
from .models import (
    BackupObservation,
    Client,
    ClientContact,
    ConsoleAuditEvent,
    ConsoleNotification,
    Deployment,
    DomainRecord,
    MonitoringObservation,
    OperationalAlert,
    OperationalRequest,
    Server,
    StoreRegistration,
)
from .serializers import (
    BackupSerializer,
    ClientSerializer,
    ContactSerializer,
    DeploymentSerializer,
    DomainSerializer,
    OperationSerializer,
    PlatformSerializer,
    ServerSerializer,
    StoreSerializer,
    TelemetrySerializer,
)
from .services import add_revision, audit, create_monitoring_credential, ingest_observation


class ConsolePlatformView(CsrfProtectedAPIView):
    authentication_classes = (ConsoleSessionAuthentication,)
    permission_classes = (IsAuthenticated,)
    serializer_class = PlatformSerializer

    @property
    def admin(self) -> AdminUser:
        return cast(AdminUser, self.request.user)

    @property
    def session(self) -> AdminSession:
        return cast(AdminSession, self.request.auth)


def page(request: Request, queryset: Any, serializer: Any) -> Response:
    paginator = PageNumberPagination()
    rows = paginator.paginate_queryset(queryset, request)
    return paginator.get_paginated_response(serializer(rows, many=True).data)


def body(
    serializer_class: Any, request: Request, *, instance: Any = None, partial: bool = False
) -> Any:
    serializer = serializer_class(instance, data=request.data, partial=partial)
    serializer.is_valid(raise_exception=True)
    return serializer


def get_or_404(model: Any, pk: uuid.UUID, **filters: Any) -> Any:
    row = model.objects.filter(pk=pk, **filters).first()
    if row is None:
        raise NotFound()
    return row


class OverviewView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "monitoring.read")
        cutoff = timezone.now() - timedelta(minutes=5)
        deployments = Deployment.objects.all()
        healthy = (
            deployments.filter(
                last_observed_at__gte=cutoff,
                observations__store_status="healthy",
                observations__api_status="healthy",
            )
            .distinct()
            .count()
        )
        stale = deployments.filter(last_observed_at__lt=cutoff).count()
        unknown = deployments.filter(last_observed_at__isnull=True).count()
        return Response(
            {
                "clients": Client.objects.count(),
                "stores": StoreRegistration.objects.count(),
                "servers": Server.objects.count(),
                "deployments": deployments.count(),
                "healthy": healthy,
                "stale": stale,
                "unknown": unknown,
                "critical_alerts": OperationalAlert.objects.filter(
                    status__in=["open", "acknowledged"], severity="critical"
                ).count(),
                "pending_operations": OperationalRequest.objects.filter(
                    status__in=["submitted", "approved", "pending_execution"]
                ).count(),
                "recent_activity": [
                    {
                        "id": str(item.pk),
                        "action": item.action,
                        "resource_type": item.resource_type,
                        "created_at": item.created_at,
                    }
                    for item in ConsoleAuditEvent.objects.all()[:10]
                ],
            }
        )


class CapabilitiesView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "stores.read")
        return Response(capabilities())


class ClientCollectionView(ConsolePlatformView):
    @extend_schema(operation_id="console_client_list", responses={200: ClientSerializer(many=True)})
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "clients.read")
        rows = Client.objects.annotate(
            stores_count=Count("stores"), servers_count=Count("servers", distinct=True)
        ).order_by("display_name", "id")
        query = request.query_params.get("q", "").strip()
        if query:
            rows = rows.filter(
                Q(display_name__icontains=query)
                | Q(contact_email__icontains=query)
                | Q(internal_reference__icontains=query)
            )
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        return page(request, rows, ClientSerializer)

    @extend_schema(
        operation_id="console_client_create",
        request=ClientSerializer,
        responses={201: ClientSerializer},
    )
    def post(self, request: Request) -> Response:
        require_permission(self.admin, "clients.manage")
        serializer = body(ClientSerializer, request)
        with transaction.atomic():
            client = serializer.save()
            audit(self.admin, "client.created", client, {"status": client.status})
        return Response(ClientSerializer(client).data, status=status.HTTP_201_CREATED)


class ClientDetailView(ConsolePlatformView):
    @extend_schema(operation_id="console_client_retrieve", responses={200: ClientSerializer})
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "clients.read")
        client = get_or_404(Client, pk)
        result = ClientSerializer(client).data
        result["contacts"] = ContactSerializer(client.contacts.all(), many=True).data
        result["stores"] = StoreSerializer(client.stores.all(), many=True).data
        result["servers"] = ServerSerializer(client.servers.all(), many=True).data
        return Response(result)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "clients.manage")
        with transaction.atomic():
            client = Client.objects.select_for_update().filter(pk=pk).first()
            if client is None:
                raise NotFound()
            before = {"status": client.status, "display_name": client.display_name}
            serializer = body(ClientSerializer, request, instance=client, partial=True)
            client = serializer.save()
            audit(
                self.admin,
                "client.updated",
                client,
                {"before": before, "status": client.status, "display_name": client.display_name},
            )
        return Response(ClientSerializer(client).data)


class ContactCollectionView(ConsolePlatformView):
    def get(self, request: Request, client_id: uuid.UUID) -> Response:
        require_permission(self.admin, "clients.read")
        return page(request, ClientContact.objects.filter(client_id=client_id), ContactSerializer)

    def post(self, request: Request, client_id: uuid.UUID) -> Response:
        require_permission(self.admin, "clients.manage")
        client = get_or_404(Client, client_id)
        serializer = body(ContactSerializer, request)
        with transaction.atomic():
            contact = serializer.save(client=client)
            audit(self.admin, "client.contact.created", contact, {"client_id": str(client.pk)})
        return Response(ContactSerializer(contact).data, status=status.HTTP_201_CREATED)


class StoreCollectionView(ConsolePlatformView):
    @extend_schema(operation_id="console_store_list", responses={200: StoreSerializer(many=True)})
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "stores.read")
        rows = StoreRegistration.objects.select_related("client")
        if query := request.query_params.get("q", "").strip():
            rows = rows.filter(
                Q(name__icontains=query)
                | Q(handle__icontains=query)
                | Q(client__display_name__icontains=query)
            )
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        return page(request, rows, StoreSerializer)

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "stores.manage")
        serializer = body(StoreSerializer, request)
        try:
            with transaction.atomic():
                store = serializer.save()
                add_revision(store, self.admin)
                audit(
                    self.admin,
                    "store.registered",
                    store,
                    {"store_id": str(store.pk), "preset": store.preset},
                )
        except IntegrityError as exc:
            raise ValidationError({"handle": "This store handle is already registered."}) from exc
        return Response(StoreSerializer(store).data, status=status.HTTP_201_CREATED)


class StoreDetailView(ConsolePlatformView):
    @extend_schema(operation_id="console_store_retrieve", responses={200: StoreSerializer})
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "stores.read")
        store = get_or_404(StoreRegistration, pk)
        result = StoreSerializer(store).data
        result["revisions"] = [
            {
                "id": str(item.pk),
                "revision": item.revision,
                "status": item.status,
                "theme": item.theme,
                "modules": item.modules,
                "created_at": item.created_at,
                "applied_at": item.applied_at,
            }
            for item in store.revisions.all()
        ]
        result["deployments"] = DeploymentSerializer(
            store.deployments.select_related("server"), many=True
        ).data
        result["domains"] = DomainSerializer(
            store.domains.select_related("deployment"), many=True
        ).data
        result["alerts"] = [
            alert_row(item)
            for item in OperationalAlert.objects.filter(deployment__store=store)[:50]
        ]
        result["provisioning_manifest"] = {
            "store_id": str(store.pk),
            "preset": store.preset,
            "country": store.country,
            "currency": store.default_currency,
            "time_zone": store.time_zone,
            "languages": store.supported_languages,
            "theme": store.desired_theme,
            "modules": store.desired_modules,
            "environment": store.environment_designation,
            "configuration_revision": store.desired_revision,
        }
        return Response(result)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "store_configuration.manage")
        require_recent_authentication(self.session)
        with transaction.atomic():
            store = StoreRegistration.objects.select_for_update().filter(pk=pk).first()
            if store is None:
                raise NotFound()
            before = (
                store.preset,
                store.desired_theme,
                list(store.desired_modules),
                list(store.supported_languages),
            )
            serializer = body(StoreSerializer, request, instance=store, partial=True)
            store = serializer.save()
            after = (
                store.preset,
                store.desired_theme,
                list(store.desired_modules),
                list(store.supported_languages),
            )
            if before != after:
                store.desired_revision += 1
                store.save(update_fields=["desired_revision", "updated_at"])
                add_revision(store, self.admin)
            audit(
                self.admin,
                "store.updated",
                store,
                {
                    "configuration_changed": before != after,
                    "desired_revision": store.desired_revision,
                },
            )
        return Response(StoreSerializer(store).data)


class ServerCollectionView(ConsolePlatformView):
    @extend_schema(operation_id="console_server_list", responses={200: ServerSerializer(many=True)})
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "servers.read")
        rows = Server.objects.select_related("owner_client").annotate(
            deployments_count=Count("deployments")
        )
        if query := request.query_params.get("q", "").strip():
            rows = rows.filter(
                Q(name__icontains=query)
                | Q(hostname__icontains=query)
                | Q(provider__icontains=query)
            )
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        return page(request, rows, ServerSerializer)

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "servers.manage")
        serializer = body(ServerSerializer, request)
        with transaction.atomic():
            server = serializer.save()
            audit(self.admin, "server.registered", server, {"ownership": server.ownership})
        return Response(ServerSerializer(server).data, status=status.HTTP_201_CREATED)


class ServerDetailView(ConsolePlatformView):
    @extend_schema(operation_id="console_server_retrieve", responses={200: ServerSerializer})
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "servers.read")
        server = get_or_404(Server, pk)
        result = ServerSerializer(server).data
        result["deployments"] = DeploymentSerializer(server.deployments.all(), many=True).data
        result["alerts"] = [alert_row(item) for item in server.alerts.all()[:50]]
        return Response(result)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "servers.manage")
        server = get_or_404(Server, pk)
        serializer = body(ServerSerializer, request, instance=server, partial=True)
        with transaction.atomic():
            server = serializer.save()
            audit(self.admin, "server.updated", server, {"fields": sorted(request.data)})
        return Response(ServerSerializer(server).data)


def deployment_health(row: Deployment) -> str:
    if row.last_observed_at is None:
        return "unknown"
    if row.last_observed_at < timezone.now() - timedelta(minutes=5):
        return "stale"
    latest = row.observations.first()
    if latest is None:
        return "unknown"
    if "unavailable" in {latest.store_status, latest.api_status}:
        return "unavailable"
    if "degraded" in {latest.store_status, latest.api_status}:
        return "degraded"
    return "healthy"


class DeploymentCollectionView(ConsolePlatformView):
    @extend_schema(
        operation_id="console_deployment_list", responses={200: DeploymentSerializer(many=True)}
    )
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "deployments.read")
        rows = list(Deployment.objects.select_related("store", "server"))
        data = DeploymentSerializer(rows, many=True).data
        for item, row in zip(data, rows, strict=True):
            item["health_state"] = deployment_health(row)
        return Response({"count": len(data), "next": None, "previous": None, "results": data})

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "deployments.manage")
        serializer = body(DeploymentSerializer, request)
        try:
            with transaction.atomic():
                deployment = serializer.save()
                audit(self.admin, "deployment.registered", deployment, {"state": deployment.state})
        except IntegrityError as exc:
            raise ValidationError(
                {"environment": "This store already has that environment."}
            ) from exc
        return Response(DeploymentSerializer(deployment).data, status=status.HTTP_201_CREATED)


class DeploymentDetailView(ConsolePlatformView):
    @extend_schema(
        operation_id="console_deployment_retrieve", responses={200: DeploymentSerializer}
    )
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "deployments.read")
        deployment = get_or_404(Deployment, pk)
        result = DeploymentSerializer(deployment).data
        result["health_state"] = deployment_health(deployment)
        result["observations"] = [
            observation_row(item) for item in deployment.observations.all()[:100]
        ]
        result["domains"] = DomainSerializer(deployment.domains.all(), many=True).data
        result["backups"] = BackupSerializer(deployment.backups.all()[:100], many=True).data
        result["alerts"] = [alert_row(item) for item in deployment.alerts.all()[:50]]
        return Response(result)


class OperationCollectionView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "operations.read")
        rows = OperationalRequest.objects.select_related("requested_by")
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        return page(request, rows, OperationSerializer)

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "operations.manage")
        serializer = body(OperationSerializer, request)
        with transaction.atomic():
            operation = serializer.save(requested_by=self.admin)
            audit(
                self.admin,
                "operation.created",
                operation,
                {"kind": operation.kind, "status": operation.status},
            )
        return Response(OperationSerializer(operation).data, status=status.HTTP_201_CREATED)


class OperationDetailView(ConsolePlatformView):
    TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        "draft": {"submitted", "cancelled"},
        "submitted": {"approved", "rejected", "cancelled"},
        "approved": {"pending_execution", "cancelled"},
        "pending_execution": {"completed", "cancelled"},
    }

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "operations.manage")
        with transaction.atomic():
            operation = OperationalRequest.objects.select_for_update().filter(pk=pk).first()
            if operation is None:
                raise NotFound()
            target = str(request.data.get("status", ""))
            if target not in self.TRANSITIONS.get(operation.status, set()):
                raise ValidationError({"status": "That workflow transition is not allowed."})
            if target in {"approved", "rejected", "completed"}:
                require_recent_authentication(self.session)
                if operation.requested_by_id == self.admin.pk and target == "approved":
                    raise ValidationError(
                        {"status": "The requester cannot approve their own request."}
                    )
                operation.decided_by = self.admin
                operation.decided_at = timezone.now()
            if target == "completed" and not request.data.get("completed_evidence"):
                raise ValidationError(
                    {"completed_evidence": "Verified evidence is required before completion."}
                )
            operation.status = target
            operation.completed_evidence = str(
                request.data.get("completed_evidence", operation.completed_evidence)
            )
            operation.save()
            audit(self.admin, f"operation.{target}", operation, {"status": target})
        return Response(OperationSerializer(operation).data)


class DomainCollectionView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "deployments.read")
        return page(
            request,
            DomainRecord.objects.select_related("store", "deployment"),
            DomainSerializer,
        )

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "deployments.manage")
        serializer = body(DomainSerializer, request)
        with transaction.atomic():
            domain = serializer.save()
            audit(self.admin, "domain.registered", domain, {"domain": domain.domain})
        return Response(DomainSerializer(domain).data, status=status.HTTP_201_CREATED)


class BackupCollectionView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "monitoring.read")
        return page(
            request,
            BackupObservation.objects.select_related("deployment__store").order_by("-observed_at"),
            BackupSerializer,
        )

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "monitoring.manage")
        serializer = body(BackupSerializer, request)
        with transaction.atomic():
            backup = serializer.save(source="manual")
            audit(
                self.admin,
                "backup.observation.recorded",
                backup,
                {"status": backup.status, "source": "manual"},
            )
        return Response(BackupSerializer(backup).data, status=status.HTTP_201_CREATED)


class MonitoringCredentialView(ConsolePlatformView):
    def post(self, request: Request, deployment_id: uuid.UUID) -> Response:
        require_permission(self.admin, "monitoring.manage")
        require_recent_authentication(self.session)
        deployment = get_or_404(Deployment, deployment_id)
        label = str(request.data.get("label", "Deployment telemetry")).strip()[:100]
        credential, token = create_monitoring_credential(deployment, self.admin, label)
        return Response(
            {
                "id": credential.pk,
                "token": token,
                "last_four": credential.last_four,
                "message": "Copy this credential now. It will not be shown again.",
            },
            status=status.HTTP_201_CREATED,
        )


class TelemetryIngestView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    serializer_class = TelemetrySerializer

    def post(self, request: Request) -> Response:
        if int(request.META.get("CONTENT_LENGTH") or 0) > 16384:
            raise ValidationError({"payload": "Telemetry payload is too large."})
        ratelimit.consume("telemetry", client_ip(request), limit=120, window=60)
        authorization = request.headers.get("Authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        serializer = TelemetrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            observation = ingest_observation(token, serializer.validated_data)
        except IntegrityError as exc:
            raise ValidationError(
                {"report_id": "This telemetry report was already received."}
            ) from exc
        return Response(
            {"observation_id": observation.pk, "received_at": observation.received_at},
            status=status.HTTP_202_ACCEPTED,
        )


def observation_row(item: MonitoringObservation) -> dict[str, Any]:
    return {
        "id": item.pk,
        "deployment_id": item.deployment_id,
        "store_id": item.deployment.store_id,
        "store_name": item.deployment.store.name,
        "observed_at": item.observed_at,
        "received_at": item.received_at,
        "source": item.source,
        "store_status": item.store_status,
        "api_status": item.api_status,
        "response_ms": item.response_ms,
        "cpu_percent": item.cpu_percent,
        "ram_percent": item.ram_percent,
        "disk_percent": item.disk_percent,
        "visitor_count_24h": item.visitor_count_24h,
        "application_version": item.application_version,
    }


class MonitoringView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "monitoring.read")
        rows = MonitoringObservation.objects.select_related("deployment__store")
        if deployment_id := request.query_params.get("deployment"):
            rows = rows.filter(deployment_id=deployment_id)
        return Response(
            {
                "count": rows.count(),
                "next": None,
                "previous": None,
                "results": [observation_row(item) for item in rows[:250]],
            }
        )


def alert_row(item: OperationalAlert) -> dict[str, Any]:
    return {
        "id": item.pk,
        "category": item.category,
        "severity": item.severity,
        "status": item.status,
        "title": item.title,
        "details": item.details,
        "deployment_id": item.deployment_id,
        "server_id": item.server_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "acknowledged_at": item.acknowledged_at,
        "resolved_at": item.resolved_at,
        "silenced_until": item.silenced_until,
    }


class AlertCollectionView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "alerts.read")
        rows = OperationalAlert.objects.all()
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        return Response(
            {
                "count": rows.count(),
                "next": None,
                "previous": None,
                "results": [alert_row(item) for item in rows[:250]],
            }
        )


class AlertDetailView(ConsolePlatformView):
    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "alerts.manage")
        with transaction.atomic():
            alert = OperationalAlert.objects.select_for_update().filter(pk=pk).first()
            if alert is None:
                raise NotFound()
            action = str(request.data.get("action", ""))
            now = timezone.now()
            if action == "acknowledge":
                alert.status = "acknowledged"
                alert.acknowledged_by = self.admin
                alert.acknowledged_at = now
            elif action == "resolve":
                alert.status = "resolved"
                alert.resolved_by = self.admin
                alert.resolved_at = now
            elif action == "silence":
                until = serializers.DateTimeField().run_validation(request.data.get("until"))
                if until <= now or until > now + timedelta(days=30):
                    raise ValidationError({"until": "Silence must end within the next 30 days."})
                alert.status = "silenced"
                alert.silenced_until = until
            else:
                raise ValidationError({"action": "Choose acknowledge, resolve, or silence."})
            alert.save()
            audit(self.admin, f"alert.{action}", alert, {"status": alert.status})
        return Response(alert_row(alert))


class NotificationView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        rows = ConsoleNotification.objects.filter(user=self.admin)
        return Response(
            {
                "unread": rows.filter(read_at__isnull=True).count(),
                "results": [
                    {
                        "id": item.pk,
                        "kind": item.kind,
                        "title": item.title,
                        "body": item.body,
                        "link": item.link,
                        "created_at": item.created_at,
                        "read_at": item.read_at,
                    }
                    for item in rows.order_by("-created_at")[:100]
                ],
            }
        )

    def post(self, request: Request) -> Response:
        notification = get_or_404(
            ConsoleNotification, uuid.UUID(str(request.data.get("id"))), user=self.admin
        )
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuditView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "audit.read")
        rows = ConsoleAuditEvent.objects.select_related("actor")
        if action := request.query_params.get("action"):
            rows = rows.filter(action__icontains=action)
        return Response(
            {
                "count": rows.count(),
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": item.pk,
                        "actor": item.actor.full_name if item.actor else "System",
                        "action": item.action,
                        "resource_type": item.resource_type,
                        "resource_id": item.resource_id,
                        "request_id": item.request_id,
                        "changes": item.changes,
                        "created_at": item.created_at,
                    }
                    for item in rows[:250]
                ],
            }
        )


class AdministratorCollectionView(ConsolePlatformView):
    def get(self, request: Request) -> Response:
        require_permission(self.admin, "administrators.read")
        rows = AdminUser.objects.all().order_by("full_name", "email")
        return Response(
            {
                "count": rows.count(),
                "next": None,
                "previous": None,
                "results": [admin_row(item) for item in rows],
            }
        )

    def post(self, request: Request) -> Response:
        require_permission(self.admin, "administrators.manage")
        require_recent_authentication(self.session)
        role = str(request.data.get("role", AdminRole.READ_ONLY_AUDITOR))
        if role == AdminRole.PLATFORM_OWNER:
            raise ValidationError(
                {
                    "role": (
                        "Invite the administrator first, then use an owner-authorized role change."
                    )
                }
            )
        if role not in AdminRole.values:
            raise ValidationError({"role": "Choose a supported administrator role."})
        with transaction.atomic():
            user = account_services.create_admin(
                email=str(request.data.get("email", "")),
                full_name=str(request.data.get("full_name", "")),
            )
            user.role = role
            user.save(update_fields=["role", "updated_at"])
            audit(self.admin, "administrator.invited", user, {"role": role})
        return Response(admin_row(user), status=status.HTTP_201_CREATED)


def admin_row(item: AdminUser) -> dict[str, Any]:
    return {
        "id": item.pk,
        "email": item.email,
        "full_name": item.full_name,
        "role": item.role,
        "is_active": item.is_active,
        "activated_at": item.activated_at,
        "mfa_enabled": item.mfa_enabled,
        "created_at": item.created_at,
    }


class AdministratorDetailView(ConsolePlatformView):
    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        require_permission(self.admin, "administrators.manage")
        require_recent_authentication(self.session)
        with transaction.atomic():
            users = AdminUser.objects.select_for_update().filter(
                Q(pk=pk) | Q(role=AdminRole.PLATFORM_OWNER, is_active=True)
            )
            target = next((item for item in users if item.pk == pk), None)
            if target is None:
                raise NotFound()
            role = str(request.data.get("role", target.role))
            active = bool(request.data.get("is_active", target.is_active))
            if role not in AdminRole.values:
                raise ValidationError({"role": "Choose a supported administrator role."})
            if target.pk == self.admin.pk and role != target.role:
                raise ValidationError({"role": "Administrators cannot change their own role."})
            active_owners = sum(
                1 for item in users if item.role == AdminRole.PLATFORM_OWNER and item.is_active
            )
            removing_owner = (
                target.role == AdminRole.PLATFORM_OWNER
                and target.is_active
                and (role != AdminRole.PLATFORM_OWNER or not active)
            )
            if removing_owner and active_owners <= 1:
                raise ValidationError({"role": "At least one active Platform Owner is required."})
            before = {"role": target.role, "is_active": target.is_active}
            target.role = role
            target.is_active = active
            target.save(update_fields=["role", "is_active", "updated_at"])
            if not active:
                revoke_all(target)
            audit(
                self.admin,
                "administrator.updated",
                target,
                {"before": before, "role": role, "is_active": active},
            )
        return Response(admin_row(target))
