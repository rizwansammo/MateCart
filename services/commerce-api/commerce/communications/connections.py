"""Connecting Messenger, Instagram Direct and WhatsApp Business accounts (D-094, D-097).

The flow is Meta's official authorization, run per store deployment:

1. ``start`` (staff with ``store.settings`` and a recent sign-in) creates a single-use state
   bound to this store, this staff member and this session, and returns Meta's dialog URL
   (Facebook Login, or WhatsApp Embedded Signup when ``META_WHATSAPP_CONFIG_ID`` is set).
2. Meta redirects to ``/api/manage/communications/oauth/callback/``. The staff cookie is
   scoped to ``/api/manage``, so the same session must be present; the state is consumed once.
   The code is exchanged server-side with the app secret.
3. The accounts that *Meta* reports for that person's token become the only choices: Pages
   whose tasks include messaging (Messenger), Pages with a linked Instagram professional
   account (Instagram), or business phone numbers in the WhatsApp Business Accounts the token
   was granted (WhatsApp). A merchant can never connect an id they typed.
4. ``select`` stores the chosen account's token encrypted, subscribes the app to its webhooks
   and marks it connected. Nothing is "connected" until Meta confirmed all of this.

Tokens never leave the server: not in responses, logs, audit changes or events.
"""

from __future__ import annotations

import logging
import secrets
import urllib.parse
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from commerce.audit.services import record_audit_event
from commerce.core import security
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import AuthSession, User
from commerce.tenancy.keys import tenant_cache_key

from . import meta
from .models import (
    EXTERNAL_CHANNELS,
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
    WhatsAppTemplate,
)

logger = logging.getLogger(__name__)

STATE_TTL = 600
SELECTION_TTL = 900
CALLBACK_PATH = "/api/manage/communications/oauth/callback/"
SCOPES = {
    ChannelType.MESSENGER: ("pages_show_list", "pages_manage_metadata", "pages_messaging"),
    ChannelType.INSTAGRAM: (
        "pages_show_list",
        "pages_manage_metadata",
        "pages_messaging",
        "instagram_basic",
        "instagram_manage_messages",
    ),
    ChannelType.WHATSAPP: ("whatsapp_business_management", "whatsapp_business_messaging"),
}
REQUIRED_SCOPES = {
    ChannelType.MESSENGER: {"pages_messaging"},
    ChannelType.INSTAGRAM: {"instagram_manage_messages", "pages_messaging"},
    ChannelType.WHATSAPP: {"whatsapp_business_messaging", "whatsapp_business_management"},
}
WEBHOOK_FIELDS = "messages,message_deliveries,message_reads"


class ConnectionFailed(DomainError):
    default_code = "connection_failed"


def meta_configured() -> bool:
    return bool(
        settings.META_APP_ID and settings.META_APP_SECRET and settings.MATECART_PUBLIC_BASE_URL
    )


def webhook_configured() -> bool:
    return bool(settings.META_APP_SECRET and settings.META_WEBHOOK_VERIFY_TOKEN)


def live(channel: str) -> ChannelConnection | None:
    return ChannelConnection.objects.filter(channel=channel, disconnected_at__isnull=True).first()


def redirect_uri() -> str:
    return f"{settings.MATECART_PUBLIC_BASE_URL}{CALLBACK_PATH}"


def _state_key(digest: str) -> str:
    return tenant_cache_key("comms", "oauth", digest)


def _selection_key(digest: str) -> str:
    return tenant_cache_key("comms", "select", digest)


def start(*, channel: str, actor: User, session: AuthSession) -> str:
    if channel not in EXTERNAL_CHANNELS:
        raise DomainError("Choose Messenger, Instagram or WhatsApp.", "unknown_channel")
    if not meta_configured():
        raise ConflictError(
            "This store's Meta app is not configured yet. NetaMate sets it up for the store.",
            "meta_not_configured",
        )
    state = secrets.token_urlsafe(32)
    cache.set(
        _state_key(security.hash_token(state)),
        {"channel": channel, "user": str(actor.pk), "session": str(session.pk)},
        STATE_TTL,
    )
    params: dict[str, str] = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": redirect_uri(),
        "state": state,
        "response_type": "code",
    }
    config_id = (
        settings.META_WHATSAPP_CONFIG_ID
        if channel == ChannelType.WHATSAPP
        else settings.META_LOGIN_CONFIG_ID
    )
    if config_id:
        params["config_id"] = config_id
        if channel == ChannelType.WHATSAPP:
            params["override_default_response_type"] = "true"
    else:
        params["scope"] = ",".join(SCOPES[ChannelType(channel)])
    version = settings.META_GRAPH_API_VERSION
    return f"https://www.facebook.com/{version}/dialog/oauth?{urllib.parse.urlencode(params)}"


