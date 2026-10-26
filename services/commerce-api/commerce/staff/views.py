"""Merchant dashboard API under /api/manage/ (staff realm only, cookie scoped to this path)."""

import uuid
from typing import Any, cast

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.core.http import client_ip
from commerce.core.totp import format_secret
from commerce.identity import mfa, passwords, sessions
from commerce.identity.auth import (
    CsrfProtectedAPIView,
    HasStaffPermission,
    StaffAuth,
    StaffPendingMfaAuthentication,
    StaffSessionAuthentication,
)
from commerce.identity.models import AccountType, AuthSession, SessionStatus, User
from commerce.identity.serializers import (
    ChallengeCodeSerializer,
    ChallengeSerializer,
    EmailSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    mask_email,
)
from commerce.stores.services import current_store

from . import services, signin
from .models import MembershipStatus
from .permissions import ROLE_DESCRIPTIONS, Permission, StaffRole
from .serializers import (
    InvitationAcceptSerializer,
    InvitationCreateSerializer,
    InvitationPreviewSerializer,
    InvitationSerializer,
    InvitationTokenSerializer,
    MemberSerializer,
    MfaEmailSendSerializer,
    MfaVerifySerializer,
    PasswordConfirmSerializer,
    RecoveryCodesSerializer,
    RoleChangeSerializer,
    SecurityStatusSerializer,
    SignInStatusSerializer,
    StaffSessionSerializer,
    StaffUserSerializer,
    TeamSerializer,
    TotpConfirmSerializer,
    TotpSetupSerializer,
    role_options,
)

STAFF = AccountType.STAFF
AUTH_TAGS = ["manage-auth"]
TEAM_TAGS = ["manage-team"]
SECURITY_TAGS = ["manage-security"]
CanManageStaff = HasStaffPermission.of(Permission.STAFF_MANAGE)
CanManageCatalog = HasStaffPermission.of(Permission.CATALOG_MANAGE)
CanManageContent = HasStaffPermission.of(Permission.CONTENT_MANAGE)
CanManageSettings = HasStaffPermission.of(Permission.STORE_SETTINGS)


def _body(serializer_class: Any, request: Request) -> dict[str, Any]:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    data: dict[str, Any] = serializer.validated_data
    return data


def _with_session(response: Response, session: AuthSession, token: str) -> Response:
    sessions.set_cookie(response, session, token)
    return response


def _rotated(response: Response, session: AuthSession) -> Response:
    return _with_session(response, session, sessions.rotate(session))


class AnonymousView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)


class PendingMfaView(CsrfProtectedAPIView):
    authentication_classes = (StaffPendingMfaAuthentication,)
    permission_classes = (IsAuthenticated,)


class StaffView(CsrfProtectedAPIView):
    authentication_classes = (StaffSessionAuthentication,)
    permission_classes: tuple[type[BasePermission], ...] = (IsAuthenticated,)

    @property
    def staff(self) -> StaffAuth:
        return cast(StaffAuth, self.request.auth)

    @property
    def user(self) -> User:
        user: User = self.request.user  # type: ignore[assignment]
        return user


class CatalogStaffView(StaffView):
    """Catalogue, pricing, inventory and media management: ``catalog.manage`` (Owner, Manager,
    Content editor). Cost prices additionally need ``catalog.costs``."""

    permission_classes = (IsAuthenticated, CanManageCatalog)

    @property
    def can_see_costs(self) -> bool:
        return Permission.CATALOG_COSTS in self.staff.permissions


class ContentStaffView(StaffView):
    """Store appearance and content: ``content.manage`` (Owner, Manager, Content editor)."""

    permission_classes = (IsAuthenticated, CanManageContent)

    @property
    def can_manage_settings(self) -> bool:
        return Permission.STORE_SETTINGS in self.staff.permissions


class SettingsStaffView(StaffView):
    """Store configuration such as modules: ``store.settings`` (Owner, Manager)."""

    permission_classes = (IsAuthenticated, CanManageSettings)


class SensitiveTeamMutationView(StaffView):
    """Owner-only team changes also require a recent password confirmation."""

    permission_classes = (IsAuthenticated, CanManageStaff)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        sessions.require_recent_authentication(self.staff.session)


