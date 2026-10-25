"""OpenAPI descriptions of the realm session cookies (drf-spectacular extensions)."""

from typing import Any

from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension


def _cookie(realm: str, description: str) -> dict[str, Any]:
    return {
        "type": "apiKey",
        "in": "cookie",
        "name": settings.MATECART_SESSION_COOKIES[realm]["name"],
        "description": description,
    }


class CustomerSessionScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "commerce.identity.auth.CustomerSessionAuthentication"
    name = "customerSession"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return _cookie("customer", "Customer session (HttpOnly). Unsafe methods need X-CSRFToken.")


class StaffSessionScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "commerce.identity.auth.StaffSessionAuthentication"
    name = "staffSession"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return _cookie("staff", "Merchant staff session (HttpOnly, path /api/manage).")


class StaffPendingMfaScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "commerce.identity.auth.StaffPendingMfaAuthentication"
    name = "staffPendingMfa"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return _cookie("staff", "A password-verified staff session waiting for the second step.")
