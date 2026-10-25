"""Request helpers for authentication endpoints: client IP, safe redirects, CSRF enforcement."""

from django.conf import settings
from django.http import HttpRequest
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.http import url_has_allowed_host_and_scheme
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request


def client_ip(request: HttpRequest | Request) -> str:
    """REMOTE_ADDR, or the address the trusted reverse proxies saw (MATECART_TRUSTED_PROXY_COUNT).
    Client-supplied X-Forwarded-For entries beyond the trusted hops are ignored."""
    meta = request.META
    trusted: int = settings.MATECART_TRUSTED_PROXY_COUNT
    if trusted > 0:
        forwarded: str = meta.get("HTTP_X_FORWARDED_FOR", "")
        hops = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(hops) >= trusted:
            return hops[-trusted]
    return str(meta.get("REMOTE_ADDR", "unknown"))


def safe_next_path(value: str | None, *, default: str, prefix: str = "/") -> str:
    """Only same-site relative paths under ``prefix``: no schemes, hosts, ``//`` or backslashes."""
    if (
        not value
        or not value.startswith(prefix)
        or value.startswith("//")
        or "\\" in value
        or not url_has_allowed_host_and_scheme(value, allowed_hosts=None, require_https=False)
    ):
        return default
    return value


class _CsrfCheck(CsrfViewMiddleware):
    def _reject(self, request: HttpRequest, reason: str) -> str:
        return reason


def enforce_csrf(request: Request) -> None:
    """Django's CSRF check for cookie-authenticated APIs (DRF views are csrf_exempt by default).
    Applied to every state-changing request, signed in or not, so login CSRF is covered too."""
    django_request = request._request
    check = _CsrfCheck(lambda _: None)  # type: ignore[arg-type]
    check.process_request(django_request)
    reason = check.process_view(django_request, None, (), {})  # type: ignore[arg-type]
    if reason:
        raise PermissionDenied(
            "Your session expired. Refresh the page and try again.", code="csrf_failed"
        )
