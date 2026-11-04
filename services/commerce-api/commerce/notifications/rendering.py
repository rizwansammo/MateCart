"""Safe template rendering and the branded email layout (D-088).

Templates are plain text with ``{variable}`` placeholders from the kind's allowlist. Nothing is
evaluated: substitution is a single regular-expression pass, every value is HTML-escaped for the
HTML part, and the layout (logo, colours, button, footer) is built here, never by merchants.
Links are always the store's own public origin plus a server-chosen path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Any

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .registry import Kind

PLACEHOLDER = re.compile(r"\{([a-z_]{1,40})\}")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_SUBJECT = 200


@dataclass(frozen=True)
class Rendered:
    subject: str
    heading: str
    text: str
    html: str
    sms: str
    title: str
    summary: str


@dataclass(frozen=True)
class Branding:
    store_name: str
    primary_color: str
    logo_url: str
    contact_email: str
    contact_phone: str
    footer_text: str


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def validate_text(kind: Kind, field: str, text: str) -> None:
    """Merchant text may use only the kind's variables and no markup."""
    unknown = placeholders(text) - set(kind.variables)
    if unknown:
        raise ValidationError(
            {field: [f"Unknown variable: {', '.join(sorted('{' + n + '}' for n in unknown))}."]}
        )
    if "<" in text or ">" in text:
        raise ValidationError({field: ["Use plain text; the layout is added automatically."]})
    if text.count("{") != len(PLACEHOLDER.findall(text)) or text.count("}") != text.count("{"):
        raise ValidationError({field: ["Use variables like {order_number}."]})


def substitute(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), ""), text)


def public_url(path: str) -> str:
    """An absolute link to the store's own origin. Only server-chosen relative paths."""
    if not path.startswith("/") or path.startswith("//"):
        return ""
    base = str(settings.MATECART_PUBLIC_BASE_URL).rstrip("/")
    return f"{base}{path}" if base else ""


def single_line(value: str, limit: int) -> str:
    """Header-safe: no line breaks or control characters (no header injection)."""
    return " ".join("".join(ch for ch in value if ch.isprintable()).split())[:limit]


def render(
    *,
    kind: Kind,
    locale: str,
    values: dict[str, Any],
    override: Any | None,
    branding: Branding,
    link_path: str = "",
    unsubscribe_path: str = "",
) -> Rendered:
    text_values = {key: str(values.get(key, "")) for key in kind.variables}
    text_values["store_name"] = branding.store_name
    text_values["link"] = public_url(link_path) if link_path else ""

    def part(name: str, fallback_attr: str) -> str:
        custom = getattr(override, name, "") if override is not None else ""
        return str(custom or kind.text(fallback_attr, locale))

    subject = single_line(substitute(part("subject", "subject"), text_values), MAX_SUBJECT)
    heading = single_line(substitute(part("heading", "heading"), text_values), 120)
    body = substitute(part("body", "body"), text_values).strip()
    sms_template = (override.sms if override is not None and override.sms else "") or kind.text(
        "sms", locale
    )
    sms = single_line(substitute(sms_template, text_values), 480) if sms_template else ""
    title = single_line(
        substitute(
            (override.in_app_title if override is not None and override.in_app_title else "")
            or kind.text("heading", locale),
            text_values,
        ),
        120,
    )
    summary_source = (
        override.in_app_body if override is not None and override.in_app_body else ""
    ) or body
    summary = single_line(substitute(summary_source, text_values), 300)
    link = text_values["link"]
    unsubscribe = public_url(unsubscribe_path) if unsubscribe_path else ""
    text_lines = [heading, "", body]
    if link:
        text_lines += ["", link]
    text_lines += ["", "—", branding.store_name]
    if branding.contact_email or branding.contact_phone:
        text_lines.append(
            " · ".join(filter(None, [branding.contact_email, branding.contact_phone]))
        )
    if unsubscribe:
        text_lines += ["", f"Unsubscribe: {unsubscribe}"]
    return Rendered(
        subject=subject,
        heading=heading,
        text="\n".join(text_lines).strip() + "\n",
        html=layout(
            heading=heading,
            body=body,
            link=link,
            link_label="View details" if locale != "bn" else "বিস্তারিত দেখুন",
            branding=branding,
            unsubscribe=unsubscribe,
            locale=locale,
        ),
        sms=sms,
        title=title,
        summary=summary,
    )


def layout(
    *,
    heading: str,
    body: str,
    link: str,
    link_label: str,
    branding: Branding,
    unsubscribe: str,
    locale: str,
) -> str:
    color = branding.primary_color if HEX.match(branding.primary_color) else "#111827"
    paragraphs = "".join(
        f'<p style="margin:0 0 14px;line-height:1.55">{escape(block).replace(chr(10), "<br>")}</p>'
        for block in body.split("\n\n")
        if block.strip()
    )
    logo = (
        f'<img src="{escape(branding.logo_url, quote=True)}" alt="{escape(branding.store_name)}"'
        ' height="36" style="display:block;height:36px;max-width:200px">'
        if branding.logo_url.startswith("https://") or branding.logo_url.startswith("http://")
        else f'<strong style="font-size:18px">{escape(branding.store_name)}</strong>'
    )
    button = (
        f'<p style="margin:22px 0"><a href="{escape(link, quote=True)}" '
        f'style="background:{color};color:#ffffff;text-decoration:none;padding:11px 18px;'
        f'border-radius:4px;display:inline-block;font-weight:600">{escape(link_label)}</a></p>'
        if link
        else ""
    )
    contact = " · ".join(
        escape(item) for item in (branding.contact_email, branding.contact_phone) if item
    )
    footer_text = escape(branding.footer_text) if branding.footer_text else ""
    unsubscribe_html = (
        f'<p style="margin:8px 0 0"><a href="{escape(unsubscribe, quote=True)}" '
        'style="color:#6b7280">Unsubscribe</a></p>'
        if unsubscribe
        else ""
    )
    return (
        f'<!doctype html><html lang="{escape(locale)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(heading)}</title></head>"
        '<body style="margin:0;background:#f4f4f5;font-family:Arial,Helvetica,sans-serif;'
        'color:#111827">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f4f5;padding:24px 12px"><tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#ffffff;border:1px solid #e5e7eb;border-radius:6px">'
        f'<tr><td style="padding:20px 24px;border-bottom:3px solid {color}">{logo}</td></tr>'
        f'<tr><td style="padding:24px"><h1 style="margin:0 0 16px;font-size:20px">'
        f"{escape(heading)}</h1>{paragraphs}{button}</td></tr>"
        '<tr><td style="padding:16px 24px;border-top:1px solid #e5e7eb;font-size:12px;'
        f'color:#6b7280">{escape(branding.store_name)}'
        f"{'<br>' + contact if contact else ''}{'<br>' + footer_text if footer_text else ''}"
        f"{unsubscribe_html}</td></tr></table></td></tr></table></body></html>"
    )
