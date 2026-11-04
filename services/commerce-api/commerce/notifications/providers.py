"""Email and SMS provider adapters (D-086, D-089). Provider details stay in this module.

Outcomes are classified so the dispatcher never sends twice:

- ``accepted``: the provider took responsibility (SMTP 250 after DATA, MateMail ``sent``). This is
  not proof of inbox delivery.
- ``retryable``: nothing was handed over (connection, TLS, authentication, 4xx before DATA,
  timeouts before DATA, MateMail 429/5xx).
- ``permanent``: the provider refused for good (5xx, refused recipient or sender, revoked
  credentials, missing permission).
- ``unknown``: the message may have been handed over but no answer arrived (failure after DATA,
  MateMail 409 for an unresolved earlier attempt). Never retried automatically.
"""

from __future__ import annotations

import contextlib
import json
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.utils import formataddr, make_msgid
from typing import Any, Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

from commerce.core import security

from . import net
from .models import MailConnection, MailProvider

ACCEPTED, RETRYABLE, PERMANENT, UNKNOWN = "accepted", "retryable", "permanent", "unknown"
GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENDED = set("^{}\\[~]|€\f")


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str
    idempotency_key: str


@dataclass(frozen=True)
class Result:
    outcome: str
    code: str = ""
    reference: str = ""
    provider: str = ""


# --- SMTP and the deployment default --------------------------------------------------------------


def _message(email: Email, sender: str, reply_to: str) -> EmailMultiAlternatives:
    message = EmailMultiAlternatives(
        subject=email.subject,
        body=email.text,
        from_email=sender,
        to=[email.to],
        reply_to=[reply_to] if reply_to else None,
        headers={
            "Message-ID": make_msgid(domain="matecart.local"),
            "Auto-Submitted": "auto-generated",
            "X-Auto-Response-Suppress": "All",
        },
    )
    if email.html:
        message.attach_alternative(email.html, "text/html")
    return message


def send_smtp(connection: MailConnection, email: Email) -> Result:
    sender = formataddr((connection.from_name, connection.from_email))
    try:
        target = net.resolve(
            connection.smtp_host, connection.smtp_port or 0, allowed_ports=net.SMTP_PORTS
        )
        password = (
            security.decrypt(connection.smtp_password_encrypted)
            if connection.smtp_password_encrypted
            else ""
        )
        client = net.open_smtp(target, connection.smtp_security, connection.smtp_username, password)
    except Exception as exc:
        code = net.error_code(exc)
        permanent = code in {"authentication_failed", "host_not_public", "tls_required"}
        return Result(PERMANENT if permanent else RETRYABLE, code, provider=MailProvider.SMTP)
    message = _message(email, sender, connection.reply_to).message()
    stage = "envelope"
    try:
        reply, _ = client.mail(connection.from_email)
        if reply >= 400:
            return Result(
                RETRYABLE if reply < 500 else PERMANENT,
                "sender_refused",
                provider=MailProvider.SMTP,
            )
        reply, _ = client.rcpt(email.to)
        if reply >= 400:
            return Result(
                RETRYABLE if reply < 500 else PERMANENT,
                "recipient_refused",
                provider=MailProvider.SMTP,
            )
        stage = "data"
        reply, _ = client.data(message.as_bytes())
        if reply != 250:
            return Result(
                RETRYABLE if 400 <= reply < 500 else PERMANENT,
                "server_rejected",
                provider=MailProvider.SMTP,
            )
        return Result(ACCEPTED, reference=str(message["Message-ID"]), provider=MailProvider.SMTP)
    except Exception as exc:
        if stage == "data" and not isinstance(exc, smtplib.SMTPResponseException):
            # The message body may have reached the server; resending could duplicate it.
            return Result(UNKNOWN, "outcome_unknown", provider=MailProvider.SMTP)
        code = net.error_code(exc)
        return Result(
            PERMANENT
            if code in {"recipient_refused", "sender_refused", "server_rejected"}
            else RETRYABLE,
            code,
            provider=MailProvider.SMTP,
        )
    finally:
        with contextlib.suppress(Exception):  # best effort; outcome already known
            client.quit()


def default_sender_available() -> bool:
    return not str(settings.EMAIL_BACKEND).endswith("dummy.EmailBackend")


