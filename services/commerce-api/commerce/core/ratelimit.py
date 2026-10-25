"""Fixed-window rate limits in the shared cache (Redis), namespaced by tenant when one is active.

Identifiers (emails, phone numbers, IPs) are hashed before they become cache keys. Counters live
in their own cache alias ("ratelimit") so tests can isolate them without touching other data.
"""

import hashlib

from django.core.cache import BaseCache, caches
from rest_framework import status
from rest_framework.exceptions import APIException

from commerce.tenancy.context import get_active_tenant_id_or_none


class RateLimited(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many attempts. Please wait a few minutes and try again."
    default_code = "rate_limited"


def _cache() -> BaseCache:
    return caches["ratelimit"]


def _key(scope: str, identifier: str) -> str:
    tenant = get_active_tenant_id_or_none()
    digest = hashlib.sha256(identifier.lower().encode()).hexdigest()[:40]
    return f"ratelimit:{tenant or 'global'}:{scope}:{digest}"


def count(scope: str, identifier: str) -> int:
    value = _cache().get(_key(scope, identifier), 0)
    return int(value)


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
