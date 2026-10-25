"""Customer account API under /api/account/ (customer realm only)."""

from typing import Any, cast
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from commerce.audit.services import record_audit_event
from commerce.core.http import client_ip, safe_next_path
from commerce.stores.services import current_store

from . import customers, google, otp, passwords, sessions
from .auth import CsrfProtectedAPIView, CustomerSessionAuthentication
from .errors import ActionNotAllowed, CodeExpired, MethodUnavailable
from .models import AccountType, AuthSession, OidcPurpose, OtpChallenge, OtpPurpose, User
from .serializers import (
    ChallengeCodeSerializer,
    ChallengeRefSerializer,
    ChallengeSerializer,
    CsrfSerializer,
    CustomerAccountSerializer,
    EmailSerializer,
    GoogleStartSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    PhoneCompleteSerializer,
    PhoneSerializer,
    PhoneVerifyResultSerializer,
    ProfileUpdateSerializer,
    ReauthenticateSerializer,
    RegisterSerializer,
    SignInMethodsSerializer,
)

CUSTOMER = AccountType.CUSTOMER
TAGS = ["account"]
ANONYMOUS_RESEND = {OtpPurpose.REGISTRATION, OtpPurpose.PHONE_SIGN_IN}
ACCOUNT_RESEND = {OtpPurpose.LINK_EMAIL, OtpPurpose.LINK_PHONE, OtpPurpose.REAUTHENTICATION}
SIGN_IN_PAGE = "/account/sign-in"


def _signed_in(user: User, *, status_code: int = status.HTTP_200_OK) -> Response:
    session, token = sessions.start(user)
    response = Response(CustomerAccountSerializer(user).data, status=status_code)
    sessions.set_cookie(response, session, token)
    return response


def _security_changed(response: Response, session: AuthSession) -> Response:
    with transaction.atomic():
        sessions.revoke_all(session.user, keep=session)
        sessions.set_cookie(response, session, sessions.rotate(session))
    return response


def _challenge(challenge: Any, *, status_code: int = status.HTTP_202_ACCEPTED) -> Response:
    return Response(ChallengeSerializer(challenge).data, status=status_code)


def _body(serializer_class: Any, request: Request) -> dict[str, Any]:
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    data: dict[str, Any] = serializer.validated_data
    return data


class AnonymousView(CsrfProtectedAPIView):
    """Sign-in and registration steps. The customer cookie is read only to sign out or link."""

    authentication_classes = ()
    permission_classes = (AllowAny,)


class CustomerView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    def session(self) -> AuthSession:
        return cast(AuthSession, self.request.auth)

    @property
    def account(self) -> User:
        return cast(User, self.request.user)


