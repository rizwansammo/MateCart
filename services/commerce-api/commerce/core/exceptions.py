from typing import Any

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Wraps DRF errors in the MateCart envelope: {"error": {"code", "message", "details"?}}.

    Exceptions DRF does not handle return None, so Django logs them and answers via handler500.
    The request id travels in the X-Request-ID header rather than the body.
    """
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
