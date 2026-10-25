from typing import Any

from rest_framework import serializers

from commerce.identity.mfa import MfaMethod
from commerce.identity.serializers import CodeField, PasswordField

from .models import MembershipStatus, StaffInvitation, StaffMembership
from .permissions import ASSIGNABLE_ROLES, ROLE_DESCRIPTIONS, Permission, StaffRole

MFA_METHODS = [MfaMethod.TOTP, MfaMethod.EMAIL, MfaMethod.RECOVERY]


# --- Requests -------------------------------------------------------------------------------------


class MfaVerifySerializer(serializers.Serializer[dict[str, Any]]):
    method = serializers.ChoiceField(choices=MFA_METHODS)
    code = CodeField(max_length=32)
    challenge_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class MfaEmailSendSerializer(serializers.Serializer[dict[str, Any]]):
    resend = serializers.BooleanField(required=False, default=False)


class InvitationTokenSerializer(serializers.Serializer[dict[str, Any]]):
    token = serializers.CharField(max_length=128)


class InvitationAcceptSerializer(serializers.Serializer[dict[str, Any]]):
    token = serializers.CharField(max_length=128)
    full_name = serializers.CharField(max_length=150)
    password = PasswordField()


class InvitationCreateSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(choices=list(ASSIGNABLE_ROLES))


class RoleChangeSerializer(serializers.Serializer[dict[str, Any]]):
    role = serializers.ChoiceField(choices=list(ASSIGNABLE_ROLES))


class PasswordConfirmSerializer(serializers.Serializer[dict[str, Any]]):
    password = PasswordField()


class TotpConfirmSerializer(serializers.Serializer[dict[str, Any]]):
    code = CodeField()


# --- Responses ------------------------------------------------------------------------------------


class SignInStatusSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=["signed_in", "mfa_required"])
    methods = serializers.ListField(child=serializers.ChoiceField(choices=MFA_METHODS))
    email_hint = serializers.CharField(allow_blank=True)


class StaffUserSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    email = serializers.EmailField(allow_null=True)


class StaffSessionSerializer(serializers.Serializer[dict[str, Any]]):
    user = StaffUserSerializer()
    role = serializers.ChoiceField(choices=StaffRole.choices)
    role_label = serializers.CharField()
    permissions = serializers.ListField(child=serializers.ChoiceField(choices=Permission.choices))
    store_name = serializers.CharField()
    mfa_enabled = serializers.BooleanField()


class RoleSerializer(serializers.Serializer[dict[str, Any]]):
    value = serializers.ChoiceField(choices=StaffRole.choices)
    # A field named "label" (Field.label is a different attribute; the metaclass collects this).
    label = serializers.CharField()  # type: ignore[assignment]
    description = serializers.CharField()


class MemberSerializer(serializers.Serializer[StaffMembership]):
    id = serializers.UUIDField()
    user = StaffUserSerializer()
    role = serializers.ChoiceField(choices=StaffRole.choices)
    status = serializers.ChoiceField(choices=MembershipStatus.choices)
    mfa_enabled = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_mfa_enabled(self, obj: StaffMembership) -> bool:
        return obj.user.mfa_enabled


class InvitationSerializer(serializers.Serializer[StaffInvitation]):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=StaffRole.choices)
    created_at = serializers.DateTimeField()
    last_sent_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField()


class TeamSerializer(serializers.Serializer[dict[str, Any]]):
    members = MemberSerializer(many=True)
    invitations = InvitationSerializer(many=True)
    roles = RoleSerializer(many=True)


class InvitationPreviewSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=StaffRole.choices)
    role_label = serializers.CharField()
    role_description = serializers.CharField()
    store_name = serializers.CharField()
    expires_at = serializers.DateTimeField()


class SecurityStatusSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(allow_null=True)
    totp = serializers.BooleanField()
    email_codes = serializers.BooleanField()
    recovery_codes_remaining = serializers.IntegerField()
    reauthenticated = serializers.BooleanField(
        help_text="True while sensitive changes are allowed without re-entering the password."
    )


class TotpSetupSerializer(serializers.Serializer[dict[str, Any]]):
    qr_svg = serializers.CharField(help_text="data: URI of an SVG QR code.")
    secret = serializers.CharField(help_text="The setup key, grouped for manual entry.")
    uri = serializers.CharField()


class RecoveryCodesSerializer(serializers.Serializer[dict[str, Any]]):
    recovery_codes = serializers.ListField(child=serializers.CharField(), allow_null=True)


def role_options() -> list[dict[str, str]]:
    return [
        {"value": role.value, "label": role.label, "description": ROLE_DESCRIPTIONS[role]}
        for role in (StaffRole(value) for value in ASSIGNABLE_ROLES)
    ]
