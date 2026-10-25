from typing import Any

from rest_framework import serializers

from .models import MediaAsset, MediaKind


class MediaUploadSerializer(serializers.Serializer[dict[str, Any]]):
    """multipart/form-data: the file is checked from its bytes, never from its name or type."""

    file = serializers.FileField(allow_empty_file=False, use_url=False)
    alt_text = serializers.CharField(max_length=300, required=False, allow_blank=True)


class MediaUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    alt_text = serializers.CharField(max_length=300, allow_blank=True)


class MediaListQuerySerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.ChoiceField(choices=MediaKind.choices, required=False)


class MediaAssetSerializer(serializers.ModelSerializer[MediaAsset]):
    url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = (
            "id",
            "kind",
            "content_type",
            "size_bytes",
            "checksum_sha256",
            "width",
            "height",
            "alt_text",
            "url",
            "created_at",
        )
        read_only_fields = fields

    def get_url(self, asset: MediaAsset) -> str:
        return f"/api/manage/media/assets/{asset.pk}/file/"