def _exchange(code: str) -> str:
    client = meta.graph_client()
    result = client.request(
        "POST",
        "oauth/access_token",
        body={
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "redirect_uri": redirect_uri(),
            "code": code,
        },
        form=True,
    )
    token = str(result.get("access_token") or "")
    if not token:
        raise ConnectionFailed("Meta did not return an access token.", "meta_no_token")
    return token


def _long_lived(token: str) -> str:
    result = meta.graph_client().request(
        "POST",
        "oauth/access_token",
        body={
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "fb_exchange_token": token,
        },
        form=True,
    )
    return str(result.get("access_token") or token)


def _debug(token: str) -> dict[str, Any]:
    result = meta.graph_client().request(
        "GET",
        "debug_token",
        params={"input_token": token},
        token=f"{settings.META_APP_ID}|{settings.META_APP_SECRET}",
    )
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def _granted(debug: Mapping[str, Any]) -> set[str]:
    return {str(scope) for scope in debug.get("scopes") or [] if isinstance(scope, str)}


def _candidates(channel: str, token: str) -> list[dict[str, str]]:
    client = meta.graph_client()
    found: list[dict[str, str]] = []
    if channel in (ChannelType.MESSENGER, ChannelType.INSTAGRAM):
        result = client.request(
            "GET",
            "me/accounts",
            token=token,
            params={
                "fields": "id,name,tasks,access_token,instagram_business_account{id,username}",
                "limit": "100",
            },
        )
        for page in result.get("data") or []:
            if not isinstance(page, dict):
                continue
            tasks = {str(task) for task in page.get("tasks") or []}
            if not tasks & {"MESSAGING", "MANAGE"} or not page.get("access_token"):
                continue
            if channel == ChannelType.MESSENGER:
                found.append(
                    {
                        "account": str(page.get("id")),
                        "business": "",
                        "name": str(page.get("name") or "")[:120],
                        "identifier": "",
                        "token": str(page["access_token"]),
                    }
                )
                continue
            ig = page.get("instagram_business_account")
            if isinstance(ig, dict) and ig.get("id"):
                found.append(
                    {
                        "account": str(ig["id"]),
                        "business": str(page.get("id")),
                        "name": str(page.get("name") or "")[:120],
                        "identifier": f"@{ig.get('username')}" if ig.get("username") else "",
                        "token": str(page["access_token"]),
                    }
                )
        return found
    debug = _debug(token)
    wabas: list[str] = []
    for grant in debug.get("granular_scopes") or []:
        if isinstance(grant, dict) and grant.get("scope") == "whatsapp_business_management":
            wabas.extend(str(target) for target in grant.get("target_ids") or [])
    for waba in dict.fromkeys(wabas):
        numbers = client.request(
            "GET",
            f"{urllib.parse.quote(waba, safe='')}/phone_numbers",
            token=token,
            params={"fields": "id,display_phone_number,verified_name,status,quality_rating"},
        )
        for number in numbers.get("data") or []:
            if isinstance(number, dict) and number.get("id"):
                found.append(
                    {
                        "account": str(number["id"]),
                        "business": waba,
                        "name": str(number.get("verified_name") or "")[:120],
                        "identifier": str(number.get("display_phone_number") or "")[:60],
                        "token": token,
                    }
                )
    return found


