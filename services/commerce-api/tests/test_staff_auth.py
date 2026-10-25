"""Merchant staff: owner activation, invitations, roles, sign-in with MFA and account security."""

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pyotp
import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from commerce.audit.models import AuditEvent
from commerce.identity.models import AccountType, AuthSession, RecoveryCode, TotpDevice, User
from commerce.staff.models import StaffInvitation, StaffMembership
from commerce.staff.permissions import StaffRole
from commerce.staff.services import invite_owner
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .helpers import Api, last_code

pytestmark = pytest.mark.usefixtures("store")

OWNER_EMAIL = "owner@rahimfashion.com"
PASSWORD = "owner-strong-passphrase-1"
STAFF_COOKIE = "mc_staff"


def invitation_token() -> str:
    return mail.outbox[-1].body.split("token=")[1].split()[0]


def accept(api: Api, token: str, name: str = "Rahim Owner", password: str = PASSWORD) -> Any:
    return api.post(
        "/api/manage/invitation/accept/",
        {"token": token, "full_name": name, "password": password},
    )


@pytest.fixture
def owner(api: Api, deployment_tenant: Tenant, django_capture_on_commit_callbacks: Any) -> User:
    """The store owner, activated through the provisioning invitation, signed in on ``api``."""
    with django_capture_on_commit_callbacks(execute=True):
        call_command("invite_owner", "--email", OWNER_EMAIL)
    assert accept(api, invitation_token()).status_code == 201
    with tenant_context(deployment_tenant):
        return User.objects.get(account_type=AccountType.STAFF, email=OWNER_EMAIL)


def invite_member(owner_api: Api, email: str, role: str = StaffRole.MANAGER) -> str:
    response = owner_api.post("/api/manage/team/invitations/", {"email": email, "role": role})
    assert response.status_code == 201, response.json()
    return invitation_token()


def member_api(
    owner_api: Api, make_api: Callable[[], Api], email: str, role: str = StaffRole.MANAGER
) -> Api:
    token = invite_member(owner_api, email, role)
    browser = make_api()
    assert (
        accept(browser, token, name="Team Member", password="member-passphrase-22").status_code
        == 201
    )
    return browser


def sign_in(api: Api, email: str = OWNER_EMAIL, password: str = PASSWORD) -> Any:
    return api.post("/api/manage/auth/login/", {"email": email, "password": password})


def enable_totp(api: Api) -> tuple[pyotp.TOTP, list[str]]:
    setup = api.post("/api/manage/account/mfa/totp/setup/").json()
    totp = pyotp.TOTP(setup["secret"].replace(" ", ""))
    confirmed = api.post("/api/manage/account/mfa/totp/confirm/", {"code": totp.now()})
    assert confirmed.status_code == 200, confirmed.json()
    return totp, confirmed.json()["recovery_codes"]


def next_code(totp: pyotp.TOTP) -> str:
    """A code from the next time step (the current one was just used; replays are refused)."""
    return totp.at(int(time.time()) + 30)


# --- Owner activation and invitations -------------------------------------------------------------


def test_owner_is_created_only_through_the_provisioning_invitation(
    api: Api, owner: User, deployment_tenant: Tenant
) -> None:
    session = api.get("/api/manage/auth/session/").json()

    assert session["role"] == "owner"
    assert "staff.manage" in session["permissions"]
    assert session["store_name"] == "Rahim Fashion"
    assert session["mfa_enabled"] is False
    with tenant_context(deployment_tenant):
        assert owner.email_verified_at is not None
        assert StaffMembership.objects.get().role == StaffRole.OWNER
        assert AuditEvent.objects.filter(action="staff.owner.invited").exists()


def test_a_store_can_only_have_one_owner(owner: User, deployment_tenant: Tenant) -> None:
    with tenant_context(deployment_tenant), pytest.raises(Exception, match="already has an owner"):
        invite_owner(email="second@example.com")


def test_there_is_no_public_staff_registration(api: Api) -> None:
    for path in ("/api/manage/register/", "/api/manage/auth/register/"):
        assert api.post(path, {"email": "x@example.com"}).status_code == 404


