"""Password sign-in, password rules and reset links."""

from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from commerce.audit.services import record_audit_event
from commerce.core import delivery, ratelimit
from commerce.core.contact import normalize_email
from commerce.core.security import hash_token, new_token

from . import sessions
from .errors import InvalidCredentials, InvalidLink, WeakPassword
from .models import ActionToken, ActionTokenPurpose, AuthSession, User

LOGIN_WINDOW = 15 * 60
LOGIN_FAILURES_PER_ACCOUNT = 8
LOGIN_FAILURES_PER_IP = 40
RESET_LIFETIME = timedelta(hours=1)
RESET_REQUESTS_PER_ACCOUNT = 3
RESET_REQUESTS_PER_IP = 20
RESET_WINDOW = 60 * 60


def check_password_strength(password: str, *, user: User | None = None, **context: str) -> None:
    candidate = user or User(**{k: v for k, v in context.items() if k in {"email", "full_name"}})
    try:
        validate_password(password, user=candidate)
    except ValidationError as exc:
        raise WeakPassword(detail=" ".join(exc.messages)) from exc


def authenticate(*, realm: str, email: str, password: str, ip: str) -> User:
    """Email + password for ``realm``. Failures are counted per account and per IP and always
    produce the same error, with the same hashing cost, whether or not the account exists."""
    normalized = normalize_email(email) or ""
    account_key = f"{realm}:{normalized}"
    ratelimit.check("login-ip", ip, limit=LOGIN_FAILURES_PER_IP)
    ratelimit.check("login-account", account_key, limit=LOGIN_FAILURES_PER_ACCOUNT)
    user = User.objects.filter(account_type=realm, email=normalized).first() if normalized else None
    if user is None:
        User().set_password(password)  # same cost as a real check
    if user is None or not user.is_active or not user.check_password(password):
        ratelimit.hit("login-ip", ip, window=LOGIN_WINDOW)
        ratelimit.hit("login-account", account_key, window=LOGIN_WINDOW)
        raise InvalidCredentials()
    ratelimit.clear("login-account", account_key)
    return user


def request_reset(*, realm: str, email: str, ip: str, link_base: str, store_name: str) -> None:
    """Email a reset link if an active account with a verified email exists. The caller always
    answers the same way, so the response never reveals whether the account exists."""
    normalized = normalize_email(email) or ""
    ratelimit.consume("reset-ip", ip, limit=RESET_REQUESTS_PER_IP, window=RESET_WINDOW)
    ratelimit.consume(
        "reset-account",
        f"{realm}:{normalized}",
        limit=RESET_REQUESTS_PER_ACCOUNT,
        window=RESET_WINDOW,
    )
    user = (
        User.objects.filter(account_type=realm, email=normalized, is_active=True)
        .exclude(email_verified_at__isnull=True)
        .first()
    )
    if user is None:
        return
    token = new_token()
    ActionToken.objects.filter(
        user=user, purpose=ActionTokenPurpose.PASSWORD_RESET, used_at__isnull=True
    ).update(used_at=timezone.now())
    ActionToken.objects.create(
        user=user,
        purpose=ActionTokenPurpose.PASSWORD_RESET,
        token_hash=hash_token(token),
        expires_at=timezone.now() + RESET_LIFETIME,
    )
    delivery.send_email(
        to=normalized,
        subject=f"{store_name}: reset your password",
        template="password_reset",
        context={"store_name": store_name, "link": f"{link_base}?token={token}"},
    )


def reset(*, realm: str, token: str, password: str) -> User:
    """Set a new password from a reset link and sign the account out everywhere."""
    with transaction.atomic():
        row = (
            ActionToken.objects.select_for_update()
            .select_related("user")
            .filter(
                token_hash=hash_token(token or ""),
                purpose=ActionTokenPurpose.PASSWORD_RESET,
                used_at__isnull=True,
                user__account_type=realm,
            )
            .first()
        )
        if row is None or row.expires_at <= timezone.now() or not row.user.is_active:
            raise InvalidLink()
        user = row.user
        check_password_strength(password, user=user)
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        user.set_password(password)
        if user.email_verified_at is None:  # the link itself proves control of the mailbox
            user.email_verified_at = timezone.now()
        user.save(update_fields=["password", "email_verified_at", "updated_at"])
        sessions.revoke_all(user)
        record_audit_event(action="account.password.reset", resource=user, actor=user)
    return user


def change(*, user: User, current_password: str, new_password: str, keep: AuthSession) -> None:
    """Change (or, for accounts without one, set) the password. Other sessions are signed out."""
    ratelimit.check("password-change", str(user.pk), limit=5)
    if user.has_usable_password() and not user.check_password(current_password):
        ratelimit.hit("password-change", str(user.pk), window=LOGIN_WINDOW)
        raise InvalidCredentials(detail="Your current password isn't right.")
    check_password_strength(new_password, user=user)
    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        sessions.revoke_all(user, keep=keep)
        record_audit_event(action="account.password.changed", resource=user, actor=user)
