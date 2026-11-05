"""Builders for Phase 15 tests: AI configuration, a scripted HTTPS layer and staff clients.

Nothing here reaches GroqCloud, WooCommerce or Shopify: ``fake_http`` replaces the outbound
transport (after URL validation) and DNS resolution with scripted answers.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from commerce.ai.models import AIConfiguration, AIFeature, AIProviderKind, AIStatus
from commerce.core import outbound, security
from commerce.stores.models import StoreModule
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

AI = "/api/manage/ai/"
ALL_FEATURES = list(AIFeature.values)
TEST_MODELS = [
    {
        "id": "matecart-test-text",
        "capabilities": ["json_object", "json_schema", "text"],
        "context_window": 8192,
        "max_output_tokens": 4096,
        "preview": False,
    },
    {
        "id": "matecart-test-vision",
        "capabilities": ["json_object", "json_schema", "text", "vision"],
        "context_window": 8192,
        "max_output_tokens": 4096,
        "preview": False,
    },
]


def enable_ai(
    tenant: Tenant,
    *,
    features: list[str] | None = None,
    customer_data: bool = True,
    module: bool = True,
    **fields: Any,
) -> AIConfiguration:
    """A connected test-provider configuration (no network)."""
    from django.utils import timezone

    with tenant_context(tenant):
        StoreModule.objects.update_or_create(key="ai_assistant", defaults={"enabled": module})
        config, _ = AIConfiguration.objects.update_or_create(
            tenant=tenant,
            defaults={
                "provider": AIProviderKind.TEST,
                "status": AIStatus.CONNECTED,
                "text_model": "matecart-test-text",
                "vision_model": "matecart-test-vision",
                "available_models": TEST_MODELS,
                "enabled_features": features if features is not None else ALL_FEATURES,
                "customer_data_confirmed_at": timezone.now() if customer_data else None,
                **fields,
            },
        )
        return config


def groq_config(tenant: Tenant, *, key: str = "gsk_" + "x" * 40, **fields: Any) -> AIConfiguration:
    from commerce.ai.groq import KNOWN_MODELS

    models = [
        {
            "id": model_id,
            "capabilities": sorted(spec["capabilities"]),
            "context_window": spec["context_window"],
            "max_output_tokens": spec["max_output_tokens"],
            "preview": spec["preview"],
        }
        for model_id, spec in KNOWN_MODELS.items()
    ]
    values: dict[str, Any] = {
        "provider": AIProviderKind.GROQ,
        "api_key_encrypted": security.encrypt(key),
        "api_key_hint": key[-4:],
        "text_model": "openai/gpt-oss-20b",
        "vision_model": "qwen/qwen3.8-27b",
        "available_models": models,
    }
    values.update(fields)
    return enable_ai(tenant, **values)


@dataclass
class Call:
    method: str
    host: str
    path: str
    headers: Mapping[str, str]
    body: bytes | None


@dataclass
class FakeHttp:
    """Scripted HTTPS: ``routes`` maps (method, host + path prefix) → handler returning
    (status, json-or-bytes, headers)."""

    handler: Callable[[Call], tuple[int, Any, dict[str, str]]]
    calls: list[Call] = field(default_factory=list)

    def __call__(
        self,
        target: outbound.Target,
        method: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        max_bytes: int,
    ) -> outbound.Response:
        call = Call(method, target.host, target.path, dict(headers), body)
        self.calls.append(call)
        status, payload, extra = self.handler(call)
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if len(data) > max_bytes:
            raise outbound.OutboundError("response_too_large", status=status)
        return outbound.Response(
            status=status,
            headers={key.lower(): value for key, value in extra.items()},
            body=data,
            url=f"https://{target.host}{target.path}",
        )

    def json(self, index: int = -1) -> Any:
        body = self.calls[index].body
        return json.loads(body) if body else None


def fake_http(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[Call], tuple[int, Any, dict[str, str]]]
) -> FakeHttp:
    fake = FakeHttp(handler)
    monkeypatch.setattr(
        outbound, "resolve", lambda target: dataclasses.replace(target, address="203.0.113.10")
    )
    monkeypatch.setattr(outbound, "transport", fake)
    return fake


def completion(content: Any, *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content if isinstance(content, str) else json.dumps(content),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        "x_groq": {"id": "req_test_1"},
    }
