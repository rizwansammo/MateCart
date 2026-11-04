"""SMS delivery integrity (D-091): outcome classification, no automatic resend of an unknown
outcome, stable idempotency keys, and atomic unit reservation (allowance first, then prepaid
credits, no overage), proven against real PostgreSQL including concurrent deliveries."""

import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import transaction
from django.utils import timezone
from pytest_django.fixtures import Settings

from commerce.audit.models import AuditEvent
from commerce.core.delivery import SmsRejected
from commerce.core.domain import ConflictError
from commerce.notifications import services, tasks
from commerce.notifications.models import (
    DeliveryAttempt,
    Notification,
    NotificationStatus,
    SenderStatus,
    SmsSettings,
    SmsUsage,
    SmsUsageState,
)
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_store_settings
from .fakes import TIMEOUT_AFTER_ACCEPT, FakeSmsProvider, ScriptedSmsProvider
from .helpers import Api
from .test_inventory_concurrency import run_concurrently

pytestmark = pytest.mark.django_db
SCRIPTED = "tests.fakes.ScriptedSmsProvider"


@pytest.fixture
def gateway(settings: Settings) -> Iterator[type[ScriptedSmsProvider]]:
    from commerce.core import delivery

    settings.MATECART_SMS_PROVIDER = SCRIPTED
    delivery._provider.cache_clear()
    ScriptedSmsProvider.reset()
    yield ScriptedSmsProvider
    ScriptedSmsProvider.reset()
    delivery._provider.cache_clear()


def configure(tenant: Tenant, *, allowance: int = 0, prepaid: int = 0) -> None:
    with tenant_context(tenant):
        SmsSettings.objects.create(
            is_enabled=True,
            requested_sender="RahimShop",
            approved_sender="RahimShop",
            sender_status=SenderStatus.APPROVED,
            monthly_allowance=allowance,
            prepaid_credits=prepaid,
        )


def queue_sms(tenant: Tenant, *, segments: int = 1, phone: str = "+8801712345678") -> Any:
    """One pending order SMS in the outbox, with a body of exactly ``segments`` GSM-7 parts."""
    body = "x" * (160 if segments == 1 else 153 * segments)
    with tenant_context(tenant):
        with transaction.atomic():
            created = services.emit(
                "order.placed",
                event_id=uuid.uuid4().hex,
                values={"order_number": "ORD-1", "order_total": "BDT 1.00"},
                customer=services.Recipient(user=None, phone=phone),
            )
        row = next(item for item in created if item.channel == "sms")
        Notification.objects.filter(pk=row.pk).update(body_text=body)
    return row.pk


def deliver(tenant: Tenant, pk: Any) -> str:
    with tenant_context(tenant):
        return services.deliver(pk)


def make_due(tenant: Tenant, pk: Any) -> None:
    with tenant_context(tenant):
        Notification.objects.filter(pk=pk).update(scheduled_at=timezone.now())


def usage(tenant: Tenant) -> list[tuple[int, str, int, int, str]]:
    with tenant_context(tenant):
        return list(
            SmsUsage.objects.order_by("occurred_at", "attempt").values_list(
                "attempt", "state", "allowance_segments", "prepaid_segments", "funded_by"
            )
        )


def notification(tenant: Tenant, pk: Any) -> Notification:
    with tenant_context(tenant):
        return Notification.objects.get(pk=pk)


def balance(tenant: Tenant, at: datetime | None = None) -> dict[str, int]:
    with tenant_context(tenant):
        return services.sms_balance(at)


# --- Fix 01: outcome classification ---------------------------------------------------------------


def test_accepted_sms_spends_its_reserved_units_and_carries_a_stable_key(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=5)
    pk = queue_sms(store.tenant)

    assert deliver(store.tenant, pk) == "sent"
    assert usage(store.tenant) == [(1, "accepted", 1, 0, "allowance")]
    assert balance(store.tenant)["used"] == 1
    assert len(gateway.calls) == 1
    assert gateway.calls[0].idempotency_key == f"matecart-sms-{pk}"
    assert gateway.calls[0].sender == "RahimShop"


