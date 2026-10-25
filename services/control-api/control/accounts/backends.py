from typing import Any

from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest


class NoLoginBackend(BaseBackend):
    """Refuses every credential: Django's ``authenticate()``/``login()`` are never the way in.
    Console sign-in runs through control.accounts.services with mandatory two-step verification
    (D-045); this keeps the refusal explicit instead of an ImproperlyConfigured error."""

    def authenticate(self, request: HttpRequest | None, **credentials: Any) -> None:
        return None

    def get_user(self, user_id: Any) -> None:
        return None
