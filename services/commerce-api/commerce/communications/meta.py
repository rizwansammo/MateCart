"""Meta platform adapter: Messenger, Instagram messaging and the WhatsApp Cloud API (D-094).

Only Meta's documented, authorised APIs are used: the Graph API at ``graph.facebook.com`` with
the version in ``META_GRAPH_API_VERSION``, webhooks signed with the app secret
(``X-Hub-Signature-256``), Page tokens for Messenger and Instagram, and a business token for
WhatsApp. Tokens travel in the ``Authorization`` header, never in a URL that could be logged.
Provider error bodies are classified and dropped; they are never logged or shown.

Errors are classified so callers can be honest about outcomes:

- ``not_sent``: the request never reached Meta (DNS, refused connection): safe to retry.
- ``unknown``: it may have reached Meta (timeout, dropped connection, 5xx): never resent
  automatically, because Meta's send APIs have no idempotency key.
- the others are definite refusals (revoked, permission, window, rate limit, invalid recipient).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from django.conf import settings

from commerce.core import security

from .models import ChannelConnection, ChannelType

GRAPH_HOST = "graph.facebook.com"
TIMEOUT = 15.0
MAX_RESPONSE = 2 * 1024 * 1024
# Hosts Meta serves message media from. Downloads go only to these, over HTTPS, to public IPs.
MEDIA_HOST_SUFFIXES = (
    ".fbcdn.net",
    ".fbsbx.com",
    ".facebook.com",
    ".cdninstagram.com",
    ".whatsapp.net",
)


class GraphError(Exception):
    def __init__(self, kind: str, code: str = "") -> None:
        super().__init__(kind)
        self.kind = kind
        self.code = code or kind

    @property
    def retryable(self) -> bool:
        return self.kind in ("not_sent", "rate_limited")


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _classify(status: int, payload: Mapping[str, Any]) -> GraphError:
    error = _map(payload.get("error"))
    code = int(error.get("code") or 0)
    sub = int(error.get("error_subcode") or 0)
    label = f"meta_{code}" + (f"_{sub}" if sub else "")
    if code == 190 or sub in (458, 460, 463, 467):
        return GraphError("revoked", label)
    if code in (4, 17, 32, 613, 80007, 130429, 131048, 131056) or status == 429:
        return GraphError("rate_limited", label)
    if (code == 10 and sub in (2018278, 2018065)) or code in (131047, 2018278, 2534022):
        return GraphError("window", label)
    if code in (368, 131031):
        return GraphError("suspended", label)
    if code in (10, 200, 3, 294) or 200 <= code < 300:
        return GraphError("permission", label)
    if code in (100, 131026, 131021, 131009, 551, 2018001, 131051, 131052, 131053, 132000):
        return GraphError("rejected", label)
    if status >= 500:
        return GraphError("unknown", f"http_{status}")
    return GraphError("rejected", label or f"http_{status}")


class GraphClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        form: bool = False,
    ) -> dict[str, Any]: ...


@dataclass
class LiveGraphClient:
    version: str
    timeout: float = TIMEOUT

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        form: bool = False,
    ) -> dict[str, Any]:
        url = f"https://{GRAPH_HOST}/{self.version}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data: bytes | None = None
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
        # Fixed https://graph.facebook.com origin; only the path and query vary.
        request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE)
                return _json(raw)
        except urllib.error.HTTPError as exc:
            try:
                payload = _json(exc.read(64 * 1024))
            except (OSError, ValueError):
                payload = {}
            raise _classify(exc.code, payload) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.gaierror | ConnectionRefusedError):
                raise GraphError("not_sent", "network") from None
            raise GraphError("unknown", "network") from None
        except (TimeoutError, OSError, http.client.HTTPException):
            raise GraphError("unknown", "timeout") from None


def _json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {"data": value}


@dataclass
class SimulatedGraphClient:
    """Development simulator (D-097): answers locally, never opens a network connection."""

    sent: list[dict[str, Any]] = field(default_factory=list)

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        form: bool = False,
    ) -> dict[str, Any]:
        from commerce.core.ids import uuid7

        self.sent.append({"method": method, "path": path})
        mid = f"sim.{uuid7().hex}"
        if path.endswith("/messages"):
            if body and body.get("messaging_product") == "whatsapp":
                return {"messages": [{"id": f"wamid.{mid}"}]}
            return {"message_id": f"m_{mid}", "recipient_id": "simulated"}
        return {"success": True}


def graph_client(connection: ChannelConnection | None = None) -> GraphClient:
    """The adapter for a connection. Tests replace this function; they never reach Meta."""
    if connection is not None and connection.is_simulated:
        return SimulatedGraphClient()
    return LiveGraphClient(version=settings.META_GRAPH_API_VERSION)


def connection_token(connection: ChannelConnection) -> str:
    if not connection.access_token_encrypted:
        raise GraphError("revoked", "token_missing")
    try:
        return security.decrypt(connection.access_token_encrypted)
    except security.DecryptionError:
        raise GraphError("revoked", "token_unreadable") from None


# --- Sending --------------------------------------------------------------------------------------


def _messenger_send(
    connection: ChannelConnection, recipient: str, message: dict[str, Any], tag: str
) -> str:
    body: dict[str, Any] = {"recipient": {"id": recipient}, "message": message}
    if tag:
        body["messaging_type"] = "MESSAGE_TAG"
        body["tag"] = tag
    else:
        body["messaging_type"] = "RESPONSE"
    # Instagram messaging through a linked Page uses the same Send API with the Page's token.
    page_id = (
        connection.business_account_id
        if connection.channel == ChannelType.INSTAGRAM
        else connection.external_account_id
    )
    result = graph_client(connection).request(
        "POST", f"{page_id}/messages", token=connection_token(connection), body=body
    )
    mid = str(result.get("message_id") or "")
    if not mid:
        raise GraphError("unknown", "no_message_id")
    return mid


def _whatsapp_send(connection: ChannelConnection, recipient: str, payload: dict[str, Any]) -> str:
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient}
    body.update(payload)
    result = graph_client(connection).request(
        "POST",
        f"{connection.external_account_id}/messages",
        token=connection_token(connection),
        body=body,
    )
    messages = result.get("messages")
    mid = str(messages[0].get("id") or "") if isinstance(messages, list) and messages else ""
    if not mid:
        raise GraphError("unknown", "no_message_id")
    return mid


def send_text(connection: ChannelConnection, recipient: str, text: str, *, tag: str = "") -> str:
    if connection.channel == ChannelType.WHATSAPP:
        return _whatsapp_send(
            connection, recipient, {"type": "text", "text": {"body": text, "preview_url": True}}
        )
    return _messenger_send(connection, recipient, {"text": text}, tag)


def send_template(
    connection: ChannelConnection,
    recipient: str,
    *,
    name: str,
    language: str,
    parameters: Sequence[str],
) -> str:
    template: dict[str, Any] = {"name": name, "language": {"code": language}}
    if parameters:
        template["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in parameters]}
        ]
    return _whatsapp_send(connection, recipient, {"type": "template", "template": template})


# --- Webhooks -------------------------------------------------------------------------------------


def valid_signature(raw: bytes, header: str | None) -> bool:
    """``X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body with the app secret>``."""
    secret = settings.META_APP_SECRET
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256=").strip().lower())


@dataclass(frozen=True)
class InboundMedia:
    kind: str  # image | document | unsupported
    media_id: str = ""
    url: str = ""
    mime_type: str = ""
    filename: str = ""


@dataclass(frozen=True)
class InboundEvent:
    channel: str
    account_id: str
    sender_id: str
    message_id: str
    timestamp: datetime | None
    kind: str  # text | image | document | unsupported
    text: str = ""
    sender_name: str = ""
    media: InboundMedia | None = None


@dataclass(frozen=True)
class StatusEvent:
    channel: str
    account_id: str
    status: str  # delivered | read | failed | accepted
    message_ids: tuple[str, ...] = ()
    watermark: datetime | None = None  # Messenger: everything sent up to this time
    recipient_id: str = ""
    error_code: str = ""
    pricing_category: str = ""


@dataclass(frozen=True)
class AccountEvent:
    channel: str
    account_id: str
    suspended: bool


Event = InboundEvent | StatusEvent | AccountEvent


def _ts(value: Any, *, millis: bool = False) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if millis:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _str(value: Any, limit: int = 128) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _messaging_events(channel: str, entry: Mapping[str, Any]) -> list[Event]:
    account = _str(entry.get("id"), 64)
    events: list[Event] = []
    for item in entry.get("messaging") or []:
        if not isinstance(item, Mapping):
            continue
        sender = _str((item.get("sender") or {}).get("id"))
        when = _ts(item.get("timestamp"), millis=True)
        message = item.get("message")
        if isinstance(message, Mapping):
            if message.get("is_echo"):
                continue  # sent by the business (including our own sends): not a customer message
            mid = _str(message.get("mid"))
            text = _str(message.get("text"), 4096)
            attachments = message.get("attachments") or []
            media = None
            kind = "text"
            if isinstance(attachments, list) and attachments:
                first = attachments[0] if isinstance(attachments[0], Mapping) else {}
                atype = _str(first.get("type"), 20)
                url = _str((first.get("payload") or {}).get("url"), 2000)
                if atype == "image":
                    kind, media = "image", InboundMedia("image", url=url)
                elif atype == "file":
                    kind, media = "document", InboundMedia("document", url=url)
                else:
                    kind, media = "unsupported", InboundMedia("unsupported")
            elif not text:
                kind = "unsupported"
            if sender and mid:
                events.append(
                    InboundEvent(channel, account, sender, mid, when, kind, text, "", media)
                )
        delivery = item.get("delivery")
        if isinstance(delivery, Mapping):
            mids = tuple(_str(m) for m in delivery.get("mids") or [] if isinstance(m, str))
            events.append(
                StatusEvent(
                    channel,
                    account,
                    "delivered",
                    mids,
                    _ts(delivery.get("watermark"), millis=True),
                    sender,
                )
            )
        read = item.get("read")
        if isinstance(read, Mapping):
            mid = _str(read.get("mid"))
            events.append(
                StatusEvent(
                    channel,
                    account,
                    "read",
                    (mid,) if mid else (),
                    _ts(read.get("watermark"), millis=True),
                    sender,
                )
            )
    return events


def _whatsapp_events(entry: Mapping[str, Any]) -> list[Event]:
    events: list[Event] = []
    for change in entry.get("changes") or []:
        if not isinstance(change, Mapping):
            continue
        value = _map(change.get("value"))
        if change.get("field") == "account_update":
            event = _str(value.get("event"), 40)
            if event in ("DISABLED_UPDATE", "ACCOUNT_RESTRICTION", "ACCOUNT_VIOLATION"):
                events.append(AccountEvent("whatsapp", _str(entry.get("id"), 64), True))
            continue
        if change.get("field") != "messages":
            continue
        account = _str((value.get("metadata") or {}).get("phone_number_id"), 64)
        names = {
            _str(c.get("wa_id")): _str((c.get("profile") or {}).get("name"), 120)
            for c in value.get("contacts") or []
            if isinstance(c, Mapping)
        }
        for message in value.get("messages") or []:
            if not isinstance(message, Mapping):
                continue
            sender = _str(message.get("from"), 32)
            mid = _str(message.get("id"))
            mtype = _str(message.get("type"), 20)
            when = _ts(message.get("timestamp"))
            text, media, kind = "", None, "unsupported"
            if mtype == "text":
                kind, text = "text", _str((message.get("text") or {}).get("body"), 4096)
            elif mtype in ("image", "document"):
                part = _map(message.get(mtype))
                kind = mtype
                text = _str(part.get("caption"), 1024)
                media = InboundMedia(
                    mtype,
                    media_id=_str(part.get("id")),
                    mime_type=_str(part.get("mime_type"), 64),
                    filename=_str(part.get("filename"), 120),
                )
            elif mtype == "button":
                kind, text = "text", _str((message.get("button") or {}).get("text"), 1024)
            if sender and mid:
                events.append(
                    InboundEvent(
                        "whatsapp",
                        account,
                        sender,
                        mid,
                        when,
                        kind,
                        text,
                        names.get(sender, ""),
                        media,
                    )
                )
        for status in value.get("statuses") or []:
            if not isinstance(status, Mapping):
                continue
            state = _str(status.get("status"), 20)
            mapped = {"sent": "accepted", "delivered": "delivered", "read": "read"}.get(
                state, "failed" if state == "failed" else ""
            )
            if not mapped:
                continue
            errors = status.get("errors") or []
            code = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
                code = f"meta_{_str(str(errors[0].get('code') or ''), 20)}"
            pricing = _map(status.get("pricing"))
            events.append(
                StatusEvent(
                    "whatsapp",
                    account,
                    mapped,
                    (_str(status.get("id")),),
                    _ts(status.get("timestamp")),
                    _str(status.get("recipient_id"), 32),
                    code,
                    _str(pricing.get("category"), 30),
                )
            )
    return events


def parse_webhook(payload: Mapping[str, Any]) -> list[Event]:
    """Normalise a verified webhook body. Unknown objects and fields are ignored."""
    obj = payload.get("object")
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return []
    events: list[Event] = []
    for entry in entries[:100]:
        if not isinstance(entry, Mapping):
            continue
        if obj == "page":
            events.extend(_messaging_events(ChannelType.MESSENGER, entry))
        elif obj == "instagram":
            events.extend(_messaging_events(ChannelType.INSTAGRAM, entry))
        elif obj == "whatsapp_business_account":
            events.extend(_whatsapp_events(entry))
    return events


# --- Media download (SSRF-safe) -------------------------------------------------------------------


class MediaRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _public_address(host: str) -> str:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise MediaRefused("media_dns") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise MediaRefused("media_private_address")
    if not infos:
        raise MediaRefused("media_dns")
    return str(infos[0][4][0])


def allowed_media_url(url: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
        or not host
        or not any(host.endswith(suffix) for suffix in MEDIA_HOST_SUFFIXES)
    ):
        raise MediaRefused("media_host_not_allowed")
    return parts


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to the address we validated, while TLS still verifies the real host name."""

    def __init__(self, host: str, address: str, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, 443), self.timeout)
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


def download(url: str, *, token: str = "", limit: int, timeout: float = TIMEOUT) -> bytes:
    """GET a Meta media URL: HTTPS, allowlisted Meta hosts, public addresses only, pinned
    connection, no redirects, bounded size. The token (WhatsApp) goes in a header."""
    parts = allowed_media_url(url)
    host = parts.hostname or ""
    address = _public_address(host)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    connection = _PinnedHTTPSConnection(host, address, timeout)
    headers = {"User-Agent": "MateCart/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise MediaRefused(
                "media_expired" if response.status in (403, 404, 410) else "media_http"
            )
        data = response.read(limit + 1)
    except (OSError, http.client.HTTPException):
        raise MediaRefused("media_network") from None
    finally:
        connection.close()
    if len(data) > limit:
        raise MediaRefused("media_too_large")
    if not data:
        raise MediaRefused("media_empty")
    return data


def whatsapp_media_url(connection: ChannelConnection, media_id: str) -> str:
    result = graph_client(connection).request(
        "GET", urllib.parse.quote(media_id, safe=""), token=connection_token(connection)
    )
    return str(result.get("url") or "")