def test_retryable_rejection_releases_units_and_the_retry_reuses_the_key(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=1)
    pk = queue_sms(store.tenant)
    gateway.script.append(SmsRejected("throttled", retryable=True))

    first = deliver(store.tenant, pk)
    released = usage(store.tenant)
    between = balance(store.tenant)
    make_due(store.tenant, pk)
    second = deliver(store.tenant, pk)

    assert first == "pending"
    assert released == [(1, "released", 1, 0, "allowance")]
    assert between["used"] == 0  # the definite non-acceptance returned the unit
    assert second == "sent"
    assert usage(store.tenant) == [
        (1, "released", 1, 0, "allowance"),
        (2, "accepted", 1, 0, "allowance"),
    ]
    assert balance(store.tenant)["used"] == 1  # charged once
    assert {call.idempotency_key for call in gateway.calls} == {f"matecart-sms-{pk}"}
    assert gateway.delivered == {f"matecart-sms-{pk}": 1}


def test_permanent_rejection_fails_without_retry_and_returns_prepaid_units(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, prepaid=2)
    pk = queue_sms(store.tenant, segments=2)
    gateway.script.append(SmsRejected("invalid_number", retryable=False))

    assert deliver(store.tenant, pk) == "failed"
    row = notification(store.tenant, pk)
    assert row.failure_code == "invalid_number"
    assert usage(store.tenant) == [(1, "released", 0, 2, "prepaid")]
    assert balance(store.tenant)["prepaid_credits"] == 2
    assert deliver(store.tenant, pk) == "failed"
    assert len(gateway.calls) == 1


