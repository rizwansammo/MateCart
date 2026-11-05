"""Migration source connections (D-107).

The merchant gives an ``https://`` store address and read-only credentials. The address is
validated before anything is saved (HTTPS, host name only, public addresses, no redirects).
Credentials are encrypted, never returned, never logged and never written to audit events (field
names only). Verification reads the store's currency, counts and readable object types; nothing
is written to the source. Customer and order migration also need the matching source permission
*and* the MateCart permissions that protect that data.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from commerce.audit.services import record_audit_event
from commerce.core import outbound, ratelimit, security
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User
from commerce.staff.permissions import Permission
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import get_active_tenant_id

from .models import (
    ACTIVE_JOB_STATUSES,
    DataTransferJob,
    SourceConnection,
    SourcePlatform,
    SourceStatus,
)
from .sources.base import Source, SourceError
from .sources.shopify import ShopifySource, shop_domain
from .sources.simulator import SimulatedSource
from .sources.woocommerce import WooCommerceSource

VERIFY_PER_HOUR = 20
ERROR_STATUS = {
    "invalid_credentials": SourceStatus.INVALID_CREDENTIALS,
    "permission": SourceStatus.PERMISSION_MISSING,
    "refused": SourceStatus.REFUSED,
}
SCOPE_PERMISSIONS = {
    "categories": (Permission.CATALOG_MANAGE,),
    "products": (Permission.CATALOG_MANAGE,),
    "coupons": (Permission.PROMOTIONS_MANAGE,),
    "customers": (Permission.CUSTOMERS_VIEW,),
    "orders": (Permission.ORDERS_MANAGE, Permission.CUSTOMERS_VIEW),
}


def simulator_allowed() -> bool:
    return bool(getattr(settings, "MATECART_MIGRATION_SIMULATOR", False))


def _store_url(platform: str, raw: str) -> tuple[str, str]:
    if platform == SourcePlatform.SHOPIFY:
        try:
            domain = shop_domain(raw)
        except SourceError:
            raise ValidationError(
                {
                    "store_url": [
                        "Enter the store's myshopify.com address, e.g. my-shop.myshopify.com."
                    ]
                }
            ) from None
        return f"https://{domain}", domain
    text = raw.strip()
    if "://" not in text:
        text = "https://" + text
    try:
        target = outbound.parse_url(text)
        outbound.resolve(target)
    except outbound.OutboundError as exc:
        messages = {
            "https_required": "Use the store's https:// address.",
            "host_not_public": "This address is not a public internet address.",
            "host_not_found": "This address could not be found.",
        }
        raise ValidationError(
            {"store_url": [messages.get(exc.code, "Enter a valid public https:// store address.")]}
        ) from None
    if target.path not in ("/", ""):
        raise ValidationError({"store_url": ["Enter the store's address without a path."]})
    return f"https://{target.host}", target.host


def _credentials(platform: str, data: Mapping[str, Any]) -> tuple[dict[str, str], str, str]:
    if platform == SourcePlatform.WOOCOMMERCE:
        key, secret = (
            str(data.get("consumer_key") or "").strip(),
            str(data.get("consumer_secret") or "").strip(),
        )
        if (
            not key.startswith("ck_")
            or not secret.startswith("cs_")
            or len(key) > 100
            or len(secret) > 100
        ):
            raise ValidationError(
                {"consumer_key": ["Enter the consumer key (ck_…) and secret (cs_…) of a Read key."]}
            )
        return {"key": key, "secret": secret}, key[-4:], "consumer_key"
    token = str(data.get("access_token") or "").strip()
    if token:
        if len(token) > 200 or not token.isprintable() or " " in token:
            raise ValidationError({"access_token": ["Enter the Admin API access token."]})
        return {"access_token": token}, token[-4:], "access_token"
    client_id = str(data.get("client_id") or "").strip()
    client_secret = str(data.get("client_secret") or "").strip()
    if not client_id or not client_secret or len(client_id) > 200 or len(client_secret) > 200:
        raise ValidationError(
            {"access_token": ["Enter an Admin API access token, or a client ID and client secret."]}
        )
    return (
        {"client_id": client_id, "client_secret": client_secret},
        client_id[-4:],
        "client_credentials",
    )


def create(*, platform: str, data: Mapping[str, Any], actor: User) -> SourceConnection:
    simulated = bool(data.get("simulated"))
    if simulated:
        if not simulator_allowed():
            raise ValidationError(
                {"simulated": ["The simulator is not available on this deployment."]}
            )
        identity = f"simulator-{platform}.matecart.test"
        creds: dict[str, str] = {}
        url, hint, kind = f"https://{identity}", "", "simulator"
    else:
        url, identity = _store_url(platform, str(data.get("store_url") or ""))
        creds, hint, kind = _credentials(platform, data)
    if SourceConnection.objects.filter(platform=platform, store_identity=identity).exists():
        raise ConflictError("This store is already connected.", "source_exists")
    with transaction.atomic():
        row = SourceConnection.objects.create(
            platform=platform,
            name=str(data.get("name") or "")[:120],
            store_url=url,
            store_identity=identity,
            credentials_encrypted=security.encrypt(json.dumps(creds)) if creds else "",
            credential_hint=hint,
            credential_kind=kind,
            is_simulated=simulated,
            created_by=actor,
        )
        record_audit_event(
            action="transfers.source.created",
            resource=row,
            actor=actor,
            changes={
                "platform": platform,
                "simulated": simulated,
                "fields": ["store_url", "credentials"],
            },
        )
    return row


def source_for(row: SourceConnection | None) -> Source:
    if row is None:
        raise DomainError("This job has no source.", "source_missing")
    if row.is_simulated:
        if not simulator_allowed():
            raise SourceError("refused", "simulator_disabled")
        currency = StoreSettings.objects.values_list("default_currency", flat=True).first() or "BDT"
        return SimulatedSource(row.platform, row.store_identity, currency)
    try:
        creds = (
            json.loads(security.decrypt(row.credentials_encrypted))
            if row.credentials_encrypted
            else {}
        )
    except (security.DecryptionError, ValueError):
        raise SourceError("invalid_credentials", "credentials_unreadable") from None
    if row.platform == SourcePlatform.WOOCOMMERCE:
        return WooCommerceSource(row.store_url, creds.get("key", ""), creds.get("secret", ""))
    return ShopifySource(row.store_identity, creds)


def verify(*, row: SourceConnection, actor: User) -> SourceConnection:
    ratelimit.consume(
        "source-verify", str(get_active_tenant_id()), limit=VERIFY_PER_HOUR, window=3600
    )
    status, error, info = SourceStatus.CONNECTED, "", None
    try:
        info = source_for(row).verify()
    except SourceError as exc:
        status, error = ERROR_STATUS.get(exc.kind, SourceStatus.UNREACHABLE), exc.code
    with transaction.atomic():
        locked = SourceConnection.objects.select_for_update().get(pk=row.pk)
        locked.status, locked.last_error = status, error
        if info is not None:
            locked.source_name = info.name[:200]
            locked.source_currency = info.currency.upper()[:3]
            locked.capabilities = info.capabilities
            locked.counts = info.counts
            locked.last_verified_at = timezone.now()
        locked.save()
        record_audit_event(
            action="transfers.source.verified",
            resource=locked,
            actor=actor,
            changes={"status": status, "error": error},
        )
    return locked


def delete(*, row: SourceConnection, actor: User) -> None:
    if DataTransferJob.objects.filter(connection=row, status__in=ACTIVE_JOB_STATUSES).exists():
        raise ConflictError("A migration from this store is still running.", "source_in_use")
    with transaction.atomic():
        locked = SourceConnection.objects.select_for_update().get(pk=row.pk)
        # The credential is erased; the row stays when jobs reference it (provenance).
        locked.credentials_encrypted = ""
        locked.credential_hint = ""
        locked.status = SourceStatus.UNVERIFIED
        locked.save()
        record_audit_event(
            action="transfers.source.disconnected",
            resource=locked,
            actor=actor,
            changes={"credentials_erased": True},
        )
        if not DataTransferJob.objects.filter(connection=locked).exists():
            locked.delete()


def ensure_scopes(row: SourceConnection, scopes: list[str], permissions: frozenset[str]) -> None:
    if row.status != SourceStatus.CONNECTED:
        raise ConflictError("Verify the store connection first.", "source_not_connected")
    for scope in scopes:
        missing = [perm for perm in SCOPE_PERMISSIONS[scope] if perm not in permissions]
        if missing:
            raise PermissionDenied(f"Your role cannot migrate {scope}.")
        if not row.capabilities.get(scope):
            raise ConflictError(
                f"The source credential cannot read {scope}. Give it that read permission first.",
                "source_scope_missing",
            )
    if row.source_currency and "products" in scopes:
        store = StoreSettings.objects.values_list("default_currency", flat=True).first()
        if store and row.source_currency != store:
            raise ConflictError(
                f"The source store sells in {row.source_currency} and this store in {store}. "
                "MateCart does not convert prices; an approved conversion policy is needed first.",
                "source_currency_mismatch",
            )


def public_state(row: SourceConnection) -> dict[str, Any]:
    return {
        "id": row.pk,
        "platform": row.platform,
        "name": row.name or row.source_name or row.store_identity,
        "store_url": row.store_url,
        "credential_kind": row.credential_kind,
        "credential_hint": row.credential_hint,
        "credential_set": bool(row.credentials_encrypted) or row.is_simulated,
        "status": row.status,
        "last_verified_at": row.last_verified_at,
        "last_error": row.last_error,
        "source_name": row.source_name,
        "source_currency": row.source_currency,
        "capabilities": row.capabilities,
        "counts": row.counts,
        "is_simulated": row.is_simulated,
        "created_at": row.created_at,
    }
