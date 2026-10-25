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
    sign in to, a store's commerce database, and store accounts never sign in here. Roles,
    mandatory MFA and sign-in flows arrive in Phases 03 and 16.
    """

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)

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
