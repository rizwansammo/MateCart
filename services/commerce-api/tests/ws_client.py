"""A minimal WebSocket test communicator on asgiref's ApplicationCommunicator.

Channels' own ``channels.testing`` package imports Daphne (for its live-server class), which this
project does not install; the WebSocket helper there is this same thin wrapper."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlparse

from asgiref.testing import ApplicationCommunicator


class WebsocketCommunicator:
    def __init__(self, application: Any, path: str, headers: list[tuple[bytes, bytes]]) -> None:
        parsed = urlparse(path)
        scope = {
            "type": "websocket",
            "path": unquote(parsed.path),
            "query_string": parsed.query.encode(),
            "headers": headers,
            "subprotocols": [],
            "client": ("127.0.0.1", 50000),
        }
        # asgiref's communicator is untyped; keep it behind this typed surface.
        factory: Any = ApplicationCommunicator
        self._inner: Any = factory(application, scope)

    async def connect(self, timeout: float = 3) -> tuple[bool, Any]:
        await self._inner.send_input({"type": "websocket.connect"})
        response = await self.receive_output(timeout)
        if response["type"] == "websocket.close":
            return False, response.get("code", 1000)
        return True, response.get("subprotocol")

    async def send_to(self, text_data: str) -> None:
        await self._inner.send_input({"type": "websocket.receive", "text": text_data})

    async def send_json_to(self, data: Any) -> None:
        await self.send_to(json.dumps(data))

    async def receive_output(self, timeout: float = 3) -> dict[str, Any]:
        output: dict[str, Any] = await self._inner.receive_output(timeout)
        return output

    async def receive_nothing(self, timeout: float = 0.1) -> bool:
        result: bool = await self._inner.receive_nothing(timeout)
        return result

    async def receive_from(self, timeout: float = 3) -> str:
        response = await self.receive_output(timeout)
        if response["type"] != "websocket.send":
            raise AssertionError(f"Expected a frame, got {response}")
        return str(response["text"])

    async def disconnect(self, code: int = 1000, timeout: float = 3) -> None:
        await self._inner.send_input({"type": "websocket.disconnect", "code": code})
        await self._inner.wait(timeout)