def callback(*, state: str, code: str, error: str, actor: User, session: AuthSession) -> str:
    """Finish Meta's redirect. Returns the dashboard path to show next (never an absolute URL)."""
    base = "/manage/settings/social"
    data = cache.get(_state_key(security.hash_token(state))) if state else None
    if data is not None:
        cache.delete(_state_key(security.hash_token(state)))
    if (
        not isinstance(data, dict)
        or data.get("user") != str(actor.pk)
        or data.get("session") != str(session.pk)
    ):
        return f"{base}?result=invalid_state"
    channel = str(data["channel"])
    if error or not code:
        return f"{base}?result=cancelled&channel={channel}"
    try:
        token = _long_lived(_exchange(code))
        granted = _granted(_debug(token))
        if not REQUIRED_SCOPES[ChannelType(channel)] <= granted:
            return f"{base}?result=permission_missing&channel={channel}"
        candidates = _candidates(channel, token)
    except meta.GraphError as exc:
        logger.warning("Meta authorization failed (channel=%s, code=%s)", channel, exc.code)
        return f"{base}?result=failed&channel={channel}"
    if not candidates:
        return f"{base}?result=no_accounts&channel={channel}"
    selection = secrets.token_urlsafe(24)
    cache.set(
        _selection_key(security.hash_token(selection)),
        {
            "channel": channel,
            "user": str(actor.pk),
            "session": str(session.pk),
            "scopes": ",".join(sorted(granted))[:500],
            "candidates": security.encrypt(signing.dumps(candidates)),
        },
        SELECTION_TTL,
    )
    return f"{base}?select={selection}&channel={channel}"


def _selection(token: str, actor: User, session: AuthSession) -> dict[str, Any]:
    data = cache.get(_selection_key(security.hash_token(token))) if token else None
    if (
        not isinstance(data, dict)
        or data.get("user") != str(actor.pk)
        or data.get("session") != str(session.pk)
    ):
        raise ConflictError(
            "This authorization expired. Start connecting again.", "selection_expired"
        )
    return data


def choices(*, selection: str, actor: User, session: AuthSession) -> dict[str, Any]:
    data = _selection(selection, actor, session)
    candidates = signing.loads(security.decrypt(data["candidates"]))
    return {
        "channel": data["channel"],
        "accounts": [
            {"id": c["account"], "name": c["name"], "identifier": c["identifier"]}
            for c in candidates
        ],
    }


def _subscribe(connection: ChannelConnection, token: str) -> None:
    client = meta.graph_client(connection)
    if connection.channel == ChannelType.WHATSAPP:
        client.request(
            "POST",
            f"{urllib.parse.quote(connection.business_account_id, safe='')}/subscribed_apps",
            token=token,
        )
        return
    page = (
        connection.business_account_id
        if connection.channel == ChannelType.INSTAGRAM
        else connection.external_account_id
    )
    client.request(
        "POST",
        f"{urllib.parse.quote(page, safe='')}/subscribed_apps",
        token=token,
        body={"subscribed_fields": WEBHOOK_FIELDS},
        form=True,
    )


def select(
    *, selection: str, account_id: str, actor: User, session: AuthSession
) -> ChannelConnection:
    data = _selection(selection, actor, session)
    candidates = signing.loads(security.decrypt(data["candidates"]))
    chosen = next((c for c in candidates if c["account"] == str(account_id)), None)
    if chosen is None:
        raise DomainError("Choose one of the accounts Meta listed.", "account_not_listed")
    channel = str(data["channel"])
    cache.delete(_selection_key(security.hash_token(selection)))
    now = timezone.now()
    with transaction.atomic():
        current = live(channel)
        if current is not None and current.external_account_id != chosen["account"]:
            raise ConflictError(
                "Disconnect the current account before connecting another one.",
                "already_connected",
            )
        row = (
            ChannelConnection.objects.select_for_update()
            .filter(channel=channel, external_account_id=chosen["account"])
            .first()
        ) or ChannelConnection(channel=channel, external_account_id=chosen["account"])
        row.business_account_id = chosen["business"]
        row.display_name = chosen["name"]
        row.display_identifier = chosen["identifier"]
        row.access_token_encrypted = security.encrypt(chosen["token"])
        row.scopes = str(data.get("scopes") or "")
        row.status = ConnectionStatus.PENDING
        row.is_enabled = True
        row.is_simulated = False
        row.connected_by = actor
        row.disconnected_at = None
        row.last_error = ""
        row.full_clean()
        row.save()
    try:
        _subscribe(row, chosen["token"])
    except meta.GraphError as exc:
        ChannelConnection.objects.filter(pk=row.pk).update(
            status=ConnectionStatus.ERROR, last_error=exc.code[:60]
        )
        raise ConflictError(
            "Meta did not accept the webhook subscription. Check the app's permissions and try "
            "again.",
            "webhook_subscription_failed",
        ) from None
    with transaction.atomic():
        row = ChannelConnection.objects.select_for_update().get(pk=row.pk)
        row.status = ConnectionStatus.CONNECTED
        row.connected_at = now
        row.last_verified_at = now
        row.save()
        record_audit_event(
            action="communications.connection.connected",
            resource=row,
            actor=actor,
            changes={"channel": channel, "account": row.external_account_id},
        )
    return row


