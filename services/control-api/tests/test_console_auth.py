"""Console sign-in: administrators created by personnel, activation, mandatory two-step."""

import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pyotp
import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from control.accounts.models import (
    AdminActionToken,
    AdminRecoveryCode,
    AdminSession,
    AdminTotpDevice,
    AdminUser,
)

from .conftest import Api

pytestmark = pytest.mark.django_db

EMAIL = "ops@netamate.example"
PASSWORD = "console-strong-passphrase-1"
CODE = re.compile(r"\b(\d{6})\b")


def emailed_token() -> str:
    return str(mail.outbox[-1].body).split("token=")[1].split()[0]


def emailed_code() -> str:
    match = CODE.search(str(mail.outbox[-1].body))
    assert match is not None
    return match.group(1)


def next_code(totp: pyotp.TOTP) -> str:
    return totp.at(int(time.time()) + 30)


@pytest.fixture
def invited(django_capture_on_commit_callbacks: Any) -> str:
    """An administrator created by NetaMate personnel; returns the activation token."""
    with django_capture_on_commit_callbacks(execute=True):
        call_command("create_admin", "--email", EMAIL, "--name", "Ops Person")
    return emailed_token()


def enroll_totp(api: Api) -> tuple[pyotp.TOTP, Any]:
    setup = api.post("/api/console/enroll/totp/setup/").json()
    totp = pyotp.TOTP(setup["secret"].replace(" ", ""))
    return totp, api.post("/api/console/enroll/totp/confirm/", {"code": totp.now()})


@pytest.fixture
def admin(api: Api, invited: str) -> pyotp.TOTP:
    """An activated, enrolled administrator signed in on ``api``; returns their authenticator."""
    activated = api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})
    assert activated.status_code == 200
    totp, confirmed = enroll_totp(api)
    assert confirmed.status_code == 200
    return totp


def sign_in(api: Api, password: str = PASSWORD) -> Any:
    return api.post("/api/console/auth/login/", {"email": EMAIL, "password": password})


# --- Creation and activation ----------------------------------------------------------------------


def test_admins_are_created_without_a_password_and_activate_by_link(api: Api, invited: str) -> None:
    user = AdminUser.objects.get()
    assert not user.has_usable_password()
    assert user.activated_at is None
    assert invited not in AdminActionToken.objects.get().token_hash
    assert sign_in(api).status_code == 401

    preview = api.post("/api/console/auth/activate/inspect/", {"token": invited}).json()
    activated = api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})

    assert preview == {"email": EMAIL, "full_name": "Ops Person"}
    assert activated.json()["status"] == "enrollment_required"
    reused = api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})
    assert reused.json()["error"]["code"] == "invalid_link"


def test_activation_links_expire(api: Api, invited: str) -> None:
    AdminActionToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})

    assert response.json()["error"]["code"] == "invalid_link"


def test_weak_admin_passwords_are_refused(api: Api, invited: str) -> None:
    response = api.post("/api/console/auth/activate/", {"token": invited, "password": "short"})

    assert response.json()["error"]["code"] == "weak_password"


def test_nothing_is_reachable_before_enrollment(api: Api, invited: str) -> None:
    api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})

    assert api.get("/api/console/auth/session/").status_code == 401
    assert api.get("/api/console/account/security/").status_code == 401


def test_enrollment_issues_recovery_codes_and_signs_in(api: Api, invited: str) -> None:
    api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})
    setup = api.post("/api/console/enroll/totp/setup/").json()
    assert setup["qr_svg"].startswith("data:image/svg+xml;base64,")

    wrong = api.post("/api/console/enroll/totp/confirm/", {"code": "000000"})
    totp = pyotp.TOTP(setup["secret"].replace(" ", ""))
    done = api.post("/api/console/enroll/totp/confirm/", {"code": totp.now()})

    assert wrong.status_code == 400
    assert done.json()["status"] == "signed_in"
    assert len(done.json()["recovery_codes"]) == 10
    assert api.get("/api/console/auth/session/").json()["email"] == EMAIL
    assert setup["secret"].replace(" ", "") not in AdminTotpDevice.objects.get().secret_encrypted


def test_enrollment_by_email_code(api: Api, invited: str) -> None:
    api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})

    started = api.post("/api/console/enroll/email/start/").json()
    done = api.post(
        "/api/console/enroll/email/confirm/",
        {"challenge_id": started["challenge_id"], "code": emailed_code()},
    )

    assert done.json()["status"] == "signed_in"
    assert AdminUser.objects.get().email_mfa_enabled_at is not None


# --- Sign-in --------------------------------------------------------------------------------------


