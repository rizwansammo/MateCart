"""Merchant staff: owner activation, invitations, team management and staff sign-in (D-044).

There is no public staff registration. The store owner is invited by NetaMate provisioning
(``invite_owner``); everyone else is invited by the owner. Only holders of ``staff.manage`` (the
owner) can invite, change roles, suspend or reactivate, and nobody can grant the owner role,
change their own access, or act on the owner's membership.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from commerce.audit.services import field_changes, record_audit_event
from commerce.core import delivery, ratelimit
from commerce.core.contact import normalize_email
from commerce.core.security import hash_token, new_token
from commerce.identity import sessions
from commerce.identity.errors import AccountConflict, AuthError, InvalidLink
from commerce.identity.models import AccountType, User
from commerce.identity.passwords import check_password_strength
from commerce.stores.services import current_store

from .models import MembershipStatus, StaffInvitation, StaffMembership
from .permissions import ASSIGNABLE_ROLES, ROLE_DESCRIPTIONS, StaffRole

INVITATION_LIFETIME = timedelta(days=7)
OWNER_INVITATION_LIFETIME = timedelta(days=14)
INVITATION_RESEND_COOLDOWN = timedelta(seconds=60)
INVITATIONS_PER_HOUR = 30


class StaffError(AuthError):
    default_detail = "This change isn't allowed."
    default_code = "staff_change_not_allowed"


class AccessSuspended(AuthError):
    status_code = 403
    default_detail = (
        "Your access to this store's dashboard has been suspended. Contact the store owner."
    )
    default_code = "access_suspended"


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: StaffInvitation
    token: str


def _invitation_link(token: str) -> str:
    return f"{settings.MATECART_PUBLIC_BASE_URL}/manage/invitation?token={token}"


def _send_invitation(invitation: StaffInvitation, token: str, *, inviter_name: str) -> None:
    store = current_store()
    delivery.send_email(
        to=invitation.email,
        subject=f"You're invited to manage {store.name}",
        template="staff_invitation",
        context={
            "store_name": store.name,
            "inviter_name": inviter_name,
            "role": StaffRole(invitation.role).label,
            "role_description": ROLE_DESCRIPTIONS[StaffRole(invitation.role)],
            "link": _invitation_link(token),
            "expires_at": invitation.expires_at,
            "is_owner": invitation.role == StaffRole.OWNER,
        },
    )


def invite_owner(*, email: str, actor_label: str = "invite_owner") -> IssuedInvitation:
    """NetaMate provisioning step: invite the store owner. Refused once the store has an owner."""
    address = normalize_email(email)
    if not address:
        raise StaffError(detail="Enter the owner's email address.")
    with transaction.atomic():
        if StaffMembership.objects.filter(role=StaffRole.OWNER).exists():
            raise StaffError(detail="This store already has an owner.")
        if User.objects.filter(account_type=AccountType.STAFF, email=address).exists():
            raise StaffError(detail="A staff account with this email already exists.")
        StaffInvitation.objects.filter(
            role=StaffRole.OWNER, accepted_at__isnull=True, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        StaffInvitation.objects.filter(
            email=address, accepted_at__isnull=True, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        token = new_token()
        invitation = StaffInvitation.objects.create(
            email=address,
            role=StaffRole.OWNER,
            token_hash=hash_token(token),
            expires_at=timezone.now() + OWNER_INVITATION_LIFETIME,
        )
        record_audit_event(
            action="staff.owner.invited",
            resource=invitation,
            actor_label=actor_label,
            changes={"role": StaffRole.OWNER},
        )
        _send_invitation(invitation, token, inviter_name="MateCart")
    return IssuedInvitation(invitation, token)


def invite(*, inviter: User, email: str, role: str) -> IssuedInvitation:
    if role not in ASSIGNABLE_ROLES:
        raise StaffError(detail="Choose a valid role.")
    address = normalize_email(email)
    if not address:
        raise StaffError(detail="Enter an email address.")
    ratelimit.consume(
        "staff-invite", str(inviter.tenant_id), limit=INVITATIONS_PER_HOUR, window=3600
    )
    with transaction.atomic():
        if User.objects.filter(account_type=AccountType.STAFF, email=address).exists():
            raise AccountConflict(
                detail="This person already has a staff account. Manage them from the team list."
            )
        if StaffInvitation.objects.filter(
            email=address, accepted_at__isnull=True, revoked_at__isnull=True
        ).exists():
            raise AccountConflict(
                detail="This person already has a pending invitation. Resend it instead."
            )
        token = new_token()
        try:
            with transaction.atomic():
                invitation = StaffInvitation.objects.create(
                    email=address,
                    role=role,
                    token_hash=hash_token(token),
                    invited_by=inviter,
                    expires_at=timezone.now() + INVITATION_LIFETIME,
                )
        except IntegrityError as exc:
            raise AccountConflict(
                detail="This person already has a pending invitation. Resend it instead."
            ) from exc
        record_audit_event(
            action="staff.invitation.created",
            resource=invitation,
            actor=inviter,
            changes={"role": role},
        )
        _send_invitation(invitation, token, inviter_name=inviter.full_name or "The store owner")
    return IssuedInvitation(invitation, token)


def pending_invitations() -> list[StaffInvitation]:
    return list(
        StaffInvitation.objects.filter(
            accepted_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now()
        )
        .exclude(role=StaffRole.OWNER)
        .order_by("-created_at")
    )


def _pending(invitation_id: uuid.UUID) -> StaffInvitation:
    invitation = (
        StaffInvitation.objects.select_for_update()
        .filter(pk=invitation_id, accepted_at__isnull=True, revoked_at__isnull=True)
        .exclude(role=StaffRole.OWNER)
        .first()
    )
    if invitation is None:
        raise StaffError(detail="This invitation is no longer pending.")
    return invitation


def resend_invitation(*, actor: User, invitation_id: uuid.UUID) -> IssuedInvitation:
    with transaction.atomic():
        invitation = _pending(invitation_id)
        now = timezone.now()
        if now - invitation.last_sent_at < INVITATION_RESEND_COOLDOWN:
            raise StaffError(detail="Please wait a moment before resending this invitation.")
        token = new_token()
        invitation.token_hash = hash_token(token)  # the previous link stops working
        invitation.last_sent_at = now
        invitation.expires_at = now + INVITATION_LIFETIME
        invitation.save(update_fields=["token_hash", "last_sent_at", "expires_at"])
        record_audit_event(action="staff.invitation.resent", resource=invitation, actor=actor)
        _send_invitation(invitation, token, inviter_name=actor.full_name or "The store owner")
    return IssuedInvitation(invitation, token)


def revoke_invitation(*, actor: User, invitation_id: uuid.UUID) -> None:
    with transaction.atomic():
        invitation = _pending(invitation_id)
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        record_audit_event(action="staff.invitation.revoked", resource=invitation, actor=actor)


def _open_invitation(token: str, *, lock: bool = False) -> StaffInvitation:
    rows = StaffInvitation.objects.select_for_update() if lock else StaffInvitation.objects.all()
    invitation = rows.filter(
        token_hash=hash_token(token or ""), accepted_at__isnull=True, revoked_at__isnull=True
    ).first()
    if invitation is None or invitation.expires_at <= timezone.now():
        raise InvalidLink(
            detail="This invitation has expired or was already used. Ask the owner for a new one."
        )
    return invitation


def inspect_invitation(token: str) -> StaffInvitation:
    return _open_invitation(token)


def accept_invitation(*, token: str, full_name: str, password: str) -> User:
    """Create the staff account from the invitation. The emailed link proves the address."""
    with transaction.atomic():
        invitation = _open_invitation(token, lock=True)
        check_password_strength(password, email=invitation.email, full_name=full_name)
        if User.objects.filter(account_type=AccountType.STAFF, email=invitation.email).exists():
            raise AccountConflict(
                detail="A staff account with this email already exists. Sign in instead."
            )
        if (
            invitation.role == StaffRole.OWNER
            and StaffMembership.objects.filter(role=StaffRole.OWNER).exists()
        ):
            raise InvalidLink(detail="This store already has an owner.")
        now = timezone.now()
        user = User(
            account_type=AccountType.STAFF,
            email=invitation.email,
            email_verified_at=now,
            full_name=full_name[:150],
        )
        user.set_password(password)
        user.full_clean()
        user.save()
        membership = StaffMembership.objects.create(user=user, role=invitation.role)
        invitation.accepted_at = now
        invitation.accepted_user = user
        invitation.save(update_fields=["accepted_at", "accepted_user"])
        record_audit_event(
            action="staff.invitation.accepted",
            resource=membership,
            actor=user,
            changes={"role": invitation.role},
        )
    return user


# --- Team management ------------------------------------------------------------------------------


def team() -> list[StaffMembership]:
    return list(StaffMembership.objects.select_related("user").order_by("created_at"))


def _member_for_change(actor: User, membership_id: uuid.UUID) -> StaffMembership:
    membership = (
        StaffMembership.objects.select_for_update()
        .select_related("user")
        .filter(pk=membership_id)
        .first()
    )
    if membership is None:
        raise StaffError(detail="This team member no longer exists.")
    if membership.user_id == actor.pk:
        raise StaffError(detail="You can't change your own access.")
    if membership.role == StaffRole.OWNER:
        raise StaffError(detail="The store owner's access can't be changed here.")
    return membership


def change_role(*, actor: User, membership_id: uuid.UUID, role: str) -> StaffMembership:
    if role not in ASSIGNABLE_ROLES:
        raise StaffError(detail="Choose a valid role.")
    with transaction.atomic():
        membership = _member_for_change(actor, membership_id)
        before = {"role": membership.role}
        membership.role = role
        membership.save(update_fields=["role", "updated_at"])
        record_audit_event(
            action="staff.membership.role_changed",
            resource=membership,
            actor=actor,
            changes=field_changes(before, {"role": role}, fields=["role"]),
        )
    return membership


def set_status(*, actor: User, membership_id: uuid.UUID, status: str) -> StaffMembership:
    with transaction.atomic():
        membership = _member_for_change(actor, membership_id)
        if membership.status == status:
            return membership
        before = {"status": membership.status}
        membership.status = status
        membership.save(update_fields=["status", "updated_at"])
        if status == MembershipStatus.SUSPENDED:
            sessions.revoke_all(membership.user)
        record_audit_event(
            action=(
                "staff.membership.suspended"
                if status == MembershipStatus.SUSPENDED
                else "staff.membership.reactivated"
            ),
            resource=membership,
            actor=actor,
            changes=field_changes(before, {"status": status}, fields=["status"]),
        )
    return membership


def membership_for(user: User) -> StaffMembership | None:
    return StaffMembership.objects.filter(user=user).first()
