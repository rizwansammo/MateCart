"""One-time codes by email or SMS (D-041).

Codes are 6 digits, valid for 10 minutes, allow 5 attempts, are single-use, and only their HMAC is
stored. A resend replaces the code (the old one stops working) after a 60-second cooldown, up to 5
sends per challenge. Verification locks the challenge row, so concurrent guesses cannot exceed the
attempt limit or reuse a code.
"""

import uuid
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from commerce.core import delivery
from commerce.core.security import keyed_hash, matches, new_numeric_code, normalize_code
from commerce.tenancy.context import get_active_tenant_id

from .errors import CodeExpired, InvalidCode, ResendTooSoon
from .models import AuthSession, OtpChallenge, OtpChannel, OtpPurpose, User

CODE_LIFETIME = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5
MAX_SENDS = 5

_EMAIL_SUBJECTS: dict[str, str] = {
    OtpPurpose.REGISTRATION: "Your verification code",
    OtpPurpose.MFA_EMAIL: "Your sign-in code",
    OtpPurpose.MFA_EMAIL_SETUP: "Confirm your email for sign-in codes",
    OtpPurpose.LINK_EMAIL: "Confirm your email address",
    OtpPurpose.REAUTHENTICATION: "Your security code",
}
_SMS_LEAD: dict[str, str] = {
    OtpPurpose.PHONE_SIGN_IN: "sign-in code",
    OtpPurpose.LINK_PHONE: "verification code",
    OtpPurpose.REAUTHENTICATION: "security code",
}


def _code_purpose(challenge: OtpChallenge) -> str:
    return f"otp:{challenge.pk}"


def _deliver(challenge: OtpChallenge, code: str, *, store_name: str) -> None:
    if challenge.channel == OtpChannel.SMS:
        delivery.send_sms(
            to=challenge.destination,
            body=f"{code} is your {store_name} {_SMS_LEAD.get(challenge.purpose, 'code')}. "
            "It expires in 10 minutes. Never share it.",
            tenant_id=get_active_tenant_id(),
            purpose=challenge.purpose,
        )
    else:
        delivery.send_email(
            to=challenge.destination,
            subject=f"{store_name}: {_EMAIL_SUBJECTS.get(challenge.purpose, 'Your code')}",
            template="code",
            context={"code": code, "store_name": store_name, "purpose": challenge.purpose},
        )


def issue(
    *,
    purpose: str,
    channel: str,
    destination: str,
    store_name: str,
    user: User | None = None,
    session: AuthSession | None = None,
    context: dict[str, Any] | None = None,
    deliver: bool = True,
) -> OtpChallenge:
    """Create a challenge and send its code. ``deliver=False`` creates a challenge that can never
    be satisfied (used where revealing that an account exists must be avoided)."""
    now = timezone.now()
    code = new_numeric_code()
    challenge = OtpChallenge(
        purpose=purpose,
        channel=channel,
        destination=destination,
        user=user,
        session=session,
        context=context or {},
        last_sent_at=now,
        expires_at=now + CODE_LIFETIME,
    )
    challenge.code_hash = keyed_hash(_code_purpose(challenge), code) if deliver else ""
    challenge.save()
    if deliver:
        _deliver(challenge, code, store_name=store_name)
    return challenge


def get_open(challenge_id: str | uuid.UUID, *, purpose: str) -> OtpChallenge:
    challenge = OtpChallenge.objects.filter(pk=challenge_pk(challenge_id), purpose=purpose).first()
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= timezone.now()
    ):
        raise CodeExpired()
    return challenge


@transaction.atomic
def resend(challenge: OtpChallenge, *, store_name: str) -> OtpChallenge:
    # Re-read under lock so simultaneous resend requests cannot both beat the cooldown.
    challenge = OtpChallenge.objects.select_for_update().get(pk=challenge.pk)
    now = timezone.now()
    if challenge.consumed_at is not None or challenge.send_count >= MAX_SENDS:
        raise CodeExpired()
    if now - challenge.last_sent_at < RESEND_COOLDOWN:
        raise ResendTooSoon()
    if not challenge.code_hash:  # a decoy stays a decoy; behave exactly like a real resend
        OtpChallenge.objects.filter(pk=challenge.pk).update(
            last_sent_at=now, send_count=challenge.send_count + 1, expires_at=now + CODE_LIFETIME
        )
        challenge.refresh_from_db()
        return challenge
    code = new_numeric_code()
    challenge.code_hash = keyed_hash(_code_purpose(challenge), code)
    challenge.last_sent_at = now
    challenge.send_count += 1
    challenge.attempts = 0
    challenge.expires_at = now + CODE_LIFETIME
    challenge.save(
        update_fields=["code_hash", "last_sent_at", "send_count", "attempts", "expires_at"]
    )
    _deliver(challenge, code, store_name=store_name)
    return challenge


def seconds_until_resend(challenge: OtpChallenge) -> int:
    remaining = RESEND_COOLDOWN - (timezone.now() - challenge.last_sent_at)
    return max(0, int(remaining.total_seconds()))


def verify(
    challenge_id: str | uuid.UUID,
    code: str,
    *,
    purpose: str,
    session: AuthSession | None = None,
    consume: bool = True,
) -> OtpChallenge:
    """Check ``code``; on success mark the challenge verified (and consumed unless a follow-up step
    still needs it). Wrong codes count towards the attempt limit even under concurrency: the
    outcome is decided under a row lock and the attempt is committed before the error is raised.
    Callers must not wrap this in a transaction that rolls back on the error."""
    failure: type[CodeExpired] | type[InvalidCode] | None = None
    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(pk=challenge_pk(challenge_id), purpose=purpose)
            .first()
        )
        now = timezone.now()
        if (
            challenge is None
            or challenge.consumed_at is not None
            or challenge.verified_at is not None
            or challenge.expires_at <= now
            or challenge.attempts >= MAX_ATTEMPTS
            or (session is not None and challenge.session_id != session.pk)
        ):
            failure = CodeExpired
        elif not challenge.code_hash or not matches(
            challenge.code_hash, _code_purpose(challenge), normalize_code(code)
        ):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            failure = CodeExpired if challenge.attempts >= MAX_ATTEMPTS else InvalidCode
        else:
            challenge.verified_at = now
            challenge.consumed_at = now if consume else None
            challenge.save(update_fields=["verified_at", "consumed_at"])
    if failure is not None or challenge is None:
        raise (failure or CodeExpired)()
    return challenge


def challenge_pk(value: str | uuid.UUID) -> uuid.UUID:
    """The challenge id as a UUID; malformed ids become the nil UUID, which matches nothing."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.UUID(int=0)


def consume(challenge: OtpChallenge) -> None:
    OtpChallenge.objects.filter(pk=challenge.pk, consumed_at__isnull=True).update(
        consumed_at=timezone.now()
    )
