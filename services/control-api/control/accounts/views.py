"""Console sign-in and account security under /api/console/."""

import logging
from typing import Any, cast

from django.conf import settings
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from control.core.http import client_ip
from control.core.totp import format_secret

from . import services, sessions
from .auth import (
    ConsoleSessionAuthentication,
    CsrfProtectedAPIView,
    PendingEnrollmentAuthentication,
    PendingMfaAuthentication,
)
from .errors import ActionNotAllowed
from .models import AdminOtpChallenge, AdminSession, AdminUser, OtpPurpose, SessionStatus

logger = logging.getLogger(__name__)

TAGS = ["console-auth"]
SECURITY_TAGS = ["console-security"]
METHODS = [services.Method.TOTP, services.Method.EMAIL, services.Method.RECOVERY]


def _password() -> serializers.CharField:
    return serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)


# --- Serializers ----------------------------------------------------------------------------------


class LoginSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(max_length=254)
    password = _password()


class EmailSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField(max_length=254)


class TokenSerializer(serializers.Serializer[dict[str, Any]]):
    token = serializers.CharField(max_length=128)


class TokenPasswordSerializer(serializers.Serializer[dict[str, Any]]):
    token = serializers.CharField(max_length=128)
    password = _password()


class PasswordSerializer(serializers.Serializer[dict[str, Any]]):
    password = _password()


class PasswordChangeSerializer(serializers.Serializer[dict[str, Any]]):
    current_password = _password()
    new_password = _password()


class SecondStepSerializer(serializers.Serializer[dict[str, Any]]):
    method = serializers.ChoiceField(choices=METHODS)
    code = serializers.CharField(max_length=32)
    challenge_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class ResendSerializer(serializers.Serializer[dict[str, Any]]):
    resend = serializers.BooleanField(required=False, default=False)


class CodeSerializer(serializers.Serializer[dict[str, Any]]):
    code = serializers.CharField(max_length=16)


class ChallengeCodeSerializer(serializers.Serializer[dict[str, Any]]):
    challenge_id = serializers.UUIDField()
    code = serializers.CharField(max_length=16)


class SignInStatusSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=["signed_in", "mfa_required", "enrollment_required"])
    methods = serializers.ListField(child=serializers.ChoiceField(choices=METHODS))
    email_hint = serializers.CharField(allow_blank=True)


class ChallengeSerializer(serializers.Serializer[AdminOtpChallenge]):
    challenge_id = serializers.UUIDField(source="pk")
    channel = serializers.SerializerMethodField()
    destination = serializers.SerializerMethodField()
    resend_in = serializers.SerializerMethodField()
    expires_in = serializers.SerializerMethodField()

    def get_channel(self, obj: AdminOtpChallenge) -> str:
        return "email"

    def get_destination(self, obj: AdminOtpChallenge) -> str:
        return mask_email(obj.destination)

    def get_resend_in(self, obj: AdminOtpChallenge) -> int:
        return services.seconds_until_resend(obj)

    def get_expires_in(self, obj: AdminOtpChallenge) -> int:
        return int(services.CODE_LIFETIME.total_seconds())


class AdminSerializer(serializers.Serializer[AdminUser]):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()


class ActivationPreviewSerializer(serializers.Serializer[AdminUser]):
    email = serializers.EmailField()
    full_name = serializers.CharField()


class SecurityStatusSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()
    totp = serializers.BooleanField()
    email_codes = serializers.BooleanField()
    recovery_codes_remaining = serializers.IntegerField()
    reauthenticated = serializers.BooleanField()


class TotpSetupSerializer(serializers.Serializer[dict[str, Any]]):
    qr_svg = serializers.CharField()
    secret = serializers.CharField()
    uri = serializers.CharField()


class RecoveryCodesSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=["signed_in"], required=False)
    recovery_codes = serializers.ListField(child=serializers.CharField(), allow_null=True)


class CsrfSerializer(serializers.Serializer[dict[str, Any]]):
    csrf_token = serializers.CharField()


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}{'•' * max(2, min(len(local) - 1, 6))}@{domain}"


def _body(serializer_class: Any, request: Request) -> dict[str, Any]:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    data: dict[str, Any] = serializer.validated_data
    return data


def _with_session(response: Response, session: AdminSession, token: str) -> Response:
    sessions.set_cookie(response, session, token)
    return response


def _rotated(response: Response, session: AdminSession) -> Response:
    return _with_session(response, session, sessions.rotate(session))


def _sign_in_status(session: AdminSession) -> dict[str, Any]:
    if session.status == SessionStatus.ACTIVE:
        return {"status": "signed_in", "methods": [], "email_hint": ""}
    if session.status == SessionStatus.PENDING_ENROLLMENT:
        return {"status": "enrollment_required", "methods": [], "email_hint": ""}
    return {
        "status": "mfa_required",
        "methods": services.methods(session.user),
        "email_hint": mask_email(session.user.email),
    }


