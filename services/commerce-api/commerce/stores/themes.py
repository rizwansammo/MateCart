"""Storefront theme catalogue (D-069): which themes exist and which business preset each serves.

A business preset decides what a store sells and which modules it has; a theme is only a visual
design for one preset. The visual definitions live in ``@matecart/shared-ui`` (``THEMES``); this
module keeps the identities so the API can validate assignments. The same list is in
``packages/shared-contracts/src/themes.json``; ``scripts/themes.test.mjs`` compares it with the
OpenAPI enum generated from ``THEME_IDS``.

Identifiers are permanent (never display names, never reused). Every preset has exactly one
default theme. A store's ``StoreSettings.active_theme`` is blank (the preset default) or a theme
of the same preset, assigned only by NetaMate (``services.assign_theme``).
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

THEME_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


@dataclass(frozen=True)
class ThemeSpec:
    id: str
    preset: str
    name: str
    is_default: bool


THEMES: tuple[ThemeSpec, ...] = (
    ThemeSpec("fashion-editorial", "fashion", "Fashion Editorial", is_default=True),
    ThemeSpec("grocery-quickshop", "grocery", "Grocery Quick Shop", is_default=True),
    ThemeSpec("electronics-spec-first", "electronics", "Electronics Spec-first", is_default=True),
    ThemeSpec("general-marketplace", "general", "General Marketplace", is_default=True),
    ThemeSpec("books-literary", "books", "Books Literary", is_default=True),
)
THEME_IDS: tuple[str, ...] = tuple(theme.id for theme in THEMES)
_BY_ID = {theme.id: theme for theme in THEMES}


def is_valid_id(value: str) -> bool:
    return bool(re.fullmatch(THEME_ID_PATTERN, value)) and len(value) <= 64


def theme(theme_id: str) -> ThemeSpec | None:
    return _BY_ID.get(theme_id)


def default_theme(preset: str) -> str:
    """The preset's default theme. Every preset has exactly one (tested)."""
    for spec in THEMES:
        if spec.preset == preset and spec.is_default:
            return spec.id
    raise LookupError(f"No default theme for the {preset!r} preset.")


def is_compatible(theme_id: str, preset: str) -> bool:
    spec = _BY_ID.get(theme_id)
    return spec is not None and spec.preset == preset


def effective_theme(assigned: str, preset: str) -> str:
    """The theme a store renders with: its assignment when that is a theme of its preset,
    otherwise its preset's default. An unusable assignment (a theme removed from a release, or a
    row changed outside the supported path) is logged for operators and never shown to
    customers; it never selects another preset's theme."""
    if not assigned:
        return default_theme(preset)
    if is_compatible(assigned, preset):
        return assigned
    logger.warning(
        "Store theme %r is not a theme of the %r preset; using the preset default.",
        assigned,
        preset,
    )
    return default_theme(preset)
