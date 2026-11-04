"""Email connection management (D-086).

- **SMTP.** Saving stores the password encrypted and leaves the connection ``unverified``.
  "Test connection" logs in. "Verify sender" emails a six-digit code *to the From address* through
  that same server; entering it proves the merchant controls that mailbox, so a store can never
  send as an address it does not own. Only a tested, sender-verified connection is ``connected``.
- **IMAP.** Credentials are stored encrypted and verified by a read-only login (INBOX opened
  read-only, nothing read, moved or deleted). Inbound processing is out of scope (D-086).
- **MateMail.** The merchant creates a Connected App in MateMail, scoped to one mailbox with
  "Send email", and pastes the Tenant ID and one-time Integration Secret. MateCart starts the
  documented connect flow, the merchant approves in MateMail, MateCart collects the revocable
  access token once and confirms ``mail.send``. The secret is used once and never stored. The
  From address is always the approved mailbox (enforced by MateMail).
- At most one connection is enabled; an enabled, connected connection is the store's only email
  transport. Changing credentials always returns a connection to ``unverified``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import record_audit_event
from commerce.core import ratelimit, security
from commerce.core.domain import ConflictError
from commerce.identity.models import User

from . import net, providers
from .models import ConnectionStatus, MailConnection, MailProvider, SmtpSecurity

SENDER_CODE_TTL = timedelta(minutes=15)
TESTS_PER_HOUR = 10


def connection(provider: str) -> MailConnection:
    found = MailConnection.objects.filter(provider=provider).first()
    if found is not None:
        return found
    with transaction.atomic():
        MailConnection.objects.get_or_create(provider=provider)
    return MailConnection.objects.get(provider=provider)


def active() -> MailConnection | None:
    """The store's email transport, when one is enabled. Disabled = deployment default."""
    return MailConnection.objects.filter(is_enabled=True).first()


def _audit(row: MailConnection, action: str, actor: User, **changes: Any) -> None:
    record_audit_event(
        action=f"notifications.connection.{action}",
        resource=row,
        actor=actor,
        changes={"provider": row.provider, "status": row.status, **changes},
    )


def _locked(provider: str) -> MailConnection:
    return MailConnection.objects.select_for_update().get(pk=connection(provider).pk)


def _check_address(value: str, field: str) -> str:
    text = value.strip().lower()
    if not text:
        return ""
    if any(ch in text for ch in "\r\n,;<>\"'"):
        raise ValidationError({field: ["Enter a single email address."]})
    try:
        validate_email(text)
    except Exception as exc:
        raise ValidationError({field: ["Enter a valid email address."]}) from exc
    return text


def save_smtp(*, data: Mapping[str, Any], actor: User) -> MailConnection:
    with transaction.atomic():
        row = _locked(MailProvider.SMTP)
        changed: list[str] = []
        for field in ("smtp_host", "smtp_port", "smtp_security", "smtp_username", "display_name"):
            if field in data and data[field] != getattr(row, field):
                setattr(row, field, data[field])
                changed.append(field)
        for field in ("from_email", "reply_to"):
            if field in data:
                value = _check_address(str(data[field]), field)
                if value != getattr(row, field):
                    setattr(row, field, value)
                    changed.append(field)
        if "from_name" in data:
            name = net_safe_name(str(data["from_name"]))
            if name != row.from_name:
                row.from_name = name
                changed.append("from_name")
        if data.get("smtp_password"):
            row.smtp_password_encrypted = security.encrypt(str(data["smtp_password"]))
            changed.append("smtp_password")
        row.smtp_host = row.smtp_host.strip().lower()
        if row.smtp_security not in SmtpSecurity.values:
            raise ValidationError({"smtp_security": ["Choose STARTTLS or TLS."]})
        if row.smtp_host:
            try:
                net.resolve(row.smtp_host, row.smtp_port or 0, allowed_ports=net.SMTP_PORTS)
            except net.UnsafeHost as exc:
                raise ValidationError(
                    {"smtp_host": [HOST_MESSAGES.get(exc.code, "Not allowed.")]}
                ) from None
            if row.smtp_security == SmtpSecurity.NONE and row.smtp_host not in net.internal_hosts():
                raise ValidationError({"smtp_security": ["Use STARTTLS or TLS."]})
        if not row.from_email or not row.smtp_host:
            raise ValidationError({"from_email": ["Enter the server and the From address."]})
        if {"smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password"} & set(
            changed
        ) or "from_email" in changed:
            row.status = ConnectionStatus.UNVERIFIED
            row.last_verified_at = None
            if "from_email" in changed:
                row.sender_verified_at = None
        elif row.status == ConnectionStatus.NOT_CONFIGURED:
            row.status = ConnectionStatus.UNVERIFIED
        row.save()
        # Field names only: values (and the password) are never written to the audit log.
        _audit(row, "updated", actor, fields=changed)
        return row


