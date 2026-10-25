"""API errors raised by authentication flows. Messages are written for shoppers and merchants;
they never reveal whether another person's account exists or why a secret check failed."""

from rest_framework import status
from rest_framework.exceptions import APIException


class AuthError(APIException):
    status_code: int = status.HTTP_400_BAD_REQUEST


class InvalidCredentials(AuthError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "That email and password don't match. Check them and try again."
    default_code = "invalid_credentials"


class InvalidCode(AuthError):
    default_detail = "That code isn't right. Check it and try again."
    default_code = "invalid_code"


class CodeExpired(AuthError):
    default_detail = "This code has expired or was already used. Request a new one."
    default_code = "code_expired"


class ResendTooSoon(AuthError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Please wait a moment before requesting another code."
    default_code = "resend_cooldown"


class InvalidLink(AuthError):
    default_detail = "This link has expired or was already used. Request a new one."
    default_code = "invalid_link"


class ReauthenticationRequired(AuthError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "For your security, please confirm it's you before making this change."
    default_code = "reauthentication_required"


class EmailNotVerified(AuthError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Please verify your email address. We've sent you a new code."
    default_code = "email_unverified"


class MethodUnavailable(AuthError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "This sign-in method isn't available right now."
    default_code = "method_unavailable"


class AccountConflict(AuthError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This is already used by another account."
    default_code = "account_conflict"


class AccountExists(AuthError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "You already have an account with this email. Sign in instead."
    default_code = "account_exists"


class WeakPassword(AuthError):
    default_detail = "Please choose a stronger password."
    default_code = "weak_password"


class ActionNotAllowed(AuthError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This change isn't allowed."
    default_code = "not_allowed"
