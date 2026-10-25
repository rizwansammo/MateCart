from typing import Any

from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest


class NoLoginBackend(BaseBackend):
    """Placeholder until Phase 03 adds tenant-aware login: refuses every credential and session.

    With no backend at all Django raises ImproperlyConfigured on ``authenticate()``; this makes
    "nobody can sign in yet" an explicit, tested behaviour instead.
    """

    def authenticate(self, request: HttpRequest | None, **credentials: Any) -> None:
        return None

    def get_user(self, user_id: Any) -> None:
        return None
