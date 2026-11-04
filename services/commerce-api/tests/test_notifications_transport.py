"""Phase 13 email transports: SMTP, IMAP and the MateMail Connected App (D-086).

No test reaches a real mail server or MateMail: sockets and the MateMail API are replaced by fakes.
"""

import re
import smtplib
import socket
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from pytest_django.fixtures import Settings

from commerce.audit.models import AuditEvent
from commerce.identity.models import AuthSession
from commerce.notifications import connections, net, providers, services
from commerce.notifications.models import ConnectionStatus, MailConnection, Notification
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .test_orders import merchant

pytestmark = pytest.mark.django_db
EMAIL = "/api/manage/notifications/email/"
SECRET = "smtp-app-password-do-not-leak"


def addresses(*ips: str) -> Any:
    return lambda host, port, **_: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips
    ]


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", addresses("93.184.216.34"))


class FakeSmtp:
    """Stands in for a logged-in SMTP session; records what would have been sent."""

    sent: list[bytes] = []
    rcpt_code = 250
    data_error: Exception | None = None

    def mail(self, sender: str) -> tuple[int, bytes]:
        return 250, b"ok"

    def rcpt(self, recipient: str) -> tuple[int, bytes]:
        return self.rcpt_code, b"ok"

    def data(self, message: bytes) -> tuple[int, bytes]:
        if self.data_error is not None:
            raise self.data_error
        FakeSmtp.sent.append(message)
        return 250, b"queued"

    def quit(self) -> None:
        return None


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> type[FakeSmtp]:
    FakeSmtp.sent = []
    FakeSmtp.rcpt_code = 250
    FakeSmtp.data_error = None
    monkeypatch.setattr(net, "open_smtp", lambda *args, **kwargs: FakeSmtp())
    return FakeSmtp


def smtp_body(**overrides: Any) -> dict[str, Any]:
    return {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_username": "shop@example.com",
        "smtp_password": SECRET,
        "from_email": "shop@example.com",
        "from_name": "Rahim Shop",
        **overrides,
    }


