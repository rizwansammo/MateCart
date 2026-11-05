"""Builders for Phase 14 communications tests: channels, signed Meta webhooks, a scripted Graph
client and staff/customer clients. Nothing here reaches Meta."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from rest_framework.test import APIClient

from commerce.communications import meta
from commerce.communications.models import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
    Participant,
)
from commerce.core import security
from commerce.identity import sessions
from commerce.identity.models import AccountType, User
from commerce.staff.models import StaffMembership
from commerce.staff.permissions import StaffRole
from commerce.stores.models import StoreModule
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_user

APP_SECRET = "test-app-secret-not-real"
VERIFY_TOKEN = "test-verify-token"
WEBHOOK = "/api/webhooks/meta/"
INBOX = "/api/manage/inbox/"


def enable_live_chat(tenant: Tenant, on: bool = True) -> None:
    with tenant_context(tenant):
        StoreModule.objects.update_or_create(key="live_chat", defaults={"enabled": on})


def staff(tenant: Tenant, role: str = StaffRole.OWNER, name: str = "") -> tuple[APIClient, User]:
    client = APIClient()
    user = make_user(
        tenant,
        account_type=AccountType.STAFF,
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.test",
        password="staff-passphrase-1234",
        full_name=name or role.replace("_", " ").title(),
    )
    with tenant_context(tenant):
        StaffMembership.objects.create(user=user, role=role)
        _, token = sessions.start(user)
    client.cookies["mc_staff"] = token
    return client, user


def customer(tenant: Tenant, name: str = "Rina Akter") -> tuple[APIClient, User, str]:
    client = APIClient()
    user = make_user(
        tenant,
        account_type=AccountType.CUSTOMER,
        email=f"c-{uuid.uuid4().hex[:8]}@example.test",
        password="customer-passphrase-1234",
        full_name=name,
    )
    with tenant_context(tenant):
        _, token = sessions.start(user)
    client.cookies["mc_customer"] = token
    return client, user, token


def connection(
    tenant: Tenant,
    channel: str = ChannelType.WHATSAPP,
    *,
    account: str = "",
    business: str = "",
    simulated: bool = False,
    status: str = ConnectionStatus.CONNECTED,
) -> ChannelConnection:
    defaults = {
        ChannelType.WHATSAPP: ("1066000001", "2077000001"),
        ChannelType.MESSENGER: ("3088000001", ""),
        ChannelType.INSTAGRAM: ("1784000001", "3088000002"),
    }[ChannelType(channel)]
    with tenant_context(tenant):
        return ChannelConnection.objects.create(
            channel=channel,
            status=status,
            external_account_id=account or defaults[0],
            business_account_id=business or defaults[1],
            display_name="Kantha & Co",
            access_token_encrypted=security.encrypt("page-or-business-token"),
            is_simulated=simulated,
        )


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_webhook(client: APIClient, payload: dict[str, Any], *, secret: str = APP_SECRET) -> Any:
    body = json.dumps(payload).encode()
    return client.post(
        WEBHOOK,
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sign(body, secret),
    )


def wa_text(
    phone_id: str = "1066000001",
    sender: str = "8801711000001",
    text: str = "Is the blue kurti available?",
    mid: str = "",
    name: str = "Sadia Rahman",
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "2077000001",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "8801700000000",
                                "phone_number_id": phone_id,
                            },
                            "contacts": [{"profile": {"name": name}, "wa_id": sender}],
                            "messages": [
                                {
                                    "from": sender,
                                    "id": mid or f"wamid.{uuid.uuid4().hex}",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def wa_media(
    kind: str = "image", media_id: str = "media-1", phone_id: str = "1066000001"
) -> dict[str, Any]:
    payload = wa_text(phone_id=phone_id)
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message.pop("text")
    message["type"] = kind
    message[kind] = {"id": media_id, "mime_type": "image/jpeg", "caption": "this one"}
    return payload


def wa_status(
    wamid: str, status: str, phone_id: str = "1066000001", code: int | None = None
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": wamid,
        "status": status,
        "timestamp": str(int(time.time())),
        "recipient_id": "8801711000001",
        "pricing": {"billable": True, "category": "service", "pricing_model": "PMP"},
    }
    if code is not None:
        entry["errors"] = [{"code": code, "title": "error"}]
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "2077000001",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_id},
                            "statuses": [entry],
                        },
                    }
                ],
            }
        ],
    }


def messenger_text(
    page_id: str = "3088000001",
    psid: str = "6100000000001",
    text: str = "Do you ship to Sylhet?",
    mid: str = "",
    obj: str = "page",
) -> dict[str, Any]:
    return {
        "object": obj,
        "entry": [
            {
                "id": page_id,
                "time": int(time.time() * 1000),
                "messaging": [
                    {
                        "sender": {"id": psid},
                        "recipient": {"id": page_id},
                        "timestamp": int(time.time() * 1000),
                        "message": {"mid": mid or f"m_{uuid.uuid4().hex}", "text": text},
                    }
                ],
            }
        ],
    }


@dataclass
class FakeGraph:
    """Scripted Graph API: records calls; ``responses`` maps path suffixes to results or errors."""

    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        params: Any = None,
        body: Any = None,
        form: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            {"method": method, "path": path, "token": token, "params": params, "body": body}
        )
        for suffix, result in self.responses.items():
            if path.endswith(suffix):
                if isinstance(result, Exception):
                    raise result
                if callable(result):
                    value: dict[str, Any] = result(body)
                    return value
                return dict(result)
        if path.endswith("/messages"):
            if body and body.get("messaging_product") == "whatsapp":
                return {"messages": [{"id": f"wamid.out-{len(self.calls)}"}]}
            return {"message_id": f"m_out-{len(self.calls)}", "recipient_id": "x"}
        return {"success": True}


def use_graph(monkeypatch: Any, fake: FakeGraph) -> FakeGraph:
    monkeypatch.setattr(meta, "graph_client", lambda connection=None: fake)
    return fake


def graph_error(kind: str, code: str = "meta_test") -> meta.GraphError:
    return meta.GraphError(kind, code)


def participant_for(tenant: Tenant, conn: ChannelConnection, external_id: str) -> Participant:
    with tenant_context(tenant):
        return Participant.objects.get(connection=conn, external_id=external_id)


def in_tenant[T](tenant: Tenant, action: Callable[[], T]) -> T:
    with tenant_context(tenant):
        return action()


@pytest.fixture
def run_on_commit(django_capture_on_commit_callbacks: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test-client request runs its on-commit callbacks (delivery, media, events) as a
    committed request would, like ``helpers.Api``."""
    original = APIClient.request

    def request(self: APIClient, **kwargs: Any) -> Any:
        with django_capture_on_commit_callbacks(execute=True):
            return original(self, **kwargs)

    monkeypatch.setattr(APIClient, "request", request)
