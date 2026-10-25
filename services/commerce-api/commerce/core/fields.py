"""Serializer fields for references to tenant-owned rows."""

from typing import Any, ClassVar

from django.db import models
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


@extend_schema_field(OpenApiTypes.UUID)
class TenantRelatedField(serializers.PrimaryKeyRelatedField[Any]):
    """A UUID reference resolved through the model's tenant-scoped manager when the request is
    validated (never at import time, when no tenant is active). Another store's id is simply
    "not found", exactly like an id that does not exist."""

    default_error_messages: ClassVar[dict[str, Any]] = {
        "required": "This field is required.",
        "does_not_exist": "Not found.",
        "incorrect_type": "Enter a valid id.",
    }

    def __init__(self, model: type[models.Model], **kwargs: Any) -> None:
        self.model = model
        kwargs.setdefault("pk_field", serializers.UUIDField())
        super().__init__(**kwargs)

    def get_queryset(self) -> models.QuerySet[Any]:
        return self.model._default_manager.all()


@extend_schema_field(OpenApiTypes.STR)
class TenantSlugRelatedField(serializers.SlugRelatedField[Any]):
    """Like TenantRelatedField, but the reference is a store-unique code (e.g. an attribute's
    ``code``), resolved through the tenant-scoped manager when validated."""

    default_error_messages: ClassVar[dict[str, Any]] = {
        "does_not_exist": "{value} was not found.",
        "invalid": "Enter a valid code.",
    }

    def __init__(self, model: type[models.Model], slug_field: str, **kwargs: Any) -> None:
        self.model = model
        super().__init__(slug_field=slug_field, **kwargs)

    def get_queryset(self) -> models.QuerySet[Any]:
        return self.model._default_manager.all()
