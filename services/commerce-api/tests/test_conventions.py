"""Shared conventions: UUIDv7 identifiers, international standards and contact normalisation."""

import time
import uuid

import pytest
from django.core.exceptions import ValidationError

from commerce.core import ids
from commerce.core.contact import normalize_email, normalize_phone
from commerce.core.standards import validate_country_code, validate_currency_code, validate_timezone


def test_uuid7_sets_version_and_variant() -> None:
    value = ids.uuid7()

    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_embeds_the_creation_millisecond() -> None:
    before = time.time_ns() // 1_000_000
    value = ids.uuid7()
    after = time.time_ns() // 1_000_000

    assert before <= value.int >> 80 <= after


def test_uuid7_is_ordered_by_time_and_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids, "_timestamp_ms", lambda: 1_700_000_000_000)
    earlier = [ids.uuid7() for _ in range(1000)]
    monkeypatch.setattr(ids, "_timestamp_ms", lambda: 1_700_000_000_001)
    later = ids.uuid7()

    assert len(set(earlier)) == 1000
    assert all(value < later for value in earlier)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Rahim@Example.COM ", "rahim@example.com"), ("", None), ("   ", None), (None, None)],
)
def test_emails_are_trimmed_and_lowercased(raw: str | None, expected: str | None) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "region", "expected"),
    [
        ("01712345678", "BD", "+8801712345678"),
        ("+880 1712-345678", None, "+8801712345678"),
        ("+1 650-253-0000", "BD", "+16502530000"),
        ("", "BD", None),
        (None, None, None),
    ],
)
def test_phone_numbers_become_e164(
    raw: str | None, region: str | None, expected: str | None
) -> None:
    assert normalize_phone(raw, region) == expected


@pytest.mark.parametrize(("raw", "region"), [("12345", "BD"), ("01712345678", None), ("abc", "BD")])
def test_invalid_phone_numbers_are_rejected(raw: str, region: str | None) -> None:
    with pytest.raises(ValidationError):
        normalize_phone(raw, region)


@pytest.mark.parametrize("code", ["BD", "US", "GB", "IN", "AE"])
def test_iso_country_codes_are_accepted(code: str) -> None:
    validate_country_code(code)


@pytest.mark.parametrize("code", ["XX", "bd", "EU", "ZZ", "BGD"])
def test_non_country_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError):
        validate_country_code(code)


def test_currency_and_timezone_validation() -> None:
    for currency in ("BDT", "USD", "EUR", "INR"):
        validate_currency_code(currency)
    validate_timezone("Asia/Dhaka")
    validate_timezone("America/New_York")
    with pytest.raises(ValidationError):
        validate_currency_code("ABC")
    with pytest.raises(ValidationError):
        validate_timezone("Mars/Olympus_Mons")
