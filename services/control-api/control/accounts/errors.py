"""API errors raised by console sign-in. Messages never reveal why a secret check failed."""

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
    default_detail = "This link has expired or was already used. Ask for a new one."
    default_code = "invalid_link"


class ReauthenticationRequired(AuthError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "For your security, please confirm your password before making this change."
    default_code = "reauthentication_required"


class WeakPassword(AuthError):
    default_detail = "Please choose a stronger password."
    default_code = "weak_password"


class ActionNotAllowed(AuthError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This change isn't allowed."
    default_code = "not_allowed"
