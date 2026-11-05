"""Merchant AI API (/api/manage/ai/, staff realm, D-101 to D-104).

- Overview: any staff member who can use at least one AI feature or manage settings. Features
  are listed with the permission each needs; usage figures only for settings managers.
- Settings, key, verification, customer-data confirmation and usage: ``store.settings``. Key,
  model, feature and limit changes also need a recent sign-in (D-048).
- Requests: the feature's own permission (catalogue, messages, promotions, reports), checked
  again by the worker before any context is read. Staff see their own requests with results;
  settings managers can list every request's metadata, never another person's results.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commerce.core.domain import found
from commerce.core.pagination import paginate, paginated
from commerce.identity import sessions
from commerce.identity.auth import HasStaffPermission, StaffAuth
from commerce.staff.permissions import Permission
from commerce.staff.views import StaffView

from . import features, services
from .models import AIRequest
from .serializers import (
    AIKeyInputSerializer,
    AIOverviewSerializer,
    AIRequestInputSerializer,
    AIRequestSerializer,
    AIReviewInputSerializer,
    AISettingsInputSerializer,
    AISettingsSerializer,
    AIUsageSerializer,
    ApplyInputSerializer,
    ConfirmInputSerializer,
    RequestQuerySerializer,
)

TAG = ["manage-ai"]
AI_PERMISSIONS = frozenset(
    {
        Permission.CATALOG_MANAGE,
        Permission.MESSAGES_MANAGE,
        Permission.PROMOTIONS_MANAGE,
        Permission.REPORTS_VIEW,
        Permission.STORE_SETTINGS,
    }
)
CanSettings = HasStaffPermission.of(Permission.STORE_SETTINGS)
PROVIDER_DATA_NOTICE = (
    "Requests send the store data each feature needs to the selected AI provider. GroqCloud "
    "documents that it does not retain inference inputs and outputs by default, may keep "
    "reliability and abuse logs for up to 30 days, stores data in the United States and offers "
    "Zero Data Retention in its Data Controls. MateCart does not send passwords, payment details, "
    "API keys or full addresses, masks e-mail addresses and phone numbers in conversation "
    "excerpts, and keeps AI results for 30 days."
)


class UsesAI(BasePermission):
    def has_permission(self, request: Request, view: APIView) -> bool:
        auth = request.auth
        return isinstance(auth, StaffAuth) and bool(auth.permissions & AI_PERMISSIONS)


def _data(serializer: Any) -> dict[str, Any]:
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


class AIView(StaffView):
    permission_classes: tuple[type[BasePermission], ...] = (IsAuthenticated, UsesAI)

    def serialize(self, row: AIRequest) -> dict[str, Any]:
        return dict(AIRequestSerializer(row, context={"viewer": self.user}).data)


class SettingsView(StaffView):
    permission_classes = (IsAuthenticated, CanSettings)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if request.method in ("PATCH", "PUT", "DELETE"):
            sessions.require_recent_authentication(self.staff.session)


class OverviewView(AIView):
    @extend_schema(
        operation_id="manage_ai_overview", tags=TAG, responses={200: AIOverviewSerializer}
    )
    def get(self, request: Request) -> Response:
        config = services.configuration()
        state = services.public_state(config)
        permissions = self.staff.permissions
        can_settings = Permission.STORE_SETTINGS in permissions
        recent = (
            AIRequest.objects.filter(requested_by=self.user)
            .select_related("requested_by")
            .order_by("-created_at", "-pk")[:8]
        )
        payload = {
            "module_enabled": state["module_enabled"],
            "provider": state["provider"],
            "status": state["status"],
            "text_model": state["text_model"],
            "vision_model": state["vision_model"],
            "is_test": state["is_test"],
            "features": [f for f in state["features"] if f["permission"] in permissions],
            "can_manage_settings": can_settings,
            "usage": services.usage_summary() if can_settings else None,
            "recent": list(recent),
            "provider_data_notice": PROVIDER_DATA_NOTICE,
        }
        return Response(AIOverviewSerializer(payload, context={"viewer": self.user}).data)


class AISettingsView(SettingsView):
    @extend_schema(
        operation_id="manage_ai_settings", tags=TAG, responses={200: AISettingsSerializer}
    )
    def get(self, request: Request) -> Response:
        return Response(AISettingsSerializer(services.public_state(services.configuration())).data)

    @extend_schema(
        operation_id="manage_ai_settings_update",
        tags=TAG,
        request=AISettingsInputSerializer,
        responses={200: AISettingsSerializer},
    )
    def patch(self, request: Request) -> Response:
        data = _data(AISettingsInputSerializer(data=request.data))
        config = services.save_settings(data=data, actor=self.user)
        return Response(AISettingsSerializer(services.public_state(config)).data)


class AIKeyView(SettingsView):
    @extend_schema(
        operation_id="manage_ai_key_set",
        tags=TAG,
        request=AIKeyInputSerializer,
        responses={200: AISettingsSerializer},
    )
    def put(self, request: Request) -> Response:
        data = _data(AIKeyInputSerializer(data=request.data))
        config = services.set_api_key(
            provider=data["provider"], api_key=data["api_key"], actor=self.user
        )
        return Response(AISettingsSerializer(services.public_state(config)).data)

    @extend_schema(
        operation_id="manage_ai_key_remove", tags=TAG, responses={200: AISettingsSerializer}
    )
    def delete(self, request: Request) -> Response:
        config = services.remove_api_key(actor=self.user)
        return Response(AISettingsSerializer(services.public_state(config)).data)


class AIVerifyView(SettingsView):
    @extend_schema(
        operation_id="manage_ai_verify",
        tags=TAG,
        request=None,
        responses={200: AISettingsSerializer},
    )
    def post(self, request: Request) -> Response:
        config = services.verify(actor=self.user)
        return Response(AISettingsSerializer(services.public_state(config)).data)


class AICustomerDataView(SettingsView):
    @extend_schema(
        operation_id="manage_ai_customer_data",
        tags=TAG,
        request=ConfirmInputSerializer,
        responses={200: AISettingsSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.staff.session)
        data = _data(ConfirmInputSerializer(data=request.data))
        config = services.confirm_customer_data(confirmed=data["confirmed"], actor=self.user)
        return Response(AISettingsSerializer(services.public_state(config)).data)


class AIUsageView(SettingsView):
    @extend_schema(operation_id="manage_ai_usage", tags=TAG, responses={200: AIUsageSerializer})
    def get(self, request: Request) -> Response:
        return Response(AIUsageSerializer(services.usage_summary()).data)


class AIRequestListView(AIView):
    @extend_schema(
        operation_id="manage_ai_requests",
        tags=TAG,
        parameters=[RequestQuerySerializer],
        responses={200: paginated(AIRequestSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = _data(RequestQuerySerializer(data=request.query_params))
        rows = AIRequest.objects.select_related("requested_by")
        if params["scope"] != "all" or Permission.STORE_SETTINGS not in self.staff.permissions:
            rows = rows.filter(requested_by=self.user)
        if params.get("feature"):
            rows = rows.filter(feature=params["feature"])
        if params.get("subject_id"):
            rows = rows.filter(subject_id=params["subject_id"])
        items, meta = paginate(rows.order_by("-created_at", "-pk"), request)
        return Response({**meta, "results": [self.serialize(row) for row in items]})

    @extend_schema(
        operation_id="manage_ai_request_create",
        tags=TAG,
        request=AIRequestInputSerializer,
        responses={202: AIRequestSerializer, 200: AIRequestSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _data(AIRequestInputSerializer(data=request.data))
        row, created = services.submit(
            feature_key=data["feature"],
            subject_id=data.get("subject_id"),
            options=data.get("options") or {},
            client_key=data.get("client_key", ""),
            actor=self.user,
            permissions=self.staff.permissions,
        )
        row.refresh_from_db()
        return Response(
            self.serialize(row), status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
        )


class _OwnRequestView(AIView):
    def own(self, request_id: uuid.UUID) -> AIRequest:
        row = found(AIRequest, pk=request_id, requested_by=self.user)
        feature = features.FEATURES[row.feature]
        if feature.permission not in self.staff.permissions:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Your role cannot use this AI feature.")
        return row


class AIRequestDetailView(_OwnRequestView):
    @extend_schema(operation_id="manage_ai_request", tags=TAG, responses={200: AIRequestSerializer})
    def get(self, request: Request, request_id: uuid.UUID) -> Response:
        return Response(self.serialize(self.own(request_id)))


class AIRequestCancelView(_OwnRequestView):
    @extend_schema(
        operation_id="manage_ai_request_cancel",
        tags=TAG,
        request=None,
        responses={200: AIRequestSerializer},
    )
    def post(self, request: Request, request_id: uuid.UUID) -> Response:
        row = services.cancel(row=self.own(request_id), actor=self.user)
        return Response(self.serialize(row))


class AIRequestRetryView(_OwnRequestView):
    @extend_schema(
        operation_id="manage_ai_request_retry",
        tags=TAG,
        request=None,
        responses={202: AIRequestSerializer},
    )
    def post(self, request: Request, request_id: uuid.UUID) -> Response:
        row = services.retry(
            row=self.own(request_id), actor=self.user, permissions=self.staff.permissions
        )
        row.refresh_from_db()
        return Response(self.serialize(row), status=status.HTTP_202_ACCEPTED)


class AIRequestApplyView(_OwnRequestView):
    @extend_schema(
        operation_id="manage_ai_request_apply",
        tags=TAG,
        request=ApplyInputSerializer,
        responses={200: AIRequestSerializer},
    )
    def post(self, request: Request, request_id: uuid.UUID) -> Response:
        data = _data(ApplyInputSerializer(data=request.data))
        row = services.apply(
            row=self.own(request_id),
            values=data["values"],
            overwrite=data["overwrite"],
            actor=self.user,
            permissions=self.staff.permissions,
        )
        return Response(self.serialize(row))


class AIRequestReviewView(_OwnRequestView):
    @extend_schema(
        operation_id="manage_ai_request_review",
        tags=TAG,
        request=AIReviewInputSerializer,
        responses={200: AIRequestSerializer},
    )
    def post(self, request: Request, request_id: uuid.UUID) -> Response:
        data = _data(AIReviewInputSerializer(data=request.data))
        row = services.review(row=self.own(request_id), decision=data["decision"], actor=self.user)
        return Response(self.serialize(row))
