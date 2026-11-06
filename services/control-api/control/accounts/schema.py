"""OpenAPI descriptions of the console session cookie (drf-spectacular extensions)."""

from typing import Any

from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension


def _cookie(description: str) -> dict[str, Any]:
    return {
        "type": "apiKey",
        "in": "cookie",
        "name": settings.MATECART_CONSOLE_COOKIE,
        "description": description,
    }


class ConsoleSessionScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "control.accounts.auth.ConsoleSessionAuthentication"
    name = "consoleSession"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return _cookie("Console session after both sign-in steps (HttpOnly, SameSite=Strict).")


class PendingMfaScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "control.accounts.auth.PendingMfaAuthentication"
    name = "consolePendingMfa"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return _cookie("A password-verified session waiting for the second step.")


class PendingEnrollmentScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "control.accounts.auth.PendingEnrollmentAuthentication"
    name = "consolePendingEnrollment"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return _cookie("A session that must set up two-step verification before anything else.")


class AgentSignatureScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "control.platform.agent_views.AgentChallenge"
    name = "deploymentAgentHmac"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-MateCart-Signature",
            "description": (
                "HMAC-SHA256 over method, path, X-MateCart-Timestamp, single-use "
                "X-MateCart-Nonce and the body digest, keyed by the Agent credential "
                "(X-MateCart-Agent-Key). Enrollment uses a one-time token instead."
            ),
        }
