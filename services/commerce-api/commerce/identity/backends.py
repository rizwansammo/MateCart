from typing import Any

from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest


class NoLoginBackend(BaseBackend):
    """Refuses every credential: Django's ``authenticate()``/``login()`` are never the way in.

    Sign-in runs through realm-aware services (commerce.identity.passwords, phone codes, Google)
    that issue realm sessions (D-040). With no backend at all Django raises ImproperlyConfigured
    on ``authenticate()``; this keeps the refusal explicit and tested.
    """

    def authenticate(self, request: HttpRequest | None, **credentials: Any) -> None:
        return None

    def get_user(self, user_id: Any) -> None:
        return None
