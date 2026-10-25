"""Querysets and managers for tenant-owned models.

``Model.objects`` returns only the active tenant's rows and raises TenantContextMissing when no
tenant is active. Django's ``_base_manager`` stays unfiltered on purpose: Django uses it to follow
foreign keys from rows that were already loaded through a scoped query, and the database-level
tenant foreign keys (TenantForeignKeyConstraint) guarantee those targets share the tenant.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from django.db import models

from .context import get_active_tenant_id, in_unscoped_access
from .exceptions import TenantImmutableError

if TYPE_CHECKING:
    from .models import TenantOwnedModel

_M = TypeVar("_M", bound="TenantOwnedModel")

_TENANT_FIELDS = frozenset({"tenant", "tenant_id"})


class TenantQuerySet(models.QuerySet[_M]):
    def update(self, **kwargs: Any) -> int:
        if _TENANT_FIELDS & kwargs.keys():
            raise TenantImmutableError("A row's tenant cannot be changed.")
        return super().update(**kwargs)

    def bulk_create(self, objs: Iterable[_M], *args: Any, **kwargs: Any) -> list[_M]:
        objs = list(objs)
        for obj in objs:
            obj.prepare_tenant_write()
        return super().bulk_create(objs, *args, **kwargs)

    def bulk_update(
        self, objs: Iterable[_M], fields: Iterable[str], batch_size: int | None = None
    ) -> int:
        fields = list(fields)
        if _TENANT_FIELDS & set(fields):
            raise TenantImmutableError("A row's tenant cannot be changed.")
        objs = list(objs)
        for obj in objs:
            obj.prepare_tenant_write()
        return super().bulk_update(objs, fields, batch_size=batch_size)


class TenantScopedManager(models.Manager[_M]):
    _queryset_class: type[TenantQuerySet[Any]] = TenantQuerySet

    def get_queryset(self) -> TenantQuerySet[_M]:
        queryset = cast("TenantQuerySet[_M]", super().get_queryset())
        if in_unscoped_access():
            return queryset
        return queryset.filter(tenant_id=get_active_tenant_id())
