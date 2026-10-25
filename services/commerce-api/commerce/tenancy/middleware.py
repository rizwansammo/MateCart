from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .context import tenant_context
from .resolution import resolve_request_tenant_id


class TenantMiddleware:
    """Runs each request inside the deployment's tenant context.

    The tenant comes from configuration only (see resolution.py), never from the request. With no
    tenant configured, no context is set: tenant-owned data stays unreachable and the API answers
    503 ``tenant_unavailable`` (liveness keeps working because it touches no tenant data).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        tenant_id = resolve_request_tenant_id(request)
        if tenant_id is None:
            return self.get_response(request)
        with tenant_context(tenant_id):
            return self.get_response(request)
