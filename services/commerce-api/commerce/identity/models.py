from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from commerce.core.contact import E164_PATTERN, normalize_email, normalize_phone
from commerce.core.models import TimestampedModel
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.context import get_active_tenant_id
from commerce.tenancy.managers import TenantScopedManager
from commerce.tenancy.models import TenantOwnedModel


class AccountType(models.TextChoices):
    """Separate realms inside one store. The same email may hold one customer account and one
    staff account; they never share credentials, sessions or permissions."""

    CUSTOMER = "customer", "Customer"
    STAFF = "staff", "Merchant staff"


class UserManager(TenantScopedManager["User"]):
    def create_user(
        self,
        *,
        account_type: str,
        email: str | None = None,
        phone: str | None = None,
        password: str | None = None,
        phone_region: str | None = None,
        **extra_fields: Any,
    ) -> "User":
        """Create an account in the active tenant. Phone numbers are stored as E.164; pass the
        store's country as ``phone_region`` to accept national formats. Without a password the
        account gets an unusable one (for example, phone OTP sign-in)."""
        user = self.model(
            tenant_id=get_active_tenant_id(),
            account_type=account_type,
            email=normalize_email(email),
            phone=normalize_phone(phone, phone_region),
            **extra_fields,
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean()
        user.save(using=self._db)
        return user

    def get_by_natural_key(self, username: Any) -> "User":
        return self.get(pk=username)


class User(TenantOwnedModel, TimestampedModel, AbstractBaseUser):
    """A person's account in one store: a customer or a member of the merchant's staff.

    Login identifiers (email, phone) are optional individually but at least one is required, so
    Phase 03 can offer email + password, phone + OTP, or both without changing this table. The
    identity key is the opaque ``id``; login backends resolve email or phone within the tenant and
    realm. Store roles and permissions are designed in Phase 03 (Django's global groups and
    permissions are deliberately not used: they are not tenant-scoped).
    """

    account_type = models.CharField(max_length=16, choices=AccountType.choices)
    # NULL (not "") means "not provided", so the partial unique indexes ignore absent identifiers.
    email = models.EmailField(null=True, blank=True)  # noqa: DJ001
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone = models.CharField(  # noqa: DJ001
        max_length=16,
        null=True,
        blank=True,
        validators=[RegexValidator(E164_PATTERN, "Store phone numbers in E.164 format.")],
    )
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    full_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    # Second factor by email code (staff accounts). TOTP lives in TotpDevice.
    email_mfa_enabled_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "id"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "account_type", "email"],
                condition=Q(email__isnull=False),
                name="identity_user_email_unique_per_realm",
            ),
            models.UniqueConstraint(
                fields=["tenant", "account_type", "phone"],
                condition=Q(phone__isnull=False),
                name="identity_user_phone_unique_per_realm",
            ),
            models.CheckConstraint(
                condition=Q(email__isnull=False) | Q(phone__isnull=False),
                name="identity_user_has_login_identifier",
            ),
            models.CheckConstraint(
                condition=Q(email__isnull=True) | (Q(email=Lower("email")) & ~Q(email="")),
                name="identity_user_email_normalized",
            ),
            models.CheckConstraint(
                condition=Q(phone__isnull=True) | Q(phone__regex=E164_PATTERN),
                name="identity_user_phone_e164",
            ),
            models.CheckConstraint(
                condition=Q(account_type__in=AccountType.values),
                name="identity_user_account_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(email_verified_at__isnull=True) | Q(email__isnull=False),
                name="identity_user_email_verified_has_email",
            ),
            models.CheckConstraint(
                condition=Q(phone_verified_at__isnull=True) | Q(phone__isnull=False),
                name="identity_user_phone_verified_has_phone",
            ),
        ]

    def __str__(self) -> str:
        return self.email or self.phone or str(self.pk)

    def clean(self) -> None:
        super().clean()
        self.email = normalize_email(self.email)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    @property
    def has_password(self) -> bool:
        return self.has_usable_password()

    @property
    def mfa_enabled(self) -> bool:
        return (
            self.email_mfa_enabled_at is not None
            or TotpDevice.objects.filter(user=self, confirmed_at__isnull=False).exists()
        )


class SessionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PENDING_MFA = "pending_mfa", "Waiting for the second step"


