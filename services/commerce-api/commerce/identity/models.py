from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from commerce.core.contact import E164_PATTERN, normalize_email, normalize_phone
from commerce.core.models import TimestampedModel
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