def send_default(email: Email, store_name: str) -> Result:
    """The deployment's own mail service (``EMAIL_URL``; Mailpit locally)."""
    if not default_sender_available():
        return Result(PERMANENT, "no_email_transport", provider="default")
    _, address = _split(settings.DEFAULT_FROM_EMAIL)
    sender = formataddr((store_name[:60], address))
    message = _message(email, sender, "")
    try:
        message.connection = get_connection(timeout=net.TIMEOUT)
        message.send()
    except Exception as exc:
        code = net.error_code(exc)
        return Result(
            UNKNOWN if code == "timeout" else RETRYABLE, code or "send_failed", provider="default"
        )
    return Result(ACCEPTED, reference=str(message.extra_headers["Message-ID"]), provider="default")


def _split(value: str) -> tuple[str, str]:
    from email.utils import parseaddr

    return parseaddr(value)


# --- MateMail Connected App ----------------------------------------------------------------------


class MateMailError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class MateMailApi(Protocol):
    def post(
        self, path: str, body: dict[str, Any], *, token: str = "", tenant: str = ""
    ) -> dict[str, Any]: ...

    def get(self, path: str, *, token: str, tenant: str) -> dict[str, Any]: ...


@dataclass
class HttpMateMail:
    """MateMail's documented Connected App API (docs/CONNECTED_APPS.md in the MateMail repo). The
    base URL is a deployment setting, never merchant input."""

    base_url: str
    timeout: float = 15.0

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None, token: str, tenant: str
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-MateMail-Tenant"] = tenant
        # The base URL is a deployment setting (HTTPS, set by NetaMate), never request input.
        request = urllib.request.Request(  # noqa: S310
            f"{self.base_url.rstrip('/')}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                payload: dict[str, Any] = json.loads(response.read() or b"{}")
                return payload
        except urllib.error.HTTPError as exc:
            raise MateMailError(exc.code, f"http_{exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise MateMailError(0, "network") from None

    def post(
        self, path: str, body: dict[str, Any], *, token: str = "", tenant: str = ""
    ) -> dict[str, Any]:
        return self._request("POST", path, body, token, tenant)

    def get(self, path: str, *, token: str, tenant: str) -> dict[str, Any]:
        return self._request("GET", path, None, token, tenant)


def matemail_available() -> bool:
    return str(getattr(settings, "MATEMAIL_BASE_URL", "")).startswith("https://")


def matemail_api() -> MateMailApi:
    """The live adapter. Tests replace this function; they never reach MateMail."""
    return HttpMateMail(settings.MATEMAIL_BASE_URL)


def send_matemail(connection: MailConnection, email: Email) -> Result:
    if not matemail_available() or not connection.matemail_access_token_encrypted:
        return Result(PERMANENT, "matemail_unavailable", provider=MailProvider.MATEMAIL)
    token = security.decrypt(connection.matemail_access_token_encrypted)
    try:
        payload = matemail_api().post(
            "/api/integrations/external/send/",
            {
                "to": [email.to],
                "subject": email.subject,
                "text": email.text,
                "html": email.html,
                "idempotency_key": email.idempotency_key,
            },
            token=token,
            tenant=connection.matemail_tenant_id,
        )
    except MateMailError as exc:
        if exc.status == 401:
            return Result(PERMANENT, "connection_revoked", provider=MailProvider.MATEMAIL)
        if exc.status == 403:
            return Result(PERMANENT, "send_not_permitted", provider=MailProvider.MATEMAIL)
        if exc.status == 409:
            # An earlier attempt with this key is unresolved: MateMail will not send it again.
            return Result(UNKNOWN, "outcome_unknown", provider=MailProvider.MATEMAIL)
        if exc.status == 429 or exc.status >= 500 or exc.status == 0:
            return Result(RETRYABLE, exc.code, provider=MailProvider.MATEMAIL)
        return Result(PERMANENT, exc.code, provider=MailProvider.MATEMAIL)
    if payload.get("sent") is True:
        return Result(
            ACCEPTED,
            reference=str(payload.get("message_id") or "")[:200],
            provider=MailProvider.MATEMAIL,
        )
    return Result(UNKNOWN, "unexpected_response", provider=MailProvider.MATEMAIL)


# --- SMS -----------------------------------------------------------------------------------------


def sms_segments(text: str) -> tuple[int, str]:
    """Billable segments: GSM-7 160 (153 per part), otherwise UCS-2 70 (67 per part)."""
    if all(ch in GSM7 or ch in GSM7_EXTENDED for ch in text):
        units = sum(2 if ch in GSM7_EXTENDED else 1 for ch in text)
        return (1 if units <= 160 else -(-units // 153)), "gsm7"
    units = sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)
    return (1 if units <= 70 else -(-units // 67)), "ucs2"