@method_decorator([never_cache, ensure_csrf_cookie], name="dispatch")
class CsrfView(APIView):
    """Sets the CSRF cookie the web apps echo in X-CSRFToken on every state-changing request."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="csrf", tags=["security"], responses={200: CsrfSerializer})
    def get(self, request: Request) -> Response:
        return Response({"csrf_token": get_token(request._request)})


@method_decorator(never_cache, name="dispatch")
class MethodsView(AnonymousView):
    @extend_schema(
        operation_id="account_methods", tags=TAGS, responses={200: SignInMethodsSerializer}
    )
    def get(self, request: Request) -> Response:
        return Response(
            {
                "email_password": True,
                "phone": customers.sms_enabled(),
                "google": google.enabled(),
                "phone_country": current_store().country,
            }
        )


class RegisterView(AnonymousView):
    @extend_schema(
        operation_id="account_register",
        tags=TAGS,
        request=RegisterSerializer,
        responses={202: ChallengeSerializer},
        description="Sends a 6-digit code to the email; the account is created once confirmed.",
    )
    def post(self, request: Request) -> Response:
        data = _body(RegisterSerializer, request)
        challenge = customers.start_registration(
            full_name=data["full_name"],
            email=data["email"],
            password=data["password"],
            ip=client_ip(request),
        )
        return _challenge(challenge)


class RegisterVerifyView(AnonymousView):
    @extend_schema(
        operation_id="account_register_verify",
        tags=TAGS,
        request=ChallengeCodeSerializer,
        responses={201: CustomerAccountSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(ChallengeCodeSerializer, request)
        user = customers.complete_registration(challenge_id=data["challenge_id"], code=data["code"])
        return _signed_in(user, status_code=status.HTTP_201_CREATED)


class ResendCodeView(CsrfProtectedAPIView):
    """Resend a code for a registration, phone sign-in, or (signed in) a linking or step-up code."""

    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="account_code_resend",
        tags=TAGS,
        request=ChallengeRefSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(ChallengeRefSerializer, request)
        challenge = OtpChallenge.objects.filter(pk=data["challenge_id"]).first()
        if challenge is None:
            raise CodeExpired()
        user = request.user if isinstance(request.user, User) else None
        allowed = challenge.purpose in ANONYMOUS_RESEND or (
            challenge.purpose in ACCOUNT_RESEND
            and user is not None
            and challenge.user_id == user.pk
        )
        if not allowed:
            raise CodeExpired()
        with transaction.atomic():
            challenge = otp.resend(challenge, store_name=current_store().name)
        return _challenge(challenge)


class LoginView(AnonymousView):
    @extend_schema(
        operation_id="account_login",
        tags=TAGS,
        request=LoginSerializer,
        responses={200: CustomerAccountSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(LoginSerializer, request)
        user = passwords.authenticate(
            realm=CUSTOMER, email=data["email"], password=data["password"], ip=client_ip(request)
        )
        return _signed_in(user)


class LogoutView(AnonymousView):
    @extend_schema(operation_id="account_logout", tags=TAGS, request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        token = request.COOKIES.get(sessions.cookie_spec(CUSTOMER)["name"])
        session = sessions.resolve(token, CUSTOMER)
        if session is not None:
            sessions.revoke(session)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        sessions.clear_cookie(response, CUSTOMER)
        return response


class PasswordForgotView(AnonymousView):
    @extend_schema(
        operation_id="account_password_forgot",
        tags=TAGS,
        request=EmailSerializer,
        responses={202: None},
        description="Always 202: the response never reveals whether the email has an account.",
    )
    def post(self, request: Request) -> Response:
        data = _body(EmailSerializer, request)
        with transaction.atomic():
            passwords.request_reset(
                realm=CUSTOMER,
                email=data["email"],
                ip=client_ip(request),
                link_base=f"{settings.MATECART_PUBLIC_BASE_URL}/account/reset-password",
                store_name=current_store().name,
            )
        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetView(AnonymousView):
    @extend_schema(
        operation_id="account_password_reset",
        tags=TAGS,
        request=PasswordResetSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(PasswordResetSerializer, request)
        passwords.reset(realm=CUSTOMER, token=data["token"], password=data["password"])
        response = Response(status=status.HTTP_204_NO_CONTENT)
        sessions.clear_cookie(response, CUSTOMER)
        return response


class PhoneStartView(AnonymousView):
    @extend_schema(
        operation_id="account_phone_start",
        tags=TAGS,
        request=PhoneSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(PhoneSerializer, request)
        return _challenge(customers.start_phone_sign_in(phone=data["phone"], ip=client_ip(request)))


class PhoneVerifyView(AnonymousView):
    @extend_schema(
        operation_id="account_phone_verify",
        tags=TAGS,
        request=ChallengeCodeSerializer,
        responses={200: PhoneVerifyResultSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(ChallengeCodeSerializer, request)
        user, completion = customers.verify_phone_sign_in(
            challenge_id=data["challenge_id"], code=data["code"]
        )
        if user is None:
            return Response({"status": "needs_name", "completion_token": completion})
        session, token = sessions.start(user)
        response = Response(
            {"status": "signed_in", "account": CustomerAccountSerializer(user).data}
        )
        sessions.set_cookie(response, session, token)
        return response


class PhoneCompleteView(AnonymousView):
    @extend_schema(
        operation_id="account_phone_complete",
        tags=TAGS,
        request=PhoneCompleteSerializer,
        responses={201: CustomerAccountSerializer},
    )
    def post(self, request: Request) -> Response:
        data = _body(PhoneCompleteSerializer, request)
        user = customers.complete_phone_sign_up(
            challenge_id=data["challenge_id"],
            completion_token=data["completion_token"],
            full_name=data["full_name"],
        )
        return _signed_in(user, status_code=status.HTTP_201_CREATED)


# --- Google ---------------------------------------------------------------------------------------


def _redirect(path: str, **query: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(f"{path}?{urlencode(query)}" if query else path)


def _with_binding(response: Any, binding: str) -> Any:
    response.set_cookie(
        google.BINDING_COOKIE,
        binding,
        max_age=int(google.AUTHORIZATION_LIFETIME.total_seconds()),
        path=google.BINDING_COOKIE_PATH,
        secure=settings.MATECART_SECURE_COOKIES,
        httponly=True,
        samesite="Lax",  # sent on the top-level redirect back from Google
    )
    return response


@method_decorator(never_cache, name="dispatch")
class GoogleStartView(AnonymousView):
    """Browser navigation (not XHR): redirects to Google's consent screen."""

    @extend_schema(
        operation_id="account_google_start",
        tags=TAGS,
        parameters=[OpenApiParameter("next", str, required=False)],
        responses={302: OpenApiResponse(description="Redirect to Google")},
    )
    def get(self, request: Request) -> HttpResponseRedirect:
        if not google.enabled():
            return _redirect(SIGN_IN_PAGE, error="google_unavailable")
        next_path = safe_next_path(request.query_params.get("next"), default="/account")
        url, binding = google.begin(purpose=OidcPurpose.SIGN_IN, next_path=next_path)
        response: HttpResponseRedirect = _with_binding(HttpResponseRedirect(url), binding)
        return response