def verify(*, connection: ChannelConnection, actor: User | None) -> ChannelConnection:
    """Ask Meta whether the stored token still works and still has messaging permission."""
    if connection.disconnected_at is not None:
        raise ConflictError("This connection is disconnected.", "connection_disconnected")
    status, error = ConnectionStatus.CONNECTED, ""
    if not connection.is_simulated:
        try:
            token = meta.connection_token(connection)
            client = meta.graph_client(connection)
            client.request(
                "GET",
                urllib.parse.quote(connection.external_account_id, safe=""),
                token=token,
                params={"fields": "id"},
            )
            if settings.META_APP_ID and settings.META_APP_SECRET:
                debug = _debug(token)
                if debug and not debug.get("is_valid", True):
                    status, error = ConnectionStatus.REVOKED, "token_invalid"
                elif debug and not REQUIRED_SCOPES[ChannelType(connection.channel)] <= _granted(
                    debug
                ):
                    status, error = ConnectionStatus.PERMISSION_MISSING, "scope_missing"
        except meta.GraphError as exc:
            status = {
                "revoked": ConnectionStatus.REVOKED,
                "permission": ConnectionStatus.PERMISSION_MISSING,
                "suspended": ConnectionStatus.SUSPENDED,
            }.get(exc.kind, ConnectionStatus.ERROR)
            error = exc.code
    with transaction.atomic():
        row = ChannelConnection.objects.select_for_update().get(pk=connection.pk)
        before = row.status
        row.status = status
        row.last_error = error[:60]
        row.last_verified_at = timezone.now()
        row.save(update_fields=["status", "last_error", "last_verified_at", "updated_at"])
        if before != status:
            record_audit_event(
                action="communications.connection.verified",
                resource=row,
                actor=actor,
                changes={"status": {"from": before, "to": status}},
            )
    return row


def set_enabled(*, connection: ChannelConnection, enabled: bool, actor: User) -> ChannelConnection:
    with transaction.atomic():
        row = ChannelConnection.objects.select_for_update().get(pk=connection.pk)
        if row.disconnected_at is not None:
            raise ConflictError("This connection is disconnected.", "connection_disconnected")
        before = row.is_enabled
        row.is_enabled = enabled
        row.save(update_fields=["is_enabled", "updated_at"])
        record_audit_event(
            action="communications.connection.enabled_changed",
            resource=row,
            actor=actor,
            changes={"is_enabled": {"from": before, "to": enabled}},
        )
    return row


def disconnect(*, connection: ChannelConnection, actor: User) -> ChannelConnection:
    """Stop all future sending at once and erase the token. Meta is asked to drop the webhook
    subscription on a best-effort basis; history stays with the store."""
    token = ""
    if connection.access_token_encrypted and not connection.is_simulated:
        try:
            token = meta.connection_token(connection)
        except meta.GraphError:
            token = ""
    with transaction.atomic():
        row = ChannelConnection.objects.select_for_update().get(pk=connection.pk)
        if row.disconnected_at is not None:
            return row
        row.status = ConnectionStatus.DISCONNECTED
        row.is_enabled = False
        row.access_token_encrypted = ""
        row.token_expires_at = None
        row.disconnected_at = timezone.now()
        row.save()
        record_audit_event(
            action="communications.connection.disconnected",
            resource=row,
            actor=actor,
            changes={"channel": row.channel, "account": row.external_account_id},
        )
    if token:
        target = (
            row.business_account_id
            if row.channel in (ChannelType.WHATSAPP, ChannelType.INSTAGRAM)
            else row.external_account_id
        )
        try:
            meta.graph_client(row).request(
                "DELETE", f"{urllib.parse.quote(target, safe='')}/subscribed_apps", token=token
            )
        except meta.GraphError:
            logger.info("Webhook unsubscription not confirmed (channel=%s)", row.channel)
    return row


