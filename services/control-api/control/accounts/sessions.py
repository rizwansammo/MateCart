"""Console sessions (D-045): opaque tokens stored hashed, short lifetimes, rotation on every step.

A password alone never yields an ``active`` session: administrators with two-step verification
get ``pending_mfa``; those without it (first sign-in after activation) get ``pending_enrollment``
and can do nothing except set it up.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone

from control.core.security import hash_token, new_token

from .errors import InvalidCredentials, ReauthenticationRequired
from .models import AdminSession, AdminUser, SessionStatus

PENDING_LIFETIME = timedelta(minutes=15)
REAUTHENTICATION_WINDOW = timedelta(minutes=10)
TOUCH_INTERVAL = timedelta(minutes=1)
MAX_PENDING_ATTEMPTS = 5


def _absolute() -> timedelta:
    return timedelta(seconds=settings.MATECART_CONSOLE_SESSION_SECONDS)


def _idle() -> timedelta:
    return timedelta(seconds=settings.MATECART_CONSOLE_IDLE_SECONDS)


def start(user: AdminUser, *, status: str) -> tuple[AdminSession, str]:
    now = timezone.now()
    token = new_token()
    session = AdminSession.objects.create(
        user=user,
        status=status,
        token_hash=hash_token(token),
        last_seen_at=now,
        authenticated_at=now,
        expires_at=now + (_absolute() if status == SessionStatus.ACTIVE else PENDING_LIFETIME),
    )
    return session, token


def resolve(token: str | None, *, status: str) -> AdminSession | None:
    if not token or len(token) > 128:
        return None
    session = (
        AdminSession.objects.select_related("user")
        .filter(token_hash=hash_token(token), status=status, revoked_at__isnull=True)
        .first()
    )
    if session is None:
        return None
    now = timezone.now()
    if (
        session.expires_at <= now
        or (status == SessionStatus.ACTIVE and session.last_seen_at + _idle() <= now)
        or not session.user.is_active
    ):
        return None
    if now - session.last_seen_at >= TOUCH_INTERVAL:
        AdminSession.objects.filter(pk=session.pk).update(last_seen_at=now)
        session.last_seen_at = now
    return session


def activate(pending: AdminSession) -> tuple[AdminSession, str]:
    """Second step or enrollment done: replace the pending session with a new active one."""
    with transaction.atomic():
        current = (
            AdminSession.objects.select_for_update()
            .filter(
                pk=pending.pk,
                status__in=[SessionStatus.PENDING_MFA, SessionStatus.PENDING_ENROLLMENT],
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
                user__is_active=True,
            )
            .first()
        )
        if current is None:
            raise InvalidCredentials(detail="This sign-in has expired. Please start again.")
        revoke(current)
        return start(current.user, status=SessionStatus.ACTIVE)


def register_failed_attempt(session: AdminSession) -> None:
    AdminSession.objects.filter(pk=session.pk).update(failed_attempts=F("failed_attempts") + 1)
    session.refresh_from_db(fields=["failed_attempts"])
    if session.failed_attempts >= MAX_PENDING_ATTEMPTS:
        revoke(session)


def revoke(session: AdminSession) -> None:
    AdminSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def revoke_all(user: AdminUser, *, keep: AdminSession | None = None) -> None:
    sessions = AdminSession.objects.filter(user=user, revoked_at__isnull=True)
    if keep is not None:
        sessions = sessions.exclude(pk=keep.pk)
    sessions.update(revoked_at=timezone.now())


def mark_reauthenticated(session: AdminSession) -> None:
    now = timezone.now()
    AdminSession.objects.filter(pk=session.pk).update(reauthenticated_at=now)
    session.reauthenticated_at = now


def rotate(session: AdminSession) -> str:
    """Invalidate the old browser token; preserve expiry and step-up metadata."""
    token = new_token()
    updated = AdminSession.objects.filter(
        pk=session.pk,
        token_hash=session.token_hash,
        revoked_at__isnull=True,
        status=SessionStatus.ACTIVE,
        expires_at__gt=timezone.now(),
    ).update(token_hash=hash_token(token))
    if updated != 1:
        raise InvalidCredentials()
    session.token_hash = hash_token(token)
    return token


def recently_authenticated(session: AdminSession) -> bool:
    recent = max(filter(None, [session.authenticated_at, session.reauthenticated_at]))
    return timezone.now() - recent <= REAUTHENTICATION_WINDOW


def require_recent_authentication(session: AdminSession) -> None:
    if not recently_authenticated(session):
        raise ReauthenticationRequired()


def set_cookie(response: HttpResponse, session: AdminSession, token: str) -> None:
    response.set_cookie(
        settings.MATECART_CONSOLE_COOKIE,
        token,
        max_age=int((session.expires_at - timezone.now()).total_seconds()),
        path=settings.MATECART_CONSOLE_COOKIE_PATH,
        secure=settings.MATECART_SECURE_COOKIES,
        httponly=True,
        samesite="Strict",
    )


def clear_cookie(response: HttpResponse) -> None:
    response.delete_cookie(
        settings.MATECART_CONSOLE_COOKIE,
        path=settings.MATECART_CONSOLE_COOKIE_PATH,
        samesite="Strict",
    )