class LinkGoogleStartView(CsrfProtectedAPIView):
    authentication_classes = (CustomerSessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="account_link_google_start",
        tags=TAGS,
        request=None,
        responses={200: GoogleStartSerializer},
    )
    def post(self, request: Request) -> Response:
        if not google.enabled():
            raise MethodUnavailable(detail="Google sign-in isn't available right now.")
        session = cast(AuthSession, request.auth)
        sessions.require_recent_authentication(session)
        url, binding = google.begin(
            purpose=OidcPurpose.LINK, next_path="/account", user=session.user
        )
        response: Response = _with_binding(Response({"url": url}), binding)
        return response


@method_decorator(never_cache, name="dispatch")
class GoogleCallbackView(AnonymousView):
    """Google redirects the browser here. Always answers with a redirect to a storefront page."""

    @extend_schema(
        operation_id="account_google_callback",
        tags=TAGS,
        parameters=[
            OpenApiParameter("state", str, required=False),
            OpenApiParameter("code", str, required=False),
            OpenApiParameter("error", str, required=False),
        ],
        responses={302: OpenApiResponse(description="Redirect to the storefront")},
    )
    def get(self, request: Request) -> HttpResponseRedirect:
        response = self._handle(request)
        response.delete_cookie(
            google.BINDING_COOKIE, path=google.BINDING_COOKIE_PATH, samesite="Lax"
        )
        return response

    def _handle(self, request: Request) -> HttpResponseRedirect:
        if not google.enabled():
            return _redirect(SIGN_IN_PAGE, error="google_unavailable")
        if request.query_params.get("error"):
            return _redirect(SIGN_IN_PAGE, error="google_cancelled")
        try:
            result = google.complete(
                state=request.query_params.get("state", ""),
                code=request.query_params.get("code", ""),
                binding=request.COOKIES.get(google.BINDING_COOKIE),
            )
            if result.purpose == OidcPurpose.LINK:
                current = sessions.resolve(
                    request.COOKIES.get(sessions.cookie_spec(CUSTOMER)["name"]), CUSTOMER
                )
                outcome = customers.google_link(result, current)
                response = _redirect("/account", google=outcome)
                if current is not None and outcome == customers.GoogleOutcome.LINKED:
                    with transaction.atomic():
                        sessions.revoke_all(current.user, keep=current)
                        sessions.set_cookie(response, current, sessions.rotate(current))
                return response
            outcome, user = customers.google_sign_in(result)
        except google.GoogleSignInFailed:
            return _redirect(SIGN_IN_PAGE, error="google_failed")
        if user is None:
            return _redirect(SIGN_IN_PAGE, error=f"google_{outcome}")
        session, token = sessions.start(user)
        response = HttpResponseRedirect(safe_next_path(result.next_path, default="/account"))
        sessions.set_cookie(response, session, token)
        return response


