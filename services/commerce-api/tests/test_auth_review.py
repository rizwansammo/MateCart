"""Regression coverage from the Phase 03 continuation security review."""

import ast
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone

from commerce.core.logging import RequestIdFilter
from commerce.core.ratelimit import RateLimited
from commerce.core.security import hash_token
from commerce.identity import customers, passwords, sessions
from commerce.identity.errors import InvalidCode, InvalidCredentials
from commerce.identity.models import (
    AccountType,
    ActionToken,
    AuthSession,
    ExternalIdentity,
    OidcAuthorization,
    OtpChallenge,
    RecoveryCode,
    SessionStatus,
    TotpDevice,
)
from commerce.staff.models import StaffInvitation, StaffMembership
from commerce.tenancy.context import tenant_context
from commerce.tenancy.exceptions import CrossTenantAccessError, TenantContextMissing
from commerce.tenancy.models import Tenant

from .factories import make_user
from .helpers import Api, last_code
from .test_staff_auth import owner  # noqa: F401 (shared owner activation fixture)


@pytest.mark.django_db
def test_pending_session_cannot_be_activated_twice_from_a_stale_reference(tenant_a: Tenant) -> None:
    user = make_user(tenant_a, account_type=AccountType.STAFF, email="staff@example.com")
    with tenant_context(tenant_a):
        pending, _ = sessions.start(user, pending_mfa=True)
        sessions.activate(pending, mfa_verified=True)
        with pytest.raises(InvalidCredentials):
            sessions.activate(pending, mfa_verified=True)
        assert AuthSession.objects.filter(status=SessionStatus.ACTIVE).count() == 1


@pytest.mark.django_db
def test_customer_password_step_up_is_rate_limited(tenant_a: Tenant) -> None:
    user = make_user(tenant_a, email="customer@example.com", password="test-password-1234")
    with tenant_context(tenant_a):
        for _ in range(5):
            with pytest.raises(InvalidCode):
                customers.verify_password_or_code(user=user, password="incorrect")
        with pytest.raises(RateLimited):
            customers.verify_password_or_code(user=user, password="test-password-1234")


@pytest.mark.django_db
def test_password_change_has_a_current_password_attempt_limit(tenant_a: Tenant) -> None:
    user = make_user(tenant_a, email="customer@example.com", password="original-password-1234")
    with tenant_context(tenant_a):
        session, _ = sessions.start(user)
        for _ in range(5):
            with pytest.raises(InvalidCredentials):
                passwords.change(
                    user=user,
                    current_password="incorrect",
                    new_password="replacement-password-4567",
                    keep=session,
                )
        with pytest.raises(RateLimited):
            passwords.change(
                user=user,
                current_password="original-password-1234",
                new_password="replacement-password-4567",
                keep=session,
            )
        user.refresh_from_db()
        assert user.check_password("original-password-1234")


@pytest.mark.parametrize("logger", ["django.server", "django.request", "django.security.csrf"])
def test_access_logs_do_not_retain_auth_query_values(logger: str) -> None:
    record = logging.LogRecord(
        logger,
        logging.INFO,
        "test",
        1,
        '"GET /api/account/google/callback/?code=%s&state=private-state HTTP/1.1" 302',
        ("private-code",),
        None,
    )
    RequestIdFilter().filter(record)
    assert "private-code" not in record.getMessage()
    assert "private-state" not in record.getMessage()
    assert "[redacted]" in record.getMessage()


@pytest.mark.usefixtures("owner")
def test_email_mfa_setup_reuses_code_and_resend_obeys_cooldown(api: Api) -> None:
    first = api.post("/api/manage/account/mfa/email/start/")
    before = len(mail.outbox)
    second = api.post("/api/manage/account/mfa/email/start/")
    assert first.json()["challenge_id"] == second.json()["challenge_id"]
    assert len(mail.outbox) == before
    refused = api.post("/api/manage/account/mfa/email/start/", {"resend": True})
    assert refused.status_code == 429
    assert len(mail.outbox) == before


