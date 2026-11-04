"""Immediate delivery for authentication messages (D-086).

Codes, reset links and invitations are rendered in memory and sent synchronously after commit by
``commerce.core.delivery``: they never enter Celery, a notification record or a delivery log, and
are never delayed behind ordinary notifications. This module only chooses the transport: the
store's connected email connection, or ``None`` so the caller uses the deployment default.
"""

from __future__ import annotations

import logging
import secrets
import uuid

from . import connections, providers
from .models import ConnectionStatus, MailConnection, MailProvider

logger = logging.getLogger(__name__)


def auth_transport() -> MailConnection | None:
    """The store's connected transport, chosen while the request (and its tenant) is active.
    ``None``: the caller uses the deployment default. Never raises."""
    try:
        row = connections.active()
    except Exception:
        return None
    if row is None or row.status != ConnectionStatus.CONNECTED:
        return None
    return row


def send_now(
    row: MailConnection, *, to: str, subject: str, text: str, html: str
) -> providers.Result:
    """Send through an already chosen connection (no database access). Never raises."""
    try:
        key = (
            f"matecart-auth-{secrets.token_hex(12)}"
            if row.provider == MailProvider.MATEMAIL
            else ""
        )
        email = providers.Email(to=to, subject=subject, text=text, html=html, idempotency_key=key)
        if row.provider == MailProvider.MATEMAIL:
            result = providers.send_matemail(row, email)
        else:
            result = providers.send_smtp(row, email)
        if result.outcome != providers.ACCEPTED:
            logger.error("Authentication email not accepted (code=%s)", result.code)
        return result
    except Exception:
        logger.error("Authentication email failed")
        return providers.Result(providers.UNKNOWN, "unexpected_error")


def count_auth_sms(*, body: str, purpose: str, tenant_id: uuid.UUID) -> None:
    """Runs after commit, when the request's tenant context may be gone: the tenant is explicit.
    Records segments only (never the code, message or number). Authentication SMS are exempt
    from the store's notification units (D-091), so this never blocks or charges sign-in."""
    try:
        from commerce.tenancy.context import tenant_context

        from .services import record_auth_sms

        segments, encoding = providers.sms_segments(body)
        with tenant_context(tenant_id):
            record_auth_sms(segments=segments, encoding=encoding, purpose=purpose)
    except Exception:
        logger.warning("SMS usage not recorded (purpose=%s)", purpose)
