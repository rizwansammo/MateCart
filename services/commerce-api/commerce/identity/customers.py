"""Customer accounts: registration, phone sign-in, Google sign-in and linking sign-in methods.

Customers register themselves (the only public registration). Every method proves control of what
it adds: an email by a code sent to it, a phone by an SMS code, a Google account by an OpenID
Connect sign-in. Adding a method to an existing account needs the signed-in session *and* that
proof, and never happens by matching email addresses (D-043). Responses never reveal whether
someone else's email or phone has an account.
"""

from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from commerce.audit.services import record_audit_event
from commerce.core import delivery, ratelimit
from commerce.core.contact import normalize_email, normalize_phone
from commerce.core.security import decrypt, encrypt, hash_token, new_token
from commerce.stores.services import current_store

from . import otp, sessions
from .errors import AccountConflict, CodeExpired, InvalidCode, MethodUnavailable
from .google import CompletedAuthorization, GoogleSignInFailed
from .models import (
    AccountType,
    AuthSession,
    ExternalIdentity,
    IdentityProvider,
    OtpChallenge,
    OtpChannel,
    OtpPurpose,
    User,
)
from .passwords import check_password_strength

HOUR = 3600
CODES_PER_DESTINATION = 5
CODES_PER_IP = 20


def _limit_codes(destination: str, ip: str) -> None:
    ratelimit.consume("code-destination", destination, limit=CODES_PER_DESTINATION, window=HOUR)
    ratelimit.consume("code-ip", ip, limit=CODES_PER_IP, window=HOUR)


class InvalidPhone(InvalidCode):
    default_detail = "Enter a valid mobile number."
    default_code = "invalid_phone"


def sms_enabled() -> bool:
    return delivery.sms_provider() is not None


def parse_phone(value: str) -> str:
    try:
        phone = normalize_phone(value, current_store().country or None)
    except ValidationError as exc:
        raise InvalidPhone() from exc
    if phone is None:
        raise InvalidPhone()
    return phone


def _customers() -> Any:
    return User.objects.filter(account_type=AccountType.CUSTOMER)


# --- Email + password registration ----------------------------------------------------------------


def start_registration(*, full_name: str, email: str, password: str, ip: str) -> OtpChallenge:
    """Send a verification code; the account is created only once the code is confirmed.

    If the email already has an account, the response is identical but the challenge can never
    be satisfied, and the mailbox owner gets a 'you already have an account' email instead.
    """
    address = normalize_email(email) or ""
    _limit_codes(address, ip)
    check_password_strength(password, email=address, full_name=full_name)
    store = current_store()
    with transaction.atomic():
        if _customers().filter(email=address).exists():
            challenge = otp.issue(
                purpose=OtpPurpose.REGISTRATION,
                channel=OtpChannel.EMAIL,
                destination=address,
                store_name=store.name,
                deliver=False,
            )
            delivery.send_email(
                to=address,
                subject=f"{store.name}: you already have an account",
                template="account_exists",
                context={"store_name": store.name, "base_url": settings.MATECART_PUBLIC_BASE_URL},
            )
            return challenge
        return otp.issue(
            purpose=OtpPurpose.REGISTRATION,
            channel=OtpChannel.EMAIL,
            destination=address,
            store_name=store.name,
            context={"full_name": full_name, "password": encrypt(make_password(password))},
        )


def complete_registration(*, challenge_id: str, code: str) -> User:
    challenge = otp.verify(challenge_id, code, purpose=OtpPurpose.REGISTRATION)
    now = timezone.now()
    try:
        with transaction.atomic():
            user = User(
                account_type=AccountType.CUSTOMER,
                email=challenge.destination,
                email_verified_at=now,
                full_name=str(challenge.context.get("full_name", ""))[:150],
                password=decrypt(str(challenge.context["password"])),
            )
            user.full_clean()
            user.save()
            OtpChallenge.objects.filter(pk=challenge.pk).update(context={})
            record_audit_event(action="account.customer.registered", resource=user, actor=user)
    except (IntegrityError, ValidationError) as exc:  # registered in another tab meanwhile
        raise AccountConflict(
            detail="You already have an account with this email. Sign in instead."
        ) from exc
    return user


# --- Phone sign-in (passwordless) -----------------------------------------------------------------


