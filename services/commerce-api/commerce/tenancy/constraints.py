from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models.constraints import BaseConstraint

CROSS_TENANT_REFERENCE = "cross_tenant_reference"


def _column(model: type[models.Model], name: str) -> str:
    field = model._meta.get_field(name)
    if not isinstance(field, models.Field) or field.column is None:
        raise TypeError(f"{model.__name__}.{name} is not a concrete column.")
    return field.column


def _require(
    model: type[models.Model] | None, schema_editor: BaseDatabaseSchemaEditor | None
) -> tuple[type[models.Model], BaseDatabaseSchemaEditor]:
    if model is None or schema_editor is None:
        raise TypeError("TenantForeignKeyConstraint needs a model and a schema editor.")
    return model, schema_editor


class TenantForeignKeyConstraint(BaseConstraint):
    """Database guarantee that a foreign key between tenant-owned models stays inside one tenant.

    Adds ``FOREIGN KEY (tenant_id, <field>_id) REFERENCES <target> (tenant_id, id)`` next to the
    ordinary foreign key, using the target's ``(tenant, id)`` unique constraint. PostgreSQL then
    rejects cross-tenant references even from raw ids, bulk operations or SQL. NULL references
    pass (MATCH SIMPLE). Like Django's foreign keys it is checked at commit (DEFERRABLE).
    """

    def __init__(self, *, field: str, name: str) -> None:
        super().__init__(name=name)
        self.field = field

    def _target(
        self, model: type[models.Model]
    ) -> tuple[models.ForeignKey[Any, Any], type[models.Model]]:
        field = model._meta.get_field(self.field)
        if not isinstance(field, models.ForeignKey) or not isinstance(field.related_model, type):
            raise TypeError(f"{model.__name__}.{self.field} must be a foreign key to a model.")
        return field, field.related_model

    def create_sql(
        self, model: type[models.Model] | None, schema_editor: BaseDatabaseSchemaEditor | None
    ) -> str:
        model, schema_editor = _require(model, schema_editor)
        quote = schema_editor.quote_name
        field, target = self._target(model)
        target_pk = target._meta.pk
        if target_pk is None or target_pk.column is None:
            raise TypeError(f"{target.__name__} has no primary key column.")
        return (
            f"ALTER TABLE {quote(model._meta.db_table)} ADD CONSTRAINT {quote(self.name)} "
            f"FOREIGN KEY ({quote(_column(model, 'tenant'))}, {quote(_column(model, field.name))}) "
            f"REFERENCES {quote(target._meta.db_table)} "
            f"({quote(_column(target, 'tenant'))}, {quote(target_pk.column)}) "
            "DEFERRABLE INITIALLY DEFERRED"
        )

    def constraint_sql(
        self, model: type[models.Model] | None, schema_editor: BaseDatabaseSchemaEditor | None
    ) -> str:
        # Called while CREATE TABLE is built, when the referenced table may not exist yet:
        # defer the ALTER TABLE until the schema editor has created every table.
        model, schema_editor = _require(model, schema_editor)
        schema_editor.deferred_sql.append(self.create_sql(model, schema_editor))
        return ""

    def remove_sql(
        self, model: type[models.Model] | None, schema_editor: BaseDatabaseSchemaEditor | None
    ) -> str:
        model, schema_editor = _require(model, schema_editor)
        table = schema_editor.quote_name(model._meta.db_table)
        return f"ALTER TABLE {table} DROP CONSTRAINT {schema_editor.quote_name(self.name)}"

    def validate(
        self,
        model: type[models.Model],
        instance: models.Model,
        exclude: Any = None,
        using: str = DEFAULT_DB_ALIAS,
    ) -> None:
        """Model.full_clean() support: report the cross-tenant reference as a field error."""
        if exclude and ("tenant" in exclude or self.field in exclude):
            return
        field, target = self._target(model)
        target_pk = getattr(instance, field.attname)
        tenant_id = getattr(instance, "tenant_id", None)
        if target_pk is None or tenant_id is None:
            return
        if not target._base_manager.using(using).filter(pk=target_pk, tenant_id=tenant_id).exists():
            raise ValidationError(
                {
                    self.field: ValidationError(
                        "The selected %(field)s belongs to a different store.",
                        code=CROSS_TENANT_REFERENCE,
                        params={"field": self.field},
                    )
                }
            )

    def deconstruct(self) -> tuple[str, Sequence[Any], dict[str, Any]]:
        path, args, kwargs = super().deconstruct()
        kwargs["field"] = self.field
        return path, args, kwargs

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TenantForeignKeyConstraint):
            return self.name == other.name and self.field == other.field
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.name, self.field))

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: field={self.field!r} name={self.name!r}>"