# --- Sign-in --------------------------------------------------------------------------------------


class LoginView(AnonymousView):
    @extend_schema(
        operation_id="manage_login",
        tags=AUTH_TAGS,
        request=LoginSerializer,
        responses={200: SignInStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(LoginSerializer, request)
        result = signin.sign_in(
            email=data["email"], password=data["password"], ip=client_ip(request)
        )
        user = result.session.user
        body = {
            "status": "mfa_required" if result.mfa_required else "signed_in",
            "methods": signin.available_methods(user) if result.mfa_required else [],
            "email_hint": mask_email(user.email) if result.mfa_required and user.email else "",
        }
        return _with_session(Response(body), result.session, result.token)


class MfaVerifyView(PendingMfaView):
    @extend_schema(
        operation_id="manage_mfa_verify",
        tags=AUTH_TAGS,
        request=MfaVerifySerializer,
        responses={200: SignInStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(MfaVerifySerializer, request)
        pending = cast(AuthSession, request.auth)
        session, token = signin.complete_mfa(
            pending,
            method=data["method"],
            code=data["code"],
            challenge_id=str(data["challenge_id"]) if data["challenge_id"] else None,
        )
        body = {"status": "signed_in", "methods": [], "email_hint": ""}
        return _with_session(Response(body), session, token)


class MfaEmailView(PendingMfaView):
    @extend_schema(
        operation_id="manage_mfa_email_send",
        tags=AUTH_TAGS,
        request=MfaEmailSendSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(MfaEmailSendSerializer, request)
        challenge = signin.send_email_code(cast(AuthSession, request.auth), resend=data["resend"])
        return Response(ChallengeSerializer(challenge).data, status=status.HTTP_202_ACCEPTED)


class LogoutView(AnonymousView):
    @extend_schema(
        operation_id="manage_logout", tags=AUTH_TAGS, request=None, responses={204: None}
    )
    def post(self, request: Request) -> Response:
        token = request.COOKIES.get(sessions.cookie_spec(STAFF)["name"])
        for state in (SessionStatus.ACTIVE, SessionStatus.PENDING_MFA):
            session = sessions.resolve(token, STAFF, status=state)
            if session is not None:
                sessions.revoke(session)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        sessions.clear_cookie(response, STAFF)
        return response


@method_decorator(never_cache, name="dispatch")
class SessionView(StaffView):
    @extend_schema(
        operation_id="manage_session", tags=AUTH_TAGS, responses={200: StaffSessionSerializer}
    )
    def get(self, request: Request) -> Response:
        membership = self.staff.membership
        role = StaffRole(membership.role)
        return Response(
            {
                "user": StaffUserSerializer(self.user).data,
                "role": role.value,
                "role_label": role.label,
                "permissions": sorted(self.staff.permissions),
                "store_name": current_store().name,
                "mfa_enabled": self.user.mfa_enabled,
            }
        )


class PasswordForgotView(AnonymousView):
    @extend_schema(
        operation_id="manage_password_forgot",
        tags=AUTH_TAGS,
        request=EmailSerializer,
        responses={202: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(EmailSerializer, request)
        with transaction.atomic():
            passwords.request_reset(
                realm=STAFF,
                email=data["email"],
                ip=client_ip(request),
                link_base=f"{settings.MATECART_PUBLIC_BASE_URL}/manage/reset-password",
                store_name=current_store().name,
            )
        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetView(AnonymousView):
    @extend_schema(
        operation_id="manage_password_reset",
        tags=AUTH_TAGS,
        request=PasswordResetSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(PasswordResetSerializer, request)
        passwords.reset(realm=STAFF, token=data["token"], password=data["password"])
        response = Response(status=status.HTTP_204_NO_CONTENT)
        sessions.clear_cookie(response, STAFF)
        return response


# --- Invitations (public side) --------------------------------------------------------------------


class InvitationInspectView(AnonymousView):
    @extend_schema(
        operation_id="manage_invitation_inspect",
        tags=TEAM_TAGS,
        request=InvitationTokenSerializer,
        responses={200: InvitationPreviewSerializer},
        description="POST keeps the token out of URLs and access logs.",
    )
    def post(self, request: Request) -> Response:
        data = _body(InvitationTokenSerializer, request)
        invitation = services.inspect_invitation(data["token"])
        role = StaffRole(invitation.role)
        return Response(
            {
                "email": invitation.email,
                "role": role.value,
                "role_label": role.label,
                "role_description": ROLE_DESCRIPTIONS[role],
                "store_name": current_store().name,
                "expires_at": invitation.expires_at,
            }
        )


class InvitationAcceptView(AnonymousView):
    @extend_schema(
        operation_id="manage_invitation_accept",
        tags=TEAM_TAGS,
        request=InvitationAcceptSerializer,
        responses={201: SignInStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(InvitationAcceptSerializer, request)
        user = services.accept_invitation(
            token=data["token"], full_name=data["full_name"].strip(), password=data["password"]
        )
        session, token = sessions.start(user)
        body = {"status": "signed_in", "methods": [], "email_hint": ""}
        return _with_session(Response(body, status=status.HTTP_201_CREATED), session, token)


# --- Team management (owner) ----------------------------------------------------------------------


def _team_payload() -> dict[str, Any]:
    return {
        "members": services.team(),
        "invitations": services.pending_invitations(),
        "roles": role_options(),
    }


@method_decorator(never_cache, name="dispatch")
class TeamView(StaffView):
    permission_classes = (IsAuthenticated, CanManageStaff)

    @extend_schema(operation_id="manage_team", tags=TEAM_TAGS, responses={200: TeamSerializer})
    def get(self, request: Request) -> Response:
        return Response(TeamSerializer(_team_payload()).data)


class InvitationCreateView(SensitiveTeamMutationView):
    @extend_schema(
        operation_id="manage_invitation_create",
        tags=TEAM_TAGS,
        request=InvitationCreateSerializer,
        responses={201: InvitationSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(InvitationCreateSerializer, request)
        issued = services.invite(inviter=self.user, email=data["email"], role=data["role"])
        return Response(
            InvitationSerializer(issued.invitation).data, status=status.HTTP_201_CREATED
        )


class InvitationResendView(SensitiveTeamMutationView):
    @extend_schema(
        operation_id="manage_invitation_resend",
        tags=TEAM_TAGS,
        request=None,
        responses={200: InvitationSerializer},
    )
    def post(self, request: Request, invitation_id: uuid.UUID) -> Response:
        issued = services.resend_invitation(actor=self.user, invitation_id=invitation_id)
        return Response(InvitationSerializer(issued.invitation).data)


class InvitationRevokeView(SensitiveTeamMutationView):
    @extend_schema(
        operation_id="manage_invitation_revoke", tags=TEAM_TAGS, request=None, responses={204: None}
    )
    def post(self, request: Request, invitation_id: uuid.UUID) -> Response:
        services.revoke_invitation(actor=self.user, invitation_id=invitation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemberRoleView(SensitiveTeamMutationView):
    @extend_schema(
        operation_id="manage_member_role",
        tags=TEAM_TAGS,
        request=RoleChangeSerializer,
        responses={200: MemberSerializer},
    )
    def post(self, request: Request, membership_id: uuid.UUID) -> Response:
        data = _body(RoleChangeSerializer, request)
        membership = services.change_role(
            actor=self.user, membership_id=membership_id, role=data["role"]
        )
        return Response(MemberSerializer(membership).data)


class _MemberStatusView(SensitiveTeamMutationView):
    target_status: str

    def post(self, request: Request, membership_id: uuid.UUID) -> Response:
        membership = services.set_status(
            actor=self.user, membership_id=membership_id, status=self.target_status
        )
        return Response(MemberSerializer(membership).data)


@extend_schema_view(
    post=extend_schema(
        operation_id="manage_member_suspend",
        tags=TEAM_TAGS,
        request=None,
        responses={200: MemberSerializer},
    ),
)
class MemberSuspendView(_MemberStatusView):
    target_status = MembershipStatus.SUSPENDED


@extend_schema_view(
    post=extend_schema(
        operation_id="manage_member_reactivate",
        tags=TEAM_TAGS,
        request=None,
        responses={200: MemberSerializer},
    ),
)
class MemberReactivateView(_MemberStatusView):
    target_status = MembershipStatus.ACTIVE


# --- Own account security -------------------------------------------------------------------------


def _security_status(session: AuthSession) -> dict[str, Any]:
    user = session.user
    state = mfa.status(user)
    recent = max(filter(None, [session.authenticated_at, session.reauthenticated_at]))
    return {
        "email": user.email,
        "totp": state.totp,
        "email_codes": state.email,
        "recovery_codes_remaining": state.recovery_codes_remaining,
        "reauthenticated": timezone.now() - recent <= sessions.REAUTHENTICATION_WINDOW,
    }


@method_decorator(never_cache, name="dispatch")
class SecurityView(StaffView):
    @extend_schema(
        operation_id="manage_security",
        tags=SECURITY_TAGS,
        responses={200: SecurityStatusSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(_security_status(self.staff.session))


class ReauthenticateView(StaffView):
    @extend_schema(
        operation_id="manage_reauthenticate",
        tags=SECURITY_TAGS,
        request=PasswordConfirmSerializer,
        responses={200: SecurityStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(PasswordConfirmSerializer, request)
        signin.reauthenticate(self.staff.session, password=data["password"])
        return Response(_security_status(self.staff.session))


@method_decorator(never_cache, name="dispatch")
class TotpSetupView(StaffView):
    @extend_schema(
        operation_id="manage_totp_setup",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: TotpSetupSerializer},
    )
    def post(self, request: Request) -> Response:
        setup = signin.begin_totp(self.staff.session)
        return Response(
            {"qr_svg": setup.qr_svg, "secret": format_secret(setup.secret), "uri": setup.uri}
        )


class TotpConfirmView(StaffView):
    @extend_schema(
        operation_id="manage_totp_confirm",
        tags=SECURITY_TAGS,
        request=TotpConfirmSerializer,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(TotpConfirmSerializer, request)
        return _rotated(
            Response({"recovery_codes": signin.confirm_totp(self.staff.session, data["code"])}),
            self.staff.session,
        )


class TotpDisableView(StaffView):
    @extend_schema(
        operation_id="manage_totp_disable",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: SecurityStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        signin.disable_totp(self.staff.session)
        return _rotated(Response(_security_status(self.staff.session)), self.staff.session)


class EmailMfaStartView(StaffView):
    @extend_schema(
        operation_id="manage_email_mfa_start",
        tags=SECURITY_TAGS,
        request=MfaEmailSendSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(MfaEmailSendSerializer, request)
        challenge = signin.begin_email_mfa(self.staff.session, resend=data["resend"])
        return Response(ChallengeSerializer(challenge).data, status=status.HTTP_202_ACCEPTED)


class EmailMfaConfirmView(StaffView):
    @extend_schema(
        operation_id="manage_email_mfa_confirm",
        tags=SECURITY_TAGS,
        request=ChallengeCodeSerializer,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(ChallengeCodeSerializer, request)
        codes = signin.confirm_email_mfa(
            self.staff.session, challenge_id=str(data["challenge_id"]), code=data["code"]
        )
        return _rotated(Response({"recovery_codes": codes}), self.staff.session)


class EmailMfaDisableView(StaffView):
    @extend_schema(
        operation_id="manage_email_mfa_disable",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: SecurityStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        signin.disable_email_mfa(self.staff.session)
        return _rotated(Response(_security_status(self.staff.session)), self.staff.session)


@method_decorator(never_cache, name="dispatch")
class RecoveryCodesView(StaffView):
    @extend_schema(
        operation_id="manage_recovery_codes_regenerate",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        return _rotated(
            Response({"recovery_codes": signin.regenerate_recovery_codes(self.staff.session)}),
            self.staff.session,
        )


class PasswordChangeView(StaffView):
    @extend_schema(
        operation_id="manage_password_change",
        tags=SECURITY_TAGS,
        request=PasswordChangeSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(PasswordChangeSerializer, request)
        passwords.change(
            user=self.user,
            current_password=data["current_password"],
            new_password=data["new_password"],
            keep=self.staff.session,
        )
        return _rotated(Response(status=status.HTTP_204_NO_CONTENT), self.staff.session)
