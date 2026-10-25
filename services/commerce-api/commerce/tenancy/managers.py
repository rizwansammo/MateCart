"""Querysets and managers for tenant-owned models.

``Model.objects`` returns only the active tenant's rows and raises TenantContextMissing when no
tenant is active. Django's ``_base_manager`` stays unfiltered on purpose: Django uses it to follow
foreign keys from rows that were already loaded through a scoped query, and the database-level
tenant foreign keys (TenantForeignKeyConstraint) guarantee those targets share the tenant. Writes
that target existing rows are tenant-qualified in SQL (see TenantOwnedModel and bulk_update).
"""

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from django.db import models

from .context import get_active_tenant_id, in_unscoped_access
from .exceptions import CrossTenantAccessError, TenantImmutableError

if TYPE_CHECKING:
    from .models import TenantOwnedModel

_M = TypeVar("_M", bound="TenantOwnedModel")

_TENANT_FIELDS = frozenset({"tenant", "tenant_id"})


class TenantQuerySet(models.QuerySet[_M]):
    def update(self, **kwargs: Any) -> int:
        if _TENANT_FIELDS & kwargs.keys():
            raise TenantImmutableError("A row's tenant cannot be changed.")
        return super().update(**kwargs)

    def bulk_create(
        self,
        objs: Iterable[_M],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[_M]:
        if update_conflicts:
            # ON CONFLICT ... DO UPDATE must only ever match a row of the same tenant.
            if not _TENANT_FIELDS & set(unique_fields or ()):
                raise CrossTenantAccessError(
                    "Upserts on tenant-owned rows need 'tenant' in unique_fields, so a conflict "
                    "can only match the same tenant's row."
                )
            if _TENANT_FIELDS & set(update_fields or ()):
                raise TenantImmutableError("A row's tenant cannot be changed.")
        objs = list(objs)
        for obj in objs:
            obj.prepare_tenant_write()
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def bulk_update(
        self, objs: Iterable[_M], fields: Iterable[str], batch_size: int | None = None
    ) -> int:
        fields = list(fields)
        if _TENANT_FIELDS & set(fields):
            raise TenantImmutableError("A row's tenant cannot be changed.")
        by_tenant: dict[uuid.UUID, list[_M]] = defaultdict(list)
        for obj in objs:
            obj.prepare_tenant_write()
            by_tenant[obj.tenant_id].append(obj)
        # One UPDATE per tenant, qualified by it (WHERE pk IN (...) AND tenant_id = %s): an object
        # carrying another tenant's primary key never reaches that row, even with unscoped access.
        return sum(
            super(TenantQuerySet, self.filter(tenant_id=tenant_id)).bulk_update(
                group, fields, batch_size=batch_size
            )
            for tenant_id, group in by_tenant.items()
        )


class TenantScopedManager(models.Manager[_M]):
    _queryset_class: type[TenantQuerySet[Any]] = TenantQuerySet

    def get_queryset(self) -> TenantQuerySet[_M]:
        queryset = cast("TenantQuerySet[_M]", super().get_queryset())
        if in_unscoped_access():
            return queryset
        return queryset.filter(tenant_id=get_active_tenant_id())
