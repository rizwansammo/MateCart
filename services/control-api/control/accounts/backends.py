from typing import Any

from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest


class NoLoginBackend(BaseBackend):
    """Placeholder until Phase 03 adds console sign-in (with MFA): refuses every credential and
    session, so "nobody can sign in yet" is explicit instead of an ImproperlyConfigured error."""

    def authenticate(self, request: HttpRequest | None, **credentials: Any) -> None:
        return None

    def get_user(self, user_id: Any) -> None:
        return None
