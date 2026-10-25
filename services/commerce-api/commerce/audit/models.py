from typing import Any, ClassVar, NoReturn

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q
from django.utils import timezone

from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.managers import TenantQuerySet, TenantScopedManager
from commerce.tenancy.models import TenantOwnedModel

AUDIT_ACTION_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"


class AuditLogImmutableError(Exception):
    """Audit events are append-only: they can be created, never changed or deleted."""


class ActorType(models.TextChoices):
    USER = "user", "User"
    SYSTEM = "system", "System"


class AuditEventQuerySet(TenantQuerySet["AuditEvent"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise AuditLogImmutableError("Audit events cannot be updated.")

    def delete(self) -> NoReturn:
        raise AuditLogImmutableError("Audit events cannot be deleted.")

    def bulk_update(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AuditLogImmutableError("Audit events cannot be updated.")


class AuditEventManager(TenantScopedManager["AuditEvent"]):
    _queryset_class = AuditEventQuerySet


class AuditEvent(TenantOwnedModel):
    """Who did what to which resource, when, in which store, within which request.

    Written with ``commerce.audit.services.record_audit_event`` inside the transaction that makes
    the change, so the event exists exactly when the change does. ``changes`` holds non-sensitive
    field differences only. The table is append-only in the ORM and in PostgreSQL (trigger in
    migration 0002), so history cannot be rewritten through the application.
    """

    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    action = models.CharField(max_length=100, help_text="Dotted verb, e.g. store.settings.updated.")
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        # Accounts with history are deactivated or anonymised, never deleted.
        on_delete=models.PROTECT,
        related_name="+",
    )
    actor_label = models.CharField(
        max_length=150, blank=True, help_text="System actor, e.g. provision_tenant."
    )
    resource_type = models.CharField(
        max_length=100, help_text="app_label.model, e.g. stores.storesettings."
    )
    resource_id = models.CharField(max_length=64, blank=True)
    changes = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    request_id = models.CharField(max_length=64, blank=True)

    objects: ClassVar[AuditEventManager] = AuditEventManager()

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="actor_user", name="audit_auditevent_actor_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(action__regex=AUDIT_ACTION_PATTERN),
                name="audit_auditevent_action_format",
            ),
            models.CheckConstraint(
                condition=(Q(actor_type=ActorType.USER) & Q(actor_user__isnull=False))
                | (Q(actor_type=ActorType.SYSTEM) & Q(actor_user__isnull=True)),
                name="audit_auditevent_actor_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "-occurred_at"], name="audit_event_tenant_time"),
            models.Index(
                fields=["tenant", "resource_type", "resource_id"],
                name="audit_event_tenant_resource",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.resource_type}:{self.resource_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AuditLogImmutableError("Audit events cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise AuditLogImmutableError("Audit events cannot be deleted.")
