"""Provider-independent AI interface (D-101).

Business features (product content, support, marketing, insights) build a
``GenerationRequest`` and receive a ``GenerationResult``; they never know which provider served
it. An adapter implements ``AIProvider`` and declares, per model, the capabilities MateCart has
verified from the provider's documentation. A feature that needs a capability the selected model
lacks is refused before any call; there is never a silent fallback to another model.

Errors are reduced to ``ProviderError(kind, code)``: ``kind`` drives behaviour (retry, status
change), ``code`` is a short machine-readable label that is safe to store and show. Provider
messages, prompts and credentials never travel in an error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# Capabilities a model may have.
TEXT = "text"
JSON_OBJECT = "json_object"  # syntactically valid JSON, schema followed by instruction only
JSON_SCHEMA = "json_schema"  # provider-enforced schema (strict structured output)
VISION = "vision"  # image inputs
CAPABILITIES = (TEXT, JSON_OBJECT, JSON_SCHEMA, VISION)


class ProviderError(Exception):
    """kinds: invalid_credentials, permission, rate_limited, unavailable, unsupported_model,
    bad_request, too_large, timeout, invalid_output, network."""

    def __init__(self, kind: str, code: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.code = (code or kind)[:48]
        self.retry_after = retry_after

    @property
    def safe_to_retry(self) -> bool:
        """True only when the provider definitely did not process the request (so a retry
        cannot be charged twice): rate limits and capacity refusals."""
        return self.kind == "rate_limited"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    capabilities: frozenset[str]
    context_window: int | None = None
    max_output_tokens: int | None = None
    preview: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "capabilities": sorted(self.capabilities),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "preview": self.preview,
        }


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    content_type: str


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    # Trusted instructions written by MateCart.
    system: str
    # Untrusted business content (serialised, delimited data) and optional images.
    user: str
    images: Sequence[ImageInput] = ()
    max_output_tokens: int = 1024
    temperature: float = 0.4
    # JSON schema for structured output (used strictly when the model supports it).
    schema: Mapping[str, Any] | None = None
    schema_name: str = "result"
    timeout: float = 45.0


@dataclass(frozen=True)
class GenerationResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    request_id: str = ""
    finish_reason: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


class AIProvider(Protocol):
    kind: str
    is_test: bool

    def list_models(self) -> list[ModelInfo]:
        """Models the credential can use now (raises ProviderError)."""
        ...

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """One completion (raises ProviderError)."""
        ...
