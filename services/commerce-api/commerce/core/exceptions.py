import logging
from typing import Any

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

from commerce.tenancy.exceptions import TenancyError

logger = logging.getLogger(__name__)


class TenantUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "This store is not available right now."
    default_code = "tenant_unavailable"


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Wraps DRF errors in the MateCart envelope: {"error": {"code", "message", "details"?}}.

    Tenancy failures (no tenant, unprovisioned tenant, crossed boundary) become a generic 503
    ``tenant_unavailable``; the detail is logged, never returned. Other exceptions DRF does not
    handle return None, so Django logs them and answers via handler500. The request id travels
    in the X-Request-ID header rather than the body.
    """
    if isinstance(exc, TenancyError):
        logger.error("Tenant unavailable: %s", exc, exc_info=exc)
        exc = TenantUnavailable()

    response = exception_handler(exc, context)
    if response is None:
        return None

    error: dict[str, Any]
    if isinstance(exc, ValidationError):
        error = {"code": "validation_error", "message": "Invalid input.", "details": response.data}
    else:
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
        error = {
            "code": getattr(detail, "code", None) or "error",
            "message": str(detail) if detail is not None else "Request failed.",
        }
    response.data = {"error": error}
    return response
