"""Normalisation of contact identifiers, so uniqueness and lookups compare like with like."""

import phonenumbers
from django.core.exceptions import ValidationError

# E.164: "+", a country code that does not start with 0, at most 15 digits in total.
E164_PATTERN = r"^\+[1-9][0-9]{6,14}$"


def normalize_email(value: str | None) -> str | None:
    """Trim and lowercase the whole address; blank becomes None.

    Mailbox names are treated case-insensitively, as every major provider does, so
    ``Rahim@Example.com`` and ``rahim@example.com`` are the same identity.
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def normalize_phone(value: str | None, default_region: str | None = None) -> str | None:
    """Return the E.164 form (``+8801712345678``); blank becomes None.

    ``default_region`` (ISO 3166-1 alpha-2, usually the store's country) lets customers type
    national numbers such as ``01712345678``. Without it, numbers must include ``+`` and a
    country code.
    """
    if value is None or not value.strip():
        return None
    try:
        parsed = phonenumbers.parse(value, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError(
            "Enter a valid phone number.", code="invalid_phone", params={"value": value}
        ) from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValidationError(
            "Enter a valid phone number.", code="invalid_phone", params={"value": value}
        )
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
