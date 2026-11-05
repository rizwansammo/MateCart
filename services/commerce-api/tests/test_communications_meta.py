"""Phase 14 Meta integrations: webhook verification, signatures, inbound messages, receipts,
outbound delivery outcomes, templates, connection authorization and media safety. A scripted
Graph client stands in for Meta; nothing here reaches the network."""

from __future__ import annotations

import json
import socket
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.cache import cache
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.communications import connections, meta, tasks
from commerce.communications.models import (
    AttachmentStatus,
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
    Conversation,
    DeliveryAttempt,
    DeliveryStatus,
    Message,
    MessageAttachment,
    Participant,
    WhatsAppTemplate,
)
from commerce.identity.models import AuthSession
from commerce.notifications.models import Notification
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import png_bytes
from .comms_helpers import (  # noqa: F401 (run_on_commit is a fixture)
    APP_SECRET,
    INBOX,
    VERIFY_TOKEN,
    WEBHOOK,
    FakeGraph,
    connection,
    graph_error,
    messenger_text,
    post_webhook,
    run_on_commit,
    staff,
    use_graph,
    wa_media,
    wa_status,
    wa_text,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("run_on_commit")]


@pytest.fixture
def meta_app(settings: Settings, store: Any) -> Settings:
    settings.META_APP_ID = "1234567890"
    settings.META_APP_SECRET = APP_SECRET
    settings.META_WEBHOOK_VERIFY_TOKEN = VERIFY_TOKEN
    settings.MATECART_PUBLIC_BASE_URL = "https://shop.example.com"
    return settings


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> FakeGraph:
    return use_graph(monkeypatch, FakeGraph())


# --- Webhook verification -------------------------------------------------------------------------