def test_sign_in_always_requires_the_second_step(
    make_api: Callable[[], Api], admin: pyotp.TOTP
) -> None:
    browser = make_api()

    first = sign_in(browser).json()
    assert first["status"] == "mfa_required"
    assert first["methods"] == ["totp", "recovery_code"]
    assert browser.get("/api/console/auth/session/").status_code == 401

    done = browser.post(
        "/api/console/auth/mfa/verify/", {"method": "totp", "code": next_code(admin)}
    )
    assert done.json()["status"] == "signed_in"
    session = browser.get("/api/console/auth/session/")
    assert session.status_code == 200
    cookie = done.cookies["mc_console"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/console"


def test_failed_sign_in_is_generic_and_rate_limited(api: Api, admin: pyotp.TOTP) -> None:
    unknown = api.post(
        "/api/console/auth/login/", {"email": "nobody@netamate.example", "password": PASSWORD}
    )
    wrong = [sign_in(api, password=f"wrong-{n}").status_code for n in range(5)]
    locked = sign_in(api)

    assert unknown.status_code == 401
    assert wrong == [401] * 5
    assert locked.status_code == 429


def test_totp_replays_and_wrong_codes_are_refused(
    make_api: Callable[[], Api], admin: pyotp.TOTP
) -> None:
    code = next_code(admin)
    first = make_api()
    sign_in(first)
    accepted = first.post("/api/console/auth/mfa/verify/", {"method": "totp", "code": code})
    assert accepted.status_code == 200

    second = make_api()
    sign_in(second)
    statuses = [
        second.post("/api/console/auth/mfa/verify/", {"method": "totp", "code": code}).status_code
        for _ in range(6)
    ]
    assert statuses[:5] == [400] * 5
    assert statuses[5] == 401


def test_recovery_codes_are_single_use(api: Api, make_api: Callable[[], Api], invited: str) -> None:
    api.post("/api/console/auth/activate/", {"token": invited, "password": PASSWORD})
    _, confirmed = enroll_totp(api)
    codes = confirmed.json()["recovery_codes"]

    first = make_api()
    sign_in(first)
    used = first.post(
        "/api/console/auth/mfa/verify/", {"method": "recovery_code", "code": codes[0]}
    )
    second = make_api()
    sign_in(second)
    again = second.post(
        "/api/console/auth/mfa/verify/", {"method": "recovery_code", "code": codes[0]}
    )

    assert used.status_code == 200
    assert again.status_code == 400
    assert not AdminRecoveryCode.objects.filter(code_hash__in=codes).exists()


def test_the_last_method_cannot_be_removed(api: Api, admin: pyotp.TOTP) -> None:
    response = api.post("/api/console/account/mfa/totp/disable/")

    assert response.status_code == 409
    assert AdminTotpDevice.objects.filter(confirmed_at__isnull=False).exists()


def test_a_second_method_can_be_added_then_one_removed(api: Api, admin: pyotp.TOTP) -> None:
    started = api.post("/api/console/account/mfa/email/start/").json()
    api.post(
        "/api/console/account/mfa/email/confirm/",
        {"challenge_id": started["challenge_id"], "code": emailed_code()},
    )

    removed = api.post("/api/console/account/mfa/totp/disable/").json()

    assert removed["totp"] is False
    assert removed["email_codes"] is True
    assert api.post("/api/console/account/mfa/email/disable/").status_code == 409


def test_security_changes_need_a_recent_password(api: Api, admin: pyotp.TOTP) -> None:
    AdminSession.objects.update(authenticated_at=timezone.now() - timedelta(minutes=30))

    blocked = api.post("/api/console/account/mfa/recovery-codes/")
    confirmed = api.post("/api/console/account/reauthenticate/", {"password": PASSWORD})
    allowed = api.post("/api/console/account/mfa/recovery-codes/")

    assert blocked.json()["error"]["code"] == "reauthentication_required"
    assert confirmed.json()["reauthenticated"] is True
    assert len(allowed.json()["recovery_codes"]) == 10


def test_password_reset_keeps_two_step_verification(
    api: Api, make_api: Callable[[], Api], admin: pyotp.TOTP
) -> None:
    other = make_api()
    other.post("/api/console/auth/password/forgot/", {"email": EMAIL})
    unknown = other.post("/api/console/auth/password/forgot/", {"email": "x@netamate.example"})
    token = emailed_token()
    new_password = "a-fresh-console-pass-22"

    reset = other.post(
        "/api/console/auth/password/reset/", {"token": token, "password": new_password}
    )

    assert unknown.status_code == 202
    assert reset.status_code == 204
    assert api.get("/api/console/auth/session/").status_code == 401  # signed out everywhere
    assert sign_in(other, password=new_password).json()["status"] == "mfa_required"


def test_store_credentials_never_work_here(api: Api) -> None:
    response = api.post(
        "/api/console/auth/login/", {"email": "owner@rahimfashion.com", "password": PASSWORD}
    )

    assert response.status_code == 401


def test_logout_revokes_the_session(api: Api, admin: pyotp.TOTP) -> None:
    token = api.client.cookies["mc_console"].value

    api.post("/api/console/auth/logout/")
    api.client.cookies["mc_console"] = token

    assert api.get("/api/console/auth/session/").status_code == 401


def test_csrf_is_enforced(admin: pyotp.TOTP) -> None:
    strict = APIClient(enforce_csrf_checks=True)

    response = strict.post(
        "/api/console/auth/login/", {"email": EMAIL, "password": PASSWORD}, format="json"
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