def test_invitations_are_single_use(api: Api, make_api: Callable[[], Api], owner: User) -> None:
    token = invite_member(api, "manager@example.com")
    first = accept(make_api(), token, name="Manager", password="member-passphrase-22")
    again = accept(make_api(), token, name="Mallory", password="member-passphrase-22")

    assert first.status_code == 201
    assert again.json()["error"]["code"] == "invalid_link"


def test_expired_and_revoked_invitations_cannot_be_used(
    api: Api, make_api: Callable[[], Api], owner: User, deployment_tenant: Tenant
) -> None:
    expired = invite_member(api, "late@example.com")
    with tenant_context(deployment_tenant):
        StaffInvitation.objects.filter(email="late@example.com").update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    revoked_token = invite_member(api, "revoked@example.com")
    with tenant_context(deployment_tenant):
        revoked = StaffInvitation.objects.get(email="revoked@example.com")
    assert api.post(f"/api/manage/team/invitations/{revoked.pk}/revoke/").status_code == 204

    for token in (expired, revoked_token):
        browser = make_api()
        assert browser.post("/api/manage/invitation/inspect/", {"token": token}).status_code == 400
        assert accept(browser, token).json()["error"]["code"] == "invalid_link"


def test_invitation_preview_shows_the_store_and_role(
    api: Api, make_api: Callable[[], Api], owner: User
) -> None:
    token = invite_member(api, "editor@example.com", StaffRole.CONTENT_EDITOR)

    preview = make_api().post("/api/manage/invitation/inspect/", {"token": token}).json()

    assert preview["email"] == "editor@example.com"
    assert preview["role_label"] == "Content editor"
    assert preview["store_name"] == "Rahim Fashion"


def test_duplicate_invitations_are_refused(api: Api, owner: User) -> None:
    invite_member(api, "dup@example.com")

    again = api.post(
        "/api/manage/team/invitations/", {"email": "DUP@example.com", "role": "manager"}
    )
    existing = api.post("/api/manage/team/invitations/", {"email": OWNER_EMAIL, "role": "manager"})

    assert again.status_code == existing.status_code == 409


def test_resending_replaces_the_link(
    api: Api, make_api: Callable[[], Api], owner: User, deployment_tenant: Tenant
) -> None:
    old_token = invite_member(api, "resend@example.com")
    with tenant_context(deployment_tenant):
        invitation = StaffInvitation.objects.get(email="resend@example.com")
    too_soon = api.post(f"/api/manage/team/invitations/{invitation.pk}/resend/")
    with tenant_context(deployment_tenant):
        StaffInvitation.objects.filter(pk=invitation.pk).update(
            last_sent_at=timezone.now() - timedelta(minutes=5)
        )
    resent = api.post(f"/api/manage/team/invitations/{invitation.pk}/resend/")

    assert too_soon.status_code == 400
    assert resent.status_code == 200
    assert accept(make_api(), old_token).json()["error"]["code"] == "invalid_link"
    assert accept(make_api(), invitation_token()).status_code == 201


@pytest.mark.parametrize("role", ["owner", "superuser", ""])
def test_owner_role_can_never_be_granted(api: Api, owner: User, role: str) -> None:
    response = api.post("/api/manage/team/invitations/", {"email": "x@example.com", "role": role})

    assert response.status_code == 400


# --- Roles and permissions ------------------------------------------------------------------------


def test_only_the_owner_manages_staff(api: Api, make_api: Callable[[], Api], owner: User) -> None:
    manager = member_api(api, make_api, "manager@example.com", StaffRole.MANAGER)

    session = manager.get("/api/manage/auth/session/").json()
    assert session["role"] == "manager"
    assert "staff.manage" not in session["permissions"]
    assert "payouts.manage" not in session["permissions"]
    assert manager.get("/api/manage/team/").status_code == 403
    assert (
        manager.post(
            "/api/manage/team/invitations/", {"email": "x@example.com", "role": "manager"}
        ).status_code
        == 403
    )


