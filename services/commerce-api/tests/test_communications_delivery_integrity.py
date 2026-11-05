"""Phase 14.1 delivery integrity: WhatsApp template parameters survive every delivery path
(broker outage, sweeper recovery, provider retry, duplicate task runs), and the messaging policy
is checked again immediately before every external send. A scripted Graph client stands in for
Meta; nothing here reaches the network."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.communications import meta, retention, tasks
from commerce.communications.models import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
    Conversation,
    ConversationStatus,
    DeliveryAttempt,
    DeliveryStatus,
    Message,
    WhatsAppTemplate,
)
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .comms_helpers import (  # noqa: F401 (run_on_commit is a fixture)
    APP_SECRET,
    INBOX,
    VERIFY_TOKEN,
    FakeGraph,
    connection,
    graph_error,
    messenger_text,
    post_webhook,
    run_on_commit,
    staff,
    use_graph,
    wa_text,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("run_on_commit")]

PARAMS = ["Sadia", "MC-1001", "Mirpur 10, Dhaka"]


@pytest.fixture
def meta_app(settings: Settings, store: Any) -> Settings:
    settings.META_APP_ID = "1234567890"
    settings.META_APP_SECRET = APP_SECRET
    settings.META_WEBHOOK_VERIFY_TOKEN = VERIFY_TOKEN
    settings.META_HUMAN_AGENT_APPROVED = False
    return settings


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> FakeGraph:
    return use_graph(monkeypatch, FakeGraph())


class Broker:
    """Stands in for the Celery broker: while ``down``, queuing a delivery raises."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.real = tasks.deliver_message
        self.monkeypatch = monkeypatch

    def down(self) -> None:
        class _Unavailable:
            def delay(self, *args: Any, **kwargs: Any) -> None:
                raise ConnectionError("broker unavailable")

            apply_async = delay

        self.monkeypatch.setattr(tasks, "deliver_message", _Unavailable())

    def up(self) -> None:
        self.monkeypatch.setattr(tasks, "deliver_message", self.real)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Broker:
    return Broker(monkeypatch)


def sends(graph: FakeGraph) -> list[dict[str, Any]]:
    return [c for c in graph.calls if c["path"].endswith("/messages")]


def sweep(tenant: Tenant) -> dict[str, int]:
    """Let the queued message age past the sweeper's threshold, then run the sweep."""
    with tenant_context(tenant):
        Message.objects.filter(delivery_status=DeliveryStatus.QUEUED).update(
            status_changed_at=timezone.now() - tasks.STALE_QUEUED - timedelta(seconds=5)
        )
        result: dict[str, int] = tasks.sweep_tenant(tenant_id=str(tenant.pk))
        return result


def whatsapp_conversation(tenant: Tenant, api_client: APIClient) -> Conversation:
    post_webhook(api_client, wa_text())
    with tenant_context(tenant):
        return Conversation.objects.get()


def template_setup(
    tenant: Tenant, api_client: APIClient
) -> tuple[APIClient, Conversation, WhatsAppTemplate]:
    """A WhatsApp conversation outside the 24-hour window with a recorded opt-in."""
    wa = connection(tenant)
    conversation = whatsapp_conversation(tenant, api_client)
    with tenant_context(tenant):
        Conversation.objects.filter(pk=conversation.pk).update(
            last_inbound_at=timezone.now() - timedelta(hours=30)
        )
        template = WhatsAppTemplate.objects.create(
            connection=wa,
            name="order_update",
            language="en",
            category="UTILITY",
            status="APPROVED",
            body_text="Hello {{1}}, order {{2}} is on its way to {{3}}.",
            parameter_count=3,
            synced_at=timezone.now(),
        )
    client, _ = staff(tenant)
    opt_in = client.post(
        f"{INBOX}conversations/{conversation.pk}/opt-in/", {"confirmed": True}, format="json"
    )
    assert opt_in.status_code == 204
    return client, conversation, template


