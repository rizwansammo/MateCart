"""System checks that keep every tenant-owned model on the isolation rules.

They run with ``manage.py check``, before ``runserver`` and ``migrate``, and in the test suite, so a
model that forgets a rule fails the build instead of leaking data later.
"""

from collections.abc import Sequence
from typing import Any

from django.apps import apps
from django.apps.config import AppConfig
from django.core import checks
from django.db import models

from .constraints import TenantForeignKeyConstraint
from .models import TenantOwnedModel


def check_tenant_owned_models(
    app_configs: Sequence[AppConfig] | None = None,
    databases: Sequence[str] | None = None,
    **kwargs: Any,
) -> list[checks.CheckMessage]:
    errors: list[checks.CheckMessage] = []
    for model in apps.get_models():
        if issubclass(model, TenantOwnedModel):
            errors.extend(check_tenant_owned_model(model))
    return errors


def check_tenant_owned_model(model: type[models.Model]) -> list[checks.CheckMessage]:
    errors: list[checks.CheckMessage] = []
    meta = model._meta
    pk_name = meta.pk.name if meta.pk else "id"

    tenant_field = meta.get_field("tenant")
    if not isinstance(tenant_field, models.ForeignKey) or tenant_field.null:
        errors.append(
            checks.Error(
                "The tenant field must be a non-nullable foreign key.", obj=model, id="tenancy.E001"
            )
        )

    has_tenant_row_key = any(
        isinstance(constraint, models.UniqueConstraint)
        and tuple(constraint.fields) == ("tenant", pk_name)
        and constraint.condition is None
        for constraint in meta.constraints
    )
    if not has_tenant_row_key:
        errors.append(
            checks.Error(
                "Tenant-owned models need UniqueConstraint(fields=['tenant', 'id']).",
                hint="Extend TenantOwnedModel.Meta.constraints instead of replacing them.",
                obj=model,
                id="tenancy.E002",
            )
        )

    guarded = {c.field for c in meta.constraints if isinstance(c, TenantForeignKeyConstraint)}
    for field in meta.get_fields(include_hidden=False):
        if not field.is_relation or field.auto_created or field.name == "tenant":
            continue
        related = field.related_model
        if not (isinstance(related, type) and issubclass(related, TenantOwnedModel)):
            continue
        if field.many_to_many:
            errors.append(
                checks.Error(
                    f"{field.name} is a many-to-many relation between tenant-owned models.",
                    hint="Use an explicit tenant-owned through model with tenant foreign keys.",
                    obj=model,
                    id="tenancy.E003",
                )
            )
        elif field.name not in guarded:
            errors.append(
                checks.Error(
                    f"{field.name} references a tenant-owned model without a tenant guard.",
                    hint=f"Add TenantForeignKeyConstraint(field={field.name!r}, name=...).",
                    obj=model,
                    id="tenancy.E004",
                )
            )
    return errors
