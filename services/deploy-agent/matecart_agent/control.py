"""Outbound client for the Control API Agent protocol (/api/agent/v1/, D-119).

- HTTPS with certificate verification (plain http only on loopback for local acceptance);
- every request signed; every response must carry a valid signature bound to the request nonce;
- redirects are never followed, so a signed request cannot be replayed to another host.
"""

import json
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from . import VERSION
from .config import AgentConfig
from .signing import response_valid, sign, signature_base


class ControlUnavailable(RuntimeError):
    """Control could not be reached; the Agent keeps Stores running and buffers reports."""


class ControlRejected(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Control refused the request ({status}): {detail[:200]}")
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass
class Response:
    status: int
    data: Any


class ControlClient:
    def __init__(self, config: AgentConfig, *, timeout: float = 20) -> None:
        self.config = config.validate()
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def _url(self, path: str) -> str:
        return urljoin(self.config.control_url.rstrip("/") + "/", path.lstrip("/"))

    def _send(self, request: urllib.request.Request) -> tuple[int, bytes, Any]:
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, response.read(1_048_576), response.headers
        except urllib.error.HTTPError as exc:
            body = exc.read(65536)
            return exc.code, body, exc.headers
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ControlUnavailable(str(exc)) from exc

    def enroll(self, token: str, host_facts: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(
            {"token": token, "agent_version": VERSION, "host_facts": host_facts}
        ).encode()
        request = urllib.request.Request(  # noqa: S310 - scheme validated in AgentConfig
            self._url("/api/agent/v1/enroll/"),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": f"matecart-agent/{VERSION}"},
        )
        status, content, _ = self._send(request)
        if status != 201:
            raise ControlRejected(status, content.decode("utf-8", "replace"))
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ControlRejected(status, "unexpected enrollment response")
        return data

    def post(self, path: str, payload: Any = None) -> Response:
        if not (self.config.key_id and self.config.secret):
            raise ControlRejected(401, "The Agent is not enrolled.")
        body = json.dumps(payload if payload is not None else {}, default=str).encode()
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        url_path = urlparse(self._url(path)).path
        secret = self.config.pending_secret or self.config.secret
        signature = sign(secret, signature_base("POST", url_path, timestamp, nonce, body))
        request = urllib.request.Request(  # noqa: S310 - scheme validated in AgentConfig
            self._url(path),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"matecart-agent/{VERSION}",
                "X-MateCart-Agent-Key": self.config.key_id,
                "X-MateCart-Timestamp": timestamp,
                "X-MateCart-Nonce": nonce,
                "X-MateCart-Signature": signature,
            },
        )
        status, content, headers = self._send(request)
        if status >= 400:
            raise ControlRejected(status, content.decode("utf-8", "replace"))
        if not response_valid(
            secret, nonce, status, content, str(headers.get("X-MateCart-Signature", ""))
        ):
            raise ControlRejected(status, "response signature is invalid")
        if self.config.pending_secret:
            # Control promoted the pending secret on this request (D-119 rotation).
            self.config.secret, self.config.pending_secret = self.config.pending_secret, ""
        return Response(status=status, data=json.loads(content) if content else None)
