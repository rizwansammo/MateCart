"""Output schemas and validation for every AI feature (D-102).

Provider output is untrusted data. Each feature has a JSON schema written for strict structured
output (every property required, ``additionalProperties: false``) and a validator that parses
the text, checks types, removes control characters and enforces MateCart's field limits. The
validator is applied whether or not the provider enforced the schema, so a model can never put
an unexpected shape, an oversized string or markup-only content into a draft. Drafts are plain
text everywhere they are shown (React escapes them); nothing here is rendered as HTML.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

PRODUCT_FIELDS = (
    "title",
    "summary",
    "description",
    "highlights",
    "seo_title",
    "seo_description",
    "tags",
    "image_alt_text",
)
SUPPORT_TOPICS = (
    "order_status",
    "delivery",
    "return_exchange",
    "product_question",
    "payment",
    "complaint",
    "other",
)
MARKETING_KINDS = (
    "campaign_title",
    "campaign_description",
    "homepage_headline",
    "email_subject",
    "email_body",
    "sms",
    "social_caption",
    "product_promo",
)
LANGUAGES = ("en", "bn")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


class InvalidOutput(Exception):
    def __init__(self, code: str = "invalid_output") -> None:
        super().__init__(code)
        self.code = code


def _string() -> dict[str, Any]:
    return {"type": "string"}


def _strings() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def _object(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": dict(properties),
    }


SCHEMAS: dict[str, dict[str, Any]] = {
    "product_content": _object(
        {
            "title": _string(),
            "summary": _string(),
            "description": _string(),
            "highlights": _strings(),
            "seo_title": _string(),
            "seo_description": _string(),
            "tags": _strings(),
            "image_alt_text": _string(),
            "missing_information": _strings(),
        }
    ),
    "image_analysis": _object(
        {
            "description": _string(),
            "alt_text": _string(),
            "visible_objects": _strings(),
            "colors": _strings(),
            "category_suggestion": _string(),
            "attribute_suggestions": {
                "type": "array",
                "items": _object({"name": _string(), "value": _string()}),
            },
            "quality_issues": _strings(),
            "mismatch_notes": _strings(),
            "cannot_determine": _strings(),
        }
    ),
    "support_reply": _object(
        {
            "reply": _string(),
            "language": {"type": "string", "enum": list(LANGUAGES)},
            "topic": {"type": "string", "enum": list(SUPPORT_TOPICS)},
            "next_action": _string(),
            "needs_verification": _strings(),
        }
    ),
    "support_summary": _object(
        {
            "summary": _string(),
            "customer_claims": _strings(),
            "interpretation": _strings(),
            "topic": {"type": "string", "enum": list(SUPPORT_TOPICS)},
            "next_action": _string(),
        }
    ),
    "marketing_copy": _object(
        {
            "variants": {
                "type": "array",
                "items": _object({"headline": _string(), "body": _string()}),
            },
            "notes": _strings(),
        }
    ),
    "business_insights": _object(
        {
            "headline": _string(),
            "observations": _strings(),
            "trends": _strings(),
            "suggestions": _strings(),
            "caveats": _strings(),
        }
    ),
}


def clean_text(value: Any, limit: int, *, multiline: bool = True) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidOutput("output_type")
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        lines = [" ".join(line.split()) for line in text.split("\n")]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    else:
        text = " ".join(text.split())
    return truncate(text, limit)


def truncate(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut at a word boundary when one is near."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip(" ,;:-")


def clean_list(value: Any, *, items: int, limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidOutput("output_type")
    result: list[str] = []
    for entry in value[: items * 2]:
        text = clean_text(entry, limit, multiline=False)
        if text and text not in result:
            result.append(text)
        if len(result) >= items:
            break
    return result


def parse(text: str) -> dict[str, Any]:
    """The JSON object in ``text`` (a code fence around it is tolerated)."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") :] if "{" in raw else raw
    try:
        value = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise InvalidOutput("output_not_json") from None
        try:
            value = json.loads(raw[start : end + 1])
        except ValueError:
            raise InvalidOutput("output_not_json") from None
    if not isinstance(value, dict):
        raise InvalidOutput("output_not_object")
    return value


def _tag(value: str) -> str:
    return " ".join(value.lower().replace("#", "").split())[:40]