def net_safe_name(value: str) -> str:
    return " ".join("".join(ch for ch in value if ch.isprintable() and ch not in '<>"').split())[
        :80
    ]


HOST_MESSAGES = {
    "host_invalid": "Enter a mail server host name.",
    "host_must_be_name": "Enter the server's host name, not an IP address.",
    "port_not_allowed": "Use a standard mail port.",
    "host_not_found": "This host name could not be found.",
    "host_not_public": "This server is on a private network and cannot be used.",
}


def _limit(actor: User, scope: str) -> None:
    ratelimit.consume(f"mail-{scope}", str(actor.pk), limit=TESTS_PER_HOUR, window=3600)


def test_smtp(*, actor: User, send_to_self: bool) -> MailConnection:
    """Log in (and optionally send a test message to the signed-in staff member's own verified
    address only: this endpoint is never a relay)."""
    _limit(actor, "test")
    row = connection(MailProvider.SMTP)
    if not row.smtp_host:
        raise ConflictError("Save the SMTP settings first.", "not_configured")
    result: providers.Result
    if send_to_self:
        if not actor.email or actor.email_verified_at is None:
            raise ConflictError("Your account needs a verified email address.", "no_email")
        result = providers.send_smtp(
            row,
            providers.Email(
                to=actor.email,
                subject="MateCart test message",
                text="This is a test message from your store's email settings.\n",
                html="",
                idempotency_key="",
            ),
        )
        ok = result.outcome == providers.ACCEPTED
        code = result.code
    else:
        code = _login_check(row)
        ok = not code
    with transaction.atomic():
        locked = _locked(MailProvider.SMTP)
        locked.last_error = "" if ok else code
        if ok:
            locked.last_verified_at = timezone.now()
            if locked.sender_verified_at is not None:
                locked.status = ConnectionStatus.CONNECTED
        else:
            locked.status = ConnectionStatus.FAILED
        locked.save()
        _audit(locked, "tested", actor, ok=ok, error=code)
        return locked


def _login_check(row: MailConnection) -> str:
    try:
        target = net.resolve(row.smtp_host, row.smtp_port or 0, allowed_ports=net.SMTP_PORTS)
        password = (
            security.decrypt(row.smtp_password_encrypted) if row.smtp_password_encrypted else ""
        )
        client = net.open_smtp(target, row.smtp_security, row.smtp_username, password)
        with contextlib.suppress(Exception):  # best effort; outcome already known
            client.quit()
    except Exception as exc:
        return net.error_code(exc)
    return ""


def send_sender_code(*, actor: User) -> MailConnection:
    _limit(actor, "sender")
    row = connection(MailProvider.SMTP)
    if not row.smtp_host or not row.from_email:
        raise ConflictError("Save the SMTP settings first.", "not_configured")
    code = security.new_numeric_code()
    result = providers.send_smtp(
        row,
        providers.Email(
            to=row.from_email,
            subject="Confirm this sender address",
            text=(
                f"Your MateCart sender confirmation code is {code}.\n\n"
                "Enter it in the store's email settings within 15 minutes. If you did not "
                "request this, ignore this message.\n"
            ),
            html="",
            idempotency_key="",
        ),
    )
    with transaction.atomic():
        locked = _locked(MailProvider.SMTP)
        if result.outcome != providers.ACCEPTED:
            locked.last_error = result.code or "send_failed"
            locked.status = ConnectionStatus.FAILED
            locked.save()
            raise ConflictError(
                "The confirmation code could not be sent through this server.", "send_failed"
            )
        locked.sender_code_hash = security.keyed_hash("mail-sender", code)
        locked.sender_code_expires_at = timezone.now() + SENDER_CODE_TTL
        locked.save()
        _audit(locked, "sender_code_sent", actor)
        return locked