def test_timeout_after_possible_acceptance_is_unknown_never_resent_and_units_held(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=1, prepaid=1)
    pk = queue_sms(store.tenant, segments=2)
    gateway.script.append(TIMEOUT_AFTER_ACCEPT)

    assert deliver(store.tenant, pk) == "failed"
    make_due(store.tenant, pk)
    again = deliver(store.tenant, pk)
    with tenant_context(store.tenant):
        due = services.due_ids()
        attempts = list(DeliveryAttempt.objects.filter(notification_id=pk).values_list("outcome"))
    row = notification(store.tenant, pk)

    assert again == "failed"
    assert row.failure_code == "outcome_unknown"
    assert pk not in due
    assert attempts == [("unknown",)]
    assert len(gateway.calls) == 1
    assert gateway.delivered == {f"matecart-sms-{pk}": 1}
    assert usage(store.tenant) == [(1, "held", 1, 1, "mixed")]
    assert balance(store.tenant)["remaining"] == 0  # held units still count
    assert balance(store.tenant)["prepaid_credits"] == 0


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("+8801712345678 token=sk-live-secret"),
        TypeError("unexpected response shape"),
        ConnectionResetError("connection reset by peer"),
    ],
)
def test_arbitrary_provider_exceptions_are_unknown_not_retried_and_not_logged(
    store: StoreSettings,
    gateway: type[ScriptedSmsProvider],
    error: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure(store.tenant, allowance=3)
    pk = queue_sms(store.tenant)
    gateway.script.append(error)

    assert deliver(store.tenant, pk) == "failed"
    assert notification(store.tenant, pk).failure_code == "outcome_unknown"
    assert usage(store.tenant) == [(1, "held", 1, 0, "allowance")]
    assert "+8801712345678" not in caplog.text
    assert "sk-live-secret" not in caplog.text


def test_rejection_codes_that_could_carry_a_number_are_not_stored(
    store: StoreSettings, gateway: type[ScriptedSmsProvider], caplog: pytest.LogCaptureFixture
) -> None:
    configure(store.tenant, allowance=3)
    pk = queue_sms(store.tenant)
    gateway.script.append(SmsRejected("blocked 8801712345678", retryable=False))

    assert deliver(store.tenant, pk) == "failed"
    assert notification(store.tenant, pk).failure_code == "sms_rejected"
    assert "8801712345678" not in caplog.text


def test_retryable_rejections_are_bounded_and_each_attempt_is_released(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=1)
    pk = queue_sms(store.tenant)
    gateway.script.extend(SmsRejected("gateway_busy", retryable=True) for _ in range(10))

    results = []
    for _ in range(8):
        make_due(store.tenant, pk)
        results.append(deliver(store.tenant, pk))
    row = notification(store.tenant, pk)

    assert results[:5] == ["pending"] * 4 + ["failed"]
    assert row.failure_code == "retries_exhausted"
    assert len(gateway.calls) == row.max_attempts == 5
    assert [entry[:2] for entry in usage(store.tenant)] == [(n, "released") for n in range(1, 6)]
    assert balance(store.tenant)["used"] == 0


def test_duplicate_and_repeated_processing_send_and_reserve_once(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=5)
    pk = queue_sms(store.tenant)
    with tenant_context(store.tenant):
        Notification.objects.filter(pk=pk).update(
            status=NotificationStatus.PROCESSING, claimed_at=timezone.now()
        )
    in_flight = deliver(store.tenant, pk)  # another worker holds a fresh claim
    with tenant_context(store.tenant):
        Notification.objects.filter(pk=pk).update(status=NotificationStatus.QUEUED, claimed_at=None)
    tenant_id = str(store.tenant.pk)
    first = tasks.deliver_notification.apply(args=(str(pk),), kwargs={"tenant_id": tenant_id})
    redelivered = tasks.deliver_notification.apply(args=(str(pk),), kwargs={"tenant_id": tenant_id})

    assert in_flight == "processing"
    assert first.get() == "sent"
    assert redelivered.get() == "sent"
    assert len(gateway.calls) == 1
    assert usage(store.tenant) == [(1, "accepted", 1, 0, "allowance")]


def test_a_worker_lost_mid_send_holds_the_reservation(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=2)
    pk = queue_sms(store.tenant)
    with tenant_context(store.tenant):
        row = Notification.objects.get(pk=pk)
        with transaction.atomic():
            assert services._reserve_sms(row, attempt=1, now=timezone.now()) == ""
        Notification.objects.filter(pk=pk).update(
            status=NotificationStatus.PROCESSING,
            attempts=1,
            claimed_at=timezone.now() - timedelta(minutes=30),
        )

    assert deliver(store.tenant, pk) == "failed"
    assert notification(store.tenant, pk).failure_code == "outcome_unknown"
    assert usage(store.tenant) == [(1, "held", 1, 0, "allowance")]
    assert gateway.calls == []


def test_providers_without_idempotency_support_are_never_given_a_key(
    store: StoreSettings, settings: Settings
) -> None:
    from commerce.core import delivery

    settings.MATECART_SMS_PROVIDER = "tests.fakes.FakeSmsProvider"
    delivery._provider.cache_clear()
    configure(store.tenant, allowance=1)
    pk = queue_sms(store.tenant)

    assert deliver(store.tenant, pk) == "sent"
    assert len(FakeSmsProvider.outbox) == 1
    assert FakeSmsProvider.outbox[0].sender == "RahimShop"
    assert usage(store.tenant) == [(1, "accepted", 1, 0, "allowance")]


def test_a_provider_claiming_idempotency_without_the_argument_is_refused_before_sending(
    store: StoreSettings, monkeypatch: pytest.MonkeyPatch, gateway: type[ScriptedSmsProvider]
) -> None:
    sent: list[str] = []

    class Inconsistent:
        supports_idempotency_key = True

        def send(
            self, *, to: str, body: str, tenant_id: uuid.UUID, purpose: str, sender: str = ""
        ) -> None:
            sent.append(to)

    from commerce.core import delivery

    monkeypatch.setattr(delivery, "sms_provider", lambda: Inconsistent())
    configure(store.tenant, prepaid=1)
    pk = queue_sms(store.tenant)

    assert deliver(store.tenant, pk) == "failed"
    assert notification(store.tenant, pk).failure_code == "sms_provider_incompatible"
    assert sent == []
    assert usage(store.tenant) == [(1, "released", 0, 1, "prepaid")]
    assert balance(store.tenant)["prepaid_credits"] == 1


def test_held_reservations_are_reconciled_with_an_audit_record(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, prepaid=2)
    lost, delivered = queue_sms(store.tenant), queue_sms(store.tenant, phone="+8801812345678")
    gateway.script.extend([TimeoutError("read timed out"), TIMEOUT_AFTER_ACCEPT])
    deliver(store.tenant, lost)
    deliver(store.tenant, delivered)
    with tenant_context(store.tenant):
        held = {item.notification_id: item.pk for item in services.held_sms()}
    listing = io.StringIO()
    call_command("reconcile_sms", stdout=listing)

    call_command("reconcile_sms", str(held[lost]), "--not-accepted", stdout=io.StringIO())
    call_command("reconcile_sms", str(held[delivered]), "--accepted", stdout=io.StringIO())
    with tenant_context(store.tenant):
        states = dict(SmsUsage.objects.values_list("notification_id", "state"))
        audits = list(
            AuditEvent.objects.filter(action="notifications.sms.reconciled").values_list(
                "actor_label", flat=True
            )
        )
        with pytest.raises(ConflictError):
            services.reconcile_sms(held[lost], accepted=True, actor_label="netamate")
    with pytest.raises(CommandError):
        call_command("reconcile_sms", "not-an-id", "--accepted", stdout=io.StringIO())

    assert str(held[lost]) in listing.getvalue()
    assert "+880" not in listing.getvalue()
    assert states == {lost: SmsUsageState.RELEASED, delivered: SmsUsageState.ACCEPTED}
    assert balance(store.tenant)["prepaid_credits"] == 1  # one returned, one spent
    assert audits == ["netamate", "netamate"]
    assert len(gateway.calls) == 2  # reconciliation never sends


# --- Fix 02: reservation and accounting -----------------------------------------------------------


def test_allowance_then_prepaid_covers_one_message_and_charges_it_once(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=1, prepaid=2)
    pk = queue_sms(store.tenant, segments=3)

    assert deliver(store.tenant, pk) == "sent"
    assert usage(store.tenant) == [(1, "accepted", 1, 2, "mixed")]
    assert balance(store.tenant) == {
        "allowance": 1,
        "used": 1,
        "remaining": 0,
        "prepaid_credits": 0,
    }
    assert len(gateway.calls) == 1


def test_exact_allowance_exhaustion_stops_the_next_message(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=2)
    first, second = queue_sms(store.tenant), queue_sms(store.tenant, phone="+8801812345678")
    third = queue_sms(store.tenant, phone="+8801912345678")

    results = [deliver(store.tenant, pk) for pk in (first, second, third)]

    assert results == ["sent", "sent", "suppressed"]
    assert notification(store.tenant, third).failure_code == "sms_allowance_exhausted"
    assert len(gateway.calls) == 2
    assert balance(store.tenant)["remaining"] == 0


def test_exact_prepaid_exhaustion_never_goes_negative(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, prepaid=3)
    first = queue_sms(store.tenant, segments=3)
    second = queue_sms(store.tenant, phone="+8801812345678")

    assert deliver(store.tenant, first) == "sent"
    assert deliver(store.tenant, second) == "suppressed"
    assert balance(store.tenant)["prepaid_credits"] == 0
    assert usage(store.tenant) == [(1, "accepted", 0, 3, "prepaid")]


def test_insufficient_combined_units_reserve_nothing_and_send_nothing(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=1, prepaid=1)
    pk = queue_sms(store.tenant, segments=3)

    assert deliver(store.tenant, pk) == "suppressed"
    assert usage(store.tenant) == []
    assert balance(store.tenant)["prepaid_credits"] == 1
    assert balance(store.tenant)["remaining"] == 1
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("zone", "periods", "results"),
    [
        ("Asia/Dhaka", ["2027-01", "2027-02"], ["sent", "sent"]),
        ("UTC", ["2027-01", "2027-01"], ["sent", "suppressed"]),
    ],
)
def test_the_allowance_month_follows_the_store_time_zone(
    store: StoreSettings,
    gateway: type[ScriptedSmsProvider],
    monkeypatch: pytest.MonkeyPatch,
    zone: str,
    periods: list[str],
    results: list[str],
) -> None:
    with tenant_context(store.tenant):
        StoreSettings.objects.filter(pk=store.pk).update(timezone=zone)
    configure(store.tenant, allowance=1)
    before, after = queue_sms(store.tenant), queue_sms(store.tenant, phone="+8801812345678")
    # 23:59 on 31 January in Dhaka, then 00:30 on 1 February there (still 31 January in UTC).
    moments = [datetime(2027, 1, 31, 17, 59, tzinfo=UTC), datetime(2027, 1, 31, 18, 30, tzinfo=UTC)]

    outcomes = []
    for pk, moment in zip((before, after), moments, strict=True):
        monkeypatch.setattr(timezone, "now", lambda moment=moment: moment)
        outcomes.append(deliver(store.tenant, pk))
    monkeypatch.undo()
    with tenant_context(store.tenant):
        recorded = list(SmsUsage.objects.order_by("occurred_at").values_list("period", flat=True))

    assert outcomes == results
    assert recorded == periods[: results.count("sent")]
    assert balance(store.tenant, moments[0])["used"] == 1