# --- Signed-in account ----------------------------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class MeView(CustomerView):
    @extend_schema(operation_id="account_me", tags=TAGS, responses={200: CustomerAccountSerializer})
    def get(self, request: Request) -> Response:
        return Response(CustomerAccountSerializer(self.account).data)

    @extend_schema(
        operation_id="account_me_update",
        tags=TAGS,
        request=ProfileUpdateSerializer,
        responses={200: CustomerAccountSerializer},
    )
    def patch(self, request: Request) -> Response:
        data = _body(ProfileUpdateSerializer, request)
        user = self.account
        with transaction.atomic():
            user.full_name = data["full_name"].strip()
            user.save(update_fields=["full_name", "updated_at"])
            record_audit_event(action="account.profile.updated", resource=user, actor=user)
        return Response(CustomerAccountSerializer(user).data)


class ReauthenticateCodeView(CustomerView):
    @extend_schema(
        operation_id="account_reauthenticate_code",
        tags=TAGS,
        request=None,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        user = self.account
        return _challenge(customers.send_reauthentication_code(user=user, ip=client_ip(request)))


class ReauthenticateView(CustomerView):
    @extend_schema(
        operation_id="account_reauthenticate",
        tags=TAGS,
        request=ReauthenticateSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        data = _body(ReauthenticateSerializer, request)
        user = self.account
        customers.verify_password_or_code(
            user=user,
            password=data["password"],
            challenge_id=str(data["challenge_id"]) if data["challenge_id"] else None,
            code=data["code"],
        )
        sessions.mark_reauthenticated(self.session())
        return Response(status=status.HTTP_204_NO_CONTENT)


class LinkEmailStartView(CustomerView):
    @extend_schema(
        operation_id="account_link_email_start",
        tags=TAGS,
        request=EmailSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session())
        data = _body(EmailSerializer, request)
        user = self.account
        return _challenge(
            customers.start_link_email(user=user, email=data["email"], ip=client_ip(request))
        )


class LinkPhoneStartView(CustomerView):
    @extend_schema(
        operation_id="account_link_phone_start",
        tags=TAGS,
        request=PhoneSerializer,
        responses={202: ChallengeSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session())
        data = _body(PhoneSerializer, request)
        user = self.account
        return _challenge(
            customers.start_link_phone(user=user, phone=data["phone"], ip=client_ip(request))
        )


class _LinkVerifyView(CustomerView):
    purpose: str

    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session())
        data = _body(ChallengeCodeSerializer, request)
        user = self.account
        user = customers.complete_link(
            user=user,
            purpose=self.purpose,
            challenge_id=str(data["challenge_id"]),
            code=data["code"],
        )
        return _security_changed(Response(CustomerAccountSerializer(user).data), self.session())


@extend_schema_view(
    post=extend_schema(
        operation_id="account_link_email_verify",
        tags=TAGS,
        request=ChallengeCodeSerializer,
        responses={200: CustomerAccountSerializer},
    ),
)
class LinkEmailVerifyView(_LinkVerifyView):
    purpose = OtpPurpose.LINK_EMAIL


@extend_schema_view(
    post=extend_schema(
        operation_id="account_link_phone_verify",
        tags=TAGS,
        request=ChallengeCodeSerializer,
        responses={200: CustomerAccountSerializer},
    ),
)
class LinkPhoneVerifyView(_LinkVerifyView):
    purpose = OtpPurpose.LINK_PHONE


class UnlinkGoogleView(CustomerView):
    @extend_schema(
        operation_id="account_unlink_google",
        tags=TAGS,
        request=None,
        responses={200: CustomerAccountSerializer},
    )
    def post(self, request: Request) -> Response:
        sessions.require_recent_authentication(self.session())
        user = self.account
        customers.unlink_google(user)
        return _security_changed(Response(CustomerAccountSerializer(user).data), self.session())


class PasswordChangeView(CustomerView):
    @extend_schema(
        operation_id="account_password_change",
        tags=TAGS,
        request=PasswordChangeSerializer,
        responses={204: None},
        description="Change the password, or set one for accounts created by phone or Google.",
    )
    def post(self, request: Request) -> Response:
        data = _body(PasswordChangeSerializer, request)
        user = self.account
        if not user.has_usable_password():
            sessions.require_recent_authentication(self.session())
            if not user.email:
                raise ActionNotAllowed(detail="Add an email address first, then set a password.")
        passwords.change(
            user=user,
            current_password=data["current_password"],
            new_password=data["new_password"],
            keep=self.session(),
        )
        return _security_changed(Response(status=status.HTTP_204_NO_CONTENT), self.session())
