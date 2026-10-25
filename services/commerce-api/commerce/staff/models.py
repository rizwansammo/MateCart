from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from commerce.core.models import TimestampedModel
from commerce.identity.models import User
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

from .permissions import StaffRole, permissions_for


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class StaffMembership(TenantOwnedModel, TimestampedModel):
    """A staff account's place in the store: its role and whether it may sign in.

    Exactly one membership per staff account; at most one owner per store (database-enforced).
    Suspension revokes sessions immediately (services.suspend_member).
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=32, choices=StaffRole.choices)
    status = models.CharField(
        max_length=16, choices=MembershipStatus.choices, default=MembershipStatus.ACTIVE
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(field="user", name="staff_membership_user_same_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "user"], name="staff_membership_one_per_user"
            ),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(role=StaffRole.OWNER),
                name="staff_membership_one_owner",
            ),
            models.CheckConstraint(
                condition=Q(role__in=StaffRole.values), name="staff_membership_role_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=MembershipStatus.values),
                name="staff_membership_status_valid",
            ),
        ]

    @property
    def permissions(self) -> frozenset[str]:
        return permissions_for(self.role) if self.status == MembershipStatus.ACTIVE else frozenset()


class StaffInvitation(TenantOwnedModel):
    """An expiring, single-use, revocable invitation to join the store's staff.

    Only the token's hash is stored. One pending invitation per email (database-enforced); the
    owner resends or revokes it instead of creating duplicates.
    """

    email = models.EmailField()
    role = models.CharField(max_length=32, choices=StaffRole.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    accepted_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="invited_by", name="staff_invitation_inviter_same_tenant"
            ),
            TenantForeignKeyConstraint(
                field="accepted_user", name="staff_invitation_accepted_same_tenant"
            ),
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="staff_invitation_one_pending_per_email",
            ),
            models.CheckConstraint(
                condition=Q(role__in=StaffRole.values), name="staff_invitation_role_valid"
            ),
            models.CheckConstraint(
                condition=Q(email=Lower("email")) & ~Q(email=""),
                name="staff_invitation_email_normalized",
            ),
            models.CheckConstraint(
                condition=Q(accepted_at__isnull=True) | Q(revoked_at__isnull=True),
                name="staff_invitation_accepted_or_revoked",
            ),
        ]