def test_accounting_is_isolated_per_tenant(
    store: StoreSettings, tenant_b: Tenant, gateway: type[ScriptedSmsProvider]
) -> None:
    make_store_settings(tenant_b, name="Other Store")
    configure(store.tenant, allowance=1, prepaid=1)
    configure(tenant_b, allowance=1, prepaid=1)
    mine = queue_sms(store.tenant, segments=2)
    gateway.script.append(TIMEOUT_AFTER_ACCEPT)
    deliver(store.tenant, mine)
    theirs = queue_sms(tenant_b, segments=2)

    assert deliver(tenant_b, theirs) == "sent"
    assert usage(store.tenant) == [(1, "held", 1, 1, "mixed")]
    assert usage(tenant_b) == [(1, "accepted", 1, 1, "mixed")]
    with tenant_context(tenant_b):
        assert services.held_sms() == []
        assert not SmsUsage.objects.filter(notification_id=mine).exists()
    assert gateway.calls[0].tenant_id == store.tenant.pk
    assert gateway.calls[1].tenant_id == tenant_b.pk


def test_authentication_sms_is_exempt_and_never_stores_the_code(
    api: Api, sms: list[Any], deployment_tenant: Tenant, store: StoreSettings
) -> None:
    configure(deployment_tenant, allowance=0, prepaid=0)  # notification units fully used up

    started = api.post("/api/account/phone/start/", {"phone": "01712 345678"})

    assert started.status_code == 202
    code = sms[-1].body[:6]
    with tenant_context(deployment_tenant):
        rows = list(SmsUsage.objects.values())
        assert Notification.objects.count() == 0  # no outbox row, so no Celery payload
        settings_row = SmsSettings.objects.get()
    assert len(rows) == 1
    assert rows[0]["funded_by"] == "auth"
    assert rows[0]["state"] == "accepted"
    assert rows[0]["purpose"] == "phone_sign_in"
    assert (rows[0]["allowance_segments"], rows[0]["prepaid_segments"]) == (0, 0)
    assert code not in repr(rows)
    assert "+8801712345678" not in repr(rows)
    assert settings_row.prepaid_credits == 0
    assert balance(deployment_tenant)["used"] == 0


