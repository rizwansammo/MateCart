"""GroqCloud adapter (D-102), written against https://console.groq.com/docs (checked 2026-11-05).

- Fixed endpoint ``https://api.groq.com/openai/v1`` (the merchant never supplies a URL), bearer
  key, ``POST /chat/completions`` and ``GET /models``. Requests go through
  ``commerce.core.outbound`` (public addresses only, pinned connection, TLS, no redirects,
  explicit timeout and response size limit).
- Capabilities come from an explicit registry of models whose behaviour is documented. Groq's
  OpenAI compatibility is partial, so only documented parameters are sent:
  ``max_completion_tokens`` (``max_tokens`` is deprecated), ``temperature`` and
  ``response_format``. ``json_schema`` with ``strict: true`` is used only on models documented to
  support it; other models get ``json_object`` mode and the result is still validated by
  MateCart. Models the API lists but MateCart does not know are shown with no capabilities and
  cannot be selected.
- At the time of writing no *production* Groq model accepts images; the documented vision models
  are preview models. They are marked ``preview`` so the merchant sees that before choosing.
- Errors: 401 invalid key, 403 permission, 404 model not found, 413 too large, 400/422 invalid
  request or schema mismatch, 429 and 498 rate limited / capacity (``retry-after`` seconds),
  5xx unavailable. Groq documents no idempotency key, so MateCart never resends a request that
  may have been processed.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from commerce.core import outbound

from .providers import (
    JSON_OBJECT,
    JSON_SCHEMA,
    TEXT,
    VISION,
    GenerationRequest,
    GenerationResult,
    ModelInfo,
    ProviderError,
)

BASE_URL = "https://api.groq.com/openai/v1"
HOST = "api.groq.com"
MAX_RESPONSE = 2 * 1024 * 1024


def _known(
    capabilities: tuple[str, ...], context: int, output: int, *, preview: bool = False
) -> dict[str, Any]:
    return {
        "capabilities": frozenset(capabilities),
        "context_window": context,
        "max_output_tokens": output,
        "preview": preview,
    }


# Documented models (console.groq.com/docs/models, structured-outputs, vision; 2026-11-05).
KNOWN_MODELS: dict[str, dict[str, Any]] = {
    "openai/gpt-oss-120b": _known((TEXT, JSON_OBJECT, JSON_SCHEMA), 131_072, 65_536),
    "openai/gpt-oss-20b": _known((TEXT, JSON_OBJECT, JSON_SCHEMA), 131_072, 65_536),
    "llama-3.3-70b-versatile": _known((TEXT, JSON_OBJECT), 131_072, 32_768),
    "llama-3.1-8b-instant": _known((TEXT, JSON_OBJECT), 131_072, 131_072),
    "qwen/qwen3.8-27b": _known(
        (TEXT, JSON_OBJECT, JSON_SCHEMA, VISION), 131_072, 32_768, preview=True
    ),
    "meta-models/muse-spark-open": _known(
        (TEXT, JSON_OBJECT, JSON_SCHEMA, VISION), 131_072, 32_768, preview=True
    ),
}


def _policy() -> outbound.Policy:
    return outbound.Policy(host_allowed=lambda host: host == HOST, max_redirects=0)


def _retry_after(response: outbound.Response) -> float | None:
    try:
        return max(0.0, min(float(response.header("retry-after")), 300.0))
    except ValueError:
        return None


def _error_label(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return ""
    code = error.get("code") or error.get("type") or ""
    return str(code)[:40] if isinstance(code, str) else ""


def classify(response: outbound.Response) -> ProviderError:
    try:
        payload = json.loads(response.body or b"{}")
    except ValueError:
        payload = {}
    label = _error_label(payload if isinstance(payload, Mapping) else {})
    status = response.status
    code = f"groq_{status}" + (f"_{label}" if label else "")
    if status == 401:
        return ProviderError("invalid_credentials", code)
    if status == 403:
        return ProviderError("permission", code)
    if status == 404 or label == "model_not_found" or label == "model_decommissioned":
        return ProviderError("unsupported_model", code)
    if status == 413:
        return ProviderError("too_large", code)
    if status in (429, 498):
        return ProviderError("rate_limited", code, retry_after=_retry_after(response))
    if status == 422 or label == "json_validate_failed":
        return ProviderError("invalid_output", code)
    if status >= 500:
        return ProviderError("unavailable", code)
    return ProviderError("bad_request", code)


class GroqProvider:
    kind = "groq"
    is_test = False

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ProviderError("invalid_credentials", "key_missing")
        self._key = api_key

    def _call(
        self, method: str, path: str, body: Mapping[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode()
        try:
            response = outbound.request(
                method,
                f"{BASE_URL}{path}",
                headers=headers,
                body=data,
                timeout=timeout,
                max_bytes=MAX_RESPONSE,
                policy=_policy(),
            )
        except outbound.OutboundError as exc:
            if exc.code == "timeout":
                raise ProviderError("timeout", "timeout") from None
            raise ProviderError("network", exc.code) from None
        if response.status != 200:
            raise classify(response)
        try:
            value = json.loads(response.body)
        except ValueError:
            raise ProviderError("unavailable", "groq_invalid_json") from None
        if not isinstance(value, dict):
            raise ProviderError("unavailable", "groq_invalid_json")
        return value

    def list_models(self) -> list[ModelInfo]:
        payload = self._call("GET", "/models", None, timeout=15.0)
        data = payload.get("data")
        result: list[ModelInfo] = []
        for row in data if isinstance(data, list) else []:
            if not isinstance(row, Mapping) or row.get("active") is False:
                continue
            model_id = str(row.get("id") or "")[:120]
            if not model_id:
                continue
            known = KNOWN_MODELS.get(model_id)
            context = row.get("context_window")
            result.append(
                ModelInfo(
                    id=model_id,
                    capabilities=known["capabilities"] if known else frozenset(),
                    context_window=context if isinstance(context, int) else None,
                    max_output_tokens=known["max_output_tokens"] if known else None,
                    preview=known["preview"] if known else False,
                )
            )
        return sorted(result, key=lambda info: (not info.capabilities, info.preview, info.id))

    def generate(self, request: GenerationRequest) -> GenerationResult:
        known = KNOWN_MODELS.get(request.model)
        capabilities = known["capabilities"] if known else frozenset()
        if TEXT not in capabilities:
            raise ProviderError("unsupported_model", "model_capability_unknown")
        if request.images and VISION not in capabilities:
            raise ProviderError("unsupported_model", "model_without_vision")
        user_content: Any = request.user
        if request.images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": request.user}]
            for image in request.images:
                encoded = base64.b64encode(image.data).decode()
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image.content_type};base64,{encoded}"},
                    }
                )
            user_content = parts
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": user_content},
            ],
            "max_completion_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.schema is not None:
            if JSON_SCHEMA in capabilities:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "schema": dict(request.schema),
                        "strict": True,
                    },
                }
            elif JSON_OBJECT in capabilities:
                body["response_format"] = {"type": "json_object"}
            else:
                raise ProviderError("unsupported_model", "model_without_json")
        payload = self._call("POST", "/chat/completions", body, timeout=request.timeout)
        choices = payload.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ProviderError("invalid_output", "empty_completion")
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        meta = payload.get("x_groq") if isinstance(payload.get("x_groq"), Mapping) else {}

        def number(key: str) -> int | None:
            value = usage.get(key) if isinstance(usage, Mapping) else None
            return value if isinstance(value, int) and value >= 0 else None

        return GenerationResult(
            text=content,
            input_tokens=number("prompt_tokens"),
            output_tokens=number("completion_tokens"),
            total_tokens=number("total_tokens"),
            request_id=str((meta or {}).get("id") or payload.get("id") or "")[:120],
            finish_reason=str(first.get("finish_reason") or "")[:24]
            if isinstance(first, Mapping)
            else "",
        )
