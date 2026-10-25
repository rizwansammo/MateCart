from contextvars import ContextVar

# Set by RequestIdMiddleware for the duration of each request; None outside requests.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()
