"""Merchant appearance and module APIs under /api/manage/ (staff realm).

Appearance needs ``content.manage``; renaming the store and switching modules need
``store.settings``. Every change goes through ``appearance`` or ``modules``.
"""

from typing import Any

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.core.fields import TenantRelatedField
from commerce.media.models import MediaAsset
from commerce.staff.views import ContentStaffView, SettingsStaffView

from . import appearance, modules, themes
from .models import StoreTheme

TAGS = ["manage-appearance"]


class AssetRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    url = serializers.SerializerMethodField()
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)

    def get_url(self, asset: MediaAsset) -> str:
        return f"/api/manage/media/assets/{asset.pk}/file/"


class AppearanceSerializer(serializers.Serializer[Any]):
    preset = serializers.CharField()
    preset_label = serializers.CharField()
    # Read-only (D-069): assigned by NetaMate; AppearanceWriteSerializer has no theme field.
    active_theme = serializers.ChoiceField(choices=themes.THEME_IDS)
    store_name = serializers.CharField()
    can_rename = serializers.BooleanField()
    default_language = serializers.CharField()
    languages = serializers.ListField(child=serializers.CharField())
    primary_color = serializers.CharField(allow_blank=True)
    accent_color = serializers.CharField(allow_blank=True)
    logo = AssetRefSerializer(allow_null=True)
    logo_dark = AssetRefSerializer(allow_null=True)
    favicon = AssetRefSerializer(allow_null=True)
    tagline = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    announcement = serializers.CharField(allow_blank=True)
    footer_text = serializers.CharField(allow_blank=True)
    show_name_with_logo = serializers.BooleanField()
    translations = serializers.JSONField()
    updated_at = serializers.DateTimeField(allow_null=True)


class AppearanceWriteSerializer(serializers.Serializer[dict[str, Any]]):
    store_name = serializers.CharField(max_length=120, required=False)
    primary_color = serializers.CharField(max_length=7, required=False, allow_blank=True)
    accent_color = serializers.CharField(max_length=7, required=False, allow_blank=True)
    logo = TenantRelatedField(MediaAsset, required=False, allow_null=True)
    logo_dark = TenantRelatedField(MediaAsset, required=False, allow_null=True)
    favicon = TenantRelatedField(MediaAsset, required=False, allow_null=True)
    tagline = serializers.CharField(max_length=160, required=False, allow_blank=True)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    announcement = serializers.CharField(max_length=160, required=False, allow_blank=True)
    footer_text = serializers.CharField(max_length=300, required=False, allow_blank=True)
    show_name_with_logo = serializers.BooleanField(required=False)
    translations = serializers.JSONField(required=False)


class ModuleSerializer(serializers.Serializer[Any]):
    key = serializers.CharField(source="spec.key")
    name = serializers.CharField(source="spec.name")
    description = serializers.CharField(source="spec.description")
    phase = serializers.CharField(source="spec.phase")
    available = serializers.BooleanField(source="spec.available")
    core = serializers.BooleanField(source="spec.core")
    requires = serializers.ListField(child=serializers.CharField(), source="spec.requires")
    applicable = serializers.BooleanField()
    default_enabled = serializers.BooleanField()
    configured = serializers.BooleanField(allow_null=True)
    enabled = serializers.BooleanField()
    blocked_by = serializers.ListField(child=serializers.CharField())


class ModuleWriteSerializer(serializers.Serializer[dict[str, Any]]):
    enabled = serializers.BooleanField(
        allow_null=True, help_text="true or false, or null to follow the preset default."
    )


def appearance_payload(view: ContentStaffView, theme: StoreTheme) -> dict[str, Any]:
    settings = appearance.store_settings()
    saved = not theme._state.adding
    return {
        "preset": settings.preset,
        "preset_label": appearance.PRESET_LABELS.get(settings.preset, settings.preset),
        "active_theme": themes.effective_theme(settings.active_theme, settings.preset),
        "store_name": settings.name,
        "can_rename": view.can_manage_settings,
        "default_language": settings.default_language,
        "languages": list(settings.supported_languages),
        "primary_color": theme.primary_color,
        "accent_color": theme.accent_color,
        "logo": theme.logo if saved else None,
        "logo_dark": theme.logo_dark if saved else None,
        "favicon": theme.favicon if saved else None,
        "tagline": theme.tagline,
        "description": theme.description,
        "announcement": theme.announcement,
        "footer_text": theme.footer_text,
        "show_name_with_logo": theme.show_name_with_logo,
        "translations": theme.translations,
        "updated_at": theme.updated_at if saved else None,
    }


@method_decorator(never_cache, name="dispatch")
class AppearanceView(ContentStaffView):
    def _response(self) -> Response:
        payload = appearance_payload(self, appearance.current_theme())
        return Response(AppearanceSerializer(payload).data)

    @extend_schema(
        operation_id="manage_appearance", tags=TAGS, responses={200: AppearanceSerializer}
    )
    def get(self, request: Request) -> Response:
        return self._response()

    @extend_schema(
        operation_id="manage_appearance_update",
        tags=TAGS,
        request=AppearanceWriteSerializer,
        responses={200: AppearanceSerializer},
    )
    def patch(self, request: Request) -> Response:
        data = AppearanceWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        appearance.update(
            data=dict(data.validated_data), actor=self.user, can_rename=self.can_manage_settings
        )
        return self._response()


class AppearanceResetView(ContentStaffView):
    @extend_schema(
        operation_id="manage_appearance_reset",
        tags=TAGS,
        request=None,
        responses={200: AppearanceSerializer},
        description="Return brand colours to the preset palette; logos and texts are kept.",
    )
    def post(self, request: Request) -> Response:
        appearance.reset_colors(actor=self.user)
        return Response(
            AppearanceSerializer(appearance_payload(self, appearance.current_theme())).data
        )


@method_decorator(never_cache, name="dispatch")
class ModuleListView(SettingsStaffView):
    @extend_schema(
        operation_id="manage_modules", tags=TAGS, responses={200: ModuleSerializer(many=True)}
    )
    def get(self, request: Request) -> Response:
        return Response(ModuleSerializer(modules.module_states(), many=True).data)


class ModuleDetailView(SettingsStaffView):
    @extend_schema(
        operation_id="manage_module_update",
        tags=TAGS,
        request=ModuleWriteSerializer,
        responses={200: ModuleSerializer},
    )
    def put(self, request: Request, key: str) -> Response:
        data = ModuleWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        state = modules.set_module(key=key, enabled=data.validated_data["enabled"], actor=self.user)
        return Response(ModuleSerializer(state).data)