def test_webhook_challenge_requires_the_verify_token(
    meta_app: Settings, api_client: APIClient
) -> None:
    ok = api_client.get(
        WEBHOOK,
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "12345"},
    )
    assert ok.status_code == 200
    assert ok.content == b"12345"
    wrong = api_client.get(
        WEBHOOK, {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
    )
    assert wrong.status_code == 403


def test_unsigned_or_forged_webhooks_are_rejected(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient
) -> None:
    connection(deployment_tenant)
    body = json.dumps(wa_text()).encode()
    unsigned = api_client.post(WEBHOOK, data=body, content_type="application/json")
    forged = post_webhook(api_client, wa_text(), secret="attacker-secret")
    assert unsigned.status_code == 401
    assert forged.status_code == 401
    with tenant_context(deployment_tenant):
        assert Message.objects.count() == 0


def test_webhooks_are_refused_without_an_app_secret(
    store: Any, deployment_tenant: Tenant, api_client: APIClient
) -> None:
    connection(deployment_tenant)
    assert post_webhook(api_client, wa_text()).status_code == 401


def test_oversized_webhook_is_refused(meta_app: Settings, api_client: APIClient) -> None:
    payload = wa_text(text="x" * (1024 * 1024))
    assert post_webhook(api_client, payload).status_code == 413


# --- Inbound --------------------------------------------------------------------------------------


def test_whatsapp_message_is_stored_once_and_alerts_once(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient
) -> None:
    staff(deployment_tenant)
    wa = connection(deployment_tenant)
    payload = wa_text(mid="wamid.ABC", text="Is size M available?")
    first = post_webhook(api_client, payload)
    replay = post_webhook(api_client, payload)
    assert first.status_code == 200
    assert replay.status_code == 200
    assert json.loads(replay.content)["duplicates"] == 1
    with tenant_context(deployment_tenant):
        message = Message.objects.get()
        person = Participant.objects.get()
        assert message.body == "Is size M available?"
        assert message.external_id == "wamid.ABC"
        assert message.connection_id == wa.pk
        assert message.conversation.channel == "whatsapp"
        assert person.display_name == "Sadia Rahman"
        assert person.phone == "+8801711000001"
        # A WhatsApp number is never turned into a customer account.
        assert person.customer_id is None
        assert Notification.objects.filter(kind="staff.conversation.new").count() == 1


def test_same_provider_id_on_another_connection_is_a_different_message(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient
) -> None:
    connection(deployment_tenant)
    connection(deployment_tenant, ChannelType.MESSENGER)
    post_webhook(api_client, wa_text(mid="shared-id"))
    post_webhook(api_client, messenger_text(mid="shared-id"))
    with tenant_context(deployment_tenant):
        assert Message.objects.filter(external_id="shared-id").count() == 2
        assert Conversation.objects.count() == 2


def test_events_for_unknown_accounts_are_ignored(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient
) -> None:
    connection(deployment_tenant)
    response = post_webhook(api_client, wa_text(phone_id="9999999999"))
    assert response.status_code == 200
    with tenant_context(deployment_tenant):
        assert Message.objects.count() == 0
        assert Participant.objects.count() == 0


def test_webhook_cannot_reach_another_stores_connection(
    meta_app: Settings, deployment_tenant: Tenant, tenant_b: Tenant, api_client: APIClient
) -> None:
    from .factories import make_store_settings

    make_store_settings(tenant_b)
    connection(tenant_b, account="5550001")
    post_webhook(api_client, wa_text(phone_id="5550001"))
    with tenant_context(tenant_b):
        assert Message.objects.count() == 0


def test_messenger_and_instagram_messages_are_mapped(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient
) -> None:
    connection(deployment_tenant, ChannelType.MESSENGER)
    connection(deployment_tenant, ChannelType.INSTAGRAM)
    post_webhook(api_client, messenger_text(psid="61001", text="Hello page"))
    post_webhook(
        api_client,
        messenger_text(page_id="1784000001", psid="17841", text="Hi IG", obj="instagram"),
    )
    echo = messenger_text()
    echo["entry"][0]["messaging"][0]["message"]["is_echo"] = True
    post_webhook(api_client, echo)
    with tenant_context(deployment_tenant):
        channels = dict(Message.objects.values_list("body", "conversation__channel"))
    assert channels == {"Hello page": "messenger", "Hi IG": "instagram"}


def test_inbound_media_is_downloaded_safely_and_validated(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection(deployment_tenant)
    graph.responses["media-1"] = {"url": "https://lookaside.fbsbx.com/whatsapp_business/x?mid=1"}
    seen: dict[str, Any] = {}

    def fake_download(url: str, *, token: str = "", limit: int, timeout: float = 15) -> bytes:
        seen.update(url=url, token=token)
        meta.allowed_media_url(url)
        return png_bytes()

    monkeypatch.setattr(meta, "download", fake_download)
    post_webhook(api_client, wa_media())
    with tenant_context(deployment_tenant):
        row = MessageAttachment.objects.get()
        assert row.status == AttachmentStatus.STORED
        assert row.content_type == "image/png"
        assert row.source_url == ""
        assert row.provider_media_id == ""
        assert Message.objects.get().kind == "image"
    assert seen["token"] == "page-or-business-token"  # header token, never in the URL
    assert "token" not in seen["url"]
    client, _ = staff(deployment_tenant)
    file = client.get(f"{INBOX}attachments/{row.pk}/")
    assert file.status_code == 200
    assert file["X-Content-Type-Options"] == "nosniff"
    assert api_client.get(f"{INBOX}attachments/{row.pk}/").status_code == 401


def test_media_downloads_refuse_unsafe_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    for url in (
        "http://lookaside.fbsbx.com/x",
        "https://evil.example.com/x",
        "https://fbcdn.net.evil.com/x",
        "https://user:pw@scontent.fbcdn.net/x",
        "https://scontent.fbcdn.net:8443/x",
        "file:///etc/passwd",
    ):
        with pytest.raises(meta.MediaRefused):
            meta.allowed_media_url(url)
    meta.allowed_media_url("https://scontent.xx.fbcdn.net/v/t1/abc.jpg?oh=1")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(meta.MediaRefused) as exc:
        meta.download("https://scontent.xx.fbcdn.net/x.jpg", limit=100)
    assert exc.value.code == "media_private_address"


def test_unsupported_media_bytes_are_not_stored(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection(deployment_tenant)
    graph.responses["media-1"] = {"url": "https://lookaside.fbsbx.com/x"}
    monkeypatch.setattr(meta, "download", lambda *a, **k: b"MZ\x90\x00 executable")
    post_webhook(api_client, wa_media())
    with tenant_context(deployment_tenant):
        row = MessageAttachment.objects.get()
        assert row.status == AttachmentStatus.UNSUPPORTED
        assert not row.file


# --- Outbound -------------------------------------------------------------------------------------


def _inbound_whatsapp(tenant: Tenant, api_client: APIClient) -> Conversation:
    post_webhook(api_client, wa_text())
    with tenant_context(tenant):
        return Conversation.objects.get()


def test_reply_is_sent_once_and_receipts_only_move_forward(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    connection(deployment_tenant)
    conversation = _inbound_whatsapp(deployment_tenant, api_client)
    client, _ = staff(deployment_tenant)
    sent = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "wa-reply-001", "body": "Yes, size M is available."},
        format="json",
    )
    assert sent.status_code == 201
    sends = [c for c in graph.calls if c["path"].endswith("/messages")]
    assert len(sends) == 1
    assert sends[0]["body"]["to"] == "8801711000001"
    assert sends[0]["token"] == "page-or-business-token"
    with tenant_context(deployment_tenant):
        message = Message.objects.get(direction="outbound")
        assert message.delivery_status == DeliveryStatus.ACCEPTED  # not "delivered"
        wamid = message.external_id
    post_webhook(api_client, wa_status(wamid, "read"))
    post_webhook(api_client, wa_status(wamid, "delivered"))  # late receipt
    with tenant_context(deployment_tenant):
        message.refresh_from_db()
        assert message.delivery_status == DeliveryStatus.READ
        assert message.pricing_category == "service"
    # A browser retry with the same key sends nothing new.
    client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "wa-reply-001", "body": "Yes, size M is available."},
        format="json",
    )
    assert len([c for c in graph.calls if c["path"].endswith("/messages")]) == 1


