"""Staff sign-in with the second step, and account security (MFA management) for staff.

A password alone yields a ``pending_mfa`` session that can do nothing except finish the second
step; completing it replaces that session with a new one (token rotation). Changing sign-in
security requires a recent sign-in or password re-entry, and signs out other sessions.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from commerce.audit.services import record_audit_event
from commerce.core import ratelimit
from commerce.identity import mfa, otp, passwords, sessions
from commerce.identity.errors import ActionNotAllowed, InvalidCode, InvalidCredentials
from commerce.identity.mfa import MfaMethod
from commerce.identity.models import (
    AccountType,
    AuthSession,
    OtpChallenge,
    OtpChannel,
    OtpPurpose,
    User,
)
from commerce.stores.services import current_store

from .models import MembershipStatus
from .services import AccessSuspended, membership_for

MFA_WINDOW = 15 * 60
MFA_FAILURES_PER_USER = 10


@dataclass(frozen=True)
class SignInResult:
    session: AuthSession
    token: str
    mfa_required: bool


def sign_in(*, email: str, password: str, ip: str) -> SignInResult:
    user = passwords.authenticate(realm=AccountType.STAFF, email=email, password=password, ip=ip)
    membership = membership_for(user)
    if membership is None:
        raise InvalidCredentials()
    if membership.status != MembershipStatus.ACTIVE:
        raise AccessSuspended()
    required = user.mfa_enabled
    session, token = sessions.start(user, pending_mfa=required)
    if not required:
        with transaction.atomic():
            record_audit_event(action="staff.signed_in", resource=user, actor=user)
    return SignInResult(session, token, required)


def available_methods(user: User) -> list[str]:
    status = mfa.status(user)
    methods = []
    if status.totp:
        methods.append(MfaMethod.TOTP)
    if status.email:
        methods.append(MfaMethod.EMAIL)
    if status.recovery_codes_remaining:
        methods.append(MfaMethod.RECOVERY)
    return methods


def send_email_code(pending: AuthSession, *, resend: bool = False) -> OtpChallenge:
    """The email code for this pending sign-in: sent once, then reused until the customer asks
    for a new one (``resend``, after the cooldown)."""
    user = pending.user
    if user.email_mfa_enabled_at is None or not user.email:
        raise ActionNotAllowed(detail="Email codes aren't turned on for this account.")
    store_name = current_store().name
    with transaction.atomic():
        existing = (
            OtpChallenge.objects.filter(
                session=pending,
                purpose=OtpPurpose.MFA_EMAIL,
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return otp.resend(existing, store_name=store_name) if resend else existing
        return otp.issue(
            purpose=OtpPurpose.MFA_EMAIL,
            channel=OtpChannel.EMAIL,
            destination=user.email,
            user=user,
            session=pending,
            store_name=store_name,
        )


def complete_mfa(
    pending: AuthSession, *, method: str, code: str, challenge_id: str | None = None
) -> tuple[AuthSession, str]:
    user = pending.user
    ratelimit.check("mfa-user", str(user.pk), limit=MFA_FAILURES_PER_USER)
    try:
        if method == MfaMethod.EMAIL:
            if user.email_mfa_enabled_at is None:
                raise InvalidCode()
            otp.verify(challenge_id or "", code, purpose=OtpPurpose.MFA_EMAIL, session=pending)
        elif method in (MfaMethod.TOTP, MfaMethod.RECOVERY):
            if not mfa.verify_factor(user, method, code):
                raise InvalidCode()
        else:
            raise InvalidCode()
    except InvalidCode:
        ratelimit.hit("mfa-user", str(user.pk), window=MFA_WINDOW)
        sessions.register_failed_attempt(pending)
        raise
    with transaction.atomic():
        session, token = sessions.activate(pending, mfa_verified=True)
        if method == MfaMethod.RECOVERY:
            record_audit_event(action="account.mfa.recovery_code_used", resource=user, actor=user)
        record_audit_event(
            action="staff.signed_in", resource=user, actor=user, changes={"second_step": method}
        )
    return session, token


# --- Account security -----------------------------------------------------------------------------


def reauthenticate(session: AuthSession, *, password: str) -> None:
    user = session.user
    ratelimit.check("reauth-user", str(user.pk), limit=MFA_FAILURES_PER_USER)
    if not user.check_password(password):
        ratelimit.hit("reauth-user", str(user.pk), window=MFA_WINDOW)
        raise InvalidCredentials(detail="That password isn't right.")
    sessions.mark_reauthenticated(session)


def _after_mfa_enabled(session: AuthSession, method: str) -> list[str] | None:
    """Audit, sign out other sessions, and issue recovery codes if the account has none yet."""
    user = session.user
    codes = None
    if not mfa.status(user).recovery_codes_remaining:
        codes = mfa.regenerate_recovery_codes(user)
    sessions.revoke_all(user, keep=session)
    record_audit_event(
        action="account.mfa.enabled", resource=user, actor=user, changes={"method": method}
    )
    return codes


def _after_mfa_disabled(session: AuthSession, method: str) -> None:
    user = session.user
    if not mfa.status(user).enabled:
        mfa.clear_recovery_codes(user)
    sessions.revoke_all(user, keep=session)
    record_audit_event(
        action="account.mfa.disabled", resource=user, actor=user, changes={"method": method}
    )


def begin_totp(session: AuthSession) -> mfa.TotpSetup:
    sessions.require_recent_authentication(session)
    return mfa.begin_totp_setup(session.user, issuer=f"{current_store().name} (MateCart)")


def confirm_totp(session: AuthSession, code: str) -> list[str] | None:
    sessions.require_recent_authentication(session)
    ratelimit.consume("totp-enrollment", str(session.user_id), limit=10, window=MFA_WINDOW)
    with transaction.atomic():
        if not mfa.confirm_totp(session.user, code):
            raise InvalidCode(
                detail="That code didn't match. Check your phone's time and try the newest code."
            )
        return _after_mfa_enabled(session, MfaMethod.TOTP)


def disable_totp(session: AuthSession) -> None:
    sessions.require_recent_authentication(session)
    with transaction.atomic():
        mfa.remove_totp(session.user)
        _after_mfa_disabled(session, MfaMethod.TOTP)


def begin_email_mfa(session: AuthSession, *, resend: bool = False) -> OtpChallenge:
    sessions.require_recent_authentication(session)
    user = session.user
    if not user.email:
        raise ActionNotAllowed(detail="Your account has no email address.")
    with transaction.atomic():
        AuthSession.objects.select_for_update().get(pk=session.pk)
        existing = (
            OtpChallenge.objects.filter(
                purpose=OtpPurpose.MFA_EMAIL_SETUP,
                session=session,
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return otp.resend(existing, store_name=current_store().name) if resend else existing
        ratelimit.consume("mfa-email-setup", str(user.pk), limit=5, window=3600)
        return otp.issue(
            purpose=OtpPurpose.MFA_EMAIL_SETUP,
            channel=OtpChannel.EMAIL,
            destination=user.email,
            user=user,
            session=session,
            store_name=current_store().name,
        )


def confirm_email_mfa(session: AuthSession, *, challenge_id: str, code: str) -> list[str] | None:
    sessions.require_recent_authentication(session)
    otp.verify(challenge_id, code, purpose=OtpPurpose.MFA_EMAIL_SETUP, session=session)
    with transaction.atomic():
        mfa.enable_email(session.user)
        return _after_mfa_enabled(session, MfaMethod.EMAIL)


def disable_email_mfa(session: AuthSession) -> None:
    sessions.require_recent_authentication(session)
    with transaction.atomic():
        mfa.disable_email(session.user)
        _after_mfa_disabled(session, MfaMethod.EMAIL)


def regenerate_recovery_codes(session: AuthSession) -> list[str]:
    sessions.require_recent_authentication(session)
    if not mfa.status(session.user).enabled:
        raise ActionNotAllowed(detail="Turn on two-step verification first.")
    with transaction.atomic():
        codes = mfa.regenerate_recovery_codes(session.user)
        record_audit_event(
            action="account.mfa.recovery_codes_regenerated",
            resource=session.user,
            actor=session.user,
        )
    return codes
