"""Customer accounts: email + password, phone codes, sessions, linking and anti-enumeration."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone

from commerce.identity import otp
from commerce.identity.models import (
    AccountType,
    ActionToken,
    AuthSession,
    OtpChallenge,
    User,
)
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_user
from .helpers import Api, last_code

pytestmark = pytest.mark.usefixtures("store")

PASSWORD = "correct-horse-battery-9"
CUSTOMER_COOKIE = "mc_customer"


def register(api: Api, email: str = "rahim@example.com", password: str = PASSWORD) -> Any:
    return api.post(
        "/api/account/register/",
        {"full_name": "Rahim Uddin", "email": email, "password": password},
    )


def customer(tenant: Tenant, **fields: Any) -> User:
    fields.setdefault("email", "rahim@example.com")
    fields.setdefault("password", PASSWORD)
    user = make_user(tenant, account_type=AccountType.CUSTOMER, **fields)
    with tenant_context(tenant):
        if user.email:
            User.objects.filter(pk=user.pk).update(email_verified_at=timezone.now())
        if user.phone:
            User.objects.filter(pk=user.pk).update(phone_verified_at=timezone.now())
    return user


def sign_in(api: Api, email: str = "rahim@example.com", password: str = PASSWORD) -> Any:
    return api.post("/api/account/login/", {"email": email, "password": password})


# --- Registration ---------------------------------------------------------------------------------


def test_registration_creates_the_account_only_after_the_emailed_code(
    api: Api, deployment_tenant: Tenant
) -> None:
    started = register(api)

    assert started.status_code == 202
    assert started.json()["destination"].endswith("@example.com")
    assert started.json()["resend_in"] > 0
    with tenant_context(deployment_tenant):
        assert not User.objects.exists()
    assert len(mail.outbox) == 1
    assert "Rahim Fashion" in mail.outbox[0].subject

    done = api.post(
        "/api/account/register/verify/",
        {"challenge_id": started.json()["challenge_id"], "code": last_code()},
    )

    assert done.status_code == 201
    assert done.json()["email"] == "rahim@example.com"
    assert done.json()["email_verified"] is True
    assert done.json()["has_password"] is True
    cookie = done.cookies[CUSTOMER_COOKIE]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/"
    with tenant_context(deployment_tenant):
        user = User.objects.get()
        assert user.check_password(PASSWORD)
        assert OtpChallenge.objects.get().context == {}  # the pending password hash is gone
    assert api.get("/api/account/me/").json()["full_name"] == "Rahim Uddin"


def test_codes_and_passwords_never_reach_storage_or_logs_in_clear(
    api: Api, deployment_tenant: Tenant, caplog: pytest.LogCaptureFixture
) -> None:
    started = register(api)
    code = last_code()

    with tenant_context(deployment_tenant):
        challenge = OtpChallenge.objects.get(pk=started.json()["challenge_id"])
    assert code not in challenge.code_hash
    assert PASSWORD not in str(challenge.context)
    assert code not in caplog.text
    assert PASSWORD not in caplog.text
    assert "rahim@example.com" not in caplog.text


def test_weak_passwords_are_refused_before_anything_is_sent(api: Api) -> None:
    response = register(api, password="12345678")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "weak_password"
    assert mail.outbox == []


def test_registering_an_existing_email_reveals_nothing(api: Api, deployment_tenant: Tenant) -> None:
    customer(deployment_tenant)

    response = register(api)

    assert response.status_code == 202
    assert set(response.json()) == {
        "challenge_id",
        "channel",
        "destination",
        "resend_in",
        "expires_in",
    }
    # The mailbox owner learns they already have an account; no code exists that could work.
    assert len(mail.outbox) == 1
    assert "already have an account" in mail.outbox[0].subject
    for guess in ("000000", "123456"):
        attempt = api.post(
            "/api/account/register/verify/",
            {"challenge_id": response.json()["challenge_id"], "code": guess},
        )
        assert attempt.status_code == 400
    with tenant_context(deployment_tenant):
        assert User.objects.count() == 1


def test_wrong_codes_are_limited_and_counted(api: Api, deployment_tenant: Tenant) -> None:
    started = register(api).json()
    real = last_code()
    wrong = "000000" if real != "000000" else "111111"

    codes = []
    for _ in range(otp.MAX_ATTEMPTS):
        reply = api.post(
            "/api/account/register/verify/",
            {"challenge_id": started["challenge_id"], "code": wrong},
        )
        codes.append(reply.json()["error"]["code"])

    assert codes == ["invalid_code"] * (otp.MAX_ATTEMPTS - 1) + ["code_expired"]
    # Even the right code no longer works once the attempts are used up.
    final = api.post(
        "/api/account/register/verify/", {"challenge_id": started["challenge_id"], "code": real}
    )
    assert final.json()["error"]["code"] == "code_expired"
    with tenant_context(deployment_tenant):
        assert not User.objects.exists()


def test_codes_are_single_use_and_expire(api: Api, deployment_tenant: Tenant) -> None:
    started = register(api).json()
    code = last_code()
    assert (
        api.post(
            "/api/account/register/verify/", {"challenge_id": started["challenge_id"], "code": code}
        ).status_code
        == 201
    )
    replay = api.post(
        "/api/account/register/verify/", {"challenge_id": started["challenge_id"], "code": code}
    )
    assert replay.json()["error"]["code"] == "code_expired"

    late = register(api, email="karim@example.com").json()
    with tenant_context(deployment_tenant):
        OtpChallenge.objects.filter(pk=late["challenge_id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    expired = api.post(
        "/api/account/register/verify/",
        {"challenge_id": late["challenge_id"], "code": last_code("karim@example.com")},
    )
    assert expired.json()["error"]["code"] == "code_expired"


def test_resend_has_a_cooldown_and_replaces_the_code(api: Api, deployment_tenant: Tenant) -> None:
    started = register(api).json()
    first = last_code()

    too_soon = api.post("/api/account/code/resend/", {"challenge_id": started["challenge_id"]})
    assert too_soon.status_code == 429
    assert too_soon.json()["error"]["code"] == "resend_cooldown"

    with tenant_context(deployment_tenant):
        OtpChallenge.objects.filter(pk=started["challenge_id"]).update(
            last_sent_at=timezone.now() - timedelta(minutes=2)
        )
    again = api.post("/api/account/code/resend/", {"challenge_id": started["challenge_id"]})
    assert again.status_code == 202
    assert len(mail.outbox) == 2
    second = last_code()
    if second != first:
        old = api.post(
            "/api/account/register/verify/",
            {"challenge_id": started["challenge_id"], "code": first},
        )
        assert old.json()["error"]["code"] == "invalid_code"
    ok = api.post(
        "/api/account/register/verify/", {"challenge_id": started["challenge_id"], "code": second}
    )
    assert ok.status_code == 201


# --- Sign-in --------------------------------------------------------------------------------------


def test_sign_in_with_email_and_password(api: Api, deployment_tenant: Tenant) -> None:
    customer(deployment_tenant)

    response = sign_in(api, email="  RAHIM@example.com ")

    assert response.status_code == 200
    assert api.get("/api/account/me/").status_code == 200


def test_failed_sign_in_never_says_which_part_was_wrong(
    api: Api, deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)

    wrong_password = sign_in(api, password="not-the-password")
    unknown_email = sign_in(api, email="nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_repeated_failures_lock_the_account_temporarily(
    api: Api, deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)

    statuses = [sign_in(api, password=f"wrong-{n}").status_code for n in range(8)]
    locked = sign_in(api)

    assert statuses == [401] * 8
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"


def test_me_requires_a_session(api: Api) -> None:
    response = api.get("/api/account/me/")

    assert response.status_code == 401


def test_logout_revokes_the_session_server_side(api: Api, deployment_tenant: Tenant) -> None:
    customer(deployment_tenant)
    sign_in(api)
    stolen_token = api.client.cookies[CUSTOMER_COOKIE].value

    assert api.post("/api/account/logout/").status_code == 204

    api.client.cookies[CUSTOMER_COOKIE] = stolen_token
    assert api.get("/api/account/me/").status_code == 401


def test_expired_and_idle_sessions_stop_working(api: Api, deployment_tenant: Tenant) -> None:
    customer(deployment_tenant)
    sign_in(api)

    with tenant_context(deployment_tenant):
        AuthSession.objects.update(last_seen_at=timezone.now() - timedelta(days=15))
    assert api.get("/api/account/me/").status_code == 401

    sign_in(api)
    with tenant_context(deployment_tenant):
        AuthSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert api.get("/api/account/me/").status_code == 401


def test_deactivated_accounts_lose_their_sessions(api: Api, deployment_tenant: Tenant) -> None:
    user = customer(deployment_tenant)
    sign_in(api)

    with tenant_context(deployment_tenant):
        User.objects.filter(pk=user.pk).update(is_active=False)

    assert api.get("/api/account/me/").status_code == 401
    assert sign_in(api).status_code == 401


def test_session_tokens_are_stored_only_as_hashes(api: Api, deployment_tenant: Tenant) -> None:
    customer(deployment_tenant)
    sign_in(api)
    token = api.client.cookies[CUSTOMER_COOKIE].value

    with tenant_context(deployment_tenant):
        stored = AuthSession.objects.get()
    assert token not in stored.token_hash
    assert len(stored.token_hash) == 64


# --- Password reset -------------------------------------------------------------------------------


def test_password_reset_answers_the_same_for_unknown_emails(
    api: Api, deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)

    known = api.post("/api/account/password/forgot/", {"email": "rahim@example.com"})
    unknown = api.post("/api/account/password/forgot/", {"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 202
    assert [message.to for message in mail.outbox] == [["rahim@example.com"]]


def test_password_reset_is_single_use_and_signs_out_everywhere(
    api: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)
    other_device = make_api()
    sign_in(other_device)
    api.post("/api/account/password/forgot/", {"email": "rahim@example.com"})
    token = mail.outbox[-1].body.split("token=")[1].split()[0]

    new_password = "a-brand-new-passphrase-7"
    reset = api.post("/api/account/password/reset/", {"token": token, "password": new_password})
    reused = api.post("/api/account/password/reset/", {"token": token, "password": new_password})

    assert reset.status_code == 204
    assert reused.json()["error"]["code"] == "invalid_link"
    assert other_device.get("/api/account/me/").status_code == 401
    assert sign_in(api, password=new_password).status_code == 200
    with tenant_context(deployment_tenant):
        assert token not in ActionToken.objects.get().token_hash


def test_expired_reset_links_are_refused(api: Api, deployment_tenant: Tenant) -> None:
    customer(deployment_tenant)
    api.post("/api/account/password/forgot/", {"email": "rahim@example.com"})
    token = mail.outbox[-1].body.split("token=")[1].split()[0]
    with tenant_context(deployment_tenant):
        ActionToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = api.post("/api/account/password/reset/", {"token": token, "password": "x" * 16})

    assert response.json()["error"]["code"] == "invalid_link"


# --- Phone sign-in --------------------------------------------------------------------------------


def test_phone_sign_in_is_unavailable_without_a_real_provider(api: Api) -> None:
    methods = api.get("/api/account/methods/").json()
    response = api.post("/api/account/phone/start/", {"phone": "01712345678"})

    assert methods == {
        "email_password": True,
        "phone": False,
        "google": False,
        "phone_country": "BD",
    }
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "method_unavailable"


def test_new_phone_number_signs_up_without_an_email(
    api: Api, sms: list[Any], deployment_tenant: Tenant
) -> None:
    started = api.post("/api/account/phone/start/", {"phone": "01712 345678"})
    assert started.status_code == 202
    assert started.json()["channel"] == "sms"
    assert sms[-1].to == "+8801712345678"
    assert sms[-1].tenant_id == deployment_tenant.pk
    assert sms[-1].purpose == "phone_sign_in"
    code = sms[-1].body[:6]

    verified = api.post(
        "/api/account/phone/verify/", {"challenge_id": started.json()["challenge_id"], "code": code}
    ).json()
    assert verified["status"] == "needs_name"

    done = api.post(
        "/api/account/phone/complete/",
        {
            "challenge_id": started.json()["challenge_id"],
            "completion_token": verified["completion_token"],
            "full_name": "Karim",
        },
    )
    assert done.status_code == 201
    assert done.json()["phone"] == "+8801712345678"
    assert done.json()["email"] is None
    assert done.json()["has_password"] is False

    replay = api.post(
        "/api/account/phone/complete/",
        {
            "challenge_id": started.json()["challenge_id"],
            "completion_token": verified["completion_token"],
            "full_name": "Mallory",
        },
    )
    assert replay.json()["error"]["code"] == "code_expired"


def test_existing_phone_number_signs_straight_in(
    api: Api, sms: list[Any], deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant, email=None, phone="+8801712345678", password=None)

    started = api.post("/api/account/phone/start/", {"phone": "+8801712345678"}).json()
    verified = api.post(
        "/api/account/phone/verify/",
        {"challenge_id": started["challenge_id"], "code": sms[-1].body[:6]},
    ).json()

    assert verified["status"] == "signed_in"
    assert api.get("/api/account/me/").json()["phone"] == "+8801712345678"


def test_completion_needs_the_token_from_this_browser(
    api: Api, sms: list[Any], make_api: Callable[[], Api]
) -> None:
    started = api.post("/api/account/phone/start/", {"phone": "01712345678"}).json()
    api.post(
        "/api/account/phone/verify/",
        {"challenge_id": started["challenge_id"], "code": sms[-1].body[:6]},
    )

    hijack = make_api().post(
        "/api/account/phone/complete/",
        {"challenge_id": started["challenge_id"], "completion_token": "guess", "full_name": "X"},
    )

    assert hijack.json()["error"]["code"] == "code_expired"


def test_codes_per_destination_are_rate_limited(api: Api, sms: list[Any]) -> None:
    statuses = []
    for _ in range(6):
        statuses.append(api.post("/api/account/phone/start/", {"phone": "01712345678"}).status_code)

    assert statuses == [202] * 5 + [429]


# --- Linking sign-in methods ----------------------------------------------------------------------


def test_adding_a_phone_needs_a_recent_sign_in_and_the_code(
    api: Api, sms: list[Any], deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)
    sign_in(api)
    with tenant_context(deployment_tenant):
        AuthSession.objects.update(authenticated_at=timezone.now() - timedelta(minutes=30))

    stale = api.post("/api/account/link/phone/start/", {"phone": "01712345678"})
    assert stale.json()["error"]["code"] == "reauthentication_required"

    assert api.post("/api/account/reauthenticate/", {"password": PASSWORD}).status_code == 204
    started = api.post("/api/account/link/phone/start/", {"phone": "01712345678"}).json()
    linked = api.post(
        "/api/account/link/phone/verify/",
        {"challenge_id": started["challenge_id"], "code": sms[-1].body[:6]},
    )

    assert linked.status_code == 200
    assert linked.json()["phone"] == "+8801712345678"
    assert linked.json()["phone_verified"] is True


def test_a_phone_already_used_by_someone_else_is_not_taken_over(
    api: Api, sms: list[Any], deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant, email=None, phone="+8801712345678", password=None)
    customer(deployment_tenant, email="karim@example.com")
    sign_in(api, email="karim@example.com")

    started = api.post("/api/account/link/phone/start/", {"phone": "01712345678"}).json()
    response = api.post(
        "/api/account/link/phone/verify/",
        {"challenge_id": started["challenge_id"], "code": sms[-1].body[:6]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "account_conflict"


def test_link_codes_belong_to_the_account_that_requested_them(
    api: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)
    customer(deployment_tenant, email="karim@example.com")
    sign_in(api)
    started = api.post("/api/account/link/email/start/", {"email": "new@example.com"}).json()
    code = last_code("new@example.com")

    attacker = make_api()
    sign_in(attacker, email="karim@example.com")
    response = attacker.post(
        "/api/account/link/email/verify/", {"challenge_id": started["challenge_id"], "code": code}
    )

    assert response.json()["error"]["code"] == "code_expired"


def test_phone_only_customers_can_set_a_password_after_adding_an_email(
    api: Api, sms: list[Any], deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant, email=None, phone="+8801712345678", password=None)
    started = api.post("/api/account/phone/start/", {"phone": "01712345678"}).json()
    api.post(
        "/api/account/phone/verify/",
        {"challenge_id": started["challenge_id"], "code": sms[-1].body[:6]},
    )

    no_email = api.post("/api/account/password/change/", {"new_password": "a-long-passphrase-1"})
    assert no_email.status_code == 409

    link = api.post("/api/account/link/email/start/", {"email": "karim@example.com"}).json()
    api.post(
        "/api/account/link/email/verify/",
        {"challenge_id": link["challenge_id"], "code": last_code("karim@example.com")},
    )
    assert (
        api.post(
            "/api/account/password/change/", {"new_password": "a-long-passphrase-1"}
        ).status_code
        == 204
    )
    assert (
        sign_in(api, email="karim@example.com", password="a-long-passphrase-1").status_code == 200
    )


def test_password_change_keeps_this_session_and_ends_others(
    api: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)
    sign_in(api)
    other = make_api()
    sign_in(other)

    wrong = api.post(
        "/api/account/password/change/",
        {"current_password": "nope", "new_password": "another-good-pass-2"},
    )
    changed = api.post(
        "/api/account/password/change/",
        {"current_password": PASSWORD, "new_password": "another-good-pass-2"},
    )

    assert wrong.status_code == 401
    assert changed.status_code == 204
    assert api.get("/api/account/me/").status_code == 200
    assert other.get("/api/account/me/").status_code == 401


# --- Realm separation -----------------------------------------------------------------------------


def test_a_customer_session_never_reaches_the_merchant_api(
    api: Api, deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)
    sign_in(api)
    token = api.client.cookies[CUSTOMER_COOKIE].value
    api.client.cookies["mc_staff"] = token  # even replayed as the staff cookie

    assert api.get("/api/manage/auth/session/").status_code == 401


def test_customer_credentials_do_not_sign_in_to_the_dashboard(
    api: Api, deployment_tenant: Tenant
) -> None:
    customer(deployment_tenant)

    response = api.post(
        "/api/manage/auth/login/", {"email": "rahim@example.com", "password": PASSWORD}
    )

    assert response.status_code == 401
