"""The tenancy system checks reject models that skip the isolation rules."""

from typing import ClassVar

from django.db import models
from django.test.utils import isolate_apps

from commerce.audit.models import AuditEvent
from commerce.identity.models import User
from commerce.stores.models import StoreSettings
from commerce.tenancy.checks import check_tenant_owned_model, check_tenant_owned_models
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel


def _ids(model: type[models.Model]) -> list[str]:
    return [message.id for message in check_tenant_owned_model(model) if message.id]


def test_project_models_pass() -> None:
    assert check_tenant_owned_models() == []


@isolate_apps("commerce.tenancy")
def test_unguarded_foreign_keys_and_many_to_many_are_flagged() -> None:
    class Parent(TenantOwnedModel):
        class Meta(TenantOwnedModel.Meta):
            app_label = "tenancy"

    class Child(TenantOwnedModel):
        parent = models.ForeignKey(Parent, on_delete=models.CASCADE)
        tags = models.ManyToManyField(Parent, related_name="+")

        class Meta(TenantOwnedModel.Meta):
            app_label = "tenancy"

    assert sorted(_ids(Child)) == ["tenancy.E003", "tenancy.E004"]


@isolate_apps("commerce.tenancy")
def test_guarded_foreign_keys_pass() -> None:
    class Parent(TenantOwnedModel):
        class Meta(TenantOwnedModel.Meta):
            app_label = "tenancy"

    class Child(TenantOwnedModel):
        parent = models.ForeignKey(Parent, on_delete=models.CASCADE)

        class Meta(TenantOwnedModel.Meta):
            app_label = "tenancy"
            constraints = [
                *TenantOwnedModel.Meta.constraints,
                TenantForeignKeyConstraint(field="parent", name="child_parent_same_tenant"),
            ]

    assert _ids(Child) == []


@isolate_apps("commerce.tenancy")
def test_unscoped_default_or_base_managers_are_flagged() -> None:
    class PlainManager(TenantOwnedModel):
        objects = models.Manager()  # type: ignore[assignment]

        class Meta(TenantOwnedModel.Meta):
            app_label = "tenancy"

    assert _ids(PlainManager) == ["tenancy.E005", "tenancy.E005"]


def test_project_models_read_through_scoped_managers() -> None:
    from commerce.tenancy.managers import TenantScopedManager

    for model in (User, StoreSettings, AuditEvent):
        assert isinstance(model._base_manager, TenantScopedManager)
        assert isinstance(model._default_manager, TenantScopedManager)


@isolate_apps("commerce.tenancy")
def test_replacing_the_base_constraints_is_flagged() -> None:
    class Careless(TenantOwnedModel):
        class Meta(TenantOwnedModel.Meta):
            app_label = "tenancy"
            constraints: ClassVar[list[models.BaseConstraint]] = []

    assert _ids(Careless) == ["tenancy.E002"]