def test_unknown_outcome_is_never_resent_automatically(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    connection(deployment_tenant)
    conversation = _inbound_whatsapp(deployment_tenant, api_client)
    graph.responses["/messages"] = graph_error("unknown", "timeout")
    client, _ = staff(deployment_tenant)
    client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "timeout-001", "body": "Hello"},
        format="json",
    )
    with tenant_context(deployment_tenant):
        message = Message.objects.get(direction="outbound")
        assert message.delivery_status == DeliveryStatus.UNKNOWN
        assert DeliveryAttempt.objects.filter(message=message).count() == 1
        tasks.sweep_tenant(tenant_id=str(deployment_tenant.pk))
        message.refresh_from_db()
        assert message.delivery_status == DeliveryStatus.UNKNOWN
    assert len([c for c in graph.calls if c["path"].endswith("/messages")]) == 1
    # Staff may deliberately resend after seeing the warning.
    graph.responses.pop("/messages")
    resent = client.post(f"{INBOX}messages/{message.pk}/resend/")
    assert resent.status_code == 200
    with tenant_context(deployment_tenant):
        message.refresh_from_db()
        assert message.delivery_status == DeliveryStatus.ACCEPTED


def test_revoked_token_fails_the_message_and_marks_the_connection(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    wa = connection(deployment_tenant)
    conversation = _inbound_whatsapp(deployment_tenant, api_client)
    graph.responses["/messages"] = meta._classify(401, {"error": {"code": 190}})
    client, _ = staff(deployment_tenant)
    client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "revoked-001", "body": "Hello"},
        format="json",
    )
    with tenant_context(deployment_tenant):
        message = Message.objects.get(direction="outbound")
        assert message.delivery_status == DeliveryStatus.FAILED
        assert message.error_code == "meta_190"
        wa.refresh_from_db()
        assert wa.status == ConnectionStatus.REVOKED
    blocked = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "revoked-002", "body": "Hello again"},
        format="json",
    )
    assert blocked.status_code == 409
    assert blocked.data["error"]["code"] == "connection_revoked"


def test_rate_limits_retry_a_bounded_number_of_times(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    connection(deployment_tenant)
    conversation = _inbound_whatsapp(deployment_tenant, api_client)
    graph.responses["/messages"] = meta._classify(429, {"error": {"code": 131056}})
    client, _ = staff(deployment_tenant)
    client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "throttled-1", "body": "Hello"},
        format="json",
    )
    with tenant_context(deployment_tenant):
        message = Message.objects.get(direction="outbound")
        assert message.attempts == tasks.MAX_ATTEMPTS
        assert message.delivery_status == DeliveryStatus.FAILED
        assert message.error_code == "meta_131056"


