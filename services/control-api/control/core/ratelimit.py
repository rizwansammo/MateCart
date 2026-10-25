"""Fixed-window rate limits in Redis for console sign-in.

Identifiers (emails, IPs, admin ids) are hashed before they become cache keys. Counters live in
their own cache alias ("ratelimit") so tests can isolate them.
"""

import hashlib

from django.core.cache import BaseCache, caches
from rest_framework import status
from rest_framework.exceptions import APIException


class RateLimited(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many attempts. Please wait a few minutes and try again."
    default_code = "rate_limited"


def _cache() -> BaseCache:
    return caches["ratelimit"]


def _key(scope: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.lower().encode()).hexdigest()[:40]
    return f"ratelimit:{scope}:{digest}"


def count(scope: str, identifier: str) -> int:
    return int(_cache().get(_key(scope, identifier), 0))


def hit(scope: str, identifier: str, *, window: int) -> int:
    key = _key(scope, identifier)
    if _cache().add(key, 1, timeout=window):
        return 1
    try:
        return int(_cache().incr(key))
    except ValueError:  # expired between add() and incr()
        _cache().add(key, 1, timeout=window)
        return 1


def check(scope: str, identifier: str, *, limit: int) -> None:
    """Refuse when ``identifier`` already used ``limit`` attempts in the current window."""
    if count(scope, identifier) >= limit:
        raise RateLimited()


def consume(scope: str, identifier: str, *, limit: int, window: int) -> None:
    """Count one attempt and refuse once the window's limit is exceeded."""
    if hit(scope, identifier, window=window) > limit:
        raise RateLimited()


def clear(scope: str, identifier: str) -> None:
    _cache().delete(_key(scope, identifier))
