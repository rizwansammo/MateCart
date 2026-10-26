"""Merchant content API under /api/manage/content/ (staff realm, ``content.manage``)."""

import uuid
from typing import Any

from django.db.models import QuerySet
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.catalog.models import Collection
from commerce.core.domain import found
from commerce.core.fields import TenantRelatedField
from commerce.media.models import MediaAsset
from commerce.staff.views import ContentStaffView
from commerce.stores.views import AssetRefSerializer

from . import services
from .models import PAGE_SLUG_PATTERN, ContentPage, HomeSection, PageStatus, PageType, SectionType

TAGS = ["manage-content"]


class PageSerializer(serializers.ModelSerializer[ContentPage]):
    class Meta:
        model = ContentPage
        fields = (
            "id",
            "slug",
            "title",
            "body",
            "page_type",
            "status",
            "published_at",
            "show_in_footer",
            "position",
            "translations",
            "updated_at",
        )
        read_only_fields = fields


class PageWriteSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(max_length=160)
    slug = serializers.RegexField(
        regex=PAGE_SLUG_PATTERN, max_length=80, required=False, allow_blank=True
    )
    body = serializers.CharField(max_length=20000, required=False, allow_blank=True)
    page_type = serializers.ChoiceField(choices=PageType.choices, required=False)
    show_in_footer = serializers.BooleanField(required=False)
    position = serializers.IntegerField(min_value=0, max_value=1000, required=False)
    translations = serializers.JSONField(required=False)


class PageStatusSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=PageStatus.choices)


class CollectionRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()


class SectionSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    section_type = serializers.ChoiceField(choices=SectionType.choices)
    position = serializers.IntegerField()
    is_enabled = serializers.BooleanField()
    title = serializers.CharField(allow_blank=True)
    subtitle = serializers.CharField(allow_blank=True)
    body = serializers.CharField(allow_blank=True)
    image = AssetRefSerializer(allow_null=True)
    collection = CollectionRefSerializer(allow_null=True)
    cta_label = serializers.CharField(allow_blank=True)
    cta_url = serializers.CharField(allow_blank=True)
    translations = serializers.JSONField()


class SectionWriteSerializer(serializers.Serializer[dict[str, Any]]):
    section_type = serializers.ChoiceField(choices=SectionType.choices)
    is_enabled = serializers.BooleanField(required=False)
    title = serializers.CharField(max_length=120, required=False, allow_blank=True)
    subtitle = serializers.CharField(max_length=200, required=False, allow_blank=True)
    body = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    image = TenantRelatedField(MediaAsset, required=False, allow_null=True)
    collection = TenantRelatedField(Collection, required=False, allow_null=True)
    cta_label = serializers.CharField(max_length=40, required=False, allow_blank=True)
    cta_url = serializers.CharField(max_length=200, required=False, allow_blank=True)
    translations = serializers.JSONField(required=False)


class SectionOrderSerializer(serializers.Serializer[dict[str, Any]]):
    sections = serializers.ListField(child=TenantRelatedField(HomeSection), max_length=20)


def _valid(serializer_class: Any, request: Request, *, partial: bool = False) -> dict[str, Any]:
    serializer = serializer_class(data=request.data, partial=partial)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _sections() -> QuerySet[HomeSection, HomeSection]:
    return HomeSection.objects.select_related("image", "collection").order_by("position", "pk")


def _sections_response(code: int = status.HTTP_200_OK) -> Response:
    return Response(SectionSerializer(_sections(), many=True).data, status=code)


@method_decorator(never_cache, name="dispatch")
class PageListView(ContentStaffView):
    @extend_schema(
        operation_id="manage_content_pages", tags=TAGS, responses={200: PageSerializer(many=True)}
    )
    def get(self, request: Request) -> Response:
        pages = ContentPage.objects.order_by("position", "title", "pk")
        return Response(PageSerializer(pages, many=True).data)

    @extend_schema(
        operation_id="manage_content_page_create",
        tags=TAGS,
        request=PageWriteSerializer,
        responses={201: PageSerializer},
    )
    def post(self, request: Request) -> Response:
        page = services.save_page(
            page=None, data=_valid(PageWriteSerializer, request), actor=self.user
        )
        return Response(PageSerializer(page).data, status=status.HTTP_201_CREATED)


