"""Console administrators: creation, activation, sign-in with mandatory two-step verification,
email codes, authenticator apps and recovery codes (D-045).

Administrators are created only by NetaMate personnel (``create_admin``), who never see or set
the password: the new administrator receives a single-use activation link, chooses a password,
and must enroll a second step before the console shows anything. The last second-step method can
never be removed. Store (merchant or customer) credentials do not exist in this database.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from control.core import delivery, ratelimit, totp
from control.core.security import (
    decrypt,
    encrypt,
    hash_token,
    keyed_hash,
    matches,
    new_numeric_code,
    new_recovery_code,
    new_token,
    normalize_code,
    normalize_recovery_code,
)

from . import sessions
from .errors import (
    ActionNotAllowed,
    CodeExpired,
    InvalidCode,
    InvalidCredentials,
    InvalidLink,
    ResendTooSoon,
    WeakPassword,
)
from .models import (
    ActionTokenPurpose,
    AdminActionToken,
    AdminOtpChallenge,
    AdminRecoveryCode,
    AdminSession,
    AdminTotpDevice,
    AdminUser,
    OtpPurpose,
    SessionStatus,
    normalize_admin_email,
)

ACTIVATION_LIFETIME = timedelta(hours=72)
RESET_LIFETIME = timedelta(hours=1)
CODE_LIFETIME = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_CODE_ATTEMPTS = 5
MAX_CODE_SENDS = 5
RECOVERY_CODE_COUNT = 10
WINDOW = 15 * 60
LOGIN_FAILURES_PER_ACCOUNT = 5
LOGIN_FAILURES_PER_IP = 30
SECOND_STEP_FAILURES_PER_USER = 10


class Method:
    TOTP = "totp"
    EMAIL = "email"
    RECOVERY = "recovery_code"


# --- Creation, activation, passwords --------------------------------------------------------------


def check_password_strength(password: str, user: AdminUser) -> None:
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        raise WeakPassword(detail=" ".join(exc.messages)) from exc


def _issue_link(user: AdminUser, purpose: str, lifetime: timedelta) -> str:
    AdminActionToken.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    token = new_token()
    AdminActionToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=hash_token(token),
        expires_at=timezone.now() + lifetime,
    )
    return token


def create_admin(*, email: str, full_name: str) -> AdminUser:
    """Create an administrator (or re-send the link to one not yet activated)."""
    address = normalize_admin_email(email)
    with transaction.atomic():
        user = AdminUser.objects.filter(email=address).first()
        if user is not None and user.activated_at is not None:
            raise ActionNotAllowed(detail="This administrator is already active.")
        if user is None:
            user = AdminUser.objects.create_user(email=address, full_name=full_name)
        token = _issue_link(user, ActionTokenPurpose.ACTIVATION, ACTIVATION_LIFETIME)
        delivery.send_email(
            to=user.email,
            subject="Activate your NetaMate Console account",
            template="admin_activation",
            context={
                "name": user.full_name,
                "link": f"{settings.MATECART_CONSOLE_PUBLIC_BASE_URL}/activate?token={token}",
            },
        )
    return user


def _open_link(token: str, purpose: str) -> AdminActionToken:
    row = (
        AdminActionToken.objects.select_for_update()
        .select_related("user")
        .filter(token_hash=hash_token(token or ""), purpose=purpose, used_at__isnull=True)
        .first()
    )
    if row is None or row.expires_at <= timezone.now() or not row.user.is_active:
        raise InvalidLink()
    return row


def inspect_activation(token: str) -> AdminUser:
    with transaction.atomic():
        return _open_link(token, ActionTokenPurpose.ACTIVATION).user


def activate(*, token: str, password: str) -> tuple[AdminSession, str]:
    """Set the password from the activation link. The result may only enroll a second step."""
    with transaction.atomic():
        row = _open_link(token, ActionTokenPurpose.ACTIVATION)
        user = row.user
        check_password_strength(password, user)
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        user.set_password(password)
        user.activated_at = timezone.now()
        user.save(update_fields=["password", "activated_at", "updated_at"])
        return sessions.start(user, status=SessionStatus.PENDING_ENROLLMENT)


def request_reset(*, email: str, ip: str) -> None:
    address = normalize_admin_email(email)
    ratelimit.consume("reset-ip", ip, limit=10, window=3600)
    ratelimit.consume("reset-account", address, limit=3, window=3600)
    with transaction.atomic():
        user = AdminUser.objects.filter(
            email=address, is_active=True, activated_at__isnull=False
        ).first()
        if user is None:
            return
        token = _issue_link(user, ActionTokenPurpose.PASSWORD_RESET, RESET_LIFETIME)
        delivery.send_email(
            to=user.email,
            subject="Reset your NetaMate Console password",
            template="admin_password_reset",
            context={
                "link": f"{settings.MATECART_CONSOLE_PUBLIC_BASE_URL}/reset-password?token={token}"
            },
        )


def reset_password(*, token: str, password: str) -> None:
    """A new password; every session ends. Two-step verification stays required."""
    with transaction.atomic():
        row = _open_link(token, ActionTokenPurpose.PASSWORD_RESET)
        check_password_strength(password, row.user)
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        row.user.set_password(password)
        row.user.save(update_fields=["password", "updated_at"])
        sessions.revoke_all(row.user)


def change_password(session: AdminSession, *, current: str, new: str) -> None:
    user = session.user
    ratelimit.check("password-change", str(user.pk), limit=5)
    if not user.check_password(current):
        ratelimit.hit("password-change", str(user.pk), window=WINDOW)
        raise InvalidCredentials(detail="Your current password isn't right.")
    check_password_strength(new, user)
    user.set_password(new)
    user.save(update_fields=["password", "updated_at"])
    sessions.revoke_all(user, keep=session)


# --- Sign-in --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SignIn:
    session: AdminSession
    token: str


def sign_in(*, email: str, password: str, ip: str) -> SignIn:
    address = normalize_admin_email(email)
    ratelimit.check("login-ip", ip, limit=LOGIN_FAILURES_PER_IP)
    ratelimit.check("login-account", address, limit=LOGIN_FAILURES_PER_ACCOUNT)
    user = AdminUser.objects.filter(email=address).first() if address else None
    if user is None:
        AdminUser().set_password(password)  # same hashing cost as a real check
    if (
        user is None
        or not user.is_active
        or user.activated_at is None
        or not user.check_password(password)
    ):
        ratelimit.hit("login-ip", ip, window=WINDOW)
        ratelimit.hit("login-account", address, window=WINDOW)
        raise InvalidCredentials()
    ratelimit.clear("login-account", address)
    status = SessionStatus.PENDING_MFA if user.mfa_enabled else SessionStatus.PENDING_ENROLLMENT
    session, token = sessions.start(user, status=status)
    return SignIn(session, token)


def methods(user: AdminUser) -> list[str]:
    available = []
    if AdminTotpDevice.objects.filter(user=user, confirmed_at__isnull=False).exists():
        available.append(Method.TOTP)
    if user.email_mfa_enabled_at is not None:
        available.append(Method.EMAIL)
    if AdminRecoveryCode.objects.filter(user=user, used_at__isnull=True).exists():
        available.append(Method.RECOVERY)
    return available


def complete_second_step(
    pending: AdminSession, *, method: str, code: str, challenge_id: str | None
) -> tuple[AdminSession, str]:
    user = pending.user
    ratelimit.check("second-step", str(user.pk), limit=SECOND_STEP_FAILURES_PER_USER)
    try:
        if method == Method.TOTP:
            ok = verify_totp(user, code)
        elif method == Method.RECOVERY:
            ok = use_recovery_code(user, code)
        elif method == Method.EMAIL and user.email_mfa_enabled_at is not None:
            verify_code(challenge_id or "", code, purpose=OtpPurpose.MFA_EMAIL, session=pending)
            ok = True
        else:
            ok = False
        if not ok:
            raise InvalidCode()
    except InvalidCode:
        ratelimit.hit("second-step", str(user.pk), window=WINDOW)
        sessions.register_failed_attempt(pending)
        raise
    return sessions.activate(pending)


# --- Email codes ----------------------------------------------------------------------------------


def _code_purpose(challenge: AdminOtpChallenge) -> str:
    return f"otp:{challenge.pk}"


def _send_code(challenge: AdminOtpChallenge, code: str) -> None:
    delivery.send_email(
        to=challenge.destination,
        subject="Your NetaMate Console code",
        template="admin_code",
        context={"code": code},
    )


def issue_code(*, purpose: str, session: AdminSession) -> AdminOtpChallenge:
    user = session.user
    ratelimit.consume("code-user", str(user.pk), limit=10, window=3600)
    now = timezone.now()
    code = new_numeric_code()
    with transaction.atomic():
        challenge = AdminOtpChallenge(
            purpose=purpose,
            destination=user.email,
            user=user,
            session=session,
            last_sent_at=now,
            expires_at=now + CODE_LIFETIME,
        )
        challenge.code_hash = keyed_hash(_code_purpose(challenge), code)
        challenge.save()
        _send_code(challenge, code)
    return challenge


@transaction.atomic
def current_or_new_code(*, purpose: str, session: AdminSession, resend: bool) -> AdminOtpChallenge:
    """The open code for this session (never re-sent on reload), or a new one."""
    AdminSession.objects.select_for_update().get(pk=session.pk)
    existing = (
        AdminOtpChallenge.objects.filter(
            session=session,
            purpose=purpose,
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )
    if existing is None:
        return issue_code(purpose=purpose, session=session)
    if not resend:
        return existing
    now = timezone.now()
    if existing.send_count >= MAX_CODE_SENDS:
        raise CodeExpired()
    if now - existing.last_sent_at < RESEND_COOLDOWN:
        raise ResendTooSoon()
    code = new_numeric_code()
    with transaction.atomic():
        existing.code_hash = keyed_hash(_code_purpose(existing), code)
        existing.last_sent_at = now
        existing.send_count += 1
        existing.attempts = 0
        existing.expires_at = now + CODE_LIFETIME
        existing.save(
            update_fields=["code_hash", "last_sent_at", "send_count", "attempts", "expires_at"]
        )
        _send_code(existing, code)
    return existing


def seconds_until_resend(challenge: AdminOtpChallenge) -> int:
    remaining = RESEND_COOLDOWN - (timezone.now() - challenge.last_sent_at)
    return max(0, int(remaining.total_seconds()))


def _challenge_pk(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return uuid.UUID(int=0)


def verify_code(challenge_id: str, code: str, *, purpose: str, session: AdminSession) -> None:
    """Locked check: wrong codes count even under concurrency and are committed before raising."""
    failure: type[CodeExpired] | type[InvalidCode] | None = None
    with transaction.atomic():
        challenge = (
            AdminOtpChallenge.objects.select_for_update()
            .filter(pk=_challenge_pk(challenge_id), purpose=purpose, session=session)
            .first()
        )
        now = timezone.now()
        if (
            challenge is None
            or challenge.consumed_at is not None
            or challenge.expires_at <= now
            or challenge.attempts >= MAX_CODE_ATTEMPTS
        ):
            failure = CodeExpired
        elif not matches(challenge.code_hash, _code_purpose(challenge), normalize_code(code)):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            failure = CodeExpired if challenge.attempts >= MAX_CODE_ATTEMPTS else InvalidCode
        else:
            challenge.verified_at = challenge.consumed_at = now
            challenge.save(update_fields=["verified_at", "consumed_at"])
    if failure is not None:
        raise failure()


# --- Authenticator app ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    uri: str
    qr_svg: str


def begin_totp(user: AdminUser) -> TotpSetup:
    secret = totp.new_secret()
    with transaction.atomic():
        AdminTotpDevice.objects.filter(user=user, confirmed_at__isnull=True).delete()
        AdminTotpDevice.objects.create(user=user, secret_encrypted=encrypt(secret))
    uri = totp.provisioning_uri(secret, account=user.email, issuer="NetaMate Console")
    return TotpSetup(secret=secret, uri=uri, qr_svg=totp.qr_svg_data_uri(uri))


def confirm_totp(user: AdminUser, code: str) -> bool:
    with transaction.atomic():
        device = (
            AdminTotpDevice.objects.select_for_update()
            .filter(user=user, confirmed_at__isnull=True)
            .first()
        )
        if device is None:
            return False
        step = totp.verify(
            decrypt(device.secret_encrypted), normalize_code(code), last_used_step=None
        )
        if step is None:
            return False
        AdminTotpDevice.objects.filter(user=user, confirmed_at__isnull=False).delete()
        device.confirmed_at = timezone.now()
        device.last_used_step = step
        device.save(update_fields=["confirmed_at", "last_used_step"])
    return True


def verify_totp(user: AdminUser, code: str) -> bool:
    with transaction.atomic():
        device = (
            AdminTotpDevice.objects.select_for_update()
            .filter(user=user, confirmed_at__isnull=False)
            .first()
        )
        if device is None:
            return False
        step = totp.verify(
            decrypt(device.secret_encrypted),
            normalize_code(code),
            last_used_step=device.last_used_step,
        )
        if step is None:
            return False
        device.last_used_step = step
        device.save(update_fields=["last_used_step"])
    return True


# --- Recovery codes -------------------------------------------------------------------------------


def _recovery_hash(user: AdminUser, code: str) -> str:
    return keyed_hash(f"recovery:{user.pk}", normalize_recovery_code(code))


def regenerate_recovery_codes(user: AdminUser) -> list[str]:
    codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    with transaction.atomic():
        AdminRecoveryCode.objects.filter(user=user).delete()
        AdminRecoveryCode.objects.bulk_create(
            [AdminRecoveryCode(user=user, code_hash=_recovery_hash(user, c)) for c in codes]
        )
    return codes


def use_recovery_code(user: AdminUser, code: str) -> bool:
    with transaction.atomic():
        row = (
            AdminRecoveryCode.objects.select_for_update()
            .filter(user=user, code_hash=_recovery_hash(user, code), used_at__isnull=True)
            .first()
        )
        if row is None:
            return False
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
    return True


def recovery_codes_remaining(user: AdminUser) -> int:
    return AdminRecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


# --- Enrollment and management --------------------------------------------------------------------


def _method_count(user: AdminUser) -> int:
    has_totp = AdminTotpDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()
    return int(has_totp) + int(user.email_mfa_enabled_at is not None)


def _after_enabled(session: AdminSession) -> list[str] | None:
    """Issue recovery codes if the account has none, and end every other session."""
    codes = None
    if not recovery_codes_remaining(session.user):
        codes = regenerate_recovery_codes(session.user)
    sessions.revoke_all(session.user, keep=session)
    return codes


def confirm_totp_for(session: AdminSession, code: str) -> list[str] | None:
    ratelimit.consume("totp-enrollment", str(session.user_id), limit=10, window=WINDOW)
    if not confirm_totp(session.user, code):
        raise InvalidCode(
            detail="That code didn't match. Check the time on your phone and try the newest code."
        )
    return _after_enabled(session)


def confirm_email_for(session: AdminSession, *, challenge_id: str, code: str) -> list[str] | None:
    verify_code(challenge_id, code, purpose=OtpPurpose.MFA_EMAIL_SETUP, session=session)
    AdminUser.objects.filter(pk=session.user.pk).update(email_mfa_enabled_at=timezone.now())
    session.user.refresh_from_db(fields=["email_mfa_enabled_at"])
    return _after_enabled(session)


@transaction.atomic
def remove_totp(session: AdminSession) -> None:
    sessions.require_recent_authentication(session)
    session.user = AdminUser.objects.select_for_update().get(pk=session.user_id)
    if (
        _method_count(session.user) < 2
        or not AdminTotpDevice.objects.filter(
            user=session.user, confirmed_at__isnull=False
        ).exists()
    ):
        raise ActionNotAllowed(
            detail="Two-step verification is required. Add another method before removing this one."
        )
    AdminTotpDevice.objects.filter(user=session.user).delete()
    sessions.revoke_all(session.user, keep=session)


@transaction.atomic
def remove_email(session: AdminSession) -> None:
    sessions.require_recent_authentication(session)
    session.user = AdminUser.objects.select_for_update().get(pk=session.user_id)
    if _method_count(session.user) < 2 or session.user.email_mfa_enabled_at is None:
        raise ActionNotAllowed(
            detail="Two-step verification is required. Add another method before removing this one."
        )
    AdminUser.objects.filter(pk=session.user.pk).update(email_mfa_enabled_at=None)
    session.user.email_mfa_enabled_at = None
    sessions.revoke_all(session.user, keep=session)


def reauthenticate(session: AdminSession, password: str) -> None:
    user = session.user
    ratelimit.check("reauth", str(user.pk), limit=SECOND_STEP_FAILURES_PER_USER)
    if not user.check_password(password):
        ratelimit.hit("reauth", str(user.pk), window=WINDOW)
        raise InvalidCredentials(detail="That password isn't right.")
    sessions.mark_reauthenticated(session)
