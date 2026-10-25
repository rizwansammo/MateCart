"""'Continue with Google' through OpenID Connect, authorization code flow (D-043).

The browser is sent to Google with ``state`` (CSRF), ``nonce`` (ID token replay) and a PKCE
challenge. All three are kept server-side in an OidcAuthorization bound to the starting browser by
a cookie. The callback exchanges the code server-side with the client secret and validates the ID
token's signature (Google's published keys), issuer, audience, expiry and nonce. Accounts are keyed
by Google's stable ``sub``; email addresses are never used to merge accounts.
"""

import base64
import hashlib
import hmac
import http.client
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from commerce.core.security import DecryptionError, decrypt, encrypt, hash_token, new_token

from .models import IdentityProvider, OidcAuthorization, User

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 (a URL, not a secret)
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
AUTHORIZATION_LIFETIME = timedelta(minutes=10)
JWKS_MAX_AGE = 3600
CLOCK_SKEW_SECONDS = 60
BINDING_COOKIE = "mc_oidc"
BINDING_COOKIE_PATH = "/api/account/google"
CALLBACK_PATH = "/api/account/google/callback/"


class GoogleSignInFailed(Exception):
    """The redirect, code exchange or ID token was not acceptable. Never shown verbatim."""


@dataclass(frozen=True)
class GoogleProfile:
    subject: str
    email: str | None
    email_verified: bool
    name: str


@dataclass(frozen=True)
class CompletedAuthorization:
    purpose: str
    user_id: Any
    next_path: str
    profile: GoogleProfile


def enabled() -> bool:
    return bool(
        settings.GOOGLE_OIDC_CLIENT_ID
        and settings.GOOGLE_OIDC_CLIENT_SECRET
        and settings.MATECART_PUBLIC_BASE_URL
    )


def redirect_uri() -> str:
    return f"{settings.MATECART_PUBLIC_BASE_URL}{CALLBACK_PATH}"


# --- HTTP (replaceable in tests; the only network calls in this module) -------------------------


def http_json(url: str, data: dict[str, str] | None = None) -> Any:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 (fixed https URLs)
        return json.loads(response.read())


def _provider_json(url: str, data: dict[str, str] | None = None) -> Any:
    """Normalize expected provider transport/decoding failures without hiding code defects."""
    try:
        return http_json(url, data)
    except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeError) as exc:
        raise GoogleSignInFailed("Google provider request failed.") from exc


_jwks: dict[str, Any] = {"set": None, "fetched": 0.0}


def _find_key(kid: str) -> Any:
    keyset: jwt.PyJWKSet | None = _jwks["set"]
    for key in keyset.keys if keyset else []:
        if key.key_id == kid:
            return key.key
    return None


def _refresh_keys() -> None:
    payload = _provider_json(JWKS_URI)
    if not isinstance(payload, dict):
        raise GoogleSignInFailed("Invalid signing key response.")
    try:
        keyset = jwt.PyJWKSet.from_dict(payload)
    except (jwt.PyJWTError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise GoogleSignInFailed("Invalid signing key response.") from exc
    _jwks["set"] = keyset
    _jwks["fetched"] = time.monotonic()


def _signing_key(kid: str) -> Any:
    """Google's public key ``kid``. The key set is cached for an hour and refetched once when an
    unknown kid appears (Google rotates keys). ID tokens arrive only from Google's token endpoint
    over TLS, so refetches cannot be triggered by callers."""
    if _jwks["set"] is None or time.monotonic() - _jwks["fetched"] > JWKS_MAX_AGE:
        _refresh_keys()
    key = _find_key(kid)
    if key is None:
        _refresh_keys()
        key = _find_key(kid)
    if key is None:
        raise GoogleSignInFailed("Unknown signing key.")
    return key


# --- Flow -----------------------------------------------------------------------------------------


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def begin(*, purpose: str, next_path: str, user: User | None = None) -> tuple[str, str]:
    """Returns (Google authorization URL, browser binding value for the BINDING_COOKIE)."""
    state, nonce, verifier, binding = new_token(), new_token(), new_token(), new_token()
    OidcAuthorization.objects.create(
        provider=IdentityProvider.GOOGLE,
        purpose=purpose,
        state_hash=hash_token(state),
        browser_binding_hash=hash_token(binding),
        nonce=hash_token(nonce),
        code_verifier_encrypted=encrypt(verifier),
        user=user,
        next_path=next_path,
        expires_at=timezone.now() + AUTHORIZATION_LIFETIME,
    )
    query = urllib.parse.urlencode(
        {
            "client_id": settings.GOOGLE_OIDC_CLIENT_ID,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"{AUTHORIZATION_ENDPOINT}?{query}", binding


def _claim(authorization_state: str, binding: str | None) -> OidcAuthorization:
    """Mark the authorization used (single-use) if the state and browser binding match."""
    with transaction.atomic():
        row = (
            OidcAuthorization.objects.select_for_update()
            .filter(state_hash=hash_token(authorization_state or ""), used_at__isnull=True)
            .first()
        )
        if row is None:
            raise GoogleSignInFailed("Unknown or used state.")
        now = timezone.now()
        if row.expires_at <= now:
            raise GoogleSignInFailed("Authorization expired.")
        if not binding or not hmac.compare_digest(row.browser_binding_hash, hash_token(binding)):
            raise GoogleSignInFailed("Browser binding mismatch.")
        row.used_at = now
        row.save(update_fields=["used_at"])
    return row


def validate_id_token(id_token: str, *, nonce_hash: str) -> GoogleProfile:
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256":
            raise GoogleSignInFailed("Unexpected algorithm.")
        claims = jwt.decode(
            id_token,
            _signing_key(str(header.get("kid", ""))),
            algorithms=["RS256"],
            audience=settings.GOOGLE_OIDC_CLIENT_ID,
            issuer=ISSUERS,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise GoogleSignInFailed("Invalid ID token.") from exc
    if claims.get("azp") not in (None, settings.GOOGLE_OIDC_CLIENT_ID):
        raise GoogleSignInFailed("Token issued to another client.")
    if not hmac.compare_digest(hash_token(str(claims.get("nonce", ""))), nonce_hash):
        raise GoogleSignInFailed("Nonce mismatch.")
    subject = str(claims["sub"])
    if not subject or len(subject) > 255:
        raise GoogleSignInFailed("Invalid subject.")
    email = claims.get("email")
    return GoogleProfile(
        subject=subject,
        email=str(email).strip().lower() if email else None,
        email_verified=claims.get("email_verified") is True,
        name=str(claims.get("name") or "")[:150],
    )


def complete(*, state: str, code: str, binding: str | None) -> CompletedAuthorization:
    row = _claim(state, binding)
    if not code:
        raise GoogleSignInFailed("Missing code.")
    try:
        verifier = decrypt(row.code_verifier_encrypted)
    except DecryptionError as exc:
        raise GoogleSignInFailed("Authorization data is invalid.") from exc
    tokens = _provider_json(
        TOKEN_ENDPOINT,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "client_id": settings.GOOGLE_OIDC_CLIENT_ID,
            "client_secret": settings.GOOGLE_OIDC_CLIENT_SECRET,
            "code_verifier": verifier,
        },
    )
    id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(id_token, str):
        raise GoogleSignInFailed("No ID token.")
    profile = validate_id_token(id_token, nonce_hash=row.nonce)
    return CompletedAuthorization(
        purpose=row.purpose, user_id=row.user_id, next_path=row.next_path, profile=profile
    )
