"""HMAC request signing shared with control.platform.deployment (same canonical form)."""

import hashlib
import hmac
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def signature_base(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    return "\n".join([method.upper(), path, timestamp, nonce, sha256_hex(body)]).encode()


def sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def response_valid(secret: str, nonce: str, status: int, body: bytes, signature: str) -> bool:
    expected = sign(secret, "\n".join(["response", nonce, str(status), sha256_hex(body)]).encode())
    return hmac.compare_digest(expected, signature)