# --- Host safety ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "port", "ips", "code"),
    [
        ("smtp.example.com", 587, ("10.0.0.5",), "host_not_public"),
        ("smtp.example.com", 587, ("127.0.0.1",), "host_not_public"),
        ("smtp.example.com", 587, ("169.254.169.254",), "host_not_public"),
        ("smtp.example.com", 587, ("93.184.216.34", "192.168.1.1"), "host_not_public"),
        ("8.8.8.8", 587, ("8.8.8.8",), "host_must_be_name"),
        ("localhost", 587, ("127.0.0.1",), "host_invalid"),
        ("smtp.example.com", 6379, ("93.184.216.34",), "port_not_allowed"),
        ("bad host!", 587, ("93.184.216.34",), "host_invalid"),
    ],
)
def test_unsafe_hosts_are_refused(
    monkeypatch: pytest.MonkeyPatch, host: str, port: int, ips: tuple[str, ...], code: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", addresses(*ips))
    with pytest.raises(net.UnsafeHost) as raised:
        net.resolve(host, port, allowed_ports=net.SMTP_PORTS)
    assert raised.value.code == code


def test_connections_are_pinned_to_the_vetted_address(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", addresses("93.184.216.34"))
    target = net.resolve("smtp.example.com", 587, allowed_ports=net.SMTP_PORTS)
    opened: list[tuple[str, int]] = []

    def connect(address: tuple[str, int], timeout: float | None = None) -> socket.socket:
        opened.append(address)
        raise ConnectionRefusedError("test stop")

    monkeypatch.setattr(socket, "create_connection", connect)
    with pytest.raises(ConnectionRefusedError, match="test stop"):
        net.open_smtp(target, "starttls", "", "")
    # A later DNS answer cannot redirect the connection: the vetted address is used.
    assert opened == [("93.184.216.34", 587)]
    settings.MATECART_MAIL_INTERNAL_HOSTS = ["mailpit"]
    monkeypatch.setattr(socket, "getaddrinfo", addresses("172.18.0.4"))
    internal = net.resolve("mailpit", 1025, allowed_ports=net.SMTP_PORTS)
    assert internal.internal is True
    public = net.Target("smtp.example.com", "93.184.216.34", 25, internal=False)
    with pytest.raises(net.UnsafeHost):
        net.open_smtp(public, "none", "", "")  # plain text only for the development allowlist


# --- SMTP configuration ---------------------------------------------------------------------------


def test_smtp_settings_need_permission_recent_sign_in_and_never_return_secrets(
    store: Any, smtp: type[FakeSmtp]
) -> None:
    owner = merchant(store.tenant)
    editor = merchant(store.tenant, StaffRole.CONTENT_EDITOR)
    handler = merchant(store.tenant, StaffRole.ORDER_HANDLER)
    assert editor.put(f"{EMAIL}smtp/", smtp_body(), format="json").status_code == 403
    assert handler.get(EMAIL).status_code == 403
    with tenant_context(store.tenant):
        AuthSession.objects.update(authenticated_at=timezone.now() - timedelta(hours=1))
    stale = owner.put(f"{EMAIL}smtp/", smtp_body(), format="json")
    with tenant_context(store.tenant):
        AuthSession.objects.update(authenticated_at=timezone.now())
    saved = owner.put(f"{EMAIL}smtp/", smtp_body(), format="json")
    injected = owner.put(
        f"{EMAIL}smtp/", smtp_body(reply_to="a@example.com\r\nBcc: x@example.com"), format="json"
    )
    read = owner.get(EMAIL)

    assert stale.status_code == 403
    assert saved.status_code == 200
    assert injected.status_code == 400
    state = read.json()["smtp"]
    assert state["smtp_password_set"] is True
    assert state["status"] == "unverified"
    assert SECRET not in read.content.decode()
    assert "password_encrypted" not in read.content.decode()
    with tenant_context(store.tenant):
        row = MailConnection.objects.get(provider="smtp")
        assert row.smtp_password_encrypted
        assert SECRET not in row.smtp_password_encrypted
        audit = str(list(AuditEvent.objects.values_list("changes", flat=True)))
    assert SECRET not in audit


def test_sender_ownership_is_proved_before_the_connection_is_used(
    store: Any, smtp: type[FakeSmtp]
) -> None:
    owner = merchant(store.tenant)
    owner.put(f"{EMAIL}smtp/", smtp_body(), format="json")
    tested = owner.post(f"{EMAIL}smtp/test/", {}, format="json").json()["smtp"]
    unusable = owner.post(f"{EMAIL}select/", {"provider": "smtp"}, format="json")
    owner.post(f"{EMAIL}smtp/sender-code/", {}, format="json")
    code = re.search(rb"code is (\d{6})", smtp.sent[-1]).group(1).decode()  # type: ignore[union-attr]
    wrong = owner.post(f"{EMAIL}smtp/sender-confirm/", {"code": "000000"}, format="json")
    confirmed = owner.post(f"{EMAIL}smtp/sender-confirm/", {"code": code}, format="json")
    selected = owner.post(f"{EMAIL}select/", {"provider": "smtp"}, format="json")

    assert tested["status"] == "unverified"
    assert tested["last_verified_at"]
    assert unusable.status_code == 409
    assert b"To: shop@example.com" in smtp.sent[-1]  # the code goes to the From address only
    assert wrong.status_code == 400
    assert confirmed.json()["smtp"]["status"] == "connected"
    assert selected.json()["active_provider"] == "smtp"
    # Changing the From address needs a new proof.
    changed = owner.put(f"{EMAIL}smtp/", smtp_body(from_email="other@example.com"), format="json")
    assert changed.json()["smtp"]["status"] == "unverified"
    assert changed.json()["smtp"]["sender_verified"] is False


def test_test_email_goes_only_to_the_signed_in_staff_member(
    store: Any, smtp: type[FakeSmtp]
) -> None:
    owner = merchant(store.tenant)
    owner.put(f"{EMAIL}smtp/", smtp_body(), format="json")
    unverified = owner.post(f"{EMAIL}smtp/test/", {"send_to_self": True}, format="json")
    with tenant_context(store.tenant):
        from commerce.identity.models import User

        User.objects.filter(account_type="staff").update(email_verified_at=timezone.now())
    sent = owner.post(
        f"{EMAIL}smtp/test/", {"send_to_self": True, "to": "x@evil.test"}, format="json"
    )
    assert unverified.status_code == 409
    assert sent.status_code == 200
    assert b"x@evil.test" not in smtp.sent[-1]
    assert b"To: owner-" in smtp.sent[-1]


def test_smtp_outcomes_are_classified_without_resending(
    store: Any, smtp: type[FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    with tenant_context(store.tenant):
        row = connections.connection("smtp")
        row.smtp_host, row.smtp_port, row.from_email = "smtp.example.com", 587, "shop@example.com"
        row.save()
    email = providers.Email("buyer@example.com", "Subject", "Text", "", "key")
    with tenant_context(store.tenant):
        accepted = providers.send_smtp(row, email)
        smtp.rcpt_code = 550
        refused = providers.send_smtp(row, email)
        smtp.rcpt_code = 451
        temporary = providers.send_smtp(row, email)
        smtp.rcpt_code = 250
        smtp.data_error = smtplib.SMTPServerDisconnected("gone after DATA")
        unknown = providers.send_smtp(row, email)

        def auth_fail(*_: Any, **__: Any) -> Any:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials for shop@example.com")

        monkeypatch.setattr(net, "open_smtp", auth_fail)
        denied = providers.send_smtp(row, email)
    assert accepted.outcome == "accepted"
    assert (refused.outcome, refused.code) == ("permanent", "recipient_refused")
    assert temporary.outcome == "retryable"
    assert (unknown.outcome, unknown.code) == ("unknown", "outcome_unknown")
    assert (denied.outcome, denied.code) == ("permanent", "authentication_failed")
    assert "shop@example.com" not in denied.code


def test_store_transport_carries_notifications_and_auth_mail(
    store: Any, smtp: type[FakeSmtp], django_capture_on_commit_callbacks: Any
) -> None:
    from commerce.identity import passwords

    from .factories import make_user

    with tenant_context(store.tenant):
        row = connections.connection("smtp")
        row.smtp_host, row.smtp_port, row.from_email = "smtp.example.com", 587, "shop@example.com"
        row.status, row.is_enabled = ConnectionStatus.CONNECTED, True
        row.save()
    make_user(
        store.tenant,
        email="reset2@example.test",
        password="customer-passphrase-1234",
        email_verified_at=timezone.now(),
    )
    with tenant_context(store.tenant), django_capture_on_commit_callbacks(execute=True):
        passwords.request_reset(
            realm="customer",
            email="reset2@example.test",
            ip="127.0.0.1",
            link_base="http://testserver/account/reset",
            store_name="Shop",
        )
    assert len(smtp.sent) == 1
    assert mail.outbox == []
    assert b"From: shop@example.com" in smtp.sent[0] or b"shop@example.com" in smtp.sent[0]
    with tenant_context(store.tenant):
        assert not Notification.objects.exists()


# --- IMAP -----------------------------------------------------------------------------------------


def test_imap_is_verified_read_only_and_errors_are_safe(
    store: Any, public_dns: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import imaplib

    owner = merchant(store.tenant)
    insecure = owner.put(
        f"{EMAIL}imap/",
        {
            "imap_host": "imap.example.com",
            "imap_port": 143,
            "imap_security": "none",
            "imap_username": "shop@example.com",
            "imap_password": SECRET,
        },
        format="json",
    )
    saved = owner.put(
        f"{EMAIL}imap/",
        {
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_security": "tls",
            "imap_username": "shop@example.com",
            "imap_password": SECRET,
        },
        format="json",
    )

    def refuse(*_: Any) -> None:
        raise imaplib.IMAP4.error(f"LOGIN failed for shop@example.com with {SECRET}")

    monkeypatch.setattr(net, "verify_imap", refuse)
    failed = owner.post(f"{EMAIL}imap/verify/", {}, format="json")
    monkeypatch.setattr(net, "verify_imap", lambda *args: None)
    ok = owner.post(f"{EMAIL}imap/verify/", {}, format="json")

    assert insecure.status_code == 400
    assert saved.json()["smtp"]["imap_status"] == "unverified"
    assert failed.json()["smtp"]["imap_status"] == "failed"
    assert failed.json()["smtp"]["imap_error"] == "authentication_failed"
    assert SECRET not in failed.content.decode()
    assert ok.json()["smtp"]["imap_status"] == "connected"


# --- MateMail Connected App -----------------------------------------------------------------------


class FakeMateMail:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.approval = "approved"
        self.scopes = ["mailbox.read", "mail.send"]
        self.send_error: int | None = None

    def post(
        self, path: str, body: dict[str, Any], *, token: str = "", tenant: str = ""
    ) -> dict[str, Any]:
        self.calls.append((path, body, token))
        if path.endswith("connect/start/"):
            if body["integration_secret"] != "integration-secret-123":
                raise providers.MateMailError(401, "http_401")
            return {
                "authorization_url": "https://mail.example.com/app/integrations/authorize?request=r",
                "poll_token": "poll-token-1",
                "mailbox_email": "orders@shop.example.com",
            }
        if path.endswith("connect/status/"):
            if self.approval != "approved":
                return {"status": self.approval}
            return {
                "status": "approved",
                "access_token": f"mmc_token_{len(self.calls)}",
                "id": "11111111-2222-3333-4444-555555555555",
                "mailbox_email": "orders@shop.example.com",
                "permissions": [{"key": key} for key in self.scopes],
            }
        if path.endswith("external/send/"):
            if self.send_error is not None:
                raise providers.MateMailError(self.send_error, f"http_{self.send_error}")
            return {"sent": True, "message_id": "<abc@shop.example.com>"}
        return {"disconnected": True}

    def get(self, path: str, *, token: str, tenant: str) -> dict[str, Any]:
        if self.send_error == 401:
            raise providers.MateMailError(401, "http_401")
        return {"permissions": [{"key": key} for key in self.scopes]}


@pytest.fixture
def matemail(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FakeMateMail:
    settings.MATEMAIL_BASE_URL = "https://mail.example.com"
    fake = FakeMateMail()
    monkeypatch.setattr(providers, "matemail_api", lambda: fake)
    return fake


def test_matemail_is_unavailable_without_a_deployment_address(store: Any) -> None:
    owner = merchant(store.tenant)
    state = owner.get(EMAIL).json()
    started = owner.post(
        f"{EMAIL}matemail/start/",
        {"tenant_id": "11111111-2222-3333-4444-555555555555", "integration_secret": "x" * 20},
        format="json",
    )
    assert state["matemail_available"] is False
    assert started.status_code == 409


def test_matemail_connected_app_flow(store: Any, matemail: FakeMateMail, tenant_b: Tenant) -> None:
    owner = merchant(store.tenant)
    tenant = "11111111-2222-3333-4444-555555555555"
    bad = owner.post(
        f"{EMAIL}matemail/start/",
        {"tenant_id": tenant, "integration_secret": "wrong-secret"},
        format="json",
    )
    started = owner.post(
        f"{EMAIL}matemail/start/",
        {"tenant_id": tenant, "integration_secret": "integration-secret-123"},
        format="json",
    )
    matemail.approval = "pending"
    pending = owner.post(f"{EMAIL}matemail/check/", {}, format="json")
    matemail.approval = "approved"
    connected = owner.post(f"{EMAIL}matemail/check/", {}, format="json")
    selected = owner.post(f"{EMAIL}select/", {"provider": "matemail"}, format="json")

    assert bad.status_code == 409
    assert started.json()["matemail"]["status"] == "awaiting_approval"
    assert started.json()["matemail"]["matemail_authorization_url"].startswith("https://")
    assert pending.json()["matemail"]["status"] == "awaiting_approval"
    state = connected.json()["matemail"]
    assert state["status"] == "connected"
    assert state["matemail_mailbox"] == "orders@shop.example.com"
    assert "mail.send" in state["matemail_scopes"]
    assert state["matemail_token_set"] is True
    assert "mmc_token" not in connected.content.decode()
    assert selected.json()["active_provider"] == "matemail"
    with tenant_context(store.tenant):
        row = MailConnection.objects.get(provider="matemail")
        stored = " ".join(str(value) for value in row.__dict__.values())
    assert "integration-secret-123" not in stored
    assert "mmc_token" not in stored
    with tenant_context(tenant_b):
        assert not MailConnection.objects.exists()


def test_matemail_without_send_permission_cannot_be_used(
    store: Any, matemail: FakeMateMail
) -> None:
    owner = merchant(store.tenant)
    matemail.scopes = ["mailbox.read"]
    owner.post(
        f"{EMAIL}matemail/start/",
        {
            "tenant_id": "11111111-2222-3333-4444-555555555555",
            "integration_secret": "integration-secret-123",
        },
        format="json",
    )
    checked = owner.post(f"{EMAIL}matemail/check/", {}, format="json").json()["matemail"]
    assert checked["status"] == "failed"
    assert checked["last_error"] == "send_not_permitted"
    assert owner.post(f"{EMAIL}select/", {"provider": "matemail"}, format="json").status_code == 409


def _connect(owner: Any) -> None:
    owner.post(
        f"{EMAIL}matemail/start/",
        {
            "tenant_id": "11111111-2222-3333-4444-555555555555",
            "integration_secret": "integration-secret-123",
        },
        format="json",
    )
    owner.post(f"{EMAIL}matemail/check/", {}, format="json")
    owner.post(f"{EMAIL}select/", {"provider": "matemail"}, format="json")


def _queue(tenant: Tenant) -> Notification:
    from django.db import transaction

    with tenant_context(tenant), transaction.atomic():
        created = services.emit(
            "order.cancelled",
            event_id="mm",
            values={"order_number": "ORD-1", "order_total": "BDT 1.00"},
            customer=services.Recipient(user=None, email="buyer@example.com"),
        )
    return created[0]


def test_matemail_delivery_outcomes_and_revocation(store: Any, matemail: FakeMateMail) -> None:
    owner = merchant(store.tenant)
    _connect(owner)
    row = _queue(store.tenant)
    with tenant_context(store.tenant):
        matemail.send_error = 429
        throttled = services.deliver(row.pk)
        Notification.objects.filter(pk=row.pk).update(scheduled_at=timezone.now())
        matemail.send_error = None
        sent = services.deliver(row.pk)
        keys = [c[1]["idempotency_key"] for c in matemail.calls if c[0].endswith("send/")]
        bodies = [c[1] for c in matemail.calls if c[0].endswith("send/")]
    assert (throttled, sent) == ("pending", "sent")
    assert keys == [f"matecart-{row.pk}", f"matecart-{row.pk}"]  # the same key on every attempt
    assert "from" not in bodies[0]
    assert "from_address" not in bodies[0]
    with tenant_context(store.tenant):
        from django.db import transaction

        with transaction.atomic():
            second = services.emit(
                "order.confirmed",
                event_id="mm2",
                values={"order_number": "ORD-2", "order_total": "BDT 1.00"},
                customer=services.Recipient(user=None, email="buyer@example.com"),
            )[0]
        matemail.send_error = 409
        unknown = services.deliver(second.pk)
        with transaction.atomic():
            third = services.emit(
                "order.confirmed",
                event_id="mm3",
                values={"order_number": "ORD-3", "order_total": "BDT 1.00"},
                customer=services.Recipient(user=None, email="buyer@example.com"),
            )[0]
        matemail.send_error = 401
        revoked = services.deliver(third.pk)
        connection = MailConnection.objects.get(provider="matemail")
        failure = Notification.objects.get(pk=third.pk).failure_code
    assert unknown == "failed"
    assert revoked == "failed"
    assert failure == "connection_revoked"
    assert connection.status == "revoked"


def test_reconnecting_matemail_rotates_and_disconnects_the_old_token(
    store: Any, matemail: FakeMateMail
) -> None:
    owner = merchant(store.tenant)
    _connect(owner)
    _connect(owner)
    disconnects = [c for c in matemail.calls if c[0].endswith("disconnect/")]
    assert len(disconnects) == 1
    assert disconnects[0][2].startswith("mmc_token")
    gone = owner.post(f"{EMAIL}matemail/disconnect/", {}, format="json").json()
    assert gone["matemail"]["status"] == "not_configured"
    assert gone["active_provider"] is None
