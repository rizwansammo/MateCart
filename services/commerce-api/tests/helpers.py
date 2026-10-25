"""Helpers for authentication tests."""

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from django.core import mail
from rest_framework.test import APIClient

CODE = re.compile(r"\b(\d{6})\b")


class Api:
    """JSON client that runs on-commit callbacks (emails, SMS) like a committed request would."""

    def __init__(
        self,
        client: APIClient,
        capture: Callable[..., AbstractContextManager[Any]],
    ) -> None:
        self.client = client
        self.capture = capture

    def get(self, url: str, **query: Any) -> Any:
        with self.capture(execute=True):
            return self.client.get(url, query or None)

    def post(self, url: str, data: dict[str, Any] | None = None) -> Any:
        with self.capture(execute=True):
            return self.client.post(url, data or {}, format="json")

    def put(self, url: str, data: dict[str, Any] | None = None) -> Any:
        with self.capture(execute=True):
            return self.client.put(url, data or {}, format="json")

    def patch(self, url: str, data: dict[str, Any] | None = None) -> Any:
        with self.capture(execute=True):
            return self.client.patch(url, data or {}, format="json")

    def delete(self, url: str) -> Any:
        with self.capture(execute=True):
            return self.client.delete(url)

    def upload(self, url: str, data: dict[str, Any]) -> Any:
        with self.capture(execute=True):
            return self.client.post(url, data, format="multipart")


def last_code(destination: str | None = None) -> str:
    """The 6-digit code in the newest email (optionally: sent to ``destination``)."""
    for message in reversed(mail.outbox):
        if destination is None or destination in message.to:
            match = CODE.search(str(message.body))
            if match:
                return match.group(1)
    raise AssertionError("No code was emailed.")
