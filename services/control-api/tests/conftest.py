from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.core.cache import caches
from rest_framework.test import APIClient


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["ratelimit"].clear()


class Api:
    """JSON client that runs on-commit callbacks (emails) like a committed request would."""

    def __init__(self, capture: Callable[..., AbstractContextManager[Any]]) -> None:
        self.client = APIClient()
        self.capture = capture

    def get(self, url: str) -> Any:
        with self.capture(execute=True):
            return self.client.get(url)

    def post(self, url: str, data: dict[str, Any] | None = None) -> Any:
        with self.capture(execute=True):
            return self.client.post(url, data or {}, format="json")


@pytest.fixture
def make_api(
    django_capture_on_commit_callbacks: Callable[..., AbstractContextManager[Any]],
) -> Callable[[], Api]:
    return lambda: Api(django_capture_on_commit_callbacks)


@pytest.fixture
def api(make_api: Callable[[], Api]) -> Api:
    return make_api()
