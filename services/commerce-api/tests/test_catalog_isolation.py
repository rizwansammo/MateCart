"""Two-tenant isolation for every Phase 04 model, against real PostgreSQL.

Each model is created in tenant B; tenant A must not read, change, delete or refresh it, nothing
works without a tenant context, and PostgreSQL itself refuses references that cross tenants.
"""

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, connection, models, transaction
from django.db.models import F

from commerce.catalog.models import Product, ProductVariant
from commerce.inventory.models import StockLevel, StockMovement
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.context import tenant_context
from commerce.tenancy.exceptions import CrossTenantAccessError, TenantContextMissing
from commerce.tenancy.models import Tenant

from .catalog_helpers import full_catalogue

pytestmark = pytest.mark.django_db

ROW_NAMES = [
    "asset",
    "brand",
    "category",
    "collection",
    "attribute",
    "option",
    "warranty",
    "product",
    "variant",
    "price_list",
    "location",
    "batch",
    "serial",
    "level",
    "movement",
    "collection_product",
    "placement",
    "product_option",
    "option_value",
    "spec",
    "media",
    "apparel",
    "grocery",
    "electronics",
    "book",
    "price",
    "batch_level",
]
# Every tenant guard declared by the new apps, as (model, constraint field).
GUARDS = sorted(
    (
        (model, constraint.field)
        for app in ("catalog", "pricing", "inventory", "media")
        for model in apps.get_app_config(app).get_models()
        for constraint in model._meta.constraints
        if isinstance(constraint, TenantForeignKeyConstraint)
    ),
    key=lambda guard: (guard[0]._meta.label, guard[1]),
)


def _write_and_check_constraints(write: Callable[[], None]) -> None:
    """Write, then check deferred constraints (tenant foreign keys) immediately."""
    write()
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.fixture
def foreign(tenant_b: Tenant) -> dict[str, Any]:
    return full_catalogue(tenant_b)


@pytest.mark.parametrize("name", ROW_NAMES)
def test_rows_of_another_tenant_are_unreachable(
    tenant_a: Tenant, tenant_b: Tenant, foreign: dict[str, Any], name: str
) -> None:
    row = foreign[name]
    model = type(row)
    with tenant_context(tenant_a):
        assert not model.objects.filter(pk=row.pk).exists()
        if name != "movement":  # the ledger refuses every update and delete anyway
            assert model.objects.filter(pk=row.pk).update(id=F("id")) == 0
            assert model.objects.filter(pk=row.pk).delete()[0] == 0
        with pytest.raises(CrossTenantAccessError):
            row.refresh_from_db()
    with pytest.raises(TenantContextMissing):
        model.objects.filter(pk=row.pk).exists()
    with tenant_context(tenant_b):
        row.refresh_from_db()
        assert model.objects.filter(pk=row.pk).exists()


@pytest.mark.parametrize(
    ("model", "field"), GUARDS, ids=lambda value: getattr(value, "__name__", value)
)
def test_postgresql_refuses_every_cross_tenant_reference(
    tenant_a: Tenant, foreign: dict[str, Any], model: type[models.Model], field: str
) -> None:
    """Point each guarded foreign key of a tenant-A row at tenant B's row: the composite tenant
    foreign key fails when constraints are checked."""
    own = full_catalogue(tenant_a)
    row = next(value for value in own.values() if type(value) is model)
    target = model._meta.get_field(field).related_model
    foreign_target = next(value for value in foreign.values() if type(value) is target)
    column = cast(models.Field[Any, Any], model._meta.get_field(field)).attname

    def write() -> None:
        if model is StockMovement:  # append-only: prove it on insert instead of update
            values = {
                f.attname: getattr(row, f.attname)
                for f in model._meta.concrete_fields
                if f.attname not in ("id", "tenant_id", "idempotency_key")
            }
            values[column] = foreign_target.pk
            if column == "reversal_of_id":
                values["movement_type"] = "reversal"
            StockMovement.objects.create(**values)
        else:
            model._default_manager.filter(pk=row.pk).update(**{column: foreign_target.pk})

    with tenant_context(tenant_a), pytest.raises(IntegrityError), transaction.atomic():
        _write_and_check_constraints(write)


def test_ledger_cross_tenant_reference_is_refused_on_insert(
    tenant_a: Tenant, foreign: dict[str, Any]
) -> None:
    own = full_catalogue(tenant_a)
    movement = own["movement"]

    def write() -> None:
        StockMovement.objects.create(
            operation_id=movement.operation_id,
            movement_type="receipt",
            variant_id=foreign["variant"].pk,
            location=own["location"],
            on_hand_delta=1,
            on_hand_after=1,
            reserved_after=0,
        )

    with tenant_context(tenant_a), pytest.raises(IntegrityError), transaction.atomic():
        _write_and_check_constraints(write)


def test_deferred_fields_and_relations_stay_in_the_active_tenant(
    tenant_a: Tenant, foreign: dict[str, Any]
) -> None:
    own = full_catalogue(tenant_a)
    with tenant_context(tenant_a):
        deferred = ProductVariant.objects.only("pk").get(pk=own["variant"].pk)
        assert deferred.sku == "THING-M"
        forged = ProductVariant(sku="X", product_id=foreign["product"].pk)
        with pytest.raises(ObjectDoesNotExist):
            _ = forged.product
        level = StockLevel(variant_id=foreign["variant"].pk, location_id=own["location"].pk)
        with pytest.raises(ObjectDoesNotExist):
            _ = level.variant


def test_forged_instances_cannot_write_another_tenants_rows(
    tenant_a: Tenant, foreign: dict[str, Any]
) -> None:
    product: Product = foreign["product"]
    with tenant_context(tenant_a):
        forged = Product(pk=product.pk, name="Hijacked", slug="hijacked")
        forged._state.adding = False
        with pytest.raises(CrossTenantAccessError), transaction.atomic():
            forged.save()
        with pytest.raises(CrossTenantAccessError), transaction.atomic():
            forged.delete()
    with tenant_context(foreign["product"].tenant_id):
        product.refresh_from_db()
        assert product.name == "Thing"


def test_tenant_owned_relations_use_guarded_through_models() -> None:
    """Phase 04 adds no many-to-many fields: every link between tenant-owned models is a model
    with tenant foreign keys (checked by tenancy.E003/E004 at startup too)."""
    for app in ("catalog", "pricing", "inventory", "media"):
        for model in apps.get_app_config(app).get_models():
            assert not model._meta.many_to_many, model


@pytest.mark.parametrize("area", ["catalog", "pricing", "inventory", "media", "content", "stores"])
def test_phase_04_code_cannot_opt_out_of_scoped_data_access(area: str) -> None:
    root = Path(__file__).resolve().parents[1] / "commerce" / area
    forbidden = {"raw", "extra", "cursor", "unscoped_access", "QuerySet", "RawSQL"}
    violations: list[str] = []
    for file in root.rglob("*.py"):
        if "migrations" in file.parts:
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else (node.func.id if isinstance(node.func, ast.Name) else "")
                )
                if name in forbidden:
                    violations.append(f"{file.name}:{node.lineno}:{name}")
    assert violations == [], f"Phase 04 code must use tenant-scoped managers: {violations}"
