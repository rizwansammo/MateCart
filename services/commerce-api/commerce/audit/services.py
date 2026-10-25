from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from django.db import models, transaction

from commerce.core.request_context import get_request_id

from .models import ActorType, AuditEvent

if TYPE_CHECKING:
    from commerce.identity.models import User

REDACTED = "[REDACTED]"
# Field names containing any of these never have their values stored in audit history.
_SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "otp",
    "api_key",
    "apikey",
    "private_key",
    "cvv",
    "cvc",
    "card_number",
)


def is_sensitive_field(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def field_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """``{"field": {"from": old, "to": new}}`` for every field whose value differs."""
    return {
        name: {"from": before.get(name), "to": after.get(name)}
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name)
    }


def record_audit_event(
    *,
    action: str,
    resource: models.Model,
    actor: "User | None" = None,
    actor_label: str = "",
    changes: Mapping[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event for the active tenant.

    Must run inside the ``transaction.atomic()`` block that performs the change: if the change
    rolls back, so does its event. Values of sensitive fields are replaced with ``[REDACTED]``.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "record_audit_event() must run inside the transaction that makes the change."
        )
    event = AuditEvent(
        action=action,
        actor_type=ActorType.USER if actor is not None else ActorType.SYSTEM,
        actor_user=actor,
        actor_label=actor_label,
        resource_type=resource._meta.label_lower,
        resource_id=str(resource.pk),
        changes={
            name: REDACTED if is_sensitive_field(name) else value
            for name, value in (changes or {}).items()
        },
        request_id=get_request_id() or "",
    )
    event.full_clean()
    event.save()
    return event
