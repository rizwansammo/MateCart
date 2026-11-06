"""Strict validation of every value that reaches a file name, a Compose argument or a template."""

import re
import uuid
from pathlib import Path

HANDLE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,31})?$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9-]{0,38}/matecart-[a-z-]{3,40}$")
DOMAIN_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PRESETS = frozenset({"fashion", "grocery", "electronics", "general", "books"})
FORBIDDEN_ENV_CHARACTERS = frozenset("\n\r\0\"'`$\\")


class ValidationError(ValueError):
    """A command, manifest or configuration value failed validation."""


def handle(value: object) -> str:
    text = str(value)
    if not HANDLE.fullmatch(text):
        raise ValidationError("Store handles are 3-50 lower-case letters, digits and hyphens.")
    return text


def identifier(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValidationError("Expected a UUID.") from exc


def version(value: object) -> str:
    text = str(value)
    if not VERSION.fullmatch(text):
        raise ValidationError("Release versions are MAJOR.MINOR.PATCH.")
    return text


def domain(value: object) -> str:
    text = str(value).strip().lower().rstrip(".")
    labels = text.split(".")
    if (
        len(text) > 253
        or len(labels) < 2
        or not all(DOMAIN_LABEL.fullmatch(label) for label in labels)
        or labels[-1].isdigit()
    ):
        raise ValidationError(f"Refusing invalid domain name {text[:80]!r}.")
    return text


def image_reference(repository: object, digest: object) -> str:
    repo, dig = str(repository), str(digest)
    if not REPOSITORY.fullmatch(repo) or not DIGEST.fullmatch(dig):
        raise ValidationError("Images must be ghcr.io/<owner>/matecart-* pinned by sha256 digest.")
    return f"{repo}@{dig}"


def within(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate``; refuse anything outside ``root`` (no traversal or symlink escape)."""
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValidationError("Refusing a path outside the managed directory.")
    return resolved


def env_value(value: object) -> str:
    """Values written to Compose env files: one line, no quotes, '$' or backslashes."""
    text = str(value)
    if any(ch in FORBIDDEN_ENV_CHARACTERS for ch in text):
        raise ValidationError("Environment values cannot contain quotes, newlines or '$'.")
    return text