@pytest.mark.usefixtures("owner")
def test_security_change_rotates_browser_credential_without_extending_expiry(
    api: Api,
    deployment_tenant: Tenant,
) -> None:
    old_token = api.client.cookies["mc_staff"].value
    with tenant_context(deployment_tenant):
        expires = AuthSession.objects.get(token_hash=hash_token(old_token)).expires_at
    challenge = api.post("/api/manage/account/mfa/email/start/").json()
    response = api.post(
        "/api/manage/account/mfa/email/confirm/",
        {
            "challenge_id": challenge["challenge_id"],
            "code": last_code(),
        },
    )
    assert response.status_code == 200
    assert api.client.cookies["mc_staff"].value != old_token
    with tenant_context(deployment_tenant):
        assert sessions.resolve(old_token, AccountType.STAFF) is None
        active = sessions.resolve(api.client.cookies["mc_staff"].value, AccountType.STAFF)
        assert active is not None
        assert active.expires_at == expires


@pytest.mark.parametrize("area", ["identity", "staff"])
def test_authentication_paths_cannot_opt_out_of_scoped_data_access(area: str) -> None:
    root = Path(__file__).resolve().parents[1] / "commerce" / area
    forbidden = {"raw", "extra", "cursor", "unscoped_access", "QuerySet"}
    violations: list[str] = []
    for file in root.rglob("*.py"):
        if "migrations" in file.parts:
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else (node.func.id if isinstance(node.func, ast.Name) else "")
                )
                if name in forbidden:
                    violations.append(f"{file.name}:{node.lineno}:{name}")
    assert violations == [], f"Authentication must use tenant-scoped managers: {violations}"


@pytest.mark.django_db
@pytest.mark.parametrize("record_index", range(9))
def test_every_new_authentication_model_preserves_the_tenant_boundary(
    tenant_a: Tenant,
    tenant_b: Tenant,
    record_index: int,
) -> None:
    user = make_user(tenant_b, account_type=AccountType.STAFF, email="foreign@example.test")
    now = timezone.now()
    expiry = now + timedelta(hours=1)
    with tenant_context(tenant_b):
        session, _ = sessions.start(user)
        rows: list[
            AuthSession
            | OtpChallenge
            | ActionToken
            | ExternalIdentity
            | OidcAuthorization
            | TotpDevice
            | RecoveryCode
            | StaffMembership
            | StaffInvitation
        ] = [
            session,
            OtpChallenge.objects.create(
                user=user,
                purpose="mfa_email",
                channel="email",
                destination="foreign@example.test",
                last_sent_at=now,
                expires_at=expiry,
            ),
            ActionToken.objects.create(
                user=user, purpose="password_reset", token_hash="1" * 64, expires_at=expiry
            ),
            ExternalIdentity.objects.create(user=user, provider="google", subject="foreign-id"),
            OidcAuthorization.objects.create(
                user=user,
                provider="google",
                purpose="link",
                state_hash="2" * 64,
                browser_binding_hash="3" * 64,
                nonce="4" * 64,
                code_verifier_encrypted="encrypted-test-value",
                expires_at=expiry,
            ),
            TotpDevice.objects.create(user=user, secret_encrypted="encrypted-test-value"),
            RecoveryCode.objects.create(user=user, code_hash="5" * 64),
            StaffMembership.objects.create(user=user, role="manager", status="active"),
            StaffInvitation.objects.create(
                email="invite@example.test", role="manager", token_hash="6" * 64, expires_at=expiry
            ),
        ]
        row = rows[record_index]
        original_created = row.created_at
    model = type(row)
    with tenant_context(tenant_a):
        assert not model.objects.filter(pk=row.pk).exists()
        assert model.objects.filter(pk=row.pk).update(created_at=now) == 0
        assert model.objects.filter(pk=row.pk).delete()[0] == 0
        with pytest.raises(CrossTenantAccessError):
            row.refresh_from_db()
    with pytest.raises(TenantContextMissing):
        model.objects.filter(pk=row.pk).exists()
    with tenant_context(tenant_b):
        row.refresh_from_db()
        assert row.created_at == original_created


@pytest.mark.django_db
def test_sms_failure_is_not_reported_as_delivery_or_logged_with_provider_details(
    tenant_a: Tenant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Any,
) -> None:
    from commerce.core import delivery

    class BrokenProvider:
        def send(self, **kwargs: Any) -> None:
            raise RuntimeError("private-provider-key-and-phone")

    monkeypatch.setattr(delivery, "sms_provider", lambda: BrokenProvider())
    with (
        pytest.raises(delivery.SmsDeliveryFailed),
        django_capture_on_commit_callbacks(execute=True),
    ):
        delivery.send_sms(to="+12025550123", body="123456", tenant_id=tenant_a.pk, purpose="test")
    assert "private-provider-key-and-phone" not in caplog.text
    assert "123456" not in caplog.text
