"""Security primitives and cross-cutting protections: CSRF, tenant isolation, rate limits."""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pyotp
import pytest
from django.apps import apps
from django.core import mail
from django.test import RequestFactory
from django.utils import timezone
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.core import ratelimit, security, totp
from commerce.core.http import client_ip, safe_next_path
from commerce.identity import otp, sessions
from commerce.identity.models import (
    AccountType,
    AuthSession,
    OtpChallenge,
    OtpChannel,
    OtpPurpose,
    User,
)
from commerce.staff.models import StaffInvitation
from commerce.staff.permissions import ASSIGNABLE_ROLES, ROLE_PERMISSIONS, Permission, StaffRole
from commerce.staff.services import invite_owner
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant, TenantOwnedModel

from .factories import make_store_settings, make_user
from .helpers import Api

PASSWORD = "correct-horse-battery-9"


# --- Primitives -----------------------------------------------------------------------------------


def test_encryption_round_trips_and_detects_tampering() -> None:
    sealed = security.encrypt("JBSWY3DPEHPK3PXP")

    assert sealed != "JBSWY3DPEHPK3PXP"
    assert security.decrypt(sealed) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(security.DecryptionError):
        security.decrypt(sealed[:-4] + "AAAA")


def test_keyed_hashes_are_purpose_bound() -> None:
    assert security.keyed_hash("a", "123456") != security.keyed_hash("b", "123456")
    assert security.matches(security.keyed_hash("a", "123456"), "a", "123456")
    assert not security.matches(security.keyed_hash("a", "123456"), "a", "123457")


def test_recovery_codes_avoid_ambiguous_characters() -> None:
    codes = {security.new_recovery_code() for _ in range(200)}

    assert len(codes) == 200
    assert all(len(code) == 11 and code[5] == "-" for code in codes)
    assert not any(set(code) & set("01OIL") for code in codes)
    assert security.normalize_recovery_code(" abcde fghjk ") == "ABCDE-FGHJK"


def test_totp_accepts_one_step_of_drift_and_refuses_replays() -> None:
    secret = totp.new_secret()
    generator = pyotp.TOTP(secret)
    now = int(time.time())

    step = totp.verify(secret, generator.at(now), last_used_step=None, now=now)
    assert step is not None
    assert totp.verify(secret, generator.at(now), last_used_step=step, now=now) is None
    assert totp.verify(secret, generator.at(now - 30), last_used_step=None, now=now) is not None
    assert totp.verify(secret, generator.at(now - 90), last_used_step=None, now=now) is None
    assert totp.verify(secret, "12345", last_used_step=None, now=now) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/account/orders", "/account/orders"),
        ("/account?tab=1", "/account?tab=1"),
        ("https://evil.example.com/", "/account"),
        ("//evil.example.com", "/account"),
        ("/\\evil.example.com", "/account"),
        ("javascript:alert(1)", "/account"),
        ("", "/account"),
        (None, "/account"),
    ],
)
def test_redirect_targets_stay_on_this_site(value: str | None, expected: str) -> None:
    assert safe_next_path(value, default="/account") == expected


def test_client_ip_trusts_only_configured_proxies(settings: Settings) -> None:
    request = RequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9", REMOTE_ADDR="10.0.0.2"
    )

    settings.MATECART_TRUSTED_PROXY_COUNT = 0
    assert client_ip(request) == "10.0.0.2"
    settings.MATECART_TRUSTED_PROXY_COUNT = 1
    assert client_ip(request) == "203.0.113.9"  # the spoofed first entry is ignored


def test_rate_limits_are_namespaced_by_tenant(tenant_a: Tenant, tenant_b: Tenant) -> None:
    with tenant_context(tenant_a):
        for _ in range(3):
            ratelimit.hit("login-ip", "203.0.113.9", window=60)
        assert ratelimit.count("login-ip", "203.0.113.9") == 3
    with tenant_context(tenant_b):
        assert ratelimit.count("login-ip", "203.0.113.9") == 0


# --- CSRF -----------------------------------------------------------------------------------------


@pytest.fixture
def strict_client() -> APIClient:
    return APIClient(enforce_csrf_checks=True)


@pytest.mark.usefixtures("store")
@pytest.mark.parametrize(
    "path",
    [
        "/api/account/login/",
        "/api/account/register/",
        "/api/account/logout/",
        "/api/manage/auth/login/",
        "/api/manage/invitation/accept/",
    ],
)
def test_state_changing_requests_need_the_csrf_token(strict_client: APIClient, path: str) -> None:
    response = strict_client.post(path, {}, format="json")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


