"""Data import, export and migration API (/api/manage/data/, staff realm, D-105 to D-109).

Permissions (enforced here and again by the services):
- File imports of the catalogue: ``catalog.manage``.
- Store migrations and source connections: ``store.settings`` and ``catalog.manage``, plus the
  data's own permission for customers (``customers.view``), orders (``orders.manage`` and
  ``customers.view``) and coupons (``promotions.manage``). Connecting a store, approving a
  migration and reversing a job need a recent sign-in.
- Exports: each kind's permissions; customer and order exports also need a recent sign-in, and
  every download of them is audited.
- Imported history: customers ``customers.view``; orders ``orders.manage``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, cast

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commerce.audit.services import record_audit_event
from commerce.core.domain import ConflictError
from commerce.core.pagination import paginate, paginated
from commerce.identity import sessions
from commerce.identity.auth import StaffAuth
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView
from commerce.stores.models import StoreSettings

from . import connections, csvio, exports, normalize, pipeline
from .models import (
    DataTransferFile,
    DataTransferJob,
    HistoricalCustomer,
    HistoricalOrder,
    ImportItem,
    JobKind,
    JobSource,
    JobStatus,
    SourceConnection,
)
from .serializers import (
    CompensationSerializer,
    DataOverviewSerializer,
    DecisionsInputSerializer,
    ExportInputSerializer,
    HistoricalCustomerSerializer,
    HistoricalOrderSerializer,
    ImportInputSerializer,
    ImportItemSerializer,
    ItemQuerySerializer,
    MigrationInputSerializer,
    OptionsSerializer,
    SourceConnectionSerializer,
    SourceInputSerializer,
    TransferJobSerializer,
)

TAG = ["manage-data"]
DATA_PERMISSIONS = frozenset(
    {
        Permission.CATALOG_MANAGE,
        Permission.STORE_SETTINGS,
        Permission.CUSTOMERS_VIEW,
        Permission.ORDERS_MANAGE,
        Permission.REPORTS_VIEW,
    }
)


class UsesData(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        auth = request.auth
        return isinstance(auth, StaffAuth) and bool(auth.permissions & DATA_PERMISSIONS)


def _data(serializer: Any) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def can_migrate(permissions: frozenset[str]) -> bool:
    return {Permission.STORE_SETTINGS, Permission.CATALOG_MANAGE} <= permissions


def can_access(job: DataTransferJob, permissions: frozenset[str]) -> bool:
    if job.kind == JobKind.IMPORT:
        return Permission.CATALOG_MANAGE in permissions
    if job.kind == JobKind.MIGRATION:
        if not can_migrate(permissions):
            return False
        return all(
            perm in permissions
            for scope in job.scopes
            for perm in connections.SCOPE_PERMISSIONS.get(scope, ())
        )
    spec = exports.EXPORTS.get(job.entity)
    return spec is not None and all(perm in permissions for perm in spec["permissions"])


class DataView(StaffView):
    permission_classes: tuple[type[BasePermission], ...] = (IsAuthenticated, UsesData)

    @property
    def permissions(self) -> frozenset[str]:
        return self.staff.permissions

    def require(self, *needed: str) -> None:
        if not all(perm in self.permissions for perm in needed):
            raise PermissionDenied("Your role cannot do this.")

    def job(self, job_id: uuid.UUID) -> DataTransferJob:
        job = (
            DataTransferJob.objects.select_related(
                "created_by",
                "approved_by",
                "connection",
                "input_file",
                "output_file",
                "report_file",
            )
            .filter(pk=job_id)
            .first()
        )
        if job is None or not can_access(job, self.permissions):
            raise Http404
        return job

    def recent(self) -> None:
        sessions.require_recent_authentication(self.staff.session)


class OverviewView(DataView):
    @extend_schema(
        operation_id="manage_data_overview", tags=TAG, responses={200: DataOverviewSerializer}
    )
    def get(self, request: Request) -> Response:
        perms = self.permissions
        store = StoreSettings.objects.first()
        payload = {
            "can_import": Permission.CATALOG_MANAGE in perms,
            "can_migrate": can_migrate(perms),
            "can_view_customers": Permission.CUSTOMERS_VIEW in perms,
            "can_view_orders": Permission.ORDERS_MANAGE in perms,
            "simulator_allowed": connections.simulator_allowed(),
            "template_fields": [
                {"key": key, "name": label, "aliases": [key, *aliases]}
                for key, (label, aliases) in normalize.CATALOG_FIELDS.items()
            ],
            "exports": [
                {
                    "key": key,
                    "name": spec["label"],
                    "sensitive": spec["sensitive"],
                    "formats": list(spec["formats"]),
                    "allowed": all(perm in perms for perm in spec["permissions"]),
                }
                for key, spec in exports.EXPORTS.items()
            ],
            "store_currency": store.default_currency if store else "",
            "max_upload_bytes": settings.MATECART_TRANSFER_MAX_UPLOAD_BYTES,
        }
        return Response(DataOverviewSerializer(payload).data)


class TemplateView(DataView):
    @extend_schema(
        operation_id="manage_data_template",
        tags=TAG,
        responses={(200, "text/csv"): OpenApiResponse(description="Catalogue import template")},
    )
    def get(self, request: Request) -> HttpResponse:
        self.require(Permission.CATALOG_MANAGE)
        example = {
            "handle": "cotton-tee",
            "name": "Cotton Tee",
            "product_type": "standard",
            "status": "draft",
            "categories": "Clothing > Tops",
            "tags": "cotton, summer",
            "summary": "A soft everyday tee.",
            "option1_name": "Size",
            "option1_value": "M",
            "sku": "TEE-M",
            "price": "450",
            "opening_stock": "10",
        }
        body = "".join(csvio.write_csv(normalize.TEMPLATE_HEADER, [example])).encode("utf-8-sig")
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="matecart-catalogue-template.csv"'
        return response


class ImportCreateView(DataView):
    parser_classes = (MultiPartParser,)

    @extend_schema(
        operation_id="manage_data_import_create",
        tags=TAG,
        request={"multipart/form-data": ImportInputSerializer},
        responses={202: TransferJobSerializer},
    )
    def post(self, request: Request) -> Response:
        self.require(Permission.CATALOG_MANAGE)
        data = _data(ImportInputSerializer(data=request.data))
        raw_options = data.get("options") or {}
        if isinstance(raw_options, str):
            try:
                raw_options = json.loads(raw_options)
            except ValueError:
                raise ValidationError({"options": ["Send options as JSON."]}) from None
        options = _data(OptionsSerializer(data=raw_options))
        upload = data["file"]
        content = pipeline.upload_bytes(upload, settings.MATECART_TRANSFER_MAX_UPLOAD_BYTES)
        job = pipeline.create_file_import(
            data=content,
            filename=str(upload.name or "import.csv")[-120:],
            actor=self.user,
            strategy=data["strategy"],
            options=options,
        )
        job.refresh_from_db()
        return Response(TransferJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class MigrationCreateView(DataView):
    @extend_schema(
        operation_id="manage_data_migration_create",
        tags=TAG,
        request=MigrationInputSerializer,
        responses={202: TransferJobSerializer},
    )
    def post(self, request: Request) -> Response:
        if not can_migrate(self.permissions):
            raise PermissionDenied("Store migrations need settings and catalogue permissions.")
        data = _data(MigrationInputSerializer(data=request.data))
        connection = SourceConnection.objects.filter(pk=data["connection"]).first()
        if connection is None:
            raise Http404
        if set(data["scopes"]) & {"customers", "orders"}:
            self.recent()
        job = pipeline.create_migration(
            connection=connection,
            scopes=list(data["scopes"]),
            actor=self.user,
            permissions=self.permissions,
            strategy=data["strategy"],
            options=data.get("options") or {},
        )
        job.refresh_from_db()
        return Response(TransferJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ExportCreateView(DataView):
    @extend_schema(
        operation_id="manage_data_export_create",
        tags=TAG,
        request=ExportInputSerializer,
        responses={202: TransferJobSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _data(ExportInputSerializer(data=request.data))
        spec = exports.EXPORTS[data["entity"]]
        self.require(*spec["permissions"])
        if data["format"] not in spec["formats"]:
            raise ValidationError({"format": ["This export is available as CSV only."]})
        if spec["sensitive"]:
            self.recent()
        with transaction.atomic():
            job = DataTransferJob.objects.create(
                kind=JobKind.EXPORT,
                source=JobSource.STORE,
                status=JobStatus.QUEUED,
                entity=data["entity"],
                file_format=data["format"],
                sensitive=spec["sensitive"],
                created_by=self.user,
            )
            record_audit_event(
                action="transfers.export.requested",
                resource=job,
                actor=self.user,
                changes={
                    "entity": job.entity,
                    "format": job.file_format,
                    "sensitive": job.sensitive,
                },
            )
            pipeline._enqueue_on_commit("export", job)
        job.refresh_from_db()
        return Response(TransferJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class JobListView(DataView):
    @extend_schema(
        operation_id="manage_data_jobs", tags=TAG, responses={200: paginated(TransferJobSerializer)}
    )
    def get(self, request: Request) -> Response:
        rows = DataTransferJob.objects.select_related(
            "created_by", "approved_by", "connection", "input_file", "output_file", "report_file"
        ).order_by("-created_at", "-pk")
        kind = request.query_params.get("kind")
        if kind in JobKind.values:
            rows = rows.filter(kind=kind)
        allowed = [job.pk for job in rows[:500] if can_access(job, self.permissions)]
        items, meta = paginate(rows.filter(pk__in=allowed), request)
        return Response({**meta, "results": TransferJobSerializer(items, many=True).data})


class JobDetailView(DataView):
    @extend_schema(operation_id="manage_data_job", tags=TAG, responses={200: TransferJobSerializer})
    def get(self, request: Request, job_id: uuid.UUID) -> Response:
        return Response(TransferJobSerializer(self.job(job_id)).data)


class JobItemsView(DataView):
    @extend_schema(
        operation_id="manage_data_job_items",
        tags=TAG,
        parameters=[ItemQuerySerializer],
        responses={200: paginated(ImportItemSerializer)},
    )
    def get(self, request: Request, job_id: uuid.UUID) -> Response:
        job = self.job(job_id)
        params = _data(ItemQuerySerializer(data=request.query_params))
        rows = ImportItem.objects.filter(job=job)
        if params.get("action") == "warning":
            rows = rows.filter(messages__contains=[{"level": "warning"}])
        elif params.get("action"):
            rows = rows.filter(action=params["action"])
        if params.get("status"):
            rows = rows.filter(status=params["status"])
        if params.get("object_type"):
            rows = rows.filter(object_type=params["object_type"])
        items, meta = paginate(rows.order_by("position"), request)
        return Response({**meta, "results": ImportItemSerializer(items, many=True).data})


class _JobAction(DataView):
    def guard(self, job: DataTransferJob) -> None:
        if job.kind == JobKind.MIGRATION or job.sensitive:
            self.recent()


class JobDecisionsView(_JobAction):
    @extend_schema(
        operation_id="manage_data_job_decisions",
        tags=TAG,
        request=DecisionsInputSerializer,
        responses={200: TransferJobSerializer},
    )
    def post(self, request: Request, job_id: uuid.UUID) -> Response:
        job = self.job(job_id)
        data = _data(DecisionsInputSerializer(data=request.data))
        pipeline.decide(job=job, decisions=data["decisions"], actor=self.user)
        return Response(TransferJobSerializer(self.job(job_id)).data)


class JobApproveView(_JobAction):
    @extend_schema(
        operation_id="manage_data_job_approve",
        tags=TAG,
        request=None,
        responses={200: TransferJobSerializer},
    )
    def post(self, request: Request, job_id: uuid.UUID) -> Response:
        job = self.job(job_id)
        if job.kind == JobKind.EXPORT:
            raise ConflictError("Exports need no approval.", "transfer_export")
        self.guard(job)
        pipeline.approve(job=job, actor=self.user)
        return Response(TransferJobSerializer(self.job(job_id)).data)


class JobCancelView(_JobAction):
    @extend_schema(
        operation_id="manage_data_job_cancel",
        tags=TAG,
        request=None,
        responses={200: TransferJobSerializer},
    )
    def post(self, request: Request, job_id: uuid.UUID) -> Response:
        pipeline.cancel(job=self.job(job_id), actor=self.user)
        return Response(TransferJobSerializer(self.job(job_id)).data)


class JobResumeView(_JobAction):
    @extend_schema(
        operation_id="manage_data_job_resume",
        tags=TAG,
        request=None,
        responses={200: TransferJobSerializer},
    )
    def post(self, request: Request, job_id: uuid.UUID) -> Response:
        job = self.job(job_id)
        self.guard(job)
        pipeline.resume(job=job, actor=self.user)
        return Response(TransferJobSerializer(self.job(job_id)).data)


class JobCompensateView(_JobAction):
    @extend_schema(
        operation_id="manage_data_job_compensate",
        tags=TAG,
        request=None,
        responses={200: CompensationSerializer},
    )
    def post(self, request: Request, job_id: uuid.UUID) -> Response:
        job = self.job(job_id)
        if job.kind == JobKind.EXPORT:
            raise ConflictError("Exports change nothing.", "transfer_export")
        self.recent()
        return Response(CompensationSerializer(pipeline.compensate(job=job, actor=self.user)).data)


class FileDownloadView(DataView):
    @extend_schema(
        operation_id="manage_data_file_download",
        tags=TAG,
        responses={(200, "application/octet-stream"): OpenApiResponse(description="The file")},
    )
    def get(self, request: Request, file_id: uuid.UUID) -> FileResponse:
        row = DataTransferFile.objects.filter(pk=file_id).first()
        if row is None:
            raise Http404
        from django.db.models import Q
        from django.utils import timezone

        job = DataTransferJob.objects.filter(
            Q(output_file=row) | Q(report_file=row) | Q(input_file=row)
        ).first()
        if job is None or not can_access(job, self.permissions) or row.expires_at < timezone.now():
            raise Http404
        if job.sensitive:
            self.recent()
        with transaction.atomic():
            DataTransferFile.objects.filter(pk=row.pk).update(downloads=F("downloads") + 1)
            if job.sensitive or job.kind == JobKind.EXPORT:
                record_audit_event(
                    action="transfers.file.downloaded",
                    resource=job,
                    actor=self.user,
                    changes={
                        "purpose": row.purpose,
                        "entity": job.entity,
                        "sensitive": job.sensitive,
                    },
                )
        safe_name = (
            "".join(ch for ch in row.display_name if ch.isalnum() or ch in "-_.") or "download.csv"
        )
        response = FileResponse(
            row.file.open("rb"),
            as_attachment=True,
            filename=safe_name,
            content_type=row.content_type,
        )
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class SourceListView(DataView):
    @extend_schema(
        operation_id="manage_data_sources",
        tags=TAG,
        responses={200: SourceConnectionSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        if not can_migrate(self.permissions):
            raise PermissionDenied("Store migrations need settings and catalogue permissions.")
        rows = SourceConnection.objects.order_by("-created_at")
        return Response(
            SourceConnectionSerializer([connections.public_state(r) for r in rows], many=True).data
        )

    @extend_schema(
        operation_id="manage_data_source_create",
        tags=TAG,
        request=SourceInputSerializer,
        responses={201: SourceConnectionSerializer},
    )
    def post(self, request: Request) -> Response:
        if not can_migrate(self.permissions):
            raise PermissionDenied("Store migrations need settings and catalogue permissions.")
        self.recent()
        data = _data(SourceInputSerializer(data=request.data))
        row = connections.create(platform=data["platform"], data=data, actor=self.user)
        return Response(
            SourceConnectionSerializer(connections.public_state(row)).data,
            status=status.HTTP_201_CREATED,
        )


class _SourceView(DataView):
    def source(self, source_id: uuid.UUID) -> SourceConnection:
        if not can_migrate(self.permissions):
            raise PermissionDenied("Store migrations need settings and catalogue permissions.")
        row = SourceConnection.objects.filter(pk=source_id).first()
        if row is None:
            raise Http404
        return row


class SourceDetailView(_SourceView):
    @extend_schema(operation_id="manage_data_source_delete", tags=TAG, responses={204: None})
    def delete(self, request: Request, source_id: uuid.UUID) -> Response:
        row = self.source(source_id)
        self.recent()
        connections.delete(row=row, actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SourceVerifyView(_SourceView):
    @extend_schema(
        operation_id="manage_data_source_verify",
        tags=TAG,
        request=None,
        responses={200: SourceConnectionSerializer},
    )
    def post(self, request: Request, source_id: uuid.UUID) -> Response:
        row = connections.verify(row=self.source(source_id), actor=self.user)
        return Response(SourceConnectionSerializer(connections.public_state(row)).data)


class HistoricalCustomerListView(DataView):
    @extend_schema(
        operation_id="manage_data_history_customers",
        tags=TAG,
        responses={200: paginated(HistoricalCustomerSerializer)},
    )
    def get(self, request: Request) -> Response:
        self.require(Permission.CUSTOMERS_VIEW)
        rows = HistoricalCustomer.objects.order_by("-created_at", "pk")
        query = (request.query_params.get("q") or "").strip()[:100]
        if query:
            from django.db.models import Q

            rows = rows.filter(
                Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(phone__icontains=query)
            )
        items, meta = paginate(rows, request)
        return Response({**meta, "results": HistoricalCustomerSerializer(items, many=True).data})


class HistoricalOrderListView(DataView):
    @extend_schema(
        operation_id="manage_data_history_orders",
        tags=TAG,
        responses={200: paginated(HistoricalOrderSerializer)},
    )
    def get(self, request: Request) -> Response:
        self.require(Permission.ORDERS_MANAGE)
        rows = HistoricalOrder.objects.order_by("-placed_at", "pk")
        query = (request.query_params.get("q") or "").strip()[:64]
        if query:
            rows = rows.filter(number__icontains=query)
        items, meta = paginate(rows, request)
        return Response({**meta, "results": HistoricalOrderSerializer(items, many=True).data})
