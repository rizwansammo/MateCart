from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from control.core.models import TimestampedModel, UUIDModel


def normalize_admin_email(value: str | None) -> str:
    return (value or "").strip().lower()


class AdminUserManager(BaseUserManager["AdminUser"]):
    def create_user(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> "AdminUser":
        normalized = normalize_admin_email(email)
        if not normalized:
            raise ValueError("Console administrators need an email address.")
        user = self.model(email=normalized, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean()
        user.save(using=self._db)
        return user


class AdminUser(UUIDModel, TimestampedModel, AbstractBaseUser):
    """A NetaMate team member who operates the Central Platform Console.

    Exists only in the control-plane database: console identities are never stored in, and never
    sign in to, a store's commerce database, and store accounts never sign in here. Created only
    by NetaMate personnel (``create_admin``); two-step verification is mandatory (D-045). Console
    roles arrive in Phase 16.
    """

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    # Set when the administrator chose a password from the activation link.
    activated_at = models.DateTimeField(null=True, blank=True)
    # Second step by email code. Authenticator apps live in AdminTotpDevice.
    email_mfa_enabled_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[AdminUserManager] = AdminUserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(email=Lower("email")) & ~Q(email=""),
                name="accounts_adminuser_email_normalized",
            ),
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self.email = normalize_admin_email(self.email)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = normalize_admin_email(self.email)
        super().save(*args, **kwargs)

    @property
    def mfa_enabled(self) -> bool:
        return (
            self.email_mfa_enabled_at is not None
            or AdminTotpDevice.objects.filter(user=self, confirmed_at__isnull=False).exists()
        )


class SessionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PENDING_MFA = "pending_mfa", "Waiting for the second step"
    PENDING_ENROLLMENT = "pending_enrollment", "Must set up two-step verification"


class AdminSession(UUIDModel):
    """A signed-in console session. The cookie holds a random token; only its hash is stored.
    Only ``active`` sessions reach console data; the pending states allow nothing but finishing
    the second step or enrolling it."""

    user = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name="+")
    status = models.CharField(max_length=24, choices=SessionStatus.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    authenticated_at = models.DateTimeField()
    reauthenticated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=SessionStatus.values),
                name="accounts_adminsession_status_valid",
            ),
        ]
        indexes = [models.Index(fields=["user", "revoked_at"], name="accounts_session_user")]


class OtpPurpose(models.TextChoices):
    MFA_EMAIL = "mfa_email", "Sign-in code by email"
    MFA_EMAIL_SETUP = "mfa_email_setup", "Turn on email codes"


class AdminOtpChallenge(UUIDModel):
    """An emailed one-time code; only its HMAC is stored. Bound to the session that asked."""

    purpose = models.CharField(max_length=32, choices=OtpPurpose.choices)
    destination = models.EmailField()
    user = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name="+")
    session = models.ForeignKey(AdminSession, on_delete=models.CASCADE, related_name="+")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField()
    send_count = models.PositiveSmallIntegerField(default=1)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(purpose__in=OtpPurpose.values),
                name="accounts_adminotpchallenge_purpose_valid",
            ),
        ]


class ActionTokenPurpose(models.TextChoices):
    ACTIVATION = "activation", "Account activation"
    PASSWORD_RESET = "password_reset", "Password reset"


class AdminActionToken(UUIDModel):
    """A single-use emailed link (activation, password reset). Only the token's hash is stored."""

    purpose = models.CharField(max_length=32, choices=ActionTokenPurpose.choices)
    user = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name="+")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(purpose__in=ActionTokenPurpose.values),
                name="accounts_adminactiontoken_purpose_valid",
            ),
        ]


class AdminTotpDevice(UUIDModel):
    """An authenticator app; the seed is encrypted at rest and usable once confirmed."""

    user = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name="+")
    secret_encrypted = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(confirmed_at__isnull=False),
                name="accounts_admintotpdevice_one_confirmed",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(confirmed_at__isnull=True),
                name="accounts_admintotpdevice_one_pending",
            ),
        ]


class AdminRecoveryCode(UUIDModel):
    """A single-use backup code for the second step, stored as an HMAC."""

    user = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name="+")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "code_hash"], name="accounts_adminrecoverycode_unique"
            ),
        ]