def confirm_sender(*, code: str, actor: User) -> MailConnection:
    ratelimit.consume("mail-sender-confirm", str(actor.pk), limit=10, window=3600)
    with transaction.atomic():
        row = _locked(MailProvider.SMTP)
        valid = (
            row.sender_code_hash
            and row.sender_code_expires_at is not None
            and row.sender_code_expires_at > timezone.now()
            and security.matches(row.sender_code_hash, "mail-sender", code.strip())
        )
        if not valid:
            raise ValidationError({"code": ["This code is not valid or has expired."]})
        row.sender_code_hash = ""
        row.sender_code_expires_at = None
        row.sender_verified_at = timezone.now()
        if row.last_verified_at is not None and not row.last_error:
            row.status = ConnectionStatus.CONNECTED
        row.save()
        _audit(row, "sender_verified", actor)
        return row


def save_imap(*, data: Mapping[str, Any], actor: User) -> MailConnection:
    with transaction.atomic():
        row = _locked(MailProvider.SMTP)
        changed = []
        for field in ("imap_host", "imap_port", "imap_security", "imap_username"):
            if field in data and data[field] != getattr(row, field):
                setattr(row, field, data[field])
                changed.append(field)
        if data.get("imap_password"):
            row.imap_password_encrypted = security.encrypt(str(data["imap_password"]))
            changed.append("imap_password")
        row.imap_host = row.imap_host.strip().lower()
        if row.imap_host:
            try:
                net.resolve(row.imap_host, row.imap_port or 0, allowed_ports=net.IMAP_PORTS)
            except net.UnsafeHost as exc:
                raise ValidationError(
                    {"imap_host": [HOST_MESSAGES.get(exc.code, "Not allowed.")]}
                ) from None
            if row.imap_security == SmtpSecurity.NONE and row.imap_host not in net.internal_hosts():
                raise ValidationError({"imap_security": ["Use STARTTLS or TLS."]})
        if changed:
            row.imap_status = (
                ConnectionStatus.UNVERIFIED if row.imap_host else ConnectionStatus.NOT_CONFIGURED
            )
            row.imap_verified_at = None
        row.save()
        _audit(row, "imap_updated", actor, fields=changed)
        return row


def verify_imap(*, actor: User) -> MailConnection:
    _limit(actor, "imap")
    row = connection(MailProvider.SMTP)
    if not row.imap_host:
        raise ConflictError("Save the IMAP settings first.", "not_configured")
    code = ""
    try:
        target = net.resolve(row.imap_host, row.imap_port or 0, allowed_ports=net.IMAP_PORTS)
        password = (
            security.decrypt(row.imap_password_encrypted) if row.imap_password_encrypted else ""
        )
        net.verify_imap(target, row.imap_security, row.imap_username, password)
    except Exception as exc:
        code = net.error_code(exc)
    with transaction.atomic():
        locked = _locked(MailProvider.SMTP)
        locked.imap_status = ConnectionStatus.FAILED if code else ConnectionStatus.CONNECTED
        locked.imap_error = code
        locked.imap_verified_at = None if code else timezone.now()
        locked.save()
        _audit(locked, "imap_verified", actor, ok=not code, error=code)
        return locked


# --- MateMail ------------------------------------------------------------------------------------