def test_role_changes_and_their_limits(
    api: Api, make_api: Callable[[], Api], owner: User, deployment_tenant: Tenant
) -> None:
    member_api(api, make_api, "handler@example.com", StaffRole.ORDER_HANDLER)
    team = api.get("/api/manage/team/").json()
    member = next(m for m in team["members"] if m["user"]["email"] == "handler@example.com")
    owner_member = next(m for m in team["members"] if m["role"] == "owner")

    changed = api.post(f"/api/manage/team/members/{member['id']}/role/", {"role": "content_editor"})
    to_owner = api.post(f"/api/manage/team/members/{member['id']}/role/", {"role": "owner"})
    own = api.post(f"/api/manage/team/members/{owner_member['id']}/role/", {"role": "manager"})

    assert changed.status_code == 200
    assert changed.json()["role"] == "content_editor"
    assert to_owner.status_code == 400
    assert own.status_code == 400
    assert {r["value"] for r in team["roles"]} == {"manager", "order_handler", "content_editor"}
    with tenant_context(deployment_tenant):
        event = AuditEvent.objects.get(action="staff.membership.role_changed")
    assert event.changes == {"role": {"from": "order_handler", "to": "content_editor"}}


def test_suspension_ends_access_immediately(
    api: Api, make_api: Callable[[], Api], owner: User
) -> None:
    manager = member_api(api, make_api, "manager@example.com")
    member_id = next(
        m["id"] for m in api.get("/api/manage/team/").json()["members"] if m["role"] == "manager"
    )

    assert (
        api.post(f"/api/manage/team/members/{member_id}/suspend/").json()["status"] == "suspended"
    )
    assert manager.get("/api/manage/auth/session/").status_code == 401
    suspended_login = sign_in(manager, "manager@example.com", "member-passphrase-22")
    assert suspended_login.json()["error"]["code"] == "access_suspended"

    api.post(f"/api/manage/team/members/{member_id}/reactivate/")
    assert sign_in(manager, "manager@example.com", "member-passphrase-22").status_code == 200


def test_staff_sessions_never_reach_customer_endpoints(api: Api, owner: User) -> None:
    token = api.client.cookies[STAFF_COOKIE].value
    assert api.client.cookies[STAFF_COOKIE]["path"] == "/api/manage"
    api.client.cookies["mc_customer"] = token

    assert api.get("/api/account/me/").status_code == 401


# --- Sign-in and MFA ------------------------------------------------------------------------------


def test_sign_in_without_mfa(api: Api, make_api: Callable[[], Api], owner: User) -> None:
    browser = make_api()

    result = sign_in(browser).json()

    assert result == {"status": "signed_in", "methods": [], "email_hint": ""}
    assert browser.get("/api/manage/auth/session/").status_code == 200


