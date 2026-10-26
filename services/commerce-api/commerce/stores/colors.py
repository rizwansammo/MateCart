"""Brand colour validation (D-061). The same WCAG 2.x formulas run in the browser
(``@matecart/shared-ui`` ``presets.ts``), so the server and the storefront agree."""

import re

HEX_COLOR = re.compile(r"^#[0-9A-F]{6}$")
WHITE = "#FFFFFF"
INK = "#0D0F0E"
# Text on a brand-coloured button or badge must reach WCAG AA for normal text.
MIN_TEXT_CONTRAST = 4.5


def normalize_hex(value: str) -> str:
    return value.strip().upper()


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(first: str, second: str) -> float:
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def on_color(background: str) -> str:
    """White or ink, whichever reads better on ``background``."""
    return WHITE if contrast(background, WHITE) >= contrast(background, INK) else INK


def readable(background: str) -> bool:
    return contrast(background, on_color(background)) >= MIN_TEXT_CONTRAST
