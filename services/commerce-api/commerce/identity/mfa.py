"""Second sign-in step for staff accounts (D-042): authenticator app (TOTP), email codes and
single-use recovery codes. Optional for merchants; once enabled it is always enforced."""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from commerce.core import totp
from commerce.core.security import (
    decrypt,
    encrypt,
    keyed_hash,
    new_recovery_code,
    normalize_code,
    normalize_recovery_code,
)

from .models import OtpChallenge, OtpPurpose, RecoveryCode, TotpDevice, User

RECOVERY_CODE_COUNT = 10


class MfaMethod:
    TOTP = "totp"
    EMAIL = "email"
    RECOVERY = "recovery_code"


@dataclass(frozen=True)
class MfaStatus:
    totp: bool
    email: bool
    recovery_codes_remaining: int

    @property
    def enabled(self) -> bool:
        return self.totp or self.email


def status(user: User) -> MfaStatus:
    return MfaStatus(
        totp=TotpDevice.objects.filter(user=user, confirmed_at__isnull=False).exists(),
        email=user.email_mfa_enabled_at is not None,
        recovery_codes_remaining=RecoveryCode.objects.filter(
            user=user, used_at__isnull=True
        ).count(),
    )


# --- Authenticator app --------------------------------------------------------------------------


@dataclass(frozen=True)
class TotpSetup:
    secret: str
    uri: str
    qr_svg: str


def begin_totp_setup(user: User, *, issuer: str) -> TotpSetup:
    """A fresh, unconfirmed device. Any earlier unfinished setup is discarded; a confirmed device
    keeps working until the new one is confirmed (safe replacement)."""
    secret = totp.new_secret()
    with transaction.atomic():
        TotpDevice.objects.filter(user=user, confirmed_at__isnull=True).delete()
        TotpDevice.objects.create(user=user, secret_encrypted=encrypt(secret))
    uri = totp.provisioning_uri(secret, account=user.email or str(user.pk), issuer=issuer)
    return TotpSetup(secret=secret, uri=uri, qr_svg=totp.qr_svg_data_uri(uri))


def confirm_totp(user: User, code: str) -> bool:
    """Activate the pending device if ``code`` comes from it; replaces any previous device."""
    with transaction.atomic():
        device = (
            TotpDevice.objects.select_for_update()
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
        TotpDevice.objects.filter(user=user, confirmed_at__isnull=False).delete()
        device.confirmed_at = timezone.now()
        device.last_used_step = step
        device.save(update_fields=["confirmed_at", "last_used_step"])
    return True


def verify_totp(user: User, code: str) -> bool:
    with transaction.atomic():
        device = (
            TotpDevice.objects.select_for_update()
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


def remove_totp(user: User) -> None:
    TotpDevice.objects.filter(user=user).delete()


# --- Email codes ----------------------------------------------------------------------------------


def enable_email(user: User) -> None:
    User.objects.filter(pk=user.pk).update(email_mfa_enabled_at=timezone.now())
    user.refresh_from_db(fields=["email_mfa_enabled_at"])


def disable_email(user: User) -> None:
    User.objects.filter(pk=user.pk).update(email_mfa_enabled_at=None)
    user.email_mfa_enabled_at = None
    OtpChallenge.objects.filter(
        user=user, purpose=OtpPurpose.MFA_EMAIL, consumed_at__isnull=True
    ).update(consumed_at=timezone.now())


# --- Recovery codes -------------------------------------------------------------------------------


def _recovery_hash(user: User, code: str) -> str:
    return keyed_hash(f"recovery:{user.pk}", normalize_recovery_code(code))


def regenerate_recovery_codes(user: User) -> list[str]:
    """A new set of codes, returned once in clear. Every earlier code stops working."""
    codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    with transaction.atomic():
        RecoveryCode.objects.filter(user=user).delete()
        RecoveryCode.objects.bulk_create(
            [RecoveryCode(user=user, code_hash=_recovery_hash(user, code)) for code in codes]
        )
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    with transaction.atomic():
        row = (
            RecoveryCode.objects.select_for_update()
            .filter(user=user, code_hash=_recovery_hash(user, code), used_at__isnull=True)
            .first()
        )
        if row is None:
            return False
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
    return True


def clear_recovery_codes(user: User) -> None:
    RecoveryCode.objects.filter(user=user).delete()


def verify_factor(user: User, method: str, code: str) -> bool:
    """TOTP or recovery code. (Email codes are verified as OTP challenges by the caller.)"""
    if method == MfaMethod.TOTP:
        return verify_totp(user, code)
    if method == MfaMethod.RECOVERY:
        return use_recovery_code(user, code)
    return False