@pytest.mark.usefixtures("store")
def test_requests_with_the_csrf_token_are_accepted(
    strict_client: APIClient, deployment_tenant: Tenant
) -> None:
    make_user(deployment_tenant, email="rahim@example.com", password=PASSWORD)
    token = strict_client.get("/api/csrf/").json()["csrf_token"]
    assert strict_client.cookies["mc_csrf"].value

    response = strict_client.post(
        "/api/account/login/",
        {"email": "rahim@example.com", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )

    assert response.status_code == 200


@pytest.mark.usefixtures("store")
def test_cross_origin_posts_are_refused_even_with_a_token(
    strict_client: APIClient, settings: Settings
) -> None:
    settings.CSRF_TRUSTED_ORIGINS = ["https://shop.example.com"]
    token = strict_client.get("/api/csrf/").json()["csrf_token"]

    response = strict_client.post(
        "/api/account/logout/",
        {},
        format="json",
        HTTP_X_CSRFTOKEN=token,
        HTTP_ORIGIN="https://evil.example.com",
    )

    assert response.status_code == 403


# --- Tenant isolation -----------------------------------------------------------------------------


@pytest.fixture
def foreign_tenant(tenant_b: Tenant) -> Tenant:
    make_store_settings(tenant_b, name="Other Store")
    return tenant_b


@pytest.mark.usefixtures("store")
def test_sessions_from_another_store_are_worthless(
    api: Api, deployment_tenant: Tenant, foreign_tenant: Tenant
) -> None:
    other = make_user(foreign_tenant, email="rahim@example.com", password=PASSWORD)
    with tenant_context(foreign_tenant):
        _, token = sessions.start(other)
        assert sessions.resolve(token, AccountType.CUSTOMER) is not None
    with tenant_context(deployment_tenant):
        assert sessions.resolve(token, AccountType.CUSTOMER) is None

    api.client.cookies["mc_customer"] = token
    assert api.get("/api/account/me/").status_code == 401


@pytest.mark.usefixtures("store")
def test_credentials_from_another_store_do_not_sign_in(api: Api, foreign_tenant: Tenant) -> None:
    make_user(foreign_tenant, email="rahim@example.com", password=PASSWORD)

    response = api.post("/api/account/login/", {"email": "rahim@example.com", "password": PASSWORD})

    assert response.status_code == 401


@pytest.mark.usefixtures("store")
def test_codes_from_another_store_cannot_be_verified(
    api: Api, foreign_tenant: Tenant, django_capture_on_commit_callbacks: Any
) -> None:
    with tenant_context(foreign_tenant), django_capture_on_commit_callbacks(execute=True):
        challenge = otp.issue(
            purpose=OtpPurpose.REGISTRATION,
            channel=OtpChannel.EMAIL,
            destination="rahim@example.com",
            store_name="Other Store",
            context={"full_name": "X", "password": security.encrypt("unused")},
        )
    code = mail.outbox[-1].body.split("code: ")[1][:6]

    response = api.post(
        "/api/account/register/verify/", {"challenge_id": str(challenge.pk), "code": code}
    )

    assert response.json()["error"]["code"] == "code_expired"
    with tenant_context(foreign_tenant):
        assert OtpChallenge.objects.get().attempts == 0


@pytest.mark.usefixtures("store")
def test_invitations_from_another_store_are_invalid(
    api: Api, foreign_tenant: Tenant, django_capture_on_commit_callbacks: Any
) -> None:
    with tenant_context(foreign_tenant), django_capture_on_commit_callbacks(execute=True):
        invite_owner(email="owner@other.example.com")
    token = mail.outbox[-1].body.split("token=")[1].split()[0]

    inspect = api.post("/api/manage/invitation/inspect/", {"token": token})
    accept = api.post(
        "/api/manage/invitation/accept/",
        {"token": token, "full_name": "Intruder", "password": "intruder-passphrase-1"},
    )

    assert inspect.status_code == accept.status_code == 400
    with tenant_context(foreign_tenant):
        assert StaffInvitation.objects.get().accepted_at is None


@pytest.mark.usefixtures("store")
def test_owner_of_one_store_cannot_manage_another(
    api: Api,
    make_api: Callable[[], Api],
    deployment_tenant: Tenant,
    foreign_tenant: Tenant,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with tenant_context(foreign_tenant), django_capture_on_commit_callbacks(execute=True):
        invite_owner(email="owner@other.example.com")
    with tenant_context(foreign_tenant):
        foreign_invitation = StaffInvitation.objects.get()
    with tenant_context(deployment_tenant), django_capture_on_commit_callbacks(execute=True):
        invite_owner(email="owner@rahimfashion.com")
    token = mail.outbox[-1].body.split("token=")[1].split()[0]
    api.post(
        "/api/manage/invitation/accept/",
        {"token": token, "full_name": "Owner", "password": "owner-passphrase-1234"},
    )

    revoke = api.post(f"/api/manage/team/invitations/{foreign_invitation.pk}/revoke/")

    assert revoke.status_code == 400
    with tenant_context(foreign_tenant):
        assert StaffInvitation.objects.get().revoked_at is None


def test_auth_tables_are_tenant_owned() -> None:
    names = {
        "identity.AuthSession",
        "identity.OtpChallenge",
        "identity.ActionToken",
        "identity.ExternalIdentity",
        "identity.OidcAuthorization",
        "identity.TotpDevice",
        "identity.RecoveryCode",
        "staff.StaffMembership",
        "staff.StaffInvitation",
    }
    for name in names:
        assert issubclass(apps.get_model(name), TenantOwnedModel), name


@pytest.mark.usefixtures("store")
def test_session_rows_respect_the_tenant_scope(
    deployment_tenant: Tenant, foreign_tenant: Tenant
) -> None:
    other = make_user(foreign_tenant, email="x@example.com", account_type=AccountType.STAFF)
    with tenant_context(foreign_tenant):
        sessions.start(other)
    with tenant_context(deployment_tenant):
        assert not AuthSession.objects.exists()
        assert not User.objects.filter(account_type=AccountType.STAFF).exists()


def test_owner_role_is_not_assignable() -> None:
    assert StaffRole.OWNER not in ASSIGNABLE_ROLES
    assert all(Permission.STAFF_MANAGE not in ROLE_PERMISSIONS[r] for r in ASSIGNABLE_ROLES)


def test_expired_sessions_are_not_resolved(deployment_tenant: Tenant) -> None:
    user = make_user(deployment_tenant, email="x@example.com", password=PASSWORD)
    with tenant_context(deployment_tenant):
        session, token = sessions.start(user)
        AuthSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(1))
        assert sessions.resolve(token, AccountType.CUSTOMER) is None