# --- Concurrency (committed transactions on real PostgreSQL) --------------------------------------


@pytest.fixture
def no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup rows stay pending instead of being delivered by the eager worker at commit."""
    monkeypatch.setattr(services, "_queue_after_commit", lambda ids: None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("no_queue")
def test_concurrent_deliveries_share_the_last_units_without_overage(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=3, prepaid=2)
    ids = [queue_sms(store.tenant, phone=f"+88017123456{n:02d}") for n in range(8)]

    results = run_concurrently(store.tenant, 8, lambda index: services.deliver(ids[index]))

    assert sorted(results) == ["sent"] * 5 + ["suppressed"] * 3
    assert len(gateway.calls) == 5
    assert balance(store.tenant) == {
        "allowance": 3,
        "used": 3,
        "remaining": 0,
        "prepaid_credits": 0,
    }
    recorded = usage(store.tenant)
    assert sum(entry[2] + entry[3] for entry in recorded) == 5
    assert {entry[1] for entry in recorded} == {"accepted"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("no_queue")
def test_concurrent_multi_segment_messages_cannot_split_the_last_units(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=1, prepaid=2)
    ids = [queue_sms(store.tenant, segments=2, phone=f"+88017123456{n:02d}") for n in range(6)]

    results = run_concurrently(store.tenant, 6, lambda index: services.deliver(ids[index]))

    assert sorted(results) == ["sent"] + ["suppressed"] * 5
    assert balance(store.tenant)["prepaid_credits"] == 1
    assert [entry[1:] for entry in usage(store.tenant)] == [("accepted", 1, 1, "mixed")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("no_queue")
def test_concurrent_duplicate_deliveries_of_one_message_send_and_reserve_once(
    store: StoreSettings, gateway: type[ScriptedSmsProvider]
) -> None:
    configure(store.tenant, allowance=5)
    pk = queue_sms(store.tenant)

    results = run_concurrently(store.tenant, 8, lambda _: services.deliver(pk))

    assert results.count("sent") >= 1
    assert all(isinstance(result, str) for result in results)
    assert len(gateway.calls) == 1
    assert usage(store.tenant) == [(1, "accepted", 1, 0, "allowance")]


@pytest.mark.django_db(transaction=True)
def test_the_reservation_migration_keeps_historical_records_as_they_were(tenant_a: Tenant) -> None:
    """Rows written before D-091 were final: accepted rows keep their charged source, unaccepted
    rows charged nothing, and legacy ``overage`` rows are kept as written, not re-priced."""
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    before = [("notifications", "0002_append_only_records")]
    after = [("notifications", "0003_sms_reservations")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    legacy = [
        (uuid.uuid4(), 1, True, "allowance"),
        (uuid.uuid4(), 2, True, "prepaid"),
        (uuid.uuid4(), 3, True, "overage"),
        (uuid.uuid4(), 1, False, ""),
    ]
    with connection.cursor() as cursor:
        for pk, segments, accepted, funded in legacy:
            cursor.execute(
                "INSERT INTO notifications_smsusage (id, tenant_id, purpose, period, segments, "
                "encoding, accepted, funded_by, occurred_at) "
                "VALUES (%s, %s, 'order.placed', '2026-10', %s, 'gsm7', %s, %s, now())",
                [pk, tenant_a.pk, segments, accepted, funded],
            )
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(after)
    with tenant_context(tenant_a):
        rows = {
            row[0]: row[1:]
            for row in SmsUsage.objects.values_list(
                "id", "state", "accepted", "allowance_segments", "prepaid_segments", "funded_by"
            )
        }
        resolved = SmsUsage.objects.filter(resolved_at__isnull=True).count()
        used = services._allowance_used("2026-10")

    assert [rows[pk] for pk, *_ in legacy] == [
        ("accepted", True, 1, 0, "allowance"),
        ("accepted", True, 0, 2, "prepaid"),
        ("accepted", True, 0, 0, "overage"),
        ("released", False, 0, 0, ""),
    ]
    assert resolved == 0
    assert used == 1  # what the old balance counted for that month