def send_template(
    client: APIClient, conversation: Conversation, template: WhatsAppTemplate, key: str
) -> Any:
    response = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": key, "template": str(template.pk), "template_parameters": PARAMS},
        format="json",
    )
    assert response.status_code == 201, response.data
    return response


def sent_parameters(call: dict[str, Any]) -> list[str]:
    template = call["body"]["template"]
    assert template["name"] == "order_update"
    assert template["language"] == {"code": "en"}
    return [p["text"] for p in template["components"][0]["parameters"]]


def outbound(tenant: Tenant) -> Message:
    with tenant_context(tenant):
        return Message.objects.get(direction="outbound")


# --- Correction 1: template parameters survive every delivery path ------------------------------


def test_normal_template_delivery_sends_parameters_in_order(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    response = send_template(client, conversation, template, "tpl-normal-0001")
    assert "template_parameters" not in response.data  # never echoed to the inbox
    assert [sent_parameters(c) for c in sends(graph)] == [PARAMS]
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.ACCEPTED
    assert message.template_parameters == []  # cleared once the provider has them


def test_broker_outage_then_sweeper_recovery_keeps_template_parameters(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-broker-0001")
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.QUEUED
    assert message.template_parameters == PARAMS  # persisted with the message
    assert sends(graph) == []
    broker.up()
    assert sweep(deployment_tenant)["queued"] == 1
    assert [sent_parameters(c) for c in sends(graph)] == [PARAMS]
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.ACCEPTED
    assert message.template_parameters == []


def test_retry_after_a_temporary_failure_reuses_the_original_parameters(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    failures = {"left": 2}

    def flaky(body: Any) -> dict[str, Any]:
        if failures["left"]:
            failures["left"] -= 1
            raise meta._classify(429, {"error": {"code": 131056}})
        return {"messages": [{"id": "wamid.after-retry"}]}

    graph.responses["/messages"] = flaky
    send_template(client, conversation, template, "tpl-retry-00001")
    assert [sent_parameters(c) for c in sends(graph)] == [PARAMS, PARAMS, PARAMS]
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.ACCEPTED
    assert message.attempts == 3
    assert message.external_id == "wamid.after-retry"


def test_duplicate_task_runs_send_a_queued_template_once(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-twice-00001")
    broker.up()
    message_id = str(outbound(deployment_tenant).pk)
    tenant_id = str(deployment_tenant.pk)
    # Two workers pick up the same message (redelivered task, sweep plus original).
    first = tasks.deliver_message(message_id, tenant_id=tenant_id)
    second = tasks.deliver_message(message_id, tenant_id=tenant_id)
    assert (first, second) == ("accepted", "skipped")
    sweep(deployment_tenant)
    assert [sent_parameters(c) for c in sends(graph)] == [PARAMS]


def test_task_queued_before_the_upgrade_still_delivers_its_parameters(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    """A task already in the broker from before Phase 14.1 carries the parameters as an
    argument and the row has none stored; it still sends them."""
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-legacy-0001")
    broker.up()
    with tenant_context(deployment_tenant):
        Message.objects.filter(direction="outbound").update(template_parameters=[])
    message_id = str(outbound(deployment_tenant).pk)
    assert tasks.deliver_message(message_id, PARAMS, tenant_id=str(deployment_tenant.pk)) == (
        "accepted"
    )
    assert [sent_parameters(c) for c in sends(graph)] == [PARAMS]


def test_template_missing_its_parameters_is_never_sent_without_them(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-lost-000001")
    broker.up()
    with tenant_context(deployment_tenant):
        Message.objects.filter(direction="outbound").update(template_parameters=[])
    sweep(deployment_tenant)
    assert sends(graph) == []
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.FAILED
    assert message.error_code == "template_parameters_invalid"


def test_stored_parameters_are_cleared_when_settled_and_by_retention(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-retain-0001")
    with tenant_context(deployment_tenant):
        # A worker died mid-send: the sweep settles it as unknown and drops the parameters.
        Message.objects.filter(direction="outbound").update(
            delivery_status=DeliveryStatus.SENDING,
            status_changed_at=timezone.now() - tasks.STUCK_SENDING - timedelta(minutes=1),
        )
    broker.up()
    sweep(deployment_tenant)
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.UNKNOWN
    assert message.template_parameters == []
    with tenant_context(deployment_tenant):
        Message.objects.filter(pk=message.pk).update(template_parameters=PARAMS)
        assert retention.redact(conversation.pk)
        message.refresh_from_db()
        assert message.template_parameters == []
    assert sends(graph) == []


# --- Correction 2: the policy is checked again at delivery time ---------------------------------


def queue_whatsapp_text(
    tenant: Tenant, api_client: APIClient, broker: Broker
) -> tuple[ChannelConnection, Conversation, APIClient]:
    wa = connection(tenant)
    conversation = whatsapp_conversation(tenant, api_client)
    client, _ = staff(tenant)
    broker.down()
    response = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "queued-text-001", "body": "Your parcel leaves today."},
        format="json",
    )
    assert response.status_code == 201
    broker.up()
    return wa, conversation, client


def assert_blocked(tenant: Tenant, graph: FakeGraph, code: str) -> None:
    sweep(tenant)
    sweep(tenant)  # a blocked message is never picked up again
    assert sends(graph) == []
    message = outbound(tenant)
    assert message.delivery_status == DeliveryStatus.FAILED
    assert message.error_code == code
    with tenant_context(tenant):
        attempts = list(DeliveryAttempt.objects.filter(message=message).values_list("outcome"))
    assert attempts == [("blocked",)]


def test_message_still_valid_at_delivery_time_is_sent(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    queue_whatsapp_text(deployment_tenant, api_client, broker)
    sweep(deployment_tenant)
    assert len(sends(graph)) == 1
    assert outbound(deployment_tenant).delivery_status == DeliveryStatus.ACCEPTED


def test_whatsapp_window_expiring_after_queueing_blocks_delivery(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    _, conversation, _ = queue_whatsapp_text(deployment_tenant, api_client, broker)
    with tenant_context(deployment_tenant):
        Conversation.objects.filter(pk=conversation.pk).update(
            last_inbound_at=timezone.now() - timedelta(hours=24, minutes=1)
        )
    assert_blocked(deployment_tenant, graph, "template_required")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"is_enabled": False}, "channel_disabled"),
        ({"status": ConnectionStatus.REVOKED}, "connection_revoked"),
        ({"status": ConnectionStatus.SUSPENDED}, "connection_suspended"),
        ({"status": ConnectionStatus.PERMISSION_MISSING}, "permission_missing"),
    ],
)
def test_connection_changes_before_delivery_block_it(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
    change: dict[str, Any],
    code: str,
) -> None:
    wa, _, _ = queue_whatsapp_text(deployment_tenant, api_client, broker)
    with tenant_context(deployment_tenant):
        ChannelConnection.objects.filter(pk=wa.pk).update(**change)
    assert_blocked(deployment_tenant, graph, code)


def test_closing_the_conversation_blocks_queued_replies(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    _, conversation, client = queue_whatsapp_text(deployment_tenant, api_client, broker)
    with tenant_context(deployment_tenant):
        version = Conversation.objects.get(pk=conversation.pk).version
    closed = client.post(
        f"{INBOX}conversations/{conversation.pk}/status/",
        {"status": "closed", "version": version},
        format="json",
    )
    assert closed.status_code == 200, closed.data
    assert_blocked(deployment_tenant, graph, "conversation_closed")


def test_opt_out_before_a_queued_template_blocks_it(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-optout-001")
    broker.up()
    assert client.post(f"{INBOX}conversations/{conversation.pk}/opt-out/").status_code == 204
    assert_blocked(deployment_tenant, graph, "opted_out")
    assert outbound(deployment_tenant).template_parameters == []


@pytest.mark.parametrize(
    "change",
    [{"status": "PAUSED"}, {"status": "REJECTED"}, {"category": "MARKETING"}, None],
)
def test_template_no_longer_eligible_is_not_sent(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
    change: dict[str, str] | None,
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-stale-00001")
    broker.up()
    with tenant_context(deployment_tenant):
        rows = WhatsAppTemplate.objects.filter(pk=template.pk)
        if change is None:
            rows.delete()  # removed from the WhatsApp account and dropped by template sync
        else:
            rows.update(**change)
    assert_blocked(deployment_tenant, graph, "template_unavailable")


def test_template_parameter_count_changed_by_sync_is_not_sent(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    client, conversation, template = template_setup(deployment_tenant, api_client)
    broker.down()
    send_template(client, conversation, template, "tpl-count-00001")
    broker.up()
    with tenant_context(deployment_tenant):
        WhatsAppTemplate.objects.filter(pk=template.pk).update(parameter_count=2)
    assert_blocked(deployment_tenant, graph, "template_parameters_invalid")


def messenger_reply(
    tenant: Tenant, api_client: APIClient, broker: Broker, inbound_age: timedelta
) -> Conversation:
    connection(tenant, ChannelType.MESSENGER)
    post_webhook(api_client, messenger_text())
    with tenant_context(tenant):
        conversation = Conversation.objects.get()
    client, _ = staff(tenant)
    broker.down()
    response = client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "messenger-001", "body": "Yes, we ship to Sylhet."},
        format="json",
    )
    assert response.status_code == 201, response.data
    broker.up()
    with tenant_context(tenant):
        Conversation.objects.filter(pk=conversation.pk).update(
            last_inbound_at=timezone.now() - inbound_age
        )
    return conversation


def test_messenger_window_expiring_after_queueing_blocks_delivery(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    messenger_reply(deployment_tenant, api_client, broker, timedelta(hours=25))
    assert_blocked(deployment_tenant, graph, "window_closed")


def test_messenger_human_agent_reply_remains_allowed_when_approved(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    meta_app.META_HUMAN_AGENT_APPROVED = True
    messenger_reply(deployment_tenant, api_client, broker, timedelta(hours=25))
    sweep(deployment_tenant)
    (call,) = sends(graph)
    assert call["body"]["messaging_type"] == "MESSAGE_TAG"
    assert call["body"]["tag"] == "HUMAN_AGENT"
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.ACCEPTED
    assert message.reply_tag == "HUMAN_AGENT"  # the record shows the tag actually used


def test_human_agent_window_also_ends_at_delivery_time(
    meta_app: Settings,
    deployment_tenant: Tenant,
    api_client: APIClient,
    graph: FakeGraph,
    broker: Broker,
) -> None:
    meta_app.META_HUMAN_AGENT_APPROVED = True
    messenger_reply(deployment_tenant, api_client, broker, timedelta(days=7, minutes=1))
    assert_blocked(deployment_tenant, graph, "window_closed")


def test_temporary_provider_failures_still_retry_and_unknown_is_not_resent(
    meta_app: Settings, deployment_tenant: Tenant, api_client: APIClient, graph: FakeGraph
) -> None:
    connection(deployment_tenant)
    conversation = whatsapp_conversation(deployment_tenant, api_client)
    client, _ = staff(deployment_tenant)
    graph.responses["/messages"] = graph_error("unknown", "timeout")
    client.post(
        f"{INBOX}conversations/{conversation.pk}/messages/",
        {"client_key": "unknown-00001", "body": "Hello"},
        format="json",
    )
    sweep(deployment_tenant)
    assert len(sends(graph)) == 1
    message = outbound(deployment_tenant)
    assert message.delivery_status == DeliveryStatus.UNKNOWN
    with tenant_context(deployment_tenant):
        assert Conversation.objects.get(pk=conversation.pk).status == ConversationStatus.OPEN
