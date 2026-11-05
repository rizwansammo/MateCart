"""Outbound HTTPS for integrations that cannot be turned against the server (Phase 15, D-103).

Used by the AI provider adapters (GroqCloud), the store migration sources (WooCommerce,
Shopify) and imported product images. Every request:

- goes only to ``https://`` URLs with a host *name* (no IP literals, no user info, no
  ``localhost`` or ``.local``), on port 443 unless a caller allows another;
- resolves the name once and refuses unless every address is public (no loopback, private,
  link-local, reserved, multicast or unspecified address). The socket then connects to that
  vetted address while TLS still verifies the certificate against the real host name, so a DNS
  answer cannot change between the check and the connection (DNS rebinding);
- follows redirects only when the caller allows them, re-validating every hop the same way
  and, when the host changes, dropping credentials (``Authorization`` and caller-named headers);
- has an explicit timeout and a response size limit;
- reports failures as short machine-readable codes. Response bodies of failed requests are kept
  only for the adapter to classify; they are never logged here.

Development-only fakes never reach this module: adapters choose them before any network call.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
MAX_REDIRECTS = 5
_HOST = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)
USER_AGENT = "MateCart/1.0 (+https://netamate.com)"


class OutboundError(Exception):
    """A request that was refused before sending or failed in transport. ``code`` is safe to
    show and store; it never contains the URL, credentials or response text."""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    path: str
    address: str = ""


@dataclass
class Policy:
    """What a caller allows beyond the defaults."""

    ports: frozenset[int] = frozenset({443})
    max_redirects: int = 0
    # Optional host check applied to the first URL and every redirect target.
    host_allowed: Callable[[str], bool] | None = None
    # Header names (lower case) removed when a redirect changes host.
    sensitive_headers: frozenset[str] = field(
        default_factory=lambda: frozenset({"authorization", "x-shopify-access-token"})
    )


def parse_url(url: str, policy: Policy | None = None) -> Target:
    """The validated parts of ``url`` (not yet resolved)."""
    rules = policy or Policy()
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise OutboundError("url_invalid") from None
    if parts.scheme != "https":
        raise OutboundError("https_required")
    if parts.username or parts.password:
        raise OutboundError("url_invalid")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise OutboundError("url_invalid")
    try:
        ipaddress.ip_address(host)
        raise OutboundError("host_must_be_name")
    except ValueError:
        pass
    if not _HOST.match(host) or host == "localhost" or host.endswith((".local", ".localhost")):
        raise OutboundError("host_invalid")
    if host.endswith((".internal", ".lan", ".home", ".corp", ".intranet")):
        raise OutboundError("host_not_public")
    effective_port = port or 443
    if effective_port not in rules.ports:
        raise OutboundError("port_not_allowed")
    if rules.host_allowed is not None and not rules.host_allowed(host):
        raise OutboundError("host_not_allowed")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Target(scheme="https", host=host, port=effective_port, path=path)


def resolve(target: Target) -> Target:
    """``target`` pinned to one vetted public address."""
    try:
        infos = socket.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
    except OSError:
        raise OutboundError("host_not_found") from None
    addresses = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise OutboundError("host_not_public") from None
        if not address.is_global:
            raise OutboundError("host_not_public")
        addresses.append(str(address))
    if not addresses:
        raise OutboundError("host_not_found")
    return Target(
        scheme=target.scheme,
        host=target.host,
        port=target.port,
        path=target.path,
        address=addresses[0],
    )


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to the vetted address; TLS verifies the real host name (SNI + certificate)."""

    def __init__(self, target: Target, timeout: float) -> None:
        super().__init__(target.host, target.port, timeout=timeout, context=tls_context())
        self._address = target.address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = tls_context().wrap_socket(raw, server_hostname=self.host)


def _send(
    target: Target,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
) -> Response:
    connection = _PinnedHTTPSConnection(target, timeout)
    try:
        connection.request(method, target.path, body=body, headers=dict(headers))
        raw = connection.getresponse()
        data = raw.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OutboundError("response_too_large", status=raw.status)
        return Response(
            status=raw.status,
            headers={key.lower(): value for key, value in raw.getheaders()},
            body=data,
            url=f"https://{target.host}{target.path}",
        )
    except OutboundError:
        raise
    except ssl.SSLCertVerificationError:
        raise OutboundError("certificate_invalid") from None
    except ssl.SSLError:
        raise OutboundError("tls_failed") from None
    except TimeoutError:
        raise OutboundError("timeout") from None
    except ConnectionRefusedError:
        raise OutboundError("connection_refused") from None
    except (OSError, http.client.HTTPException):
        raise OutboundError("network_error") from None
    finally:
        connection.close()


# Tests replace this seam (``monkeypatch.setattr(outbound, "transport", fake)``); a fake receives
# the already validated target and never opens a socket.
transport: Callable[[Target, str, Mapping[str, str], bytes | None, float, int], Response] = _send


def request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    policy: Policy | None = None,
) -> Response:
    """Send one request under ``policy``. Redirects are followed only when allowed."""
    rules = policy or Policy()
    current = parse_url(url, rules)
    sent_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    current_method, current_body = method.upper(), body
    for hop in range(rules.max_redirects + 1):
        response = transport(
            resolve(current), current_method, sent_headers, current_body, timeout, max_bytes
        )
        if response.status not in (301, 302, 303, 307, 308):
            return response
        if hop >= rules.max_redirects:
            raise OutboundError("redirect_refused", status=response.status)
        location = response.header("location")
        if not location:
            raise OutboundError("redirect_invalid", status=response.status)
        absolute = urllib.parse.urljoin(f"https://{current.host}{current.path}", location)
        following = parse_url(absolute, rules)
        if following.host != current.host:
            sent_headers = {
                key: value
                for key, value in sent_headers.items()
                if key.lower() not in rules.sensitive_headers
            }
        if response.status == 303 or (response.status in (301, 302) and current_method != "GET"):
            current_method, current_body = "GET", None
        current = following
    raise OutboundError("redirect_refused")  # pragma: no cover - loop always returns or raises