def sync_templates(*, connection: ChannelConnection, actor: User) -> int:
    """Copy the WhatsApp Business Account's templates as Meta reports them, including their
    approval status. Templates are created and approved in Meta's tools, never here."""
    if connection.channel != ChannelType.WHATSAPP or not connection.can_send:
        raise ConflictError("Connect WhatsApp first.", "connection_unavailable")
    if connection.is_simulated:
        rows: list[dict[str, Any]] = [
            {
                "name": "order_update",
                "language": "en",
                "category": "UTILITY",
                "status": "APPROVED",
                "components": [
                    {"type": "BODY", "text": "Hello {{1}}, there is an update on your order {{2}}."}
                ],
            }
        ]
    else:
        try:
            result = meta.graph_client(connection).request(
                "GET",
                f"{urllib.parse.quote(connection.business_account_id, safe='')}/message_templates",
                token=meta.connection_token(connection),
                params={"fields": "name,language,status,category,components", "limit": "200"},
            )
        except meta.GraphError as exc:
            raise ConflictError(
                "Meta did not return the templates. Verify the connection.", exc.code
            ) from None
        rows = [row for row in result.get("data") or [] if isinstance(row, dict)]
    now = timezone.now()
    seen: set[tuple[str, str]] = set()
    with transaction.atomic():
        for item in rows[:500]:
            name, language = str(item.get("name") or "")[:512], str(item.get("language") or "")[:15]
            if not name or not language:
                continue
            body = next(
                (
                    str(part.get("text") or "")
                    for part in item.get("components") or []
                    if isinstance(part, dict) and str(part.get("type")).upper() == "BODY"
                ),
                "",
            )[:1024]
            values = {
                "category": str(item.get("category") or "").upper()[:30],
                "status": str(item.get("status") or "").upper()[:30],
                "body_text": body,
                "parameter_count": body.count("{{"),
                "synced_at": now,
            }
            seen.add((name, language))
            updated = WhatsAppTemplate.objects.filter(
                connection=connection, name=name, language=language
            ).update(**values, updated_at=now)
            if not updated:
                try:
                    with transaction.atomic():
                        WhatsAppTemplate.objects.create(
                            connection=connection, name=name, language=language, **values
                        )
                except IntegrityError:
                    pass
        for template in WhatsAppTemplate.objects.filter(connection=connection):
            if (template.name, template.language) not in seen:
                template.status = "DELETED"
                template.synced_at = now
                template.save(update_fields=["status", "synced_at", "updated_at"])
        record_audit_event(
            action="communications.templates.synced",
            resource=connection,
            actor=actor,
            changes={"count": len(seen)},
        )
    return len(seen)


def connect_simulator(*, channel: str, actor: User) -> ChannelConnection:
    """Development only (D-097): a clearly labelled connection that never reaches Meta."""
    if not settings.MATECART_CHANNEL_SIMULATOR:
        raise ConflictError("The channel simulator is not available here.", "simulator_disabled")
    if channel not in EXTERNAL_CHANNELS:
        raise DomainError("Choose Messenger, Instagram or WhatsApp.", "unknown_channel")
    account = f"sim-{channel}"
    with transaction.atomic():
        current = live(channel)
        if current is not None and not current.is_simulated:
            raise ConflictError(
                "A real account is connected for this channel.", "already_connected"
            )
        row = (
            ChannelConnection.objects.select_for_update()
            .filter(channel=channel, external_account_id=account)
            .first()
        ) or ChannelConnection(channel=channel, external_account_id=account)
        row.business_account_id = f"sim-{channel}-business"
        row.display_name = "Development simulator"
        row.display_identifier = {"whatsapp": "+880 1700-000000"}.get(channel, "")
        row.access_token_encrypted = security.encrypt("simulated")
        row.status = ConnectionStatus.CONNECTED
        row.is_enabled = True
        row.is_simulated = True
        row.connected_by = actor
        row.connected_at = row.last_verified_at = timezone.now()
        row.disconnected_at = None
        row.full_clean()
        row.save()
        record_audit_event(
            action="communications.connection.simulator_connected",
            resource=row,
            actor=actor,
            changes={"channel": channel},
        )
    return row


def token_expiry_warning(connection: ChannelConnection) -> bool:
    return bool(
        connection.token_expires_at
        and connection.token_expires_at - timezone.now() < timedelta(days=7)
    )
