import logging

import pytest

from control.accounts import services, sessions
from control.accounts.errors import InvalidCredentials
from control.accounts.models import AdminSession, AdminUser, SessionStatus
from control.core.logging import RequestIdFilter
from control.core.ratelimit import RateLimited

from .conftest import Api
from .test_console_auth import admin, invited  # noqa: F401


@pytest.mark.django_db
def test_pending_admin_session_has_only_one_successful_activation() -> None:
    user = AdminUser.objects.create_user("review@example.com")
    pending, _ = sessions.start(user, status=SessionStatus.PENDING_ENROLLMENT)
    sessions.activate(pending)
    with pytest.raises(InvalidCredentials):
        sessions.activate(pending)
    assert AdminSession.objects.filter(status=SessionStatus.ACTIVE).count() == 1


@pytest.mark.django_db
def test_password_change_has_a_current_password_attempt_limit() -> None:
    user = AdminUser.objects.create_user("review@example.com", password="original-password-1234")
    session, _ = sessions.start(user, status=SessionStatus.ACTIVE)
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            services.change_password(session, current="incorrect", new="replacement-password-4567")
    with pytest.raises(RateLimited):
        services.change_password(
            session, current="original-password-1234", new="replacement-password-4567"
        )
    user.refresh_from_db()
    assert user.check_password("original-password-1234")


def test_activation_link_values_are_removed_from_access_logs() -> None:
    record = logging.LogRecord(
        "django.server",
        logging.INFO,
        "test",
        1,
        "GET /api/console/auth/activate/inspect/?token=%s HTTP/1.1",
        ("private-token",),
        None,
    )
    RequestIdFilter().filter(record)
    assert "private-token" not in record.getMessage()
    assert "[redacted]" in record.getMessage()


@pytest.mark.django_db
@pytest.mark.usefixtures("admin")
def test_recovery_code_regeneration_rotates_the_console_cookie(api: Api) -> None:
    old_token = api.client.cookies["mc_console"].value
    old = sessions.resolve(old_token, status=SessionStatus.ACTIVE)
    assert old is not None
    response = api.post("/api/console/account/mfa/recovery-codes/")
    assert response.status_code == 200
    assert api.client.cookies["mc_console"].value != old_token
    assert sessions.resolve(old_token, status=SessionStatus.ACTIVE) is None
    active = sessions.resolve(api.client.cookies["mc_console"].value, status=SessionStatus.ACTIVE)
    assert active is not None
    assert active.expires_at == old.expires_at