def test_failed_whatsapp_message_never_falls_back_to_other_channels(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    connection(deployment_tenant)
    conversation = _inbound_whatsapp(deployment_tenant, api_client)
    graph.responses["/messages"] = meta._classify(400, {"error": {"code": 131026}})
    client, _ = staff(deployment_tenant)
    client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "undeliverable", "body": "Hello"},
        format="json",
    )
    with tenant_context(deployment_tenant):
        assert Notification.objects.filter(channel__in=["sms", "email"]).count() == 0


def test_templates_need_approval_utility_category_and_opt_in(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    wa = connection(deployment_tenant)
    conversation = _inbound_whatsapp(deployment_tenant, api_client)
    with tenant_context(deployment_tenant):
        Conversation.objects.filter(pk=conversation.pk).update(
            last_inbound_at=timezone.now() - timedelta(hours=30)
        )
        approved = WhatsAppTemplate.objects.create(
            connection=wa,
            name="order_update",
            language="en",
            category="UTILITY",
            status="APPROVED",
            body_text="Hello {{1}}, order {{2}} is on its way.",
            parameter_count=2,
            synced_at=timezone.now(),
        )
        marketing = WhatsAppTemplate.objects.create(
            connection=wa,
            name="eid_offer",
            language="en",
            category="MARKETING",
            status="APPROVED",
            body_text="Big sale!",
            synced_at=timezone.now(),
        )
        pending = WhatsAppTemplate.objects.create(
            connection=wa,
            name="draft",
            language="en",
            category="UTILITY",
            status="PENDING",
            body_text="x",
            synced_at=timezone.now(),
        )
    client, _ = staff(deployment_tenant)
    url = f"{INBOX}conversations/{conversation.pk}/messages/"

    def send(template: WhatsAppTemplate, key: str, params: list[str]) -> Any:
        return client.post(
            url,
            {"client_key": key, "template": str(template.pk), "template_parameters": params},
            format="json",
        )

    assert send(marketing, "tpl-marketing", []).status_code == 400
    assert send(pending, "tpl-pending-1", []).status_code == 400
    no_opt_in = send(approved, "tpl-no-optin", ["Sadia", "MC-1001"])
    assert no_opt_in.status_code == 409
    assert no_opt_in.data["error"]["code"] == "template_not_allowed"
    assert (
        client.post(
            f"{INBOX}conversations/{conversation.pk}/opt-in/", {"confirmed": True}, format="json"
        ).status_code
        == 204
    )
    assert send(approved, "tpl-bad-params", ["Sadia"]).status_code == 400
    ok = send(approved, "tpl-ok-00001", ["Sadia", "MC-1001"])
    assert ok.status_code == 201, ok.data
    assert ok.data["body"] == "Hello Sadia, order MC-1001 is on its way."
    template_call = [c for c in graph.calls if c["path"].endswith("/messages")][-1]
    assert template_call["body"]["type"] == "template"
    assert template_call["body"]["template"]["name"] == "order_update"
    client.post(f"{INBOX}conversations/{conversation.pk}/opt-out/")
    assert send(approved, "tpl-after-out", ["Sadia", "MC-1001"]).status_code == 409


# --- Connections ----------------------------------------------------------------------------------


def _session(tenant: Tenant, client: APIClient) -> AuthSession:
    from commerce.core.security import hash_token

    with tenant_context(tenant):
        return AuthSession.objects.get(token_hash=hash_token(client.cookies["mc_staff"].value))


def test_connection_flow_is_bound_to_session_and_meta_listed_accounts(
    meta_app: Settings, deployment_tenant: Tenant, graph: FakeGraph
) -> None:
    client, _owner = staff(deployment_tenant)
    graph.responses.update(
        {
            "oauth/access_token": {"access_token": "user-token"},
            "debug_token": {
                "data": {
                    "is_valid": True,
                    "scopes": ["pages_show_list", "pages_manage_metadata", "pages_messaging"],
                }
            },
            "me/accounts": {
                "data": [
                    {
                        "id": "3088000001",
                        "name": "Kantha & Co",
                        "tasks": ["MESSAGING", "ANALYZE"],
                        "access_token": "page-token-secret",
                    },
                    {"id": "999", "name": "Not mine", "tasks": ["ANALYZE"], "access_token": "x"},
                ]
            },
        }
    )
    start = client.post(
        "/api/manage/communications/connections/start/", {"channel": "messenger"}, format="json"
    )
    assert start.status_code == 200, start.data
    query = parse_qs(urlsplit(start.data["authorize_url"]).query)
    assert query["redirect_uri"] == [
        "https://shop.example.com/api/manage/communications/oauth/callback/"
    ]
    state = query["state"][0]
    other, _ = staff(deployment_tenant, StaffRole.MANAGER)
    hijack = other.get(
        "/api/manage/communications/oauth/callback/", {"state": state, "code": "abc"}
    )
    assert hijack.status_code == 302
    assert "invalid_state" in hijack["Location"]
    # The state was single-use: even the right session cannot reuse it after the attempt.
    reuse = client.get("/api/manage/communications/oauth/callback/", {"state": state, "code": "x"})
    assert "invalid_state" in reuse["Location"]
    start = client.post(
        "/api/manage/communications/connections/start/", {"channel": "messenger"}, format="json"
    )
    state = parse_qs(urlsplit(start.data["authorize_url"]).query)["state"][0]
    done = client.get("/api/manage/communications/oauth/callback/", {"state": state, "code": "ok"})
    assert done.status_code == 302
    assert "select=" in done["Location"]
    selection = parse_qs(urlsplit(done["Location"]).query)["select"][0]
    listed = client.get(
        "/api/manage/communications/connections/selection/", {"selection": selection}
    )
    assert [a["id"] for a in listed.data["accounts"]] == ["3088000001"]
    assert "page-token-secret" not in json.dumps(listed.data)
    forged = client.post(
        "/api/manage/communications/connections/select/",
        {"selection": selection, "account": "999"},
        format="json",
    )
    assert forged.status_code == 400
    chosen = client.post(
        "/api/manage/communications/connections/select/",
        {"selection": selection, "account": "3088000001"},
        format="json",
    )
    assert chosen.status_code == 200, chosen.data
    assert chosen.data["state"] == "connected"
    assert "page-token-secret" not in json.dumps(chosen.data)
    assert any(c["path"].endswith("3088000001/subscribed_apps") for c in graph.calls)
    with tenant_context(deployment_tenant):
        row = ChannelConnection.objects.get()
        assert row.access_token_encrypted
        assert "page-token" not in row.access_token_encrypted
        from commerce.audit.models import AuditEvent

        assert "page-token" not in json.dumps(
            list(AuditEvent.objects.values_list("changes", flat=True))
        )
    settings_view = client.get("/api/manage/communications/settings/")
    assert "page-token-secret" not in json.dumps(settings_view.data)


def test_connection_changes_need_settings_permission_and_recent_sign_in(
    meta_app: Settings, deployment_tenant: Tenant, graph: FakeGraph
) -> None:
    handler, _ = staff(deployment_tenant, StaffRole.ORDER_HANDLER)
    assert handler.get("/api/manage/communications/settings/").status_code == 403
    client, _ = staff(deployment_tenant)
    session = _session(deployment_tenant, client)
    with tenant_context(deployment_tenant):
        AuthSession.objects.filter(pk=session.pk).update(
            authenticated_at=timezone.now() - timedelta(hours=1)
        )
    stale = client.post(
        "/api/manage/communications/connections/start/", {"channel": "whatsapp"}, format="json"
    )
    assert stale.status_code == 403
    assert stale.data["error"]["code"] == "reauthentication_required"


def test_unconfigured_meta_is_reported_honestly(store: Any, deployment_tenant: Tenant) -> None:
    client, _ = staff(deployment_tenant)
    data = client.get("/api/manage/communications/settings/").data
    assert {c["state"] for c in data["connections"]} == {"configuration_required"}
    refused = client.post(
        "/api/manage/communications/connections/start/", {"channel": "whatsapp"}, format="json"
    )
    assert refused.status_code == 409
    assert refused.data["error"]["code"] == "meta_not_configured"


def test_disconnect_erases_token_and_stops_sending(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    wa = connection(deployment_tenant)
    conversation = _inbound_whatsapp(deployment_tenant, api_client)
    client, _ = staff(deployment_tenant)
    gone = client.post(f"/api/manage/communications/connections/{wa.pk}/disconnect/")
    assert gone.status_code == 200
    with tenant_context(deployment_tenant):
        wa.refresh_from_db()
        assert wa.access_token_encrypted == ""
        assert wa.disconnected_at is not None
    refused = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "after-disc", "body": "Hello"},
        format="json",
    )
    assert refused.status_code == 409
    # History stays readable after disconnecting.
    assert client.get(f"{INBOX}conversations/{conversation.pk}/thread/").status_code == 200
    # Webhooks for the disconnected account are no longer accepted.
    post_webhook(api_client, wa_text(mid="after-disconnect"))
    with tenant_context(deployment_tenant):
        assert not Message.objects.filter(external_id="after-disconnect").exists()


