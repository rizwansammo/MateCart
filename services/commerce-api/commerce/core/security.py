"""Cryptographic primitives shared by authentication features (D-039).

- Opaque tokens (sessions, links): 256-bit random, stored only as SHA-256 hashes.
- Short codes (OTP, recovery codes): stored as HMAC-SHA-256 with a server key, so a database
  leak alone does not allow offline guessing of 6-digit codes.
- Secrets that must be read back (TOTP seeds): Fernet-encrypted with a key derived from
  ``MATECART_ENCRYPTION_KEY`` (HKDF), separate from Django's SECRET_KEY.
"""

import base64
import hashlib
import hmac
import secrets
from functools import cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

RECOVERY_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L


class DecryptionError(Exception):
    """A stored secret could not be decrypted (wrong or rotated MATECART_ENCRYPTION_KEY)."""


@cache
def _derive(master: str, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(master.encode())


def _key(info: bytes) -> bytes:
    return _derive(settings.MATECART_ENCRYPTION_KEY, info)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_numeric_code(digits: int = 6) -> str:
    return f"{secrets.randbelow(10**digits):0{digits}d}"


def keyed_hash(purpose: str, value: str) -> str:
    return hmac.new(
        _key(b"matecart-hmac:" + purpose.encode()), value.encode(), "sha256"
    ).hexdigest()


def matches(expected_hash: str, purpose: str, value: str) -> bool:
    return hmac.compare_digest(expected_hash, keyed_hash(purpose, value))


def encrypt(value: str) -> str:
    fernet = Fernet(base64.urlsafe_b64encode(_key(b"matecart-fernet")))
    return fernet.encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    fernet = Fernet(base64.urlsafe_b64encode(_key(b"matecart-fernet")))
    try:
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("Stored secret could not be decrypted.") from exc


def new_recovery_code() -> str:
    chars = "".join(secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(10))
    return f"{chars[:5]}-{chars[5:]}"


def normalize_recovery_code(value: str) -> str:
    cleaned = "".join(ch for ch in value.upper() if ch.isalnum())
    return f"{cleaned[:5]}-{cleaned[5:]}" if len(cleaned) == 10 else cleaned


def normalize_code(value: str) -> str:
    """Digits only: users paste codes with spaces or dashes."""
    return "".join(ch for ch in value if ch.isdigit())