def test_totp_setup_wizard_and_mandatory_second_step(
    api: Api, make_api: Callable[[], Api], owner: User, deployment_tenant: Tenant
) -> None:
    setup = api.post("/api/manage/account/mfa/totp/setup/").json()
    assert setup["qr_svg"].startswith("data:image/svg+xml;base64,")
    assert setup["uri"].startswith("otpauth://totp/")
    assert len(setup["secret"].replace(" ", "")) == 32
    totp = pyotp.TOTP(setup["secret"].replace(" ", ""))

    wrong = api.post("/api/manage/account/mfa/totp/confirm/", {"code": "000000"})
    assert wrong.status_code == 400
    confirmed = api.post("/api/manage/account/mfa/totp/confirm/", {"code": totp.now()}).json()
    codes = confirmed["recovery_codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10
    with tenant_context(deployment_tenant):
        stored = TotpDevice.objects.get()
        assert setup["secret"].replace(" ", "") not in stored.secret_encrypted
        assert not RecoveryCode.objects.filter(code_hash__in=codes).exists()

    browser = make_api()
    first_step = sign_in(browser).json()
    assert first_step["status"] == "mfa_required"
    assert first_step["methods"] == ["totp", "recovery_code"]
    # A password alone grants nothing.
    assert browser.get("/api/manage/auth/session/").status_code == 401
    assert browser.get("/api/manage/team/").status_code == 401

    done = browser.post("/api/manage/auth/mfa/verify/", {"method": "totp", "code": next_code(totp)})
    assert done.status_code == 200
    assert browser.get("/api/manage/auth/session/").json()["mfa_enabled"] is True


def test_totp_codes_cannot_be_replayed(api: Api, make_api: Callable[[], Api], owner: User) -> None:
    totp, _ = enable_totp(api)
    code = next_code(totp)

    first = make_api()
    sign_in(first)
    assert (
        first.post("/api/manage/auth/mfa/verify/", {"method": "totp", "code": code}).status_code
        == 200
    )

    second = make_api()
    sign_in(second)
    replay = second.post("/api/manage/auth/mfa/verify/", {"method": "totp", "code": code})
    assert replay.json()["error"]["code"] == "invalid_code"


def test_recovery_codes_work_once_and_regeneration_replaces_them(
    api: Api, make_api: Callable[[], Api], owner: User, deployment_tenant: Tenant
) -> None:
    _, codes = enable_totp(api)

    browser = make_api()
    sign_in(browser)
    used = browser.post(
        "/api/manage/auth/mfa/verify/", {"method": "recovery_code", "code": codes[0].lower()}
    )
    assert used.status_code == 200
    again = make_api()
    sign_in(again)
    assert (
        again.post(
            "/api/manage/auth/mfa/verify/", {"method": "recovery_code", "code": codes[0]}
        ).status_code
        == 400
    )
    assert api.get("/api/manage/account/security/").json()["recovery_codes_remaining"] == 9

    fresh = api.post("/api/manage/account/mfa/recovery-codes/").json()["recovery_codes"]
    assert set(fresh).isdisjoint(codes)
    third = make_api()
    sign_in(third)
    assert (
        third.post(
            "/api/manage/auth/mfa/verify/", {"method": "recovery_code", "code": codes[1]}
        ).status_code
        == 400
    )
    with tenant_context(deployment_tenant):
        assert AuditEvent.objects.filter(action="account.mfa.recovery_code_used").count() == 1


def test_email_codes_as_the_second_step(api: Api, make_api: Callable[[], Api], owner: User) -> None:
    started = api.post("/api/manage/account/mfa/email/start/").json()
    assert started["destination"].startswith("o")
    enabled = api.post(
        "/api/manage/account/mfa/email/confirm/",
        {"challenge_id": started["challenge_id"], "code": last_code(OWNER_EMAIL)},
    )
    assert enabled.status_code == 200
    assert len(enabled.json()["recovery_codes"]) == 10

    browser = make_api()
    first_step = sign_in(browser).json()
    assert first_step["methods"] == ["email", "recovery_code"]
    assert first_step["email_hint"].endswith("@rahimfashion.com")
    sent = browser.post("/api/manage/auth/mfa/email/", {}).json()
    reused = browser.post("/api/manage/auth/mfa/email/", {}).json()
    assert sent["challenge_id"] == reused["challenge_id"]  # reloading never re-sends
    too_soon = browser.post("/api/manage/auth/mfa/email/", {"resend": True})
    assert too_soon.status_code == 429

    done = browser.post(
        "/api/manage/auth/mfa/verify/",
        {"method": "email", "challenge_id": sent["challenge_id"], "code": last_code(OWNER_EMAIL)},
    )
    assert done.status_code == 200


def test_email_codes_are_bound_to_their_sign_in(
    api: Api, make_api: Callable[[], Api], owner: User
) -> None:
    started = api.post("/api/manage/account/mfa/email/start/").json()
    api.post(
        "/api/manage/account/mfa/email/confirm/",
        {"challenge_id": started["challenge_id"], "code": last_code(OWNER_EMAIL)},
    )
    victim = make_api()
    sign_in(victim)
    sent = victim.post("/api/manage/auth/mfa/email/", {}).json()
    code = last_code(OWNER_EMAIL)

    attacker = make_api()
    sign_in(attacker)
    stolen = attacker.post(
        "/api/manage/auth/mfa/verify/",
        {"method": "email", "challenge_id": sent["challenge_id"], "code": code},
    )

    assert stolen.status_code == 400


def test_too_many_wrong_second_steps_end_the_attempt(
    api: Api, make_api: Callable[[], Api], owner: User
) -> None:
    enable_totp(api)
    browser = make_api()
    sign_in(browser)

    statuses = [
        browser.post(
            "/api/manage/auth/mfa/verify/", {"method": "totp", "code": "000000"}
        ).status_code
        for _ in range(6)
    ]

    assert statuses[:5] == [400] * 5
    assert statuses[5] == 401  # the pending session was revoked


def test_pending_sessions_expire_quickly(
    api: Api, make_api: Callable[[], Api], owner: User, deployment_tenant: Tenant
) -> None:
    totp, _ = enable_totp(api)
    browser = make_api()
    sign_in(browser)
    with tenant_context(deployment_tenant):
        AuthSession.objects.filter(status="pending_mfa").update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    late = browser.post("/api/manage/auth/mfa/verify/", {"method": "totp", "code": next_code(totp)})

    assert late.status_code == 401


def test_mfa_changes_need_a_recent_password(
    api: Api, owner: User, deployment_tenant: Tenant
) -> None:
    with tenant_context(deployment_tenant):
        AuthSession.objects.update(authenticated_at=timezone.now() - timedelta(minutes=30))

    blocked = api.post("/api/manage/account/mfa/totp/setup/")
    security = api.get("/api/manage/account/security/").json()
    wrong = api.post("/api/manage/account/reauthenticate/", {"password": "nope"})
    right = api.post("/api/manage/account/reauthenticate/", {"password": PASSWORD})

    assert blocked.json()["error"]["code"] == "reauthentication_required"
    assert security["reauthenticated"] is False
    assert wrong.status_code == 401
    assert right.json()["reauthenticated"] is True
    assert api.post("/api/manage/account/mfa/totp/setup/").status_code == 200


def test_turning_mfa_on_signs_out_other_sessions(
    api: Api, make_api: Callable[[], Api], owner: User
) -> None:
    laptop = make_api()
    sign_in(laptop)

    enable_totp(api)

    assert laptop.get("/api/manage/auth/session/").status_code == 401
    assert api.get("/api/manage/auth/session/").status_code == 200


def test_turning_the_last_method_off_removes_recovery_codes(
    api: Api, owner: User, deployment_tenant: Tenant
) -> None:
    enable_totp(api)

    disabled = api.post("/api/manage/account/mfa/totp/disable/").json()

    assert disabled == {
        "email": OWNER_EMAIL,
        "totp": False,
        "email_codes": False,
        "recovery_codes_remaining": 0,
        "reauthenticated": True,
    }
    with tenant_context(deployment_tenant):
        assert not RecoveryCode.objects.exists()
        assert AuditEvent.objects.filter(action="account.mfa.disabled").exists()


def test_staff_password_reset_is_realm_scoped(
    api: Api, make_api: Callable[[], Api], owner: User
) -> None:
    customer_reset = make_api().post("/api/account/password/forgot/", {"email": OWNER_EMAIL})
    assert customer_reset.status_code == 202
    sent_before = len(mail.outbox)

    staff_reset = make_api().post("/api/manage/auth/password/forgot/", {"email": OWNER_EMAIL})

    assert staff_reset.status_code == 202
    assert len(mail.outbox) == sent_before + 1
    assert "/manage/reset-password?token=" in mail.outbox[-1].body


def test_audit_events_carry_no_secrets(api: Api, owner: User, deployment_tenant: Tenant) -> None:
    _, codes = enable_totp(api)

    with tenant_context(deployment_tenant):
        payloads = " ".join(str(e.changes) for e in AuditEvent.objects.all())
    assert all(code not in payloads for code in codes)
    assert PASSWORD not in payloads