def test_verify_detects_revoked_and_missing_permissions(
    meta_app: Settings, deployment_tenant: Tenant, graph: FakeGraph
) -> None:
    wa = connection(deployment_tenant)
    _, owner = staff(deployment_tenant)
    graph.responses["debug_token"] = {
        "data": {"is_valid": True, "scopes": ["whatsapp_business_management"]}
    }
    with tenant_context(deployment_tenant):
        assert connections.verify(connection=wa, actor=owner).status == "permission_missing"
    graph.responses["1066000001"] = meta._classify(401, {"error": {"code": 190}})
    with tenant_context(deployment_tenant):
        assert connections.verify(connection=wa, actor=owner).status == "revoked"


def test_simulator_is_development_only(
    store: Any, deployment_tenant: Tenant, settings: Settings
) -> None:
    client, _ = staff(deployment_tenant)
    refused = client.post(
        "/api/manage/communications/simulator/", {"channel": "whatsapp"}, format="json"
    )
    assert refused.status_code == 409
    settings.MATECART_CHANNEL_SIMULATOR = True
    made = client.post(
        "/api/manage/communications/simulator/", {"channel": "whatsapp"}, format="json"
    )
    assert made.status_code == 200
    assert made.data["is_simulated"] is True


def test_template_sync_records_provider_status(
    meta_app: Settings, deployment_tenant: Tenant, graph: FakeGraph
) -> None:
    wa = connection(deployment_tenant)
    client, _ = staff(deployment_tenant)
    graph.responses["message_templates"] = {
        "data": [
            {
                "name": "order_update",
                "language": "en",
                "status": "APPROVED",
                "category": "UTILITY",
                "components": [{"type": "BODY", "text": "Hi {{1}}"}],
            },
            {"name": "promo", "language": "bn", "status": "REJECTED", "category": "MARKETING"},
        ]
    }
    synced = client.post(f"/api/manage/communications/connections/{wa.pk}/templates/sync/")
    assert synced.status_code == 200
    assert synced.data["count"] == 2
    listed = client.get(f"{INBOX}templates/").data
    states = {(row["name"], row["sendable"]) for row in listed}
    assert states == {("order_update", True), ("promo", False)}


