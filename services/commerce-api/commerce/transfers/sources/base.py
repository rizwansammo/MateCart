"""The interface every migration source implements (D-107).

A source only *reads*. ``verify`` proves the credential works and reports which object types it
may read; ``page`` returns one page of normalised records (``normalize`` shapes) and the cursor
of the next page, so extraction is checkpointed and resumable. Errors are ``SourceError`` with a
behaviour ``kind`` and a short, safe ``code``; source responses never reach logs or users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

OBJECT_TYPES = ("categories", "products", "customers", "orders", "coupons")
SENSITIVE_TYPES = frozenset({"customers", "orders"})


class SourceError(Exception):
    """kinds: invalid_credentials, permission, rate_limited, unreachable, refused, bad_response."""

    def __init__(self, kind: str, code: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.code = (code or kind)[:48]
        self.retry_after = retry_after


@dataclass
class SourceInfo:
    name: str
    currency: str
    capabilities: dict[str, bool]
    counts: dict[str, int | None] = field(default_factory=dict)


@dataclass
class Page:
    records: list[dict[str, Any]]
    next_cursor: str | None
    # Set while a long-running source export (Shopify bulk operation) is still being prepared:
    # the caller should ask again after ``wait`` seconds with the same cursor.
    wait: float | None = None


class Source(Protocol):
    platform: str
    identity: str

    def verify(self) -> SourceInfo: ...

    def page(self, object_type: str, cursor: str | None) -> Page: ...
