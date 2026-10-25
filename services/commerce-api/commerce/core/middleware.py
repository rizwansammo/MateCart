import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .request_context import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")


class RequestIdMiddleware:
    """Gives every request an id, exposes it to logging and returns it as X-Request-ID.

    A well-formed incoming X-Request-ID (for example from the reverse proxy) is reused so logs
    can be correlated across hops; anything else is replaced so clients cannot inject log text.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[REQUEST_ID_HEADER] = request_id
        return response
