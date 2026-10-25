"""RFC 6238 time-based one-time passwords for authenticator apps (Google Authenticator, etc.)."""

import base64
import time

import pyotp
import segno

STEP_SECONDS = 30
DIGITS = 6
# Accept the previous and next 30-second step to absorb clock drift on phones.
DRIFT_STEPS = 1


def new_secret() -> str:
    return pyotp.random_base32(length=32)  # 160 bits


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    return pyotp.TOTP(secret, digits=DIGITS, interval=STEP_SECONDS).provisioning_uri(
        name=account, issuer_name=issuer
    )


def qr_svg_data_uri(uri: str) -> str:
    """The provisioning URI as an inline SVG QR code (no third-party rendering service)."""
    svg = segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#0d0f0e", light="#ffffff")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def verify(
    secret: str, code: str, *, last_used_step: int | None, now: float | None = None
) -> int | None:
    """Return the matched time step, or None. A step at or before ``last_used_step`` is refused,
    so each code works once (replay protection) and older codes never work again."""
    if len(code) != DIGITS or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret, digits=DIGITS, interval=STEP_SECONDS)
    current = int((now if now is not None else time.time()) // STEP_SECONDS)
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if pyotp.utils.strings_equal(totp.generate_otp(step), code):
            return step
    return None


def format_secret(secret: str) -> str:
    """Groups of four for manual entry: ``ABCD EFGH ...``."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