def start_phone_sign_in(*, phone: str, ip: str) -> OtpChallenge:
    if not sms_enabled():
        raise MethodUnavailable(detail="Sign-in by text message isn't available right now.")
    number = parse_phone(phone)
    _limit_codes(number, ip)
    with transaction.atomic():
        return otp.issue(
            purpose=OtpPurpose.PHONE_SIGN_IN,
            channel=OtpChannel.SMS,
            destination=number,
            store_name=current_store().name,
        )


def verify_phone_sign_in(*, challenge_id: str, code: str) -> tuple[User | None, str | None]:
    """(user, None) for an existing account, or (None, completion token) when the number is new
    and the customer still has to tell us their name."""
    challenge = otp.verify(challenge_id, code, purpose=OtpPurpose.PHONE_SIGN_IN, consume=False)
    user = _customers().filter(phone=challenge.destination, is_active=True).first()
    if user is not None:
        otp.consume(challenge)
        return user, None
    token = new_token()
    OtpChallenge.objects.filter(pk=challenge.pk).update(context={"completion": hash_token(token)})
    return None, token


def complete_phone_sign_up(*, challenge_id: str, completion_token: str, full_name: str) -> User:
    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(
                pk=otp.challenge_pk(challenge_id),
                purpose=OtpPurpose.PHONE_SIGN_IN,
                verified_at__isnull=False,
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        expected = str(challenge.context.get("completion", "")) if challenge else ""
        if challenge is None or not expected or expected != hash_token(completion_token or ""):
            raise CodeExpired()
        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at"])
        if _customers().filter(phone=challenge.destination).exists():
            raise AccountConflict(detail="This number already has an account. Sign in instead.")
        user = User(
            account_type=AccountType.CUSTOMER,
            phone=challenge.destination,
            phone_verified_at=timezone.now(),
            full_name=full_name[:150],
        )
        user.set_unusable_password()
        user.full_clean()
        user.save()
        record_audit_event(action="account.customer.registered", resource=user, actor=user)
    return user


# --- Google ---------------------------------------------------------------------------------------


class GoogleOutcome:
    SIGNED_IN = "signed_in"
    LINKED = "linked"
    EMAIL_IN_USE = "email_in_use"
    ALREADY_LINKED = "already_linked"
    EMAIL_UNVERIFIED = "email_unverified"


def google_sign_in(result: CompletedAuthorization) -> tuple[str, User | None]:
    """Sign in by Google ``sub``, or create a new account. An existing account with the same email
    is never taken over: the customer signs in to it first and links Google from there."""
    profile = result.profile
    identity = (
        ExternalIdentity.objects.select_related("user")
        .filter(provider=IdentityProvider.GOOGLE, subject=profile.subject)
        .first()
    )
    if identity is not None:
        if not identity.user.is_active or identity.user.account_type != AccountType.CUSTOMER:
            raise GoogleSignInFailed("Account not usable.")
        ExternalIdentity.objects.filter(pk=identity.pk).update(last_used_at=timezone.now())
        return GoogleOutcome.SIGNED_IN, identity.user
    if not profile.email or not profile.email_verified:
        return GoogleOutcome.EMAIL_UNVERIFIED, None
    if _customers().filter(email=profile.email).exists():
        return GoogleOutcome.EMAIL_IN_USE, None
    try:
        with transaction.atomic():
            user = User(
                account_type=AccountType.CUSTOMER,
                email=profile.email,
                email_verified_at=timezone.now(),
                full_name=profile.name,
            )
            user.set_unusable_password()
            user.full_clean()
            user.save()
            ExternalIdentity.objects.create(
                user=user,
                provider=IdentityProvider.GOOGLE,
                subject=profile.subject,
                last_used_at=timezone.now(),
            )
            record_audit_event(
                action="account.customer.registered",
                resource=user,
                actor=user,
                changes={"method": "google"},
            )
    except (IntegrityError, ValidationError):
        return GoogleOutcome.EMAIL_IN_USE, None
    return GoogleOutcome.SIGNED_IN, user


def google_link(result: CompletedAuthorization, session: AuthSession | None) -> str:
    """Link the Google account to the customer who started the flow, in the same browser."""
    if session is None or session.user_id != result.user_id:
        raise GoogleSignInFailed("Linking session mismatch.")
    user = session.user
    try:
        with transaction.atomic():
            if (
                ExternalIdentity.objects.filter(
                    provider=IdentityProvider.GOOGLE, subject=result.profile.subject
                ).exists()
                or ExternalIdentity.objects.filter(
                    user=user, provider=IdentityProvider.GOOGLE
                ).exists()
            ):
                return GoogleOutcome.ALREADY_LINKED
            ExternalIdentity.objects.create(
                user=user, provider=IdentityProvider.GOOGLE, subject=result.profile.subject
            )
            record_audit_event(
                action="account.sign_in_method.linked",
                resource=user,
                actor=user,
                changes={"method": "google"},
            )
    except IntegrityError:
        return GoogleOutcome.ALREADY_LINKED
    return GoogleOutcome.LINKED


def unlink_google(user: User) -> None:
    if not (user.has_usable_password() and user.email) and not user.phone:
        raise AccountConflict(
            detail="Add a password or a phone number first, so you can still sign in."
        )
    with transaction.atomic():
        deleted, _ = ExternalIdentity.objects.filter(
            user=user, provider=IdentityProvider.GOOGLE
        ).delete()
        if deleted:
            record_audit_event(
                action="account.sign_in_method.unlinked",
                resource=user,
                actor=user,
                changes={"method": "google"},
            )


# --- Adding an email or phone to a signed-in account ----------------------------------------------


def start_link_email(*, user: User, email: str, ip: str) -> OtpChallenge:
    address = normalize_email(email) or ""
    _limit_codes(address, ip)
    with transaction.atomic():
        return otp.issue(
            purpose=OtpPurpose.LINK_EMAIL,
            channel=OtpChannel.EMAIL,
            destination=address,
            user=user,
            store_name=current_store().name,
        )


def start_link_phone(*, user: User, phone: str, ip: str) -> OtpChallenge:
    if not sms_enabled():
        raise MethodUnavailable(detail="Text messages aren't available right now.")
    number = parse_phone(phone)
    _limit_codes(number, ip)
    with transaction.atomic():
        return otp.issue(
            purpose=OtpPurpose.LINK_PHONE,
            channel=OtpChannel.SMS,
            destination=number,
            user=user,
            store_name=current_store().name,
        )


def complete_link(*, user: User, purpose: str, challenge_id: str, code: str) -> User:
    """Attach the verified email or phone. Uniqueness is checked only after the code proves
    control of the address, so the step cannot be used to discover other people's accounts."""
    challenge = otp.get_open(challenge_id, purpose=purpose)
    if challenge.user_id != user.pk:
        raise CodeExpired()
    challenge = otp.verify(challenge_id, code, purpose=purpose)
    field = "email" if purpose == OtpPurpose.LINK_EMAIL else "phone"
    try:
        with transaction.atomic():
            setattr(user, field, challenge.destination)
            setattr(user, f"{field}_verified_at", timezone.now())
            user.full_clean()
            user.save(update_fields=[field, f"{field}_verified_at", "updated_at"])
            record_audit_event(
                action="account.sign_in_method.linked",
                resource=user,
                actor=user,
                changes={"method": field},
            )
    except (IntegrityError, ValidationError) as exc:
        user.refresh_from_db()
        noun = "email" if field == "email" else "number"
        raise AccountConflict(detail=f"This {noun} is already used by another account.") from exc
    return user


def verify_password_or_code(
    *,
    user: User,
    password: str | None = None,
    challenge_id: str | None = None,
    code: str | None = None,
) -> None:
    """Step-up check for sensitive changes: the current password, or a code sent to the account's
    own verified email or phone."""
    if password:
        ratelimit.check("customer-reauth", str(user.pk), limit=5)
        if not user.check_password(password):
            ratelimit.hit("customer-reauth", str(user.pk), window=15 * 60)
            raise InvalidCode(detail="That password isn't right.")
        return
    challenge = otp.get_open(challenge_id or "", purpose=OtpPurpose.REAUTHENTICATION)
    if challenge.user_id != user.pk:
        raise CodeExpired()
    otp.verify(challenge_id or "", code or "", purpose=OtpPurpose.REAUTHENTICATION)


def send_reauthentication_code(*, user: User, ip: str) -> OtpChallenge:
    if user.email and user.email_verified_at:
        channel, destination = OtpChannel.EMAIL, user.email
    elif user.phone and user.phone_verified_at and sms_enabled():
        channel, destination = OtpChannel.SMS, user.phone
    else:
        raise MethodUnavailable(detail="We can't send you a code right now.")
    _limit_codes(destination, ip)
    with transaction.atomic():
        return otp.issue(
            purpose=OtpPurpose.REAUTHENTICATION,
            channel=channel,
            destination=destination,
            user=user,
            store_name=current_store().name,
        )


def sign_in(user: User) -> tuple[AuthSession, str]:
    return sessions.start(user)
