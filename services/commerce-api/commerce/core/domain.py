"""Errors and helpers shared by the commerce domain services (catalogue, pricing, inventory, media).

Services raise these instead of returning flags, so every API answers in the standard envelope
(``{"error": {"code", "message", "details"?}}``) and business rules stay out of the views.
"""

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, models, transaction
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError as ApiValidationError


class DomainError(APIException):
    """A request the business rules refuse (400). ``code`` names the rule for clients."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    default_detail = "This change isn't allowed."
    default_code = "invalid_request"

    def __init__(self, detail: str | None = None, code: str | None = None) -> None:
        super().__init__(detail=detail, code=code)


class ConflictError(DomainError):
    """The request conflicts with the current state (409), e.g. deleting something still used."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


def validation_error(field: str, message: str, code: str = "invalid") -> ApiValidationError:
    return ApiValidationError({field: [message]}, code=code)


def validated[M: models.Model](instance: M, *, exclude: list[str] | None = None) -> M:
    """``full_clean()`` with Django's errors turned into the API's validation envelope."""
    try:
        instance.full_clean(exclude=exclude)
    except DjangoValidationError as exc:
        detail: Any = (
            exc.message_dict if hasattr(exc, "error_dict") else {"non_field": exc.messages}
        )
        raise ApiValidationError(detail) from exc
    return instance


def save_unique(
    instance: models.Model, conflicts: Mapping[str, tuple[str, str]], **save_kwargs: Any
) -> None:
    """Save in a savepoint and report a unique-constraint violation named in ``conflicts`` as a
    field error. Covers races a pre-check cannot: the database is the authority on uniqueness."""
    try:
        with transaction.atomic():
            instance.save(**save_kwargs)
    except IntegrityError as exc:
        diag = getattr(exc.__cause__, "diag", None)
        name = getattr(diag, "constraint_name", None)
        if isinstance(name, str) and name in conflicts:
            field, message = conflicts[name]
            raise ApiValidationError({field: [message]}, code="unique") from exc
        raise


def found[M: models.Model](model: type[M], **lookup: Any) -> M:
    """The row matching ``lookup`` through the model's (tenant-scoped) default manager, or 404."""
    row = model._default_manager.filter(**lookup).first()
    if row is None:
        raise Http404
    return row


def found_in[M: models.Model](queryset: models.QuerySet[M, M], **lookup: Any) -> M:
    """Like ``found`` for a prepared queryset (prefetches, annotations)."""
    row = queryset.filter(**lookup).first()
    if row is None:
        raise Http404
    return row
