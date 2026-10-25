import uuid
from collections.abc import Collection, Iterable
from typing import Any, Self, cast

from django.core.validators import RegexValidator
from django.db import models, router, transaction
from django.db.models import Q

from commerce.core.models import TimestampedModel, UUIDModel

from .context import get_active_tenant_id_or_none, in_unscoped_access
from .exceptions import CrossTenantAccessError, TenantContextMissing, TenantImmutableError
from .managers import TenantScopedManager

# Lowercase DNS label, so a handle can later double as a subdomain.
TENANT_HANDLE_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"


class TenantStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    ARCHIVED = "archived", "Archived"


class Tenant(UUIDModel, TimestampedModel):
    """One client store's ownership and isolation boundary in the commerce database.

    In single-tenant mode a deployment serves exactly one tenant, identified by
    ``settings.MATECART_TENANT_ID``; the same opaque id is the Central Console's reference to the
    store. Business configuration lives in ``stores.StoreSettings``.
    """

    handle = models.CharField(
        max_length=63,
        unique=True,
        validators=[RegexValidator(TENANT_HANDLE_PATTERN, "Use lowercase letters, digits and -.")],
        help_text="Stable, human-readable operations identifier, e.g. rahim-fashion.",
    )
    status = models.CharField(
        max_length=16, choices=TenantStatus.choices, default=TenantStatus.ACTIVE
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(handle__regex=TENANT_HANDLE_PATTERN),
                name="tenancy_tenant_handle_format",
            ),
            models.CheckConstraint(
                condition=Q(status__in=TenantStatus.values), name="tenancy_tenant_status_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.handle


class TenantOwnedModel(UUIDModel):
    """Base for every row that belongs to a tenant.

    Guarantees, all covered by tests:
    - ``objects`` is scoped to the active tenant and refuses to run without one;
    - new rows take the active tenant; saving or deleting another tenant's row is refused;
    - updates, deletes and refreshes are qualified by the tenant *stored* in the database, so an
      instance built in code with another tenant's primary key cannot reach that row;
    - a row's tenant never changes;
    - foreign keys to other tenant-owned models must carry a TenantForeignKeyConstraint, which
      PostgreSQL enforces (checked by the ``tenancy`` system checks).
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="+",
        editable=False,
        # The (tenant, id) unique index below already serves tenant-prefixed lookups.
        db_index=False,
    )

    objects = TenantScopedManager()

    # Tenant the row had when loaded from the database; used to keep ownership immutable.
    _loaded_tenant_id: uuid.UUID | None = None

    class Meta:
        abstract = True
        constraints = [
            # Target of TenantForeignKeyConstraint: lets children reference (tenant_id, id).
            models.UniqueConstraint(
                fields=["tenant", "id"], name="%(app_label)s_%(class)s_tenant_row"
            ),
        ]

    @classmethod
    def from_db(cls, db: str | None, field_names: Collection[str], values: Collection[Any]) -> Self:
        instance = super().from_db(db, field_names, values)
        instance._loaded_tenant_id = instance.__dict__.get("tenant_id")
        return instance

    def full_clean(self, *args: Any, **kwargs: Any) -> None:
        # Validate new rows as they will be saved: owned by the active tenant.
        active = get_active_tenant_id_or_none()
        if cast(uuid.UUID | None, self.tenant_id) is None and active is not None:
            self.tenant_id = active
        super().full_clean(*args, **kwargs)

    def prepare_tenant_write(self) -> None:
        """Assign the active tenant to new rows and refuse writes that would cross tenants."""
        active = get_active_tenant_id_or_none()
        tenant_id = cast(uuid.UUID | None, self.tenant_id)
        if tenant_id is None:
            if active is None:
                raise TenantContextMissing(
                    f"Cannot save {type(self).__name__} without an active tenant."
                )
            self.tenant_id = tenant_id = active
        if self._loaded_tenant_id is not None and tenant_id != self._loaded_tenant_id:
            raise TenantImmutableError(f"{type(self).__name__} {self.pk} cannot change tenant.")
        self._check_tenant_access(tenant_id, active)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.prepare_tenant_write()
        super().save(*args, **kwargs)
        self._loaded_tenant_id = self.tenant_id

    # The tenant id on an instance built in code proves nothing about the row its primary key
    # points at, and Django targets existing rows by primary key alone. Every statement that
    # touches an existing row is therefore qualified by tenant at the database (Phase 02.1, D-034).

    def _do_update(self, base_qs: models.QuerySet[Any], *args: Any, **kwargs: Any) -> bool:
        """The UPDATE issued by every save() path: ``WHERE id = %s AND tenant_id = %s``."""
        updated: bool = super()._do_update(
            base_qs.filter(tenant_id=self.tenant_id), *args, **kwargs
        )
        if not updated and base_qs.filter(pk=self.pk).exclude(tenant_id=self.tenant_id).exists():
            raise CrossTenantAccessError(
                f"{type(self).__name__} {self.pk} belongs to another tenant."
            )
        return updated

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        self._check_tenant_access(self.tenant_id, get_active_tenant_id_or_none())
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            # Lock the stored row and check *its* tenant before Django deletes by primary key.
            stored_tenant_id = (
                type(self)
                ._base_manager.using(using)
                .select_for_update()
                .filter(pk=self.pk)
                .values_list("tenant_id", flat=True)
                .first()
            )
            if stored_tenant_id is not None and stored_tenant_id != self.tenant_id:
                raise CrossTenantAccessError(
                    f"{type(self).__name__} {self.pk} belongs to another tenant."
                )
            return super().delete(using=using, keep_parents=keep_parents)

    def refresh_from_db(
        self,
        using: str | None = None,
        fields: Iterable[str] | None = None,
        from_queryset: models.QuerySet[Self] | None = None,
    ) -> None:
        if from_queryset is None:
            # Read only a row stored under this instance's tenant (or the active tenant while the
            # instance's own tenant is deferred). __dict__ avoids re-entering deferred loading.
            tenant_id = self.__dict__.get("tenant_id") or get_active_tenant_id_or_none()
            if tenant_id is not None:
                from_queryset = (
                    type(self)
                    ._base_manager.db_manager(using, hints={"instance": self})
                    .filter(tenant_id=tenant_id)
                )
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)

    def _check_tenant_access(self, tenant_id: uuid.UUID, active: uuid.UUID | None) -> None:
        if active is not None:
            if tenant_id != active:
                raise CrossTenantAccessError(
                    f"{type(self).__name__} {self.pk} belongs to another tenant."
                )
        elif not in_unscoped_access():
            raise TenantContextMissing(
                f"Cannot write {type(self).__name__} without an active tenant."
            )
