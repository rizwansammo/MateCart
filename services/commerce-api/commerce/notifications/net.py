"""Outbound mail connections that cannot be turned against the server (D-086).

- Host names are validated, resolved once and every address must be public (no loopback,
  private, link-local, reserved, multicast or unspecified addresses). The socket then connects to
  that vetted address, so a DNS answer cannot change between the check and the connection (DNS
  rebinding). TLS still verifies the certificate against the original host name.
- Only standard mail ports are allowed.
- Hosts listed in ``MATECART_MAIL_INTERNAL_HOSTS`` (development settings only: the local
  Mailpit) may resolve to private addresses and may skip TLS. Production never sets it.
- Every connection has a short timeout. Errors are reduced to safe codes; server replies,
  credentials and addresses never reach logs or API responses.
"""

from __future__ import annotations

import contextlib
import imaplib
import ipaddress
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass
from typing import Any

from django.conf import settings

TIMEOUT = 15
HOST = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$")
SMTP_PORTS = {25, 465, 587, 2525}
IMAP_PORTS = {143, 993}


class UnsafeHost(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Target:
    host: str
    address: str
    port: int
    internal: bool


def internal_hosts() -> set[str]:
    return {host.lower() for host in getattr(settings, "MATECART_MAIL_INTERNAL_HOSTS", [])}


def resolve(host: str, port: int, *, allowed_ports: set[int]) -> Target:
    name = host.strip().lower().rstrip(".")
    internal = name in internal_hosts()
    invalid = not HOST.match(name) or name == "localhost" or name.endswith(".local")
    if invalid and not internal:
        raise UnsafeHost("host_invalid")
    try:
        ipaddress.ip_address(name)
        raise UnsafeHost("host_must_be_name")
    except ValueError:
        pass
    if port not in allowed_ports and not internal:
        raise UnsafeHost("port_not_allowed")
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeHost("host_not_found") from exc
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not internal and not address.is_global:
            raise UnsafeHost("host_not_public")
        addresses.append(str(address))
    if not addresses:
        raise UnsafeHost("host_not_found")
    return Target(host=name, address=addresses[0], port=port, internal=internal)


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class PinnedSMTP(smtplib.SMTP):
    """smtplib.SMTP connecting to a vetted address while keeping the host name for TLS."""

    def __init__(self, target: Target, **kwargs: Any) -> None:
        self._target = target
        super().__init__(**kwargs)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return socket.create_connection((self._target.address, port), timeout)


class PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, target: Target, **kwargs: Any) -> None:
        self._target = target
        super().__init__(**kwargs)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        raw = socket.create_connection((self._target.address, port), timeout)
        return self.context.wrap_socket(raw, server_hostname=self._target.host)


def open_smtp(target: Target, security: str, username: str, password: str) -> smtplib.SMTP:
    """A logged-in SMTP session. Raises smtplib/socket/ssl errors for the caller to classify."""
    if security == "none" and not target.internal:
        raise UnsafeHost("tls_required")
    if security == "tls":
        client: smtplib.SMTP = PinnedSMTPSSL(target, timeout=TIMEOUT, context=tls_context())
        client.connect(target.host, target.port)
    else:
        client = PinnedSMTP(target, timeout=TIMEOUT)
        client.connect(target.host, target.port)
        client.ehlo()
        if security == "starttls":
            client.starttls(context=tls_context())
            client.ehlo()
    if username:
        client.login(username, password)
    return client


class PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, target: Target) -> None:
        self._target = target
        super().__init__(target.host, target.port, ssl_context=tls_context(), timeout=TIMEOUT)

    def _create_socket(self, timeout: float | None = None) -> socket.socket:
        raw = socket.create_connection((self._target.address, self.port), timeout)
        wrapped: socket.socket = self.ssl_context.wrap_socket(
            raw, server_hostname=self._target.host
        )
        return wrapped


class PinnedIMAP4(imaplib.IMAP4):
    def __init__(self, target: Target) -> None:
        self._target = target
        super().__init__(target.host, target.port, timeout=TIMEOUT)

    def _create_socket(self, timeout: float | None = None) -> socket.socket:
        return socket.create_connection((self._target.address, self.port), timeout)


def verify_imap(target: Target, security: str, username: str, password: str) -> None:
    """Log in, open INBOX read-only and log out. Reads and changes nothing in the mailbox."""
    if security == "none" and not target.internal:
        raise UnsafeHost("tls_required")
    client: imaplib.IMAP4 = PinnedIMAP4SSL(target) if security == "tls" else PinnedIMAP4(target)
    try:
        if security == "starttls":
            client.starttls(ssl_context=tls_context())
        client.login(username, password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error("select failed")
    finally:
        with contextlib.suppress(Exception):  # best effort; outcome already known
            client.logout()


def error_code(exc: BaseException) -> str:
    """A safe, coarse category for an SMTP/IMAP/network failure (never the server's text)."""
    if isinstance(exc, UnsafeHost):
        return exc.code
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "authentication_failed"
    if isinstance(exc, imaplib.IMAP4.error):
        return "authentication_failed"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "certificate_invalid"
    if isinstance(exc, ssl.SSLError):
        return "tls_failed"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "tls_not_supported"
    if isinstance(exc, TimeoutError | socket.timeout):
        return "timeout"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "recipient_refused"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "sender_refused"
    if isinstance(exc, smtplib.SMTPResponseException):
        return "server_rejected_temporary" if 400 <= exc.smtp_code < 500 else "server_rejected"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, OSError | smtplib.SMTPException):
        return "connection_failed"
    return "unexpected_error"
