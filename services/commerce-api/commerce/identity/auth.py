"""DRF authentication for the two realms (D-040).

Each realm reads only its own cookie, and a session only authenticates in the realm it was issued
for, so a customer session can never reach a merchant endpoint and vice versa. Staff sessions also
require an active store membership on every request. Every state-changing request is CSRF-checked,
whether or not anyone is signed in (login CSRF).
"""

from dataclasses import dataclass
from typing import Any

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from commerce.core.http import enforce_csrf

from . import sessions
from .models import AccountType, AuthSession, SessionStatus, User


@dataclass(frozen=True)
class StaffAuth:
    """``request.auth`` for merchant endpoints."""

    session: AuthSession
    membership: Any  # commerce.staff.models.StaffMembership (imported lazily: app dependency)

    @property
    def permissions(self) -> frozenset[str]:
        perms: frozenset[str] = self.membership.permissions
        return perms


class _RealmCookieAuthentication(BaseAuthentication):
    realm: str
    status: str = SessionStatus.ACTIVE

    def session(self, request: Request) -> AuthSession | None:
        token = request.COOKIES.get(sessions.cookie_spec(self.realm)["name"])
        return sessions.resolve(token, self.realm, status=self.status)

    def authenticate_header(self, request: Request) -> str:
        # A WWW-Authenticate value makes DRF answer 401 (not 403) when no session is present.
        return f'Session realm="{self.realm}"'


class CustomerSessionAuthentication(_RealmCookieAuthentication):
    realm = AccountType.CUSTOMER

    def authenticate(self, request: Request) -> tuple[User, AuthSession] | None:
        session = self.session(request)
        return (session.user, session) if session else None


class StaffSessionAuthentication(_RealmCookieAuthentication):
    realm = AccountType.STAFF

    def authenticate(self, request: Request) -> tuple[User, StaffAuth] | None:
        from commerce.staff.models import MembershipStatus, StaffMembership

        session = self.session(request)
        if session is None:
            return None
        membership = StaffMembership.objects.filter(
            user=session.user, status=MembershipStatus.ACTIVE
        ).first()
        if membership is None:
            return None
        return session.user, StaffAuth(session=session, membership=membership)


class StaffPendingMfaAuthentication(_RealmCookieAuthentication):
    """Only for the second sign-in step: a password-verified session awaiting MFA."""

    realm = AccountType.STAFF
    status = SessionStatus.PENDING_MFA

    def authenticate(self, request: Request) -> tuple[User, AuthSession] | None:
        session = self.session(request)
        return (session.user, session) if session else None


class HasStaffPermission(BasePermission):
    """``permission_classes = [HasStaffPermission.of("staff.manage")]``."""

    required: str = ""

    @classmethod
    def of(cls, permission: str) -> type["HasStaffPermission"]:
        return type(f"Has_{permission.replace('.', '_')}", (cls,), {"required": permission})

    def has_permission(self, request: Request, view: APIView) -> bool:
        auth = request.auth
        return isinstance(auth, StaffAuth) and self.required in auth.permissions


@method_decorator(never_cache, name="dispatch")
class CsrfProtectedAPIView(APIView):
    """Base for every cookie-authenticated or sign-in endpoint."""

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        if request.method not in SAFE_METHODS:
            enforce_csrf(request)
        super().initial(request, *args, **kwargs)