def _security(session: AdminSession) -> dict[str, Any]:
    user = session.user
    methods = services.methods(user)
    return {
        "email": user.email,
        "totp": services.Method.TOTP in methods,
        "email_codes": services.Method.EMAIL in methods,
        "recovery_codes_remaining": services.recovery_codes_remaining(user),
        "reauthenticated": sessions.recently_authenticated(session),
    }


# --- Views ----------------------------------------------------------------------------------------


class AnonymousView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)


class _SessionView(CsrfProtectedAPIView):
    permission_classes = (IsAuthenticated,)

    @property
    def session(self) -> AdminSession:
        return cast(AdminSession, self.request.auth)


class PendingMfaView(_SessionView):
    authentication_classes = (PendingMfaAuthentication,)


class EnrollmentView(_SessionView):
    authentication_classes = (PendingEnrollmentAuthentication,)


class ConsoleView(_SessionView):
    authentication_classes = (ConsoleSessionAuthentication,)


@method_decorator([never_cache, ensure_csrf_cookie], name="dispatch")
class CsrfView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="csrf", tags=["security"], responses={200: CsrfSerializer})
    def get(self, request: Request) -> Response:
        return Response({"csrf_token": get_token(request._request)})


class LoginView(AnonymousView):
    @extend_schema(
        operation_id="console_login",
        tags=TAGS,
        request=LoginSerializer,
        responses={200: SignInStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(LoginSerializer, request)
        result = services.sign_in(
            email=data["email"], password=data["password"], ip=client_ip(request)
        )
        return _with_session(
            Response(_sign_in_status(result.session)), result.session, result.token
        )


class SecondStepView(PendingMfaView):
    @extend_schema(
        operation_id="console_mfa_verify",
        tags=TAGS,
        request=SecondStepSerializer,
        responses={200: SignInStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(SecondStepSerializer, request)
        session, token = services.complete_second_step(
            self.session,
            method=data["method"],
            code=data["code"],
            challenge_id=str(data["challenge_id"]) if data["challenge_id"] else None,
        )
        logger.info(
            "Console sign-in completed (admin=%s, method=%s)", session.user_id, data["method"]
        )
        return _with_session(Response(_sign_in_status(session)), session, token)


class SecondStepEmailView(PendingMfaView):
    @extend_schema(
        operation_id="console_mfa_email_send",
        tags=TAGS,
        request=ResendSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(ResendSerializer, request)
        if self.session.user.email_mfa_enabled_at is None:
            raise ActionNotAllowed(detail="Email codes aren't turned on for this account.")
        challenge = services.current_or_new_code(
            purpose=OtpPurpose.MFA_EMAIL, session=self.session, resend=data["resend"]
        )
        return Response(ChallengeSerializer(challenge).data, status=status.HTTP_202_ACCEPTED)


class LogoutView(AnonymousView):
    @extend_schema(operation_id="console_logout", tags=TAGS, request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        token = request.COOKIES.get(settings.MATECART_CONSOLE_COOKIE)
        for state in SessionStatus.values:
            session = sessions.resolve(token, status=state)
            if session is not None:
                sessions.revoke(session)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        sessions.clear_cookie(response)
        return response


@method_decorator(never_cache, name="dispatch")
class SessionView(ConsoleView):
    @extend_schema(operation_id="console_session", tags=TAGS, responses={200: AdminSerializer})
    def get(self, request: Request) -> Response:
        return Response(AdminSerializer(self.session.user).data)


class ActivationInspectView(AnonymousView):
    @extend_schema(
        operation_id="console_activation_inspect",
        tags=TAGS,
        request=TokenSerializer,
        responses={200: ActivationPreviewSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(TokenSerializer, request)
        return Response(
            ActivationPreviewSerializer(services.inspect_activation(data["token"])).data
        )


class ActivationView(AnonymousView):
    @extend_schema(
        operation_id="console_activate",
        tags=TAGS,
        request=TokenPasswordSerializer,
        responses={200: SignInStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(TokenPasswordSerializer, request)
        session, token = services.activate(token=data["token"], password=data["password"])
        logger.info("Console administrator activated (admin=%s)", session.user_id)
        return _with_session(Response(_sign_in_status(session)), session, token)


class PasswordForgotView(AnonymousView):
    @extend_schema(
        operation_id="console_password_forgot",
        tags=TAGS,
        request=EmailSerializer,
        responses={202: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(EmailSerializer, request)
        services.request_reset(email=data["email"], ip=client_ip(request))
        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetView(AnonymousView):
    @extend_schema(
        operation_id="console_password_reset",
        tags=TAGS,
        request=TokenPasswordSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(TokenPasswordSerializer, request)
        services.reset_password(token=data["token"], password=data["password"])
        response = Response(status=status.HTTP_204_NO_CONTENT)
        sessions.clear_cookie(response)
        return response


# --- Enrollment (mandatory before anything else) --------------------------------------------------


class EnrollTotpSetupView(EnrollmentView):
    @extend_schema(
        operation_id="console_enroll_totp_setup",
        tags=TAGS,
        request=None,
        responses={200: TotpSetupSerializer},
    )
    def post(self, request: Request) -> Response:
        setup = services.begin_totp(self.session.user)
        return Response(
            {"qr_svg": setup.qr_svg, "secret": format_secret(setup.secret), "uri": setup.uri}
        )


class EnrollTotpConfirmView(EnrollmentView):
    @extend_schema(
        operation_id="console_enroll_totp_confirm",
        tags=TAGS,
        request=CodeSerializer,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(CodeSerializer, request)
        codes = services.confirm_totp_for(self.session, data["code"])
        session, token = sessions.activate(self.session)
        body = {"status": "signed_in", "recovery_codes": codes}
        return _with_session(Response(body), session, token)


class EnrollEmailStartView(EnrollmentView):
    @extend_schema(
        operation_id="console_enroll_email_start",
        tags=TAGS,
        request=ResendSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(ResendSerializer, request)
        challenge = services.current_or_new_code(
            purpose=OtpPurpose.MFA_EMAIL_SETUP, session=self.session, resend=data["resend"]
        )
        return Response(ChallengeSerializer(challenge).data, status=status.HTTP_202_ACCEPTED)


class EnrollEmailConfirmView(EnrollmentView):
    @extend_schema(
        operation_id="console_enroll_email_confirm",
        tags=TAGS,
        request=ChallengeCodeSerializer,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(ChallengeCodeSerializer, request)
        codes = services.confirm_email_for(
            self.session, challenge_id=str(data["challenge_id"]), code=data["code"]
        )
        session, token = sessions.activate(self.session)
        return _with_session(
            Response({"status": "signed_in", "recovery_codes": codes}), session, token
        )


# --- Account security (signed in) -----------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class SecurityView(ConsoleView):
    @extend_schema(
        operation_id="console_security",
        tags=SECURITY_TAGS,
        responses={200: SecurityStatusSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(_security(self.session))


class ReauthenticateView(ConsoleView):
    @extend_schema(
        operation_id="console_reauthenticate",
        tags=SECURITY_TAGS,
        request=PasswordSerializer,
        responses={200: SecurityStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(PasswordSerializer, request)
        services.reauthenticate(self.session, data["password"])
        return Response(_security(self.session))


class TotpSetupView(ConsoleView):
    @extend_schema(
        operation_id="console_totp_setup",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: TotpSetupSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session)
        setup = services.begin_totp(self.session.user)
        return Response(
            {"qr_svg": setup.qr_svg, "secret": format_secret(setup.secret), "uri": setup.uri}
        )


class TotpConfirmView(ConsoleView):
    @extend_schema(
        operation_id="console_totp_confirm",
        tags=SECURITY_TAGS,
        request=CodeSerializer,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session)
        data = _body(CodeSerializer, request)
        return _rotated(
            Response({"recovery_codes": services.confirm_totp_for(self.session, data["code"])}),
            self.session,
        )


class TotpDisableView(ConsoleView):
    @extend_schema(
        operation_id="console_totp_disable",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: SecurityStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        services.remove_totp(self.session)
        return _rotated(Response(_security(self.session)), self.session)


class EmailCodesStartView(ConsoleView):
    @extend_schema(
        operation_id="console_email_codes_start",
        tags=SECURITY_TAGS,
        request=ResendSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session)
        data = _body(ResendSerializer, request)
        challenge = services.current_or_new_code(
            purpose=OtpPurpose.MFA_EMAIL_SETUP, session=self.session, resend=data["resend"]
        )
        return Response(ChallengeSerializer(challenge).data, status=status.HTTP_202_ACCEPTED)


class EmailCodesConfirmView(ConsoleView):
    @extend_schema(
        operation_id="console_email_codes_confirm",
        tags=SECURITY_TAGS,
        request=ChallengeCodeSerializer,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session)
        data = _body(ChallengeCodeSerializer, request)
        codes = services.confirm_email_for(
            self.session, challenge_id=str(data["challenge_id"]), code=data["code"]
        )
        return _rotated(Response({"recovery_codes": codes}), self.session)


class EmailCodesDisableView(ConsoleView):
    @extend_schema(
        operation_id="console_email_codes_disable",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: SecurityStatusSerializer},
    )
    def post(self, request: Request) -> Response:
        services.remove_email(self.session)
        return _rotated(Response(_security(self.session)), self.session)


class RecoveryCodesView(ConsoleView):
    @extend_schema(
        operation_id="console_recovery_codes_regenerate",
        tags=SECURITY_TAGS,
        request=None,
        responses={200: RecoveryCodesSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session)
        return _rotated(
            Response({"recovery_codes": services.regenerate_recovery_codes(self.session.user)}),
            self.session,
        )


class PasswordChangeView(ConsoleView):
    @extend_schema(
        operation_id="console_password_change",
        tags=SECURITY_TAGS,
        request=PasswordChangeSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(PasswordChangeSerializer, request)
        services.change_password(
            self.session, current=data["current_password"], new=data["new_password"]
        )
        return _rotated(Response(status=status.HTTP_204_NO_CONTENT), self.session)
