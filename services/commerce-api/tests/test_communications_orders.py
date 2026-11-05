"""Phase 14 social commerce (orders from conversations through the D-077 assisted engine) and the
website chat HTTP API (guest credentials, history, attachments, account linking, settings)."""

from __future__ import annotations

import io
import uuid
from decimal import Decimal
from typing import Any

import pytest
from rest_framework.test import APIClient

from commerce.communications import livechat, services
from commerce.communications.models import (
    ChannelType,
    Conversation,
    ConversationEvent,
    ConversationOrderLink,
    Message,
    MessageAttachment,
    Participant,
)
from commerce.identity.models import User
from commerce.inventory.models import StockLevel
from commerce.orders.models import AssistedOrder, Order
from commerce.staff import permissions
from commerce.staff.permissions import Permission, StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import png_bytes
from .comms_helpers import (  # noqa: F401 (run_on_commit is a fixture)
    INBOX,
    connection,
    customer,
    enable_live_chat,
    run_on_commit,
    staff,
)
from .factories import make_store_settings
from .test_checkout import product
from .test_merchant_dashboard import TOTALS, details, eligible_method
from .test_orders import stock

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("run_on_commit")]


@pytest.fixture
def shop(store: Any, deployment_tenant: Tenant) -> Tenant:
    enable_live_chat(deployment_tenant)
    return deployment_tenant


def whatsapp_conversation(tenant: Tenant) -> Conversation:
    wa = connection(tenant)
    with tenant_context(tenant):
        person = Participant.objects.create(
            channel="whatsapp", connection=wa, external_id="8801711000001", display_name="Sadia"
        )
        services.receive_inbound(
            participant=person, kind="text", body="I want 2 kurtis", external_id="wamid.order1"
        )
        return Conversation.objects.get(participant=person)


def order_body(
    client: APIClient, variant: Any, quantity: str = "2", **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "items": [{"variant": str(variant.pk), "quantity": quantity}],
        **details(**overrides),
        "shipping_method": eligible_method(),
    }
    body.pop("source")
    body.pop("source_reference")
    quote = client.post("/api/manage/orders/assisted/quote/", body, format="json")
    totals = quote.data["totals"]
    body["expected_totals"] = {field: totals[field] for field in TOTALS}
    return body


# --- Orders from conversations --------------------------------------------------------------------


def test_whatsapp_conversation_becomes_a_linked_cod_order(shop: Tenant) -> None:
    conversation = whatsapp_conversation(shop)
    variant = product(shop, tracked=True, amount="850.00")
    stock(shop, variant, 5)
    client, handler = staff(shop, StaffRole.ORDER_HANDLER)
    with tenant_context(shop):
        accounts = User.objects.count()
    body = order_body(client, variant)
    url = f"{INBOX}conversations/{conversation.pk}/orders/"
    created = client.post(url, body, format="json")
    assert created.status_code == 201, created.data
    data = created.data
    assert data["source"] == "whatsapp"
    assert data["payment_method"] == "cod"
    assert data["payment_status"] == "unpaid"
    assert data["customer"] is None
    assert data["assisted"]["customer_request_attested"] is True
    assert data["assisted"]["source_reference"].startswith("Conversation ")
    replay = client.post(url, body, format="json")
    assert replay.status_code == 200
    assert replay.data["id"] == data["id"]
    with tenant_context(shop):
        order = Order.objects.get()
        assert User.objects.count() == accounts  # no account was created
        link = ConversationOrderLink.objects.get()
        assert link.order_id == order.pk
        assert link.kind == "created"
        assert link.created_by_id == handler.pk
        assert AssistedOrder.objects.get(order=order).created_by_id == handler.pk
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(2)
        assert ConversationEvent.objects.filter(kind="order_linked", order=order).count() == 1
        # The customer received nothing automatically.
        assert not Message.objects.filter(direction="outbound").exists()
    detail = client.get(f"{INBOX}conversations/{conversation.pk}/")
    assert [o["number"] for o in detail.data["orders"]] == [order.number]