class AuthSession(TenantOwnedModel):
    """A signed-in browser session in one realm (customer or staff), D-040.

    The cookie holds a random token; only its SHA-256 hash is stored. A session in
    ``pending_mfa`` grants nothing except completing the second sign-in step.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    realm = models.CharField(max_length=16, choices=AccountType.choices)
    status = models.CharField(max_length=16, choices=SessionStatus.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    authenticated_at = models.DateTimeField()
    mfa_verified_at = models.DateTimeField(null=True, blank=True)
    reauthenticated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="identity_authsession_user_same_tenant"),
            models.CheckConstraint(
                condition=Q(realm__in=AccountType.values), name="identity_authsession_realm_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=SessionStatus.values),
                name="identity_authsession_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "user", "revoked_at"], name="identity_session_user")
        ]


class OtpPurpose(models.TextChoices):
    REGISTRATION = "registration", "Email registration"
    PHONE_SIGN_IN = "phone_sign_in", "Phone sign-in"
    MFA_EMAIL = "mfa_email", "Sign-in code by email"
    MFA_EMAIL_SETUP = "mfa_email_setup", "Turn on email codes"
    LINK_EMAIL = "link_email", "Add an email"
    LINK_PHONE = "link_phone", "Add a phone"
    REAUTHENTICATION = "reauthentication", "Confirm it is you"


class OtpChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"


class OtpChallenge(TenantOwnedModel):
    """A one-time code sent to an email address or phone number.

    Only an HMAC of the code is stored. Codes expire, allow a few attempts, are single-use, and a
    resend replaces the code. ``context`` carries non-secret data for the step after verification
    (for example a pending registration's name and password *hash*).
    """

    purpose = models.CharField(max_length=32, choices=OtpPurpose.choices)
    channel = models.CharField(max_length=8, choices=OtpChannel.choices)
    destination = models.CharField(max_length=254)
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    session = models.ForeignKey(
        AuthSession, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    code_hash = models.CharField(max_length=64, blank=True)
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField()
    send_count = models.PositiveSmallIntegerField(default=1)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="identity_otpchallenge_user_same_tenant"),
            TenantForeignKeyConstraint(
                field="session", name="identity_otpchallenge_session_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(purpose__in=OtpPurpose.values),
                name="identity_otpchallenge_purpose_valid",
            ),
            models.CheckConstraint(
                condition=Q(channel__in=OtpChannel.values),
                name="identity_otpchallenge_channel_valid",
            ),
        ]


class ActionTokenPurpose(models.TextChoices):
    PASSWORD_RESET = "password_reset", "Password reset"


class ActionToken(TenantOwnedModel):
    """A single-use link token (for example password reset). Only the token's hash is stored."""

    purpose = models.CharField(max_length=32, choices=ActionTokenPurpose.choices)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="identity_actiontoken_user_same_tenant"),
            models.CheckConstraint(
                condition=Q(purpose__in=ActionTokenPurpose.values),
                name="identity_actiontoken_purpose_valid",
            ),
        ]


class IdentityProvider(models.TextChoices):
    GOOGLE = "google", "Google"


class ExternalIdentity(TenantOwnedModel):
    """A sign-in method from an external identity provider, keyed by its stable subject id.

    Linked only by an explicit, authenticated flow or when the account is created through it;
    never by matching email addresses (D-043).
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    provider = models.CharField(max_length=16, choices=IdentityProvider.choices)
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        verbose_name_plural = "external identities"
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="user", name="identity_externalidentity_user_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "provider", "subject"], name="identity_externalidentity_subject"
            ),
            models.UniqueConstraint(
                fields=["tenant", "user", "provider"],
                name="identity_externalidentity_one_per_user",
            ),
            models.CheckConstraint(
                condition=Q(provider__in=IdentityProvider.values),
                name="identity_externalidentity_provider_valid",
            ),
        ]


class OidcPurpose(models.TextChoices):
    SIGN_IN = "sign_in", "Sign in"
    LINK = "link", "Link to the signed-in account"


class OidcAuthorization(TenantOwnedModel):
    """Server-side state of one OpenID Connect redirect: state, nonce and PKCE verifier.

    Bound to the starting browser by a cookie whose hash is stored here; single-use; short-lived.
    """

    provider = models.CharField(max_length=16, choices=IdentityProvider.choices)
    purpose = models.CharField(max_length=16, choices=OidcPurpose.choices)
    state_hash = models.CharField(max_length=64, unique=True)
    browser_binding_hash = models.CharField(max_length=64)
    nonce = models.CharField(max_length=64)
    code_verifier_encrypted = models.TextField()
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    next_path = models.CharField(max_length=200, default="/account")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="user", name="identity_oidcauthorization_user_same_tenant"
            ),
            models.CheckConstraint(
                condition=Q(provider__in=IdentityProvider.values),
                name="identity_oidcauthorization_provider_valid",
            ),
            models.CheckConstraint(
                condition=Q(purpose__in=OidcPurpose.values),
                name="identity_oidcauthorization_purpose_valid",
            ),
        ]


class TotpDevice(TenantOwnedModel):
    """An authenticator app registration. The seed is encrypted at rest; it becomes usable only
    after the user proves the app works (``confirmed_at``). At most one confirmed and one pending.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    secret_encrypted = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="identity_totpdevice_user_same_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "user"],
                condition=Q(confirmed_at__isnull=False),
                name="identity_totpdevice_one_confirmed",
            ),
            models.UniqueConstraint(
                fields=["tenant", "user"],
                condition=Q(confirmed_at__isnull=True),
                name="identity_totpdevice_one_pending",
            ),
        ]


class RecoveryCode(TenantOwnedModel):
    """A single-use backup code for the second step, stored as an HMAC (never in clear)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="identity_recoverycode_user_same_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "user", "code_hash"], name="identity_recoverycode_unique"
            ),
        ]
