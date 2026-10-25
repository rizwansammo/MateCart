"""Realm-separated browser sessions (D-040).

Customer and staff sessions are different rows, cookies and lifetimes. The staff cookie is scoped
to ``/api/manage`` so the browser never even sends it to customer endpoints. Tokens are random,
stored hashed, rotated on every sign-in and privilege change, and revocable per user.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone

from commerce.core.security import hash_token, new_token

from .errors import InvalidCredentials, ReauthenticationRequired
from .models import AccountType, AuthSession, SessionStatus, User

PENDING_MFA_LIFETIME = timedelta(minutes=10)
REAUTHENTICATION_WINDOW = timedelta(minutes=10)
TOUCH_INTERVAL = timedelta(minutes=1)
MAX_PENDING_ATTEMPTS = 5


def _policy(realm: str) -> tuple[timedelta, timedelta]:
    """(absolute lifetime, idle timeout) for a realm."""
    cfg: dict[str, Any] = settings.MATECART_SESSIONS[realm]
    return timedelta(seconds=cfg["absolute_seconds"]), timedelta(seconds=cfg["idle_seconds"])


def start(
    user: User, *, pending_mfa: bool = False, mfa_verified: bool = False
) -> tuple[AuthSession, str]:
    now = timezone.now()
    absolute, _ = _policy(user.account_type)
    token = new_token()
    session = AuthSession.objects.create(
        user=user,
        realm=user.account_type,
        status=SessionStatus.PENDING_MFA if pending_mfa else SessionStatus.ACTIVE,
        token_hash=hash_token(token),
        last_seen_at=now,
        authenticated_at=now,
        mfa_verified_at=now if mfa_verified else None,
        expires_at=now + (PENDING_MFA_LIFETIME if pending_mfa else absolute),
    )
    return session, token


def resolve(
    token: str | None, realm: str, *, status: str = SessionStatus.ACTIVE
) -> AuthSession | None:
    """The live session for ``token`` in ``realm``; None if unknown, expired or revoked."""
    if not token or len(token) > 128:
        return None
    session = (
        AuthSession.objects.select_related("user")
        .filter(token_hash=hash_token(token), realm=realm, status=status, revoked_at__isnull=True)
        .first()
    )
    if session is None:
        return None
    now = timezone.now()
    _, idle = _policy(realm)
    if (
        session.expires_at <= now
        or (status == SessionStatus.ACTIVE and session.last_seen_at + idle <= now)
        or not session.user.is_active
        or session.user.account_type != realm
    ):
        return None
    if now - session.last_seen_at >= TOUCH_INTERVAL:
        AuthSession.objects.filter(pk=session.pk).update(last_seen_at=now)
        session.last_seen_at = now
    return session


def activate(pending: AuthSession, *, mfa_verified: bool) -> tuple[AuthSession, str]:
    """Second step done: replace the pending session with a fresh active one (token rotation)."""
    with transaction.atomic():
        current = (
            AuthSession.objects.select_for_update()
            .filter(
                pk=pending.pk,
                status=SessionStatus.PENDING_MFA,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
                user__is_active=True,
            )
            .first()
        )
        if current is None:
            raise InvalidCredentials(detail="This sign-in has expired. Please start again.")
        revoke(current)
        return start(current.user, mfa_verified=mfa_verified)


def register_failed_attempt(session: AuthSession) -> None:
    AuthSession.objects.filter(pk=session.pk).update(failed_attempts=F("failed_attempts") + 1)
    session.refresh_from_db(fields=["failed_attempts"])
    if session.failed_attempts >= MAX_PENDING_ATTEMPTS:
        revoke(session)


def revoke(session: AuthSession) -> None:
    AuthSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def revoke_all(user: User, *, keep: AuthSession | None = None) -> int:
    """Sign the user out everywhere (optionally except the current session)."""
    sessions = AuthSession.objects.filter(user=user, revoked_at__isnull=True)
    if keep is not None:
        sessions = sessions.exclude(pk=keep.pk)
    return sessions.update(revoked_at=timezone.now())


def mark_reauthenticated(session: AuthSession) -> None:
    now = timezone.now()
    AuthSession.objects.filter(pk=session.pk).update(reauthenticated_at=now)
    session.reauthenticated_at = now


def rotate(session: AuthSession) -> str:
    """Replace the current credential without extending its absolute lifetime."""
    token = new_token()
    updated = AuthSession.objects.filter(
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


def require_recent_authentication(session: AuthSession) -> None:
    """Sensitive changes need a sign-in or step-up confirmation within the last 10 minutes."""
    recent = max(filter(None, [session.authenticated_at, session.reauthenticated_at]))
    if timezone.now() - recent > REAUTHENTICATION_WINDOW:
        raise ReauthenticationRequired()


def cookie_spec(realm: str) -> dict[str, Any]:
    spec: dict[str, Any] = settings.MATECART_SESSION_COOKIES[realm]
    return spec


def set_cookie(response: HttpResponse, session: AuthSession, token: str) -> None:
    spec = cookie_spec(session.realm)
    response.set_cookie(
        spec["name"],
        token,
        max_age=int((session.expires_at - timezone.now()).total_seconds()),
        path=spec["path"],
        secure=settings.MATECART_SECURE_COOKIES,
        httponly=True,
        samesite="Lax",
    )


def clear_cookie(response: HttpResponse, realm: str) -> None:
    spec = cookie_spec(realm)
    response.delete_cookie(spec["name"], path=spec["path"], samesite="Lax")


REALMS = (AccountType.CUSTOMER, AccountType.STAFF)