def product_content(raw: Mapping[str, Any], *, fields: list[str]) -> dict[str, Any]:
    wanted = set(fields)
    out: dict[str, Any] = {
        "title": clean_text(raw.get("title"), 200, multiline=False),
        "summary": clean_text(raw.get("summary"), 500),
        "description": clean_text(raw.get("description"), 5000),
        "highlights": clean_list(raw.get("highlights"), items=6, limit=160),
        "seo_title": clean_text(raw.get("seo_title"), 70, multiline=False),
        "seo_description": clean_text(raw.get("seo_description"), 320, multiline=False),
        "tags": list(
            dict.fromkeys(
                t for t in (_tag(t) for t in clean_list(raw.get("tags"), items=10, limit=40)) if t
            )
        ),
        "image_alt_text": clean_text(raw.get("image_alt_text"), 300, multiline=False),
        "missing_information": clean_list(raw.get("missing_information"), items=8, limit=200),
    }
    for key in PRODUCT_FIELDS:
        if key not in wanted:
            out[key] = [] if isinstance(out[key], list) else ""
    if not any(out[key] for key in PRODUCT_FIELDS):
        raise InvalidOutput("output_empty")
    return out


def image_analysis(raw: Mapping[str, Any]) -> dict[str, Any]:
    suggestions = raw.get("attribute_suggestions")
    attributes: list[dict[str, str]] = []
    if isinstance(suggestions, list):
        for entry in suggestions[:12]:
            if not isinstance(entry, Mapping):
                continue
            name = clean_text(entry.get("name"), 60, multiline=False)
            value = clean_text(entry.get("value"), 120, multiline=False)
            if name and value:
                attributes.append({"name": name, "value": value})
            if len(attributes) >= 8:
                break
    out = {
        "description": clean_text(raw.get("description"), 1000),
        "alt_text": clean_text(raw.get("alt_text"), 300, multiline=False),
        "visible_objects": clean_list(raw.get("visible_objects"), items=10, limit=80),
        "colors": clean_list(raw.get("colors"), items=8, limit=40),
        "category_suggestion": clean_text(raw.get("category_suggestion"), 120, multiline=False),
        "attribute_suggestions": attributes,
        "quality_issues": clean_list(raw.get("quality_issues"), items=6, limit=200),
        "mismatch_notes": clean_list(raw.get("mismatch_notes"), items=6, limit=200),
        "cannot_determine": clean_list(raw.get("cannot_determine"), items=10, limit=160),
    }
    if not out["description"] and not out["alt_text"]:
        raise InvalidOutput("output_empty")
    return out


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def support_reply(raw: Mapping[str, Any]) -> dict[str, Any]:
    reply = clean_text(raw.get("reply"), 1500)
    if not reply:
        raise InvalidOutput("output_empty")
    return {
        "reply": reply,
        "language": _choice(raw.get("language"), LANGUAGES, "en"),
        "topic": _choice(raw.get("topic"), SUPPORT_TOPICS, "other"),
        "next_action": clean_text(raw.get("next_action"), 300, multiline=False),
        "needs_verification": clean_list(raw.get("needs_verification"), items=6, limit=200),
    }


def support_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    summary = clean_text(raw.get("summary"), 1200)
    if not summary:
        raise InvalidOutput("output_empty")
    return {
        "summary": summary,
        "customer_claims": clean_list(raw.get("customer_claims"), items=8, limit=240),
        "interpretation": clean_list(raw.get("interpretation"), items=6, limit=240),
        "topic": _choice(raw.get("topic"), SUPPORT_TOPICS, "other"),
        "next_action": clean_text(raw.get("next_action"), 300, multiline=False),
    }


MARKETING_LIMITS: dict[str, tuple[int, int]] = {
    # kind: (headline limit, body limit)
    "campaign_title": (120, 0),
    "campaign_description": (120, 500),
    "homepage_headline": (120, 200),
    "email_subject": (120, 0),
    "email_body": (120, 3000),
    "sms": (0, 300),
    "social_caption": (120, 1500),
    "product_promo": (120, 800),
}


def marketing_copy(raw: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    head_limit, body_limit = MARKETING_LIMITS[kind]
    variants: list[dict[str, str]] = []
    entries = raw.get("variants")
    if not isinstance(entries, list):
        raise InvalidOutput("output_type")
    for entry in entries[:6]:
        if not isinstance(entry, Mapping):
            continue
        headline = (
            clean_text(entry.get("headline"), head_limit, multiline=False) if head_limit else ""
        )
        body = clean_text(entry.get("body"), body_limit) if body_limit else ""
        if headline or body:
            variants.append({"headline": headline, "body": body})
        if len(variants) >= 3:
            break
    if not variants:
        raise InvalidOutput("output_empty")
    return {"variants": variants, "notes": clean_list(raw.get("notes"), items=5, limit=240)}


def business_insights(raw: Mapping[str, Any]) -> dict[str, Any]:
    headline = clean_text(raw.get("headline"), 240, multiline=False)
    observations = clean_list(raw.get("observations"), items=8, limit=400)
    if not headline and not observations:
        raise InvalidOutput("output_empty")
    return {
        "headline": headline,
        "observations": observations,
        "trends": clean_list(raw.get("trends"), items=6, limit=400),
        "suggestions": clean_list(raw.get("suggestions"), items=6, limit=400),
        "caveats": clean_list(raw.get("caveats"), items=6, limit=400),
    }
