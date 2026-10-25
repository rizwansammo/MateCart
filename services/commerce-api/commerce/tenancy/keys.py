"""Tenant namespaces outside the database: cache keys and file storage paths."""

import re
from pathlib import PurePosixPath

from commerce.core.ids import uuid7

from .context import get_active_tenant_id
from .models import TenantOwnedModel

_SAFE_EXTENSION = re.compile(r"^\.[a-z0-9]{1,10}$")


def tenant_cache_key(*parts: str) -> str:
    """``tenant:<tenant id>:<part>:...`` for the active tenant. Use for every cache entry that
    holds tenant data, even though each dedicated deployment also has its own Redis."""
    if not parts:
        raise ValueError("tenant_cache_key() needs at least one key part.")
    return ":".join(("tenant", str(get_active_tenant_id()), *parts))


def tenant_storage_path(instance: TenantOwnedModel, filename: str) -> str:
    """``upload_to`` for tenant files: ``tenants/<tenant>/<app>/<model>/<uuid7><ext>``.

    The client's filename never becomes part of the path; only a plain extension is kept.
    """
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    extension = suffix if _SAFE_EXTENSION.match(suffix) else ""
    meta = instance._meta
    return (
        f"tenants/{instance.tenant_id}/{meta.app_label}/{meta.model_name}/{uuid7().hex}{extension}"
    )
