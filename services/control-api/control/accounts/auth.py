"""DRF authentication for console sessions. Each class accepts exactly one session state, so a
password-only or not-yet-enrolled session can never reach console data."""

from typing import Any

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.views import APIView

from control.core.http import enforce_csrf

from . import sessions
from .models import AdminSession, AdminUser, SessionStatus


class _ConsoleSessionAuthentication(BaseAuthentication):
    status: str

    def authenticate(self, request: Request) -> tuple[AdminUser, AdminSession] | None:
        token = request.COOKIES.get(settings.MATECART_CONSOLE_COOKIE)
        session = sessions.resolve(token, status=self.status)
        return (session.user, session) if session else None

    def authenticate_header(self, request: Request) -> str:
        return 'Session realm="console"'


class ConsoleSessionAuthentication(_ConsoleSessionAuthentication):
    status = SessionStatus.ACTIVE


class PendingMfaAuthentication(_ConsoleSessionAuthentication):
    status = SessionStatus.PENDING_MFA


class PendingEnrollmentAuthentication(_ConsoleSessionAuthentication):
    status = SessionStatus.PENDING_ENROLLMENT


@method_decorator(never_cache, name="dispatch")
class CsrfProtectedAPIView(APIView):
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        if request.method not in SAFE_METHODS:
            enforce_csrf(request)
        super().initial(request, *args, **kwargs)
