"""Dependency checks behind the readiness endpoint.

Failures are logged with full detail here and reported to callers only as "error", so the
public response never reveals hostnames, credentials or driver messages.
"""

import logging
from collections.abc import Callable
from typing import Literal

from django.db import connection

logger = logging.getLogger(__name__)

CheckState = Literal["ok", "error"]


def check_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


READINESS_CHECKS: dict[str, Callable[[], None]] = {
    "database": check_database,
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