@pytest.mark.parametrize(
    ("channel", "source"),
    [
        (ChannelType.MESSENGER, "facebook"),
        (ChannelType.INSTAGRAM, "instagram"),
        (ChannelType.WEBSITE, "live_chat"),
    ],
)
def test_order_source_follows_the_channel(shop: Tenant, channel: str, source: str) -> None:
    variant = product(shop, amount="100.00")
    client, _ = staff(shop)
    with tenant_context(shop):
        if channel == ChannelType.WEBSITE:
            visitor = livechat.resolve_visitor(guest_token=None, customer=None, create=True)
            assert visitor.participant is not None
            person = visitor.participant
        else:
            conn = connection(shop, channel)
            person = Participant.objects.create(channel=channel, connection=conn, external_id="77")
        services.receive_inbound(
            participant=person, kind="text", body="buy", client_key="buy-key-01"
        )
        conversation = Conversation.objects.get()
    response = client.post(
        f"{INBOX}conversations/{conversation.pk}/orders/",
        order_body(client, variant, "1"),
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["source"] == source


def test_order_rules_of_the_assisted_engine_still_apply(shop: Tenant) -> None:
    conversation = whatsapp_conversation(shop)
    variant = product(shop, tracked=True, amount="500.00")
    stock(shop, variant, 1)
    client, _ = staff(shop)
    url = f"{INBOX}conversations/{conversation.pk}/orders/"
    unattested = order_body(client, variant, "1", customer_request_attested=False)
    assert client.post(url, unattested, format="json").status_code == 400
    online = order_body(client, variant, "1", payment_method="online")
    assert client.post(url, online, format="json").status_code == 400
    changed = order_body(client, variant, "1")
    changed["expected_totals"]["grand_total"] = "1.0000"
    stale = client.post(url, changed, format="json")
    assert stale.status_code == 409
    assert stale.data["error"]["code"] == "quote_changed"
    too_many = order_body(client, variant, "1")
    too_many["items"][0]["quantity"] = "5"
    assert client.post(url, too_many, format="json").status_code in (400, 409)
    with tenant_context(shop):
        assert Order.objects.count() == 0
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(0)


def test_creating_orders_needs_order_permission_too(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = whatsapp_conversation(shop)
    variant = product(shop, amount="100.00")
    owner, _ = staff(shop)
    body = order_body(owner, variant, "1")
    reduced = dict(permissions.ROLE_PERMISSIONS)
    reduced[StaffRole.ORDER_HANDLER] = frozenset({Permission.MESSAGES_MANAGE})
    monkeypatch.setattr(permissions, "ROLE_PERMISSIONS", reduced)
    replier, _ = staff(shop, StaffRole.ORDER_HANDLER)
    assert replier.get(f"{INBOX}conversations/").status_code == 200
    refused = replier.post(f"{INBOX}conversations/{conversation.pk}/orders/", body, format="json")
    assert refused.status_code == 403
    detail = replier.get(f"{INBOX}conversations/{conversation.pk}/")
    assert detail.data["can_create_orders"] is False
    assert detail.data["orders"] == []
    editor, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    assert (
        editor.post(
            f"{INBOX}conversations/{conversation.pk}/orders/", body, format="json"
        ).status_code
        == 403
    )


def test_orders_cannot_be_created_from_closed_conversations(shop: Tenant) -> None:
    conversation = whatsapp_conversation(shop)
    variant = product(shop, amount="100.00")
    client, owner = staff(shop)
    with tenant_context(shop):
        services.change_status(
            conversation=conversation, actor=owner, status="closed", expected_version=1
        )
    response = client.post(
        f"{INBOX}conversations/{conversation.pk}/orders/",
        order_body(client, variant, "1"),
        format="json",
    )
    assert response.status_code == 409


def test_linking_existing_orders_stays_inside_the_store(shop: Tenant, tenant_b: Tenant) -> None:
    conversation = whatsapp_conversation(shop)
    variant = product(shop, amount="100.00")
    client, _ = staff(shop)
    made = client.post(
        "/api/manage/orders/assisted/",
        {**order_body(client, variant, "1"), "source": "phone"},
        format="json",
    )
    assert made.status_code == 201, made.data
    linked = client.post(
        f"{INBOX}conversations/{conversation.pk}/orders/link/",
        {"order": made.data["id"]},
        format="json",
    )
    assert linked.status_code == 200
    assert linked.data["number"] == made.data["number"]
    missing = client.post(
        f"{INBOX}conversations/{conversation.pk}/orders/link/",
        {"order": str(uuid.uuid4())},
        format="json",
    )
    assert missing.status_code == 400


# --- Website chat HTTP ----------------------------------------------------------------------------


def test_chat_endpoints_are_closed_while_live_chat_is_off(
    store: Any, api_client: APIClient
) -> None:
    assert api_client.get("/api/chat/config/").status_code == 403
    sent = api_client.post(
        "/api/chat/messages/", {"client_key": "k-00000001", "body": "x"}, format="json"
    )
    assert sent.status_code == 403


def test_guest_chat_uses_a_scoped_httponly_credential(shop: Tenant) -> None:
    browser = APIClient()
    config = browser.get("/api/chat/config/", {"locale": "bn"})
    assert config.status_code == 200
    assert config.data["config"]["online"] is False  # nobody has the inbox open
    assert config.data["messages"] == []
    assert config.data["conversation_status"] is None
    assert "mc_chat" not in config.cookies  # no identity until the visitor writes
    first = browser.post(
        "/api/chat/messages/", {"client_key": "guest-key-01", "body": "Hello"}, format="json"
    )
    assert first.status_code == 201, first.data
    cookie = first.cookies["mc_chat"]
    assert cookie["httponly"]
    assert cookie["path"] == "/api/chat"
    assert cookie["samesite"] == "Lax"
    browser.cookies["mc_chat"] = cookie.value
    browser.post(
        "/api/chat/messages/", {"client_key": "guest-key-02", "body": "Anyone?"}, format="json"
    )
    history = browser.get("/api/chat/config/")
    assert [m["body"] for m in history.data["messages"]] == ["Hello", "Anyone?"]
    assert history.data["conversation_status"] == "open"
    stranger = APIClient()
    assert stranger.get("/api/chat/config/").data["messages"] == []
    forged = APIClient()
    forged.cookies["mc_chat"] = "x" * 43
    assert forged.get("/api/chat/config/").data["messages"] == []
    with tenant_context(shop):
        assert Participant.objects.count() == 1
        assert Participant.objects.get().token_hash != cookie.value
        assert not User.objects.filter(account_type="customer").exists()


def test_visitor_attachments_are_validated_and_private(shop: Tenant) -> None:
    alice = APIClient()
    upload = alice.post(
        "/api/chat/attachments/",
        {"file": io.BytesIO(png_bytes()), "client_key": "photo-key-01"},
        format="multipart",
    )
    assert upload.status_code == 201, upload.data
    alice.cookies["mc_chat"] = upload.cookies["mc_chat"].value
    attachment = upload.data["attachment"]
    assert attachment["content_type"] == "image/png"
    assert attachment["url"] == f"/api/chat/attachments/{attachment['id']}/"
    fetched = alice.get(attachment["url"])
    assert fetched.status_code == 200
    assert {"private", "no-store"} <= {part.strip() for part in fetched["Cache-Control"].split(",")}
    bob = APIClient()
    bob.post("/api/chat/messages/", {"client_key": "bob-key-0001", "body": "hi"}, format="json")
    assert bob.get(attachment["url"]).status_code == 404
    evil = alice.post(
        "/api/chat/attachments/",
        {"file": io.BytesIO(b"MZ\x90\x00 not an image"), "client_key": "exe-key-001"},
        format="multipart",
    )
    assert evil.status_code == 400
    owner, _ = staff(shop)
    assert owner.get(f"{INBOX}attachments/{attachment['id']}/").status_code == 200
    with tenant_context(shop):
        assert MessageAttachment.objects.count() == 1


def test_guest_chat_joins_an_account_only_when_the_visitor_confirms(shop: Tenant) -> None:
    browser = APIClient()
    sent = browser.post(
        "/api/chat/messages/", {"client_key": "pre-login-1", "body": "Hi"}, format="json"
    )
    guest_token = sent.cookies["mc_chat"].value
    signed_in, user, _token = customer(shop)
    signed_in.cookies["mc_chat"] = guest_token
    state = signed_in.get("/api/chat/config/")
    assert state.data["signed_in"] is True
    assert state.data["guest_chat_linkable"] is True
    with tenant_context(shop):
        guest = Participant.objects.get(token_hash__gt="")
        assert guest.customer_id is None  # nothing happened automatically
    assert signed_in.post("/api/chat/link-account/").status_code == 204
    with tenant_context(shop):
        guest.refresh_from_db()
        assert guest.customer_id == user.pk
    anonymous = APIClient()
    anonymous.cookies["mc_chat"] = guest_token
    assert anonymous.post("/api/chat/link-account/").status_code == 409


def test_live_chat_settings_are_validated(shop: Tenant) -> None:
    client, _ = staff(shop)
    current = client.get("/api/manage/communications/settings/").data
    assert current["live_chat_enabled"] is True
    assert current["timezone"] == "Asia/Dhaka"
    payload = {**current["live_chat"], "hours": {"mon": [["18:00", "09:00"]]}}
    assert (
        client.put("/api/manage/communications/live-chat/", payload, format="json").status_code
        == 400
    )
    payload["hours"] = {"mon": [["09:00", "18:00"]], "fri": []}
    payload["welcome_en"] = "<b>Welcome</b>"
    saved = client.put("/api/manage/communications/live-chat/", payload, format="json")
    assert saved.status_code == 200, saved.data
    assert saved.data["live_chat"]["hours"] == {"mon": [["09:00", "18:00"]]}
    assert saved.data["live_chat"]["welcome_en"] == "<b>Welcome</b>"  # stored as literal text
    handler, _ = staff(shop, StaffRole.ORDER_HANDLER)
    assert (
        handler.put("/api/manage/communications/live-chat/", payload, format="json").status_code
        == 403
    )


def test_store_without_messages_is_isolated_from_another_store(
    shop: Tenant, tenant_b: Tenant
) -> None:
    make_store_settings(tenant_b)
    browser = APIClient()
    browser.post("/api/chat/messages/", {"client_key": "iso-key-001", "body": "Hi"}, format="json")
    with tenant_context(tenant_b):
        assert Participant.objects.count() == 0
        assert Message.objects.count() == 0
