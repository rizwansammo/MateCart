"""International identifiers used across the commerce domain.

- Countries: ISO 3166-1 alpha-2 (``BD``, ``US``), from Unicode CLDR via Babel.
- Currencies: ISO 4217 alphabetic codes (``BDT``, ``USD``), from Unicode CLDR via Babel.
- Time zones: IANA tz database names (``Asia/Dhaka``).
- Languages: the BCP 47 tags MateCart ships translations for.

No market is special-cased here: Bangladesh is the first market, not a schema assumption.
"""

import zoneinfo
from functools import cache

from babel import Locale
from babel.numbers import list_currencies
from django.core.exceptions import ValidationError

# Languages the storefront and dashboard are translated into. Adding one is a product decision
# (translations, fonts, QA); stores may only offer languages from this list.
PLATFORM_LANGUAGES: dict[str, str] = {
    "en": "English",
    "bn": "Bangla",
}
LANGUAGE_CHOICES = list(PLATFORM_LANGUAGES.items())

# CLDR territory codes that are groupings or placeholders rather than countries.
_NON_COUNTRY_TERRITORIES = frozenset({"EU", "EZ", "QO", "UN", "ZZ"})


@cache
def country_codes() -> frozenset[str]:
    return frozenset(
        code
        for code in Locale("en").territories
        if isinstance(code, str)
        and len(code) == 2
        and code.isalpha()
        and code not in _NON_COUNTRY_TERRITORIES
    )


@cache
def currency_codes() -> frozenset[str]:
    return frozenset(list_currencies())


@cache
def timezone_names() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def validate_country_code(value: str) -> None:
    if value not in country_codes():
        raise ValidationError(
            "%(value)s is not an ISO 3166-1 alpha-2 country code.",
            code="invalid_country",
            params={"value": value},
        )


def validate_currency_code(value: str) -> None:
    if value not in currency_codes():
        raise ValidationError(
            "%(value)s is not an ISO 4217 currency code.",
            code="invalid_currency",
            params={"value": value},
        )


def validate_timezone(value: str) -> None:
    if value not in timezone_names():
        raise ValidationError(
            "%(value)s is not an IANA time zone.", code="invalid_timezone", params={"value": value}
        )