def matemail_start(*, tenant_id: str, secret: str, actor: User) -> MailConnection:
    if not providers.matemail_available():
        raise ConflictError(
            "MateMail is not available for this store yet. NetaMate sets its address.",
            "matemail_unavailable",
        )
    _limit(actor, "matemail")
    try:
        payload = providers.matemail_api().post(
            "/api/integrations/connect/start/",
            {"tenant_id": tenant_id, "integration_secret": secret},
        )
    except providers.MateMailError as exc:
        message = {
            401: "MateMail did not accept this Tenant ID and Integration Secret.",
            409: "The MateMail mailbox for this connection is not available.",
        }.get(exc.status, "MateMail could not be reached. Try again later.")
        raise ConflictError(message, "matemail_rejected") from None
    url = str(payload.get("authorization_url") or "")
    poll = str(payload.get("poll_token") or "")
    if not url.startswith("https://") or not poll:
        raise ConflictError("MateMail returned an unexpected answer.", "matemail_unexpected")
    with transaction.atomic():
        row = _locked(MailProvider.MATEMAIL)
        row.matemail_tenant_id = tenant_id
        row.matemail_poll_token_encrypted = security.encrypt(poll)
        row.matemail_authorization_url = url[:500]
        row.matemail_request_expires_at = timezone.now() + timedelta(minutes=10)
        row.matemail_mailbox = str(payload.get("mailbox_email") or "")[:254]
        row.status = ConnectionStatus.AWAITING_APPROVAL
        row.last_error = ""
        row.save()
        _audit(row, "matemail_started", actor)
        return row


def matemail_check(*, actor: User) -> MailConnection:
    row = connection(MailProvider.MATEMAIL)
    if row.status != ConnectionStatus.AWAITING_APPROVAL or not row.matemail_poll_token_encrypted:
        raise ConflictError("Start the MateMail connection first.", "not_started")
    try:
        payload = providers.matemail_api().post(
            "/api/integrations/connect/status/",
            {
                "tenant_id": row.matemail_tenant_id,
                "poll_token": security.decrypt(row.matemail_poll_token_encrypted),
            },
        )
    except providers.MateMailError:
        raise ConflictError("MateMail could not be reached. Try again.", "matemail_down") from None
    state = str(payload.get("status") or "")
    with transaction.atomic():
        locked = _locked(MailProvider.MATEMAIL)
        if state == "pending":
            return locked
        if state in {"expired", "rejected"} or not payload.get("access_token"):
            locked.status = ConnectionStatus.FAILED
            locked.last_error = f"approval_{state or 'missing'}"[:60]
            locked.matemail_poll_token_encrypted = ""
            locked.save()
            _audit(locked, "matemail_not_approved", actor, result=state)
            return locked
        scopes = [str(item.get("key")) for item in payload.get("permissions") or []]
        previous = locked.matemail_access_token_encrypted
        locked.matemail_access_token_encrypted = security.encrypt(str(payload["access_token"]))
        locked.matemail_poll_token_encrypted = ""
        locked.matemail_integration_id = str(payload.get("id") or "")[:36]
        locked.matemail_mailbox = str(payload.get("mailbox_email") or "")[:254]
        locked.matemail_scopes = ",".join(scopes)[:200]
        if "mail.send" in scopes:
            locked.status = ConnectionStatus.CONNECTED
            locked.last_verified_at = timezone.now()
            locked.last_error = ""
        else:
            locked.status = ConnectionStatus.FAILED
            locked.last_error = "send_not_permitted"
        locked.save()
        _audit(locked, "matemail_connected", actor, scopes=scopes)
    if previous:
        _disconnect_token(previous, row.matemail_tenant_id)
    return locked


def matemail_verify(*, actor: User) -> MailConnection:
    """Health check: the token still works and still has ``mail.send``."""
    _limit(actor, "matemail")
    row = connection(MailProvider.MATEMAIL)
    if not row.matemail_access_token_encrypted:
        raise ConflictError("Connect MateMail first.", "not_connected")
    code = ""
    try:
        profile = providers.matemail_api().get(
            "/api/integrations/external/profile/",
            token=security.decrypt(row.matemail_access_token_encrypted),
            tenant=row.matemail_tenant_id,
        )
        scopes = [str(item.get("key")) for item in profile.get("permissions") or []]
        if "mail.send" not in scopes:
            code = "send_not_permitted"
    except providers.MateMailError as exc:
        code = "connection_revoked" if exc.status == 401 else exc.code
    with transaction.atomic():
        locked = _locked(MailProvider.MATEMAIL)
        if code == "connection_revoked":
            locked.status = ConnectionStatus.REVOKED
        elif code:
            locked.status = ConnectionStatus.FAILED
        else:
            locked.status = ConnectionStatus.CONNECTED
            locked.last_verified_at = timezone.now()
        locked.last_error = code
        locked.save()
        _audit(locked, "matemail_verified", actor, ok=not code, error=code)
        return locked


