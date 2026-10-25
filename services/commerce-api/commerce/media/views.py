"""Merchant media API under /api/manage/media/ (staff realm, ``catalog.manage``), and the file
response shared with the public catalogue."""

import uuid
from typing import Any

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.core.domain import DomainError, found
from commerce.core.pagination import PageQuerySerializer, paginate, paginated
from commerce.staff.views import CatalogStaffView

from . import services
from .models import MediaAsset, MediaKind
from .serializers import (
    MediaAssetSerializer,
    MediaListQuerySerializer,
    MediaUpdateSerializer,
    MediaUploadSerializer,
)

TAGS = ["manage-media"]
# Multipart framing around the file; a body larger than the file limit plus this is refused unread.
_MULTIPART_OVERHEAD = 64 * 1024


def file_response(asset: MediaAsset, *, public: bool) -> FileResponse:
    """Stream a stored file with headers that stop browsers from sniffing or executing it."""
    try:
        handle = asset.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    is_image = asset.kind == MediaKind.IMAGE
    extension = asset.file.name.rsplit(".", 1)[-1] if "." in asset.file.name else "bin"
    response = FileResponse(
        handle,
        content_type=asset.content_type,
        as_attachment=not is_image,
        filename=f"{'image' if is_image else 'document'}.{extension}",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["Cache-Control"] = "public, max-age=300" if public else "private, no-store"
    return response


@method_decorator(never_cache, name="dispatch")
class MediaListView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_media_assets",
        tags=TAGS,
        parameters=[MediaListQuerySerializer, PageQuerySerializer],
        responses={200: paginated(MediaAssetSerializer)},
    )
    def get(self, request: Request) -> Response:
        params = MediaListQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        assets = MediaAsset.objects.order_by("-created_at", "-pk")
        if params.validated_data.get("kind"):
            assets = assets.filter(kind=params.validated_data["kind"])
        items, meta = paginate(assets, request)
        return Response({**meta, "results": MediaAssetSerializer(items, many=True).data})


class MediaUploadView(CatalogStaffView):
    parser_classes = (MultiPartParser,)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        # Before CSRF and authentication: both can read the form body, and an oversized body
        # must be refused before anything parses it (or spools it to disk).
        if request.method == "POST":
            limit: int = settings.MATECART_MEDIA_MAX_BYTES
            try:
                length = int(request.META.get("CONTENT_LENGTH") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                raise DomainError("Send the file with a Content-Length.", "length_required")
            if length > limit + _MULTIPART_OVERHEAD:
                raise DomainError(
                    f"Files can be at most {limit // (1024 * 1024)} MB.", "file_too_large"
                )
        super().initial(request, *args, **kwargs)

    @extend_schema(
        operation_id="manage_media_upload",
        tags=TAGS,
        request={"multipart/form-data": MediaUploadSerializer},
        responses={201: MediaAssetSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = MediaUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        asset = services.upload(
            stream=serializer.validated_data["file"],
            alt_text=serializer.validated_data.get("alt_text", ""),
            actor=self.user,
        )
        return Response(MediaAssetSerializer(asset).data, status=status.HTTP_201_CREATED)


@method_decorator(never_cache, name="dispatch")
class MediaDetailView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_media_asset", tags=TAGS, responses={200: MediaAssetSerializer}
    )
    def get(self, request: Request, asset_id: uuid.UUID) -> Response:
        asset = found(MediaAsset, pk=asset_id)
        return Response(MediaAssetSerializer(asset).data)

    @extend_schema(
        operation_id="manage_media_update",
        tags=TAGS,
        request=MediaUpdateSerializer,
        responses={200: MediaAssetSerializer},
    )
    def patch(self, request: Request, asset_id: uuid.UUID) -> Response:
        asset = found(MediaAsset, pk=asset_id)
        data = MediaUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        saved = services.update(
            asset=asset, alt_text=data.validated_data["alt_text"], actor=self.user
        )
        return Response(MediaAssetSerializer(saved).data)

    @extend_schema(operation_id="manage_media_delete", tags=TAGS, responses={204: None})
    def delete(self, request: Request, asset_id: uuid.UUID) -> Response:
        services.delete(asset=found(MediaAsset, pk=asset_id), actor=self.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MediaFileView(CatalogStaffView):
    @extend_schema(
        operation_id="manage_media_file",
        tags=TAGS,
        responses={(200, "application/octet-stream"): bytes},
    )
    def get(self, request: Request, asset_id: uuid.UUID) -> Any:
        return file_response(found(MediaAsset, pk=asset_id), public=False)