@method_decorator(never_cache, name="dispatch")
class PageDetailView(ContentStaffView):
    @extend_schema(operation_id="manage_content_page", tags=TAGS, responses={200: PageSerializer})
    def get(self, request: Request, page_id: uuid.UUID) -> Response:
        return Response(PageSerializer(found(ContentPage, pk=page_id)).data)

    @extend_schema(
        operation_id="manage_content_page_update",
        tags=TAGS,
        request=PageWriteSerializer,
        responses={200: PageSerializer},
    )
    def patch(self, request: Request, page_id: uuid.UUID) -> Response:
        page = found(ContentPage, pk=page_id)
        data = _valid(PageWriteSerializer, request, partial=True)
        return Response(
            PageSerializer(services.save_page(page=page, data=data, actor=self.user)).data
        )

    @extend_schema(operation_id="manage_content_page_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, page_id: uuid.UUID) -> Response:
        services.delete_page(page=found(ContentPage, pk=page_id), actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PageStatusView(ContentStaffView):
    @extend_schema(
        operation_id="manage_content_page_status",
        tags=TAGS,
        request=PageStatusSerializer,
        responses={200: PageSerializer},
    )
    def post(self, request: Request, page_id: uuid.UUID) -> Response:
        page = found(ContentPage, pk=page_id)
        target = _valid(PageStatusSerializer, request)["status"]
        return Response(
            PageSerializer(services.set_page_status(page=page, status=target, actor=self.user)).data
        )


@method_decorator(never_cache, name="dispatch")
class SectionListView(ContentStaffView):
    @extend_schema(
        operation_id="manage_home_sections",
        tags=TAGS,
        responses={200: SectionSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        return _sections_response()

    @extend_schema(
        operation_id="manage_home_section_create",
        tags=TAGS,
        request=SectionWriteSerializer,
        responses={201: SectionSerializer},
    )
    def post(self, request: Request) -> Response:
        section = services.save_section(
            section=None, data=_valid(SectionWriteSerializer, request), actor=self.user
        )
        return Response(
            SectionSerializer(_sections().get(pk=section.pk)).data, status=status.HTTP_201_CREATED
        )


class SectionDetailView(ContentStaffView):
    @extend_schema(
        operation_id="manage_home_section_update",
        tags=TAGS,
        request=SectionWriteSerializer,
        responses={200: SectionSerializer},
    )
    def patch(self, request: Request, section_id: uuid.UUID) -> Response:
        section = found(HomeSection, pk=section_id)
        data = _valid(SectionWriteSerializer, request, partial=True)
        services.save_section(section=section, data=data, actor=self.user)
        return Response(SectionSerializer(_sections().get(pk=section.pk)).data)

    @extend_schema(operation_id="manage_home_section_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, section_id: uuid.UUID) -> Response:
        services.delete_section(section=found(HomeSection, pk=section_id), actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SectionOrderView(ContentStaffView):
    @extend_schema(
        operation_id="manage_home_sections_order",
        tags=TAGS,
        request=SectionOrderSerializer,
        responses={200: SectionSerializer(many=True)},
    )
    def put(self, request: Request) -> Response:
        services.reorder_sections(
            ordered=_valid(SectionOrderSerializer, request)["sections"], actor=self.user
        )
        return _sections_response()


class SectionDefaultsView(ContentStaffView):
    @extend_schema(
        operation_id="manage_home_sections_defaults",
        tags=TAGS,
        request=None,
        responses={201: SectionSerializer(many=True)},
        description="Create the preset's suggested homepage sections (only while empty).",
    )
    def post(self, request: Request) -> Response:
        services.apply_default_sections(actor=self.user)
        return _sections_response(status.HTTP_201_CREATED)
