"""Writing audit events, and keeping secrets and needless personal data out of them.

Two layers protect the ``changes`` payload (Phase 02.1, D-035):
1. Allowlist: ``field_changes()`` records only the fields a caller names explicitly.
2. Sanitiser: ``sanitize_audit_value()`` walks every nested mapping and list, redacts values under
   sensitive keys and values that look like secrets, and reduces anything that is not plain JSON
   data to a type marker. It builds new objects and never mutates the caller's data.
"""

import math
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from django.db import models, transaction

from commerce.core.request_context import get_request_id

from .models import ActorType, AuditEvent

if TYPE_CHECKING:
    from commerce.identity.models import User

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
MAX_DEPTH = 8
MAX_STRING_LENGTH = 1000

# Keys are split into words (snake_case, kebab-case and camelCase) before matching.
_KEY_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
# A whole word that marks a secret ("pin" matches `pin`, not `shipping`).
_SENSITIVE_WORDS = frozenset(
    {
        "auth",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "cvc",
        "cvv",
        "iban",
        "mfa",
        "otp",
        "pan",
        "passphrase",
        "passwd",
        "password",
        "pin",
        "pwd",
        "salt",
        "secret",
        "secrets",
        "signature",
        "token",
        "tokens",
        "totp",
    }
)
# Two adjacent words, or one run-together word (`api_key`, `apiKey`, `APIKEY`).
_SENSITIVE_COMPOUNDS = frozenset(
    {
        "accesskey",
        "accountnumber",
        "apikey",
        "backupcode",
        "backupcodes",
        "cardnumber",
        "clientsecret",
        "encryptionkey",
        "privatekey",
        "recoverycode",
        "recoverycodes",
        "routingnumber",
        "secretkey",
        "securitycode",
        "sessionid",
        "sessionkey",
        "signingkey",
    }
)
# Unambiguous fragments matched anywhere in the joined key (`newpassword`, `xapikeyid`).
_SENSITIVE_FRAGMENTS = (
    "apikey",
    "backupcode",
    "cardnumber",
    "credential",
    "passphrase",
    "password",
    "privatekey",
    "recoverycode",
    "secret",
    "token",
)
# Values that are secrets whatever key they sit under.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"^(?:pbkdf2_sha256|pbkdf2_sha1|argon2|bcrypt|bcrypt_sha256|scrypt|md5)\$"),
    re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]+$"),  # JSON Web Token
    re.compile(r"^(?:Bearer|Basic)\s+\S+", re.IGNORECASE),
)
_CARD_CANDIDATE = re.compile(r"^[\d -]{13,23}$")
_SCALARS = (bool, int, Decimal, uuid.UUID, datetime, date, time)


def is_sensitive_field(name: str) -> bool:
    words = [word.lower() for word in _KEY_WORDS.findall(name)]
    joined = "".join(words)
    return (
        any(word in _SENSITIVE_WORDS or word in _SENSITIVE_COMPOUNDS for word in words)
        or any(a + b in _SENSITIVE_COMPOUNDS for a, b in pairwise(words))
        or any(fragment in joined for fragment in _SENSITIVE_FRAGMENTS)
    )


def _is_card_number(value: str) -> bool:
    if not _CARD_CANDIDATE.match(value):
        return False
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        checksum += digit
    return checksum % 10 == 0


def _looks_secret(value: str) -> bool:
    return _is_card_number(value) or any(p.search(value) for p in _SECRET_VALUE_PATTERNS)


def sanitize_audit_value(value: object, _depth: int = 0) -> Any:
    """A JSON-safe copy of ``value`` with secrets redacted at every nesting level."""
    if _depth > MAX_DEPTH:
        return TRUNCATED
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED
            if is_sensitive_field(str(key))
            else sanitize_audit_value(item, _depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (set, frozenset)):
        return [sanitize_audit_value(item, _depth + 1) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_value(item, _depth + 1) for item in value]
    if isinstance(value, str):
        if _looks_secret(value):
            return REDACTED
        if len(value) > MAX_STRING_LENGTH:
            return f"{value[:MAX_STRING_LENGTH]}…{TRUNCATED}"
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if value is None or isinstance(value, _SCALARS):
        return value
    # Model instances, bytes, files and arbitrary objects never reach audit history.
    return f"[{type(value).__name__}]"


def field_changes(
    before: Mapping[str, Any], after: Mapping[str, Any], *, fields: Iterable[str]
) -> dict[str, Any]:
    """``{"field": {"from": old, "to": new}}`` for each allowlisted field whose value differs.

    ``fields`` is required: name exactly what the audit trail needs, never "everything".
    """
    return {
        name: {"from": before.get(name), "to": after.get(name)}
        for name in fields
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
    rolls back, so does its event. ``changes`` is sanitised before it is stored or validated.
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
        changes=sanitize_audit_value(dict(changes or {})),
        request_id=get_request_id() or "",
    )
    event.full_clean()
    event.save()
    return event