def test_graph_errors_are_classified() -> None:
    def kind(status: int, code: int, sub: int = 0) -> str:
        return meta._classify(status, {"error": {"code": code, "error_subcode": sub}}).kind

    assert kind(400, 190) == "revoked"
    assert kind(400, 10, 2018278) == "window"
    assert kind(400, 131047) == "window"
    assert kind(400, 200) == "permission"
    assert kind(429, 4) == "rate_limited"
    assert kind(500, 1) == "unknown"
    assert kind(400, 131026) == "rejected"


def test_cached_selection_keeps_tokens_encrypted(
    meta_app: Settings, deployment_tenant: Tenant, graph: FakeGraph
) -> None:
    client, owner = staff(deployment_tenant)
    session = _session(deployment_tenant, client)
    graph.responses.update(
        {
            "oauth/access_token": {"access_token": "user-token"},
            "debug_token": {"data": {"scopes": ["pages_messaging", "pages_show_list"]}},
            "me/accounts": {
                "data": [
                    {"id": "1", "name": "P", "tasks": ["MESSAGING"], "access_token": "TOKEN-XYZ"}
                ]
            },
        }
    )
    with tenant_context(deployment_tenant):
        connections.start(channel="messenger", actor=owner, session=session)
        url = connections.start(channel="messenger", actor=owner, session=session)
        state = parse_qs(urlsplit(url).query)["state"][0]
        target = connections.callback(state=state, code="c", error="", actor=owner, session=session)
        selection = parse_qs(urlsplit(target).query)["select"][0]
        from commerce.core.security import hash_token

        cached = cache.get(connections._selection_key(hash_token(selection)))
    assert "TOKEN-XYZ" not in json.dumps(cached)
