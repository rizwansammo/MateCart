"""Dependency checks behind the readiness endpoint.

Failures are logged with full detail here and reported to callers only as "error", so the
public response never reveals hostnames, credentials or driver messages.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Literal

import redis
from django.conf import settings
from django.core.cache import cache
from django.db import connection

from commerce.tenancy.resolution import check_deployment_tenant

logger = logging.getLogger(__name__)

CheckState = Literal["ok", "error"]


def check_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def check_cache() -> None:
    key = f"health:{uuid.uuid4().hex}"
    cache.set(key, "ok", timeout=10)
    if cache.get(key) != "ok":
        raise RuntimeError("Cache round-trip returned an unexpected value.")
    cache.delete(key)


def check_broker() -> None:
    client = redis.Redis.from_url(
        settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2
    )
    try:
        client.ping()
    finally:
        client.close()


READINESS_CHECKS: dict[str, Callable[[], None]] = {
    "database": check_database,
    "cache": check_cache,
    "broker": check_broker,
    # The configured tenant is provisioned and is the only tenant (single-tenant mode).
    "tenant": check_deployment_tenant,
}


def run_readiness_checks() -> dict[str, CheckState]:
    results: dict[str, CheckState] = {}
    for name, check in READINESS_CHECKS.items():
        try:
            check()
        except Exception:
            logger.exception("Readiness check failed: %s", name)
            results[name] = "error"
        else:
            results[name] = "ok"
    return results