def _disconnect_token(encrypted: str, tenant: str) -> None:
    with contextlib.suppress(Exception):  # best effort; outcome already known
        providers.matemail_api().post(
            "/api/integrations/external/disconnect/",
            {},
            token=security.decrypt(encrypted),
            tenant=tenant,
        )


def matemail_disconnect(*, actor: User) -> MailConnection:
    with transaction.atomic():
        row = _locked(MailProvider.MATEMAIL)
        token, tenant = row.matemail_access_token_encrypted, row.matemail_tenant_id
        row.matemail_access_token_encrypted = ""
        row.matemail_poll_token_encrypted = ""
        row.matemail_authorization_url = ""
        row.matemail_scopes = ""
        row.is_enabled = False
        row.status = ConnectionStatus.NOT_CONFIGURED
        row.save()
        _audit(row, "matemail_disconnected", actor)
    if token:
        _disconnect_token(token, tenant)
    return row


def mark_revoked(row: MailConnection) -> None:
    MailConnection.objects.filter(pk=row.pk).update(
        status=ConnectionStatus.REVOKED, last_error="connection_revoked", updated_at=timezone.now()
    )


# --- Transport selection -------------------------------------------------------------------------


def select(*, provider: str | None, actor: User) -> MailConnection | None:
    """Enable one connected provider as the store's email transport, or none (``None``: the
    deployment's default sender)."""
    with transaction.atomic():
        rows = list(MailConnection.objects.select_for_update().order_by("pk"))
        target = None
        if provider is not None:
            target = next((row for row in rows if row.provider == provider), None)
            if target is None or target.status != ConnectionStatus.CONNECTED:
                raise ConflictError("Only a connected provider can be used.", "not_connected")
        for row in rows:
            if row.is_enabled and row is not target:
                row.is_enabled = False
                row.save(update_fields=["is_enabled", "updated_at"])
        if target is not None and not target.is_enabled:
            target.is_enabled = True
            target.save(update_fields=["is_enabled", "updated_at"])
        record_audit_event(
            action="notifications.connection.selected",
            resource=target if target is not None else rows[0] if rows else _placeholder(),
            actor=actor,
            changes={"provider": provider or "default"},
        )
        return target


def _placeholder() -> MailConnection:
    return connection(MailProvider.SMTP)


def public_state(row: MailConnection) -> dict[str, Any]:
    """What the dashboard may see: never a password, token or secret, only whether one is set."""
    return {
        "provider": row.provider,
        "is_enabled": row.is_enabled,
        "status": row.status,
        "display_name": row.display_name,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_security": row.smtp_security,
        "smtp_username": row.smtp_username,
        "smtp_password_set": bool(row.smtp_password_encrypted),
        "from_email": row.from_email,
        "from_name": row.from_name,
        "reply_to": row.reply_to,
        "sender_verified": row.sender_verified_at is not None,
        "sender_code_pending": bool(
            row.sender_code_hash
            and row.sender_code_expires_at
            and row.sender_code_expires_at > timezone.now()
        ),
        "imap_host": row.imap_host,
        "imap_port": row.imap_port,
        "imap_security": row.imap_security,
        "imap_username": row.imap_username,
        "imap_password_set": bool(row.imap_password_encrypted),
        "imap_status": row.imap_status,
        "imap_verified_at": row.imap_verified_at,
        "imap_error": row.imap_error,
        "matemail_tenant_id": row.matemail_tenant_id,
        "matemail_authorization_url": row.matemail_authorization_url
        if row.status == ConnectionStatus.AWAITING_APPROVAL
        else "",
        "matemail_request_expires_at": row.matemail_request_expires_at,
        "matemail_mailbox": row.matemail_mailbox,
        "matemail_scopes": [s for s in row.matemail_scopes.split(",") if s],
        "matemail_token_set": bool(row.matemail_access_token_encrypted),
        "last_verified_at": row.last_verified_at,
        "last_error": row.last_error,
    }
