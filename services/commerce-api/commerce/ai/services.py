"""AI configuration, requests, limits and approval (D-101 to D-104).

Lifecycle of a request: ``pending`` (created by staff, checks passed) → ``processing`` (claimed
by the worker under the row lock) → ``completed`` | ``failed`` | ``cancelled``; requests that
never start or whose worker died become ``expired`` in the sweep. A request is processed at most
once by the worker: a duplicate task finds it already claimed. Provider calls happen outside any
transaction or row lock, and are never retried when the provider may have processed them; only a
documented rate-limit refusal (nothing processed, nothing billed) is retried, a bounded number
of times.

Gating: the ``ai_assistant`` module must be effective, the feature enabled, the configuration
connected, the selected model capable, the monthly allowance not used up and the requester
within their hourly limit. Turning the module or a feature off stops new requests at once and
fails queued ones; history stays readable for its retention period.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from commerce.audit.services import record_audit_event
from commerce.catalog import services as catalog
from commerce.catalog.models import Product, ProductMedia
from commerce.communications.models import Conversation
from commerce.core import ratelimit, security
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User
from commerce.media import services as media
from commerce.media.models import MediaAsset, MediaKind
from commerce.staff.models import MembershipStatus, StaffMembership
from commerce.staff.permissions import Permission
from commerce.stores.modules import enabled_modules
from commerce.tenancy.context import get_active_tenant_id

from . import features as registry
from . import schemas
from .groq import GroqProvider
from .models import (
    AIConfiguration,
    AIFeature,
    AIProviderKind,
    AIRequest,
    AIRequestStatus,
    AIReviewStatus,
    AIStatus,
)
from .providers import (
    JSON_OBJECT,
    JSON_SCHEMA,
    TEXT,
    VISION,
    AIProvider,
    GenerationRequest,
    ProviderError,
)
from .testing import TestProvider

logger = logging.getLogger(__name__)

MODULE = "ai_assistant"
MAX_IN_FLIGHT_PER_USER = 3
MAX_ATTEMPTS = 3
VERIFY_PER_HOUR = 12
PENDING_EXPIRY = timedelta(minutes=15)
PROCESSING_EXPIRY = timedelta(minutes=10)
USABLE = (AIStatus.CONNECTED, AIStatus.RATE_LIMITED, AIStatus.UNAVAILABLE)
LIMIT_FIELDS = (
    "monthly_request_limit",
    "monthly_token_limit",
    "max_output_tokens",
    "user_hourly_limit",
)
ERROR_STATUS = {
    "invalid_credentials": AIStatus.INVALID_CREDENTIALS,
    "permission": AIStatus.INVALID_CREDENTIALS,
    "rate_limited": AIStatus.RATE_LIMITED,
    "unavailable": AIStatus.UNAVAILABLE,
    "network": AIStatus.UNAVAILABLE,
    "timeout": AIStatus.UNAVAILABLE,
    "unsupported_model": AIStatus.UNSUPPORTED_MODEL,
}


class AIUnavailable(ConflictError):
    default_code = "ai_unavailable"


# --- Configuration --------------------------------------------------------------------------------


def configuration() -> AIConfiguration:
    row = AIConfiguration.objects.first()
    if row is not None:
        return row
    with transaction.atomic():
        AIConfiguration.objects.get_or_create(tenant_id=get_active_tenant_id())
    return AIConfiguration.objects.get()


def _locked() -> AIConfiguration:
    return AIConfiguration.objects.select_for_update().get(pk=configuration().pk)


def test_provider_allowed() -> bool:
    return bool(getattr(settings, "MATECART_AI_TEST_PROVIDER", False))


def module_enabled() -> bool:
    return MODULE in enabled_modules()


def build_provider(config: AIConfiguration) -> AIProvider:
    """The adapter for the store's configuration (tests may replace this function)."""
    if config.provider == AIProviderKind.TEST:
        if not test_provider_allowed():
            raise ProviderError("unsupported_model", "test_provider_disabled")
        return TestProvider()
    try:
        key = security.decrypt(config.api_key_encrypted) if config.api_key_encrypted else ""
    except security.DecryptionError:
        raise ProviderError("invalid_credentials", "key_unreadable") from None
    return GroqProvider(key)


def _model_capabilities(config: AIConfiguration, model: str) -> set[str]:
    for row in config.available_models or []:
        if isinstance(row, dict) and row.get("id") == model:
            return {str(c) for c in row.get("capabilities") or []}
    return set()


def model_problem(config: AIConfiguration, feature: registry.Feature) -> str:
    """Why the configured model cannot serve ``feature`` ('' when it can)."""
    model = config.vision_model if feature.needs_vision else config.text_model
    if not model:
        return "ai_vision_model_missing" if feature.needs_vision else "ai_model_missing"
    capabilities = _model_capabilities(config, model)
    if not capabilities:
        return "ai_model_unavailable"
    if TEXT not in capabilities or not capabilities & {JSON_OBJECT, JSON_SCHEMA}:
        return "ai_model_unsupported"
    if feature.needs_vision and VISION not in capabilities:
        return "ai_model_without_vision"
    return ""


def availability_problem(config: AIConfiguration, feature: registry.Feature) -> str:
    if not module_enabled():
        return "ai_module_off"
    if config.status == AIStatus.NOT_CONFIGURED or (
        config.provider == AIProviderKind.GROQ and not config.api_key_encrypted
    ):
        return "ai_not_configured"
    if config.provider == AIProviderKind.TEST and not test_provider_allowed():
        return "ai_test_provider_disabled"
    if config.status not in USABLE:
        return "ai_not_connected"
    if feature.key not in config.enabled_features:
        return "ai_feature_off"
    if feature.customer_data and config.customer_data_confirmed_at is None:
        return "ai_customer_data_not_confirmed"
    return model_problem(config, feature)


PROBLEM_TEXT = {
    "ai_module_off": "The AI assistant is switched off in store features.",
    "ai_not_configured": "Add an AI provider key in AI settings first.",
    "ai_test_provider_disabled": "The test provider is not allowed on this deployment.",
    "ai_not_connected": "The AI provider connection is not verified. Test it in AI settings.",
    "ai_feature_off": "This AI feature is switched off in AI settings.",
    "ai_customer_data_not_confirmed": (
        "Support suggestions send short conversation excerpts to the AI provider. A settings "
        "manager must confirm this first."
    ),
    "ai_model_missing": "Choose a text model in AI settings.",
    "ai_vision_model_missing": "Choose an image analysis model in AI settings.",
    "ai_model_unavailable": "The chosen model is not available from the provider any more.",
    "ai_model_unsupported": "The chosen model does not support structured answers.",
    "ai_model_without_vision": "The chosen image model cannot read images.",
}


def effective_status(config: AIConfiguration) -> str:
    return AIStatus.DISABLED if not module_enabled() else config.status


def _audit(config: AIConfiguration, action: str, actor: User, **changes: Any) -> None:
    record_audit_event(
        action=f"ai.settings.{action}", resource=config, actor=actor, changes=changes
    )


def set_api_key(*, provider: str, api_key: str, actor: User) -> AIConfiguration:
    key = api_key.strip()
    if provider != AIProviderKind.GROQ:
        raise ValidationError({"provider": ["Only GroqCloud uses an API key."]})
    if not 20 <= len(key) <= 200 or any(ch.isspace() for ch in key) or not key.isprintable():
        raise ValidationError({"api_key": ["Enter the API key exactly as GroqCloud shows it."]})
    with transaction.atomic():
        row = _locked()
        row.provider = AIProviderKind.GROQ
        row.api_key_encrypted = security.encrypt(key)
        row.api_key_hint = key[-4:]
        row.status = AIStatus.UNVERIFIED
        row.last_error = ""
        row.last_verified_at = None
        row.save()
        # Field names only: the key itself never enters the audit log.
        _audit(row, "key_updated", actor, provider=row.provider, fields=["api_key"])
    return row


def remove_api_key(*, actor: User) -> AIConfiguration:
    with transaction.atomic():
        row = _locked()
        had = bool(row.api_key_encrypted)
        row.api_key_encrypted = ""
        row.api_key_hint = ""
        row.status = (
            AIStatus.UNVERIFIED if row.provider == AIProviderKind.TEST else AIStatus.NOT_CONFIGURED
        )
        row.available_models = []
        row.last_verified_at = None
        row.save()
        _audit(row, "key_removed", actor, had_key=had)
    _fail_queued("ai_not_configured")
    return row


def save_settings(*, data: Mapping[str, Any], actor: User) -> AIConfiguration:
    with transaction.atomic():
        row = _locked()
        before = {
            "provider": row.provider,
            "text_model": row.text_model,
            "vision_model": row.vision_model,
            "enabled_features": sorted(row.enabled_features),
            **{field: getattr(row, field) for field in LIMIT_FIELDS},
        }
        if "provider" in data and data["provider"] != row.provider:
            if data["provider"] == AIProviderKind.TEST and not test_provider_allowed():
                raise ValidationError(
                    {"provider": ["The test provider is not available on this deployment."]}
                )
            row.provider = data["provider"]
            row.available_models = []
            row.text_model = row.vision_model = ""
            row.status = (
                AIStatus.UNVERIFIED
                if row.provider == AIProviderKind.TEST or row.api_key_encrypted
                else AIStatus.NOT_CONFIGURED
            )
        for field in ("text_model", "vision_model"):
            if field in data and data[field] != getattr(row, field):
                value = str(data[field] or "")
                if value:
                    capabilities = _model_capabilities(row, value)
                    needed = VISION if field == "vision_model" else TEXT
                    if needed not in capabilities:
                        raise ValidationError(
                            {
                                field: [
                                    "Choose a model from the verified list that supports "
                                    + ("images." if needed == VISION else "text.")
                                ]
                            }
                        )
                setattr(row, field, value)
        if "enabled_features" in data:
            unknown = set(data["enabled_features"]) - set(AIFeature.values)
            if unknown:
                raise ValidationError({"enabled_features": ["Unknown feature."]})
            row.enabled_features = sorted(set(data["enabled_features"]))
        for field in LIMIT_FIELDS:
            if field in data:
                setattr(row, field, data[field])
        row.full_clean(exclude=["tenant", "customer_data_confirmed_by"])
        row.save()
        after = {
            "provider": row.provider,
            "text_model": row.text_model,
            "vision_model": row.vision_model,
            "enabled_features": sorted(row.enabled_features),
            **{field: getattr(row, field) for field in LIMIT_FIELDS},
        }
        changed = {
            key: {"from": before[key], "to": after[key]}
            for key in after
            if before[key] != after[key]
        }
        if changed:
            _audit(row, "updated", actor, **changed)
    stopped = [f for f in before["enabled_features"] if f not in row.enabled_features]
    if stopped:
        _fail_queued("ai_feature_off", features=stopped)
    return row


def confirm_customer_data(*, confirmed: bool, actor: User) -> AIConfiguration:
    with transaction.atomic():
        row = _locked()
        before = row.customer_data_confirmed_at is not None
        row.customer_data_confirmed_at = timezone.now() if confirmed else None
        row.customer_data_confirmed_by = actor if confirmed else None
        row.save(
            update_fields=["customer_data_confirmed_at", "customer_data_confirmed_by", "updated_at"]
        )
        _audit(row, "customer_data", actor, confirmed={"from": before, "to": confirmed})
    if not confirmed:
        _fail_queued(
            "ai_customer_data_not_confirmed",
            features=[AIFeature.SUPPORT_REPLY, AIFeature.SUPPORT_SUMMARY],
        )
    return row


def verify(*, actor: User) -> AIConfiguration:
    """List the provider's models with the stored credential and check the chosen ones."""
    ratelimit.consume("ai-verify", str(get_active_tenant_id()), limit=VERIFY_PER_HOUR, window=3600)
    config = configuration()
    if config.provider == AIProviderKind.GROQ and not config.api_key_encrypted:
        raise DomainError("Add a GroqCloud API key first.", "ai_not_configured")
    status, error, models = AIStatus.CONNECTED, "", None
    try:
        listed = build_provider(config).list_models()
        models = [info.as_dict() for info in listed]
    except ProviderError as exc:
        status, error = ERROR_STATUS.get(exc.kind, AIStatus.UNAVAILABLE), exc.code
    with transaction.atomic():
        row = _locked()
        if models is not None:
            row.available_models = models
            row.models_checked_at = timezone.now()
            row.last_verified_at = timezone.now()
            # The merchant's choice is kept even when it disappeared: the state says so.
            for field in ("text_model", "vision_model"):
                chosen = getattr(row, field)
                if chosen and not _model_capabilities(row, chosen):
                    status, error = AIStatus.UNSUPPORTED_MODEL, f"{field}_unavailable"
            if not row.text_model and status == AIStatus.CONNECTED:
                status, error = AIStatus.UNVERIFIED, "text_model_not_chosen"
        row.status, row.last_error = status, error
        row.save()
        _audit(row, "verified", actor, status=status, error=error)
    return row


def record_provider_failure(exc: ProviderError) -> None:
    """Persistent problems seen during a request change the connection state."""
    status = {
        "invalid_credentials": AIStatus.INVALID_CREDENTIALS,
        "permission": AIStatus.INVALID_CREDENTIALS,
        "unsupported_model": AIStatus.UNSUPPORTED_MODEL,
    }.get(exc.kind)
    if status is None:
        return
    AIConfiguration.objects.filter(pk=configuration().pk).update(
        status=status, last_error=exc.code, updated_at=timezone.now()
    )


def public_state(config: AIConfiguration) -> dict[str, Any]:
    """What staff may see: never the key, only whether one is set and its last characters."""
    feature_states = []
    for key, feature in registry.FEATURES.items():
        problem = availability_problem(config, feature)
        feature_states.append(
            {
                "key": key,
                "name": AIFeature(key).label,
                "permission": feature.permission,
                "enabled": key in config.enabled_features,
                "available": not problem,
                "problem": problem,
                "problem_text": PROBLEM_TEXT.get(problem, ""),
                "needs_vision": feature.needs_vision,
                "uses_customer_data": feature.customer_data,
            }
        )
    return {
        "module_enabled": module_enabled(),
        "provider": config.provider,
        "status": effective_status(config),
        "key_set": bool(config.api_key_encrypted),
        "key_hint": config.api_key_hint,
        "text_model": config.text_model,
        "vision_model": config.vision_model,
        "available_models": config.available_models or [],
        "models_checked_at": config.models_checked_at,
        "last_verified_at": config.last_verified_at,
        "last_error": config.last_error,
        "enabled_features": list(config.enabled_features),
        "monthly_request_limit": config.monthly_request_limit,
        "monthly_token_limit": config.monthly_token_limit,
        "max_output_tokens": config.max_output_tokens,
        "user_hourly_limit": config.user_hourly_limit,
        "customer_data_confirmed": config.customer_data_confirmed_at is not None,
        "customer_data_confirmed_at": config.customer_data_confirmed_at,
        "test_provider_allowed": test_provider_allowed(),
        "is_test": config.provider == AIProviderKind.TEST,
        "features": feature_states,
    }


# --- Usage ----------------------------------------------------------------------------------------


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    from commerce.orders.reports import store_zone

    local = (now or timezone.now()).astimezone(store_zone())
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def _counted(start: datetime, end: datetime) -> Any:
    """Requests that count towards the allowance: everything except those stopped before the
    provider was ever called."""
    return AIRequest.objects.filter(created_at__gte=start, created_at__lt=end).filter(
        Q(provider_called=True)
        | Q(status__in=(AIRequestStatus.PENDING, AIRequestStatus.PROCESSING))
    )


def usage_summary() -> dict[str, Any]:
    config = configuration()
    start, end = month_bounds()
    month = AIRequest.objects.filter(created_at__gte=start, created_at__lt=end)
    counted = _counted(start, end)
    tokens = month.aggregate(
        input=Sum("input_tokens"), output=Sum("output_tokens"), total=Sum("total_tokens")
    )
    from commerce.orders.reports import store_zone

    by_day = (
        month.annotate(day=TruncDate("created_at", tzinfo=store_zone()))
        .values("day")
        .annotate(requests=Count("pk"), tokens=Sum("total_tokens"))
        .order_by("day")
    )
    by_feature = (
        month.values("feature")
        .annotate(
            requests=Count("pk"),
            completed=Count("pk", filter=Q(status=AIRequestStatus.COMPLETED)),
            failed=Count("pk", filter=Q(status=AIRequestStatus.FAILED)),
            tokens=Sum("total_tokens"),
        )
        .order_by("feature")
    )
    used_requests = counted.count()
    used_tokens = int(tokens["total"] or 0)
    return {
        "month_start": start,
        "month_end": end,
        "requests": used_requests,
        "request_limit": config.monthly_request_limit,
        "tokens_input": int(tokens["input"] or 0),
        "tokens_output": int(tokens["output"] or 0),
        "tokens_total": used_tokens,
        "token_limit": config.monthly_token_limit,
        "requests_without_usage": month.filter(
            provider_called=True, total_tokens__isnull=True
        ).count(),
        "failed": month.filter(status=AIRequestStatus.FAILED).count(),
        "test_requests": month.filter(is_test=True).count(),
        "by_status": {
            row["status"]: row["n"]
            for row in month.values("status").annotate(n=Count("pk")).order_by()
        },
        "by_feature": [
            {
                "feature": row["feature"],
                "requests": row["requests"],
                "completed": row["completed"],
                "failed": row["failed"],
                "tokens": int(row["tokens"] or 0),
            }
            for row in by_feature
        ],
        "by_day": [
            {"date": row["day"], "requests": row["requests"], "tokens": int(row["tokens"] or 0)}
            for row in by_day
        ],
    }


# --- Requests -------------------------------------------------------------------------------------


def current_permissions(user: User) -> frozenset[str]:
    membership = StaffMembership.objects.filter(user=user).first()
    if membership is None or membership.status != MembershipStatus.ACTIVE or not user.is_active:
        return frozenset()
    return membership.permissions


def _check_subject(feature: registry.Feature, subject_id: Any, options: Mapping[str, Any]) -> None:
    if feature.subject_type == "product":
        if subject_id is None or not Product.objects.filter(pk=subject_id).exists():
            raise Http404
    elif feature.subject_type == "media":
        asset = MediaAsset.objects.filter(pk=subject_id).first() if subject_id else None
        if asset is None or asset.kind != MediaKind.IMAGE:
            raise Http404
        product = options.get("product")
        if product and not ProductMedia.objects.filter(product_id=product, asset=asset).exists():
            raise ValidationError({"options.product": ["This image is not used by that product."]})
    elif feature.subject_type == "conversation":
        if subject_id is None or not Conversation.objects.filter(pk=subject_id).exists():
            raise Http404
    elif feature.key == AIFeature.MARKETING_COPY:
        products = options.get("products") or []
        if Product.objects.filter(pk__in=products).count() != len(set(products)):
            raise ValidationError({"options.products": ["A chosen product does not exist."]})
        if options.get("campaign"):
            from commerce.marketing.models import Campaign

            if not Campaign.objects.filter(pk=options["campaign"]).exists():
                raise ValidationError({"options.campaign": ["This campaign does not exist."]})


def submit(
    *,
    feature_key: str,
    subject_id: Any,
    options: Mapping[str, Any] | None,
    client_key: str,
    actor: User,
    permissions: frozenset[str],
) -> tuple[AIRequest, bool]:
    """Create a pending request (or return the one with the same client key)."""
    feature = registry.FEATURES.get(feature_key)
    if feature is None:
        raise ValidationError({"feature": ["Unknown AI feature."]})
    if feature.permission not in permissions:
        raise PermissionDenied("Your role cannot use this AI feature.")
    if client_key:
        existing = AIRequest.objects.filter(requested_by=actor, client_key=client_key).first()
        if existing is not None:
            if existing.feature != feature_key or (
                feature.subject_type and str(existing.subject_id) != str(subject_id)
            ):
                raise ConflictError("This request key was used for another request.", "key_reused")
            return existing, False
    cleaned = feature.clean_options(options or {})
    if not feature.subject_type:
        subject_id = None
    _check_subject(feature, subject_id, cleaned)
    config = configuration()
    problem = availability_problem(config, feature)
    if problem:
        raise AIUnavailable(PROBLEM_TEXT.get(problem, "AI is not available."), problem)
    in_flight = AIRequest.objects.filter(
        requested_by=actor, status__in=(AIRequestStatus.PENDING, AIRequestStatus.PROCESSING)
    ).count()
    if in_flight >= MAX_IN_FLIGHT_PER_USER:
        raise ConflictError("Wait for your current AI requests to finish.", "ai_busy")
    ratelimit.consume("ai-user", str(actor.pk), limit=config.user_hourly_limit, window=3600)
    with transaction.atomic():
        locked = _locked()  # serialises the monthly allowance check
        start, end = month_bounds()
        if _counted(start, end).count() >= locked.monthly_request_limit:
            raise AIUnavailable("This month's AI request allowance is used up.", "ai_request_limit")
        used = (
            AIRequest.objects.filter(created_at__gte=start, created_at__lt=end).aggregate(
                total=Sum("total_tokens")
            )["total"]
            or 0
        )
        if used >= locked.monthly_token_limit:
            raise AIUnavailable("This month's AI token allowance is used up.", "ai_token_limit")
        row = AIRequest.objects.create(
            feature=feature.key,
            requested_by=actor,
            provider=locked.provider,
            model=locked.vision_model if feature.needs_vision else locked.text_model,
            is_test=locked.provider == AIProviderKind.TEST,
            subject_type=feature.subject_type,
            subject_id=subject_id,
            options=cleaned,
            client_key=client_key,
            review_status=AIReviewStatus.PENDING if feature.reviewable else AIReviewStatus.NONE,
        )
        tenant_id = str(get_active_tenant_id())
        request_id = str(row.pk)

        def enqueue() -> None:
            from .tasks import run_request

            try:
                run_request.delay(request_id, tenant_id=tenant_id)
            except Exception:  # broker down: the sweep expires it; staff can retry
                logger.warning("ai.enqueue_failed", extra={"ai_request": request_id})

        transaction.on_commit(enqueue)
    return row, True


def _finish(
    row: AIRequest, status: str, *, error: str = "", usage: Mapping[str, Any] | None = None
) -> None:
    AIRequest.objects.filter(pk=row.pk).update(
        status=status,
        error_code=error[:48],
        completed_at=timezone.now(),
        updated_at=timezone.now(),
        **(usage or {}),
    )


def _fail_queued(error: str, *, features: list[str] | None = None) -> None:
    rows = AIRequest.objects.filter(status=AIRequestStatus.PENDING)
    if features:
        rows = rows.filter(feature__in=features)
    rows.update(
        status=AIRequestStatus.FAILED,
        error_code=error,
        completed_at=timezone.now(),
        updated_at=timezone.now(),
    )


def run(request_id: str) -> str:
    """Process one request (Celery task body). Returns the final status."""
    with transaction.atomic():
        row = AIRequest.objects.select_for_update().filter(pk=request_id).first()
        if row is None or row.status != AIRequestStatus.PENDING:
            return row.status if row else "missing"
        if row.cancel_requested:
            row.status = AIRequestStatus.CANCELLED
            row.completed_at = timezone.now()
            row.save(update_fields=["status", "completed_at", "updated_at"])
            return row.status
        row.status = AIRequestStatus.PROCESSING
        row.started_at = row.started_at or timezone.now()
        row.attempts += 1
        row.save(update_fields=["status", "started_at", "attempts", "updated_at"])
    feature = registry.FEATURES[row.feature]
    config = configuration()
    permissions = current_permissions(row.requested_by)
    if feature.permission not in permissions:
        _finish(row, AIRequestStatus.FAILED, error="requester_not_allowed")
        return AIRequestStatus.FAILED
    problem = availability_problem(config, feature)
    if problem:
        _finish(row, AIRequestStatus.FAILED, error=problem)
        return AIRequestStatus.FAILED
    try:
        prompt = feature.build(row, permissions)
        provider = build_provider(config)
    except ObjectDoesNotExist:
        _finish(row, AIRequestStatus.FAILED, error="subject_missing")
        return AIRequestStatus.FAILED
    except ProviderError as exc:
        _finish(row, AIRequestStatus.FAILED, error=exc.code)
        return AIRequestStatus.FAILED
    except Exception:
        logger.exception("ai.context_failed", extra={"ai_request": str(row.pk)})
        _finish(row, AIRequestStatus.FAILED, error="context_failed")
        return AIRequestStatus.FAILED
    AIRequest.objects.filter(pk=row.pk).update(
        provider_called=True, context_sources=prompt.sources, updated_at=timezone.now()
    )
    request = GenerationRequest(
        model=row.model,
        system=prompt.system,
        user=registry.user_message(prompt),
        images=prompt.images,
        max_output_tokens=config.max_output_tokens,
        temperature=feature.temperature,
        schema=schemas.SCHEMAS[row.feature],
        schema_name=row.feature,
        timeout=float(getattr(settings, "MATECART_AI_TIMEOUT_SECONDS", 45)),
    )
    started = time.monotonic()
    try:
        result = provider.generate(request)
    except ProviderError as exc:
        if exc.safe_to_retry and row.attempts < MAX_ATTEMPTS:
            AIRequest.objects.filter(pk=row.pk, status=AIRequestStatus.PROCESSING).update(
                status=AIRequestStatus.PENDING, error_code=exc.code, updated_at=timezone.now()
            )
            _requeue(row, delay=exc.retry_after)
            return AIRequestStatus.PENDING
        record_provider_failure(exc)
        _finish(row, AIRequestStatus.FAILED, error=exc.code)
        return AIRequestStatus.FAILED
    latency = int((time.monotonic() - started) * 1000)
    usage = {
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
        "provider_request_id": result.request_id,
        "latency_ms": latency,
    }
    try:
        output = feature.validate(schemas.parse(result.text), row.options)
    except schemas.InvalidOutput as exc:
        _finish(row, AIRequestStatus.FAILED, error=exc.code, usage=usage)
        return AIRequestStatus.FAILED
    if result.finish_reason == "length":
        output.setdefault("warnings", []).append("The answer reached the length limit.")
    if row.feature == AIFeature.MARKETING_COPY:
        allowed = registry.user_message(prompt)
        warnings = []
        for variant in output["variants"]:
            warnings += registry.claim_warnings(f"{variant['headline']} {variant['body']}", allowed)
        if warnings:
            output.setdefault("warnings", []).extend(dict.fromkeys(warnings))
    output.update(prompt.extras)
    with transaction.atomic():
        locked = AIRequest.objects.select_for_update().get(pk=row.pk)
        if locked.cancel_requested:
            _finish(locked, AIRequestStatus.CANCELLED, usage=usage)
            return AIRequestStatus.CANCELLED
        locked.status = AIRequestStatus.COMPLETED
        locked.result = output
        locked.error_code = ""
        locked.completed_at = timezone.now()
        for key, value in usage.items():
            setattr(locked, key, value)
        locked.save()
    return AIRequestStatus.COMPLETED


def _requeue(row: AIRequest, *, delay: float | None) -> None:
    from .tasks import run_request

    countdown = int(min(max(delay or 5.0, 2.0), 60.0))
    tenant_id = str(get_active_tenant_id())
    try:
        run_request.apply_async((str(row.pk),), {"tenant_id": tenant_id}, countdown=countdown)
    except Exception:
        logger.warning("ai.requeue_failed", extra={"ai_request": str(row.pk)})


def cancel(*, row: AIRequest, actor: User) -> AIRequest:
    with transaction.atomic():
        locked = AIRequest.objects.select_for_update().get(pk=row.pk)
        if locked.status == AIRequestStatus.PENDING:
            locked.status = AIRequestStatus.CANCELLED
            locked.completed_at = timezone.now()
        elif locked.status == AIRequestStatus.PROCESSING:
            # The provider call cannot be recalled; its answer will be discarded.
            locked.cancel_requested = True
        else:
            raise ConflictError("This request has already finished.", "ai_request_finished")
        locked.save()
    return locked


def retry(*, row: AIRequest, actor: User, permissions: frozenset[str]) -> AIRequest:
    """A new request with the same feature, subject and options (never a silent resend)."""
    if row.status not in (
        AIRequestStatus.FAILED,
        AIRequestStatus.EXPIRED,
        AIRequestStatus.CANCELLED,
    ):
        raise ConflictError(
            "Only failed, expired or cancelled requests can be retried.", "ai_retry"
        )
    created, _ = submit(
        feature_key=row.feature,
        subject_id=row.subject_id,
        options=dict(row.options),
        client_key="",
        actor=actor,
        permissions=permissions,
    )
    return created


# --- Review and apply -----------------------------------------------------------------------------

PRODUCT_TARGETS = {
    "en": {
        "title": "name",
        "summary": "summary",
        "description": "description",
        "seo_title": "seo_title",
        "seo_description": "seo_description",
        "tags": "tags",
    },
    "bn": {"title": "name", "summary": "summary", "description": "description"},
}


def _reviewable(row: AIRequest, actor: User) -> None:
    if row.requested_by_id != actor.pk:
        raise PermissionDenied("Only the person who asked for this draft can apply it.")
    if row.status != AIRequestStatus.COMPLETED or row.result is None:
        raise ConflictError("This draft is not available.", "ai_draft_unavailable")
    if row.review_status != AIReviewStatus.PENDING:
        raise ConflictError("This draft has already been reviewed.", "ai_draft_reviewed")


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValidationError({f"values.{field}": ["Enter text."]})
    text = schemas.clean_text(value, 10**6)
    if len(text) > limit:
        raise ValidationError({f"values.{field}": [f"Use at most {limit} characters."]})
    return text


def _apply_product(
    row: AIRequest, values: Mapping[str, Any], overwrite: bool, actor: User
) -> list[str]:
    product = Product.objects.filter(pk=registry.subject_id(row)).first()
    if product is None:
        raise Http404
    language = row.options.get("language", "en")
    targets = PRODUCT_TARGETS[language]
    allowed = set(row.options.get("fields", [])) & (set(targets) | {"image_alt_text"})
    unknown = set(values) - allowed
    if unknown:
        raise ValidationError(
            {"values": [f"{sorted(unknown)[0]} cannot be applied from this draft."]}
        )
    limits = {
        "title": 200,
        "summary": 500,
        "description": 20000,
        "seo_title": 70,
        "seo_description": 320,
        "image_alt_text": 300,
    }
    update: dict[str, Any] = {}
    occupied: list[str] = []
    translations = dict(product.translations or {})
    bangla = dict(translations.get("bn") or {})
    for field, value in values.items():
        if field == "image_alt_text":
            continue
        target = targets[field]
        if field == "tags":
            if not isinstance(value, list) or len(value) > 20:
                raise ValidationError({"values.tags": ["Give at most 20 tags."]})
            cleaned = [schemas.clean_text(tag, 40, multiline=False) for tag in value]
            current: Any = list(product.tags)
            new: Any = [tag for tag in cleaned if tag]
        else:
            new = _text(value, field, limits[field])
            current = bangla.get(target, "") if language == "bn" else getattr(product, target)
        if current and current != new:
            occupied.append(field)
        if language == "bn":
            bangla[target] = new
        else:
            update[target] = new
    if occupied and not overwrite:
        raise ConflictError(
            "Confirm replacing existing content: " + ", ".join(occupied) + ".",
            "ai_overwrite_needs_confirmation",
        )
    if language == "bn" and bangla != translations.get("bn"):
        translations["bn"] = bangla
        update["translations"] = translations
    applied = [field for field in values if field != "image_alt_text"]
    if update:
        catalog.update_product(product=product, data=update, actor=actor)
    if "image_alt_text" in values:
        text = _text(values["image_alt_text"], "image_alt_text", 300)
        primary = (
            ProductMedia.objects.filter(product=product, is_primary=True)
            .select_related("asset")
            .first()
        )
        if primary is None:
            raise ConflictError("This product has no main photo yet.", "ai_no_primary_image")
        if primary.asset.alt_text and primary.asset.alt_text != text and not overwrite:
            raise ConflictError(
                "Confirm replacing existing content: image_alt_text.",
                "ai_overwrite_needs_confirmation",
            )
        media.update(asset=primary.asset, alt_text=text, actor=actor)
        applied.append("image_alt_text")
    return applied


def _apply_image(
    row: AIRequest, values: Mapping[str, Any], overwrite: bool, actor: User
) -> list[str]:
    if set(values) - {"alt_text"}:
        raise ValidationError({"values": ["Only alt text can be applied from an image analysis."]})
    asset = MediaAsset.objects.filter(pk=registry.subject_id(row)).first()
    if asset is None:
        raise Http404
    text = _text(values.get("alt_text", ""), "alt_text", 300)
    if asset.alt_text and asset.alt_text != text and not overwrite:
        raise ConflictError(
            "Confirm replacing existing content: alt_text.", "ai_overwrite_needs_confirmation"
        )
    media.update(asset=asset, alt_text=text, actor=actor)
    return ["alt_text"]


CAMPAIGN_TARGETS = {"campaign_title": ("name", 120), "campaign_description": ("description", 500)}


def _apply_marketing(
    row: AIRequest, values: Mapping[str, Any], overwrite: bool, actor: User
) -> list[str]:
    from commerce.marketing import promotions
    from commerce.marketing.models import Campaign

    kind = row.options.get("kind")
    if kind not in CAMPAIGN_TARGETS or not row.options.get("campaign"):
        raise ConflictError(
            "This kind of draft is copied, not applied: paste it where you need it.", "ai_copy_only"
        )
    field, limit = CAMPAIGN_TARGETS[kind]
    if set(values) != {field}:
        raise ValidationError({"values": [f"Give the campaign {field}."]})
    campaign = Campaign.objects.filter(pk=row.options["campaign"]).first()
    if campaign is None:
        raise Http404
    text = _text(values[field], field, limit)
    current = getattr(campaign, field)
    if current and current != text and not overwrite:
        raise ConflictError(
            f"Confirm replacing existing content: {field}.", "ai_overwrite_needs_confirmation"
        )
    promotions.save_campaign(campaign=campaign, data={field: text}, actor=actor)
    return [field]


APPLIERS: dict[str, Callable[[AIRequest, Mapping[str, Any], bool, User], list[str]]] = {
    AIFeature.PRODUCT_CONTENT: _apply_product,
    AIFeature.IMAGE_ANALYSIS: _apply_image,
    AIFeature.MARKETING_COPY: _apply_marketing,
}


def apply(
    *,
    row: AIRequest,
    values: Mapping[str, Any],
    overwrite: bool,
    actor: User,
    permissions: frozenset[str],
) -> AIRequest:
    """Apply staff-reviewed (and possibly edited) values through the owning domain service."""
    feature = registry.FEATURES[row.feature]
    if feature.permission not in permissions:
        raise PermissionDenied("Your role cannot use this AI feature.")
    if row.feature == AIFeature.MARKETING_COPY and Permission.PROMOTIONS_MANAGE not in permissions:
        raise PermissionDenied("Your role cannot change campaigns.")
    applier = APPLIERS.get(row.feature)
    if applier is None:
        raise ConflictError("This draft is used in place, not applied.", "ai_copy_only")
    if not values:
        raise ValidationError({"values": ["Choose at least one field to apply."]})
    with transaction.atomic():
        locked = AIRequest.objects.select_for_update().get(pk=row.pk)
        _reviewable(locked, actor)
        applied = applier(locked, values, overwrite, actor)
        locked.review_status = AIReviewStatus.APPLIED
        locked.applied_fields = applied
        locked.reviewed_by = actor
        locked.reviewed_at = timezone.now()
        locked.save()
        record_audit_event(
            action="ai.draft.applied",
            resource=locked,
            actor=actor,
            changes={
                "feature": locked.feature,
                "subject_type": locked.subject_type,
                "fields": applied,
                "edited_by_staff": True,
            },
        )
    return locked


def review(*, row: AIRequest, decision: str, actor: User) -> AIRequest:
    """Mark a draft as rejected, or as used in place (e.g. a reply placed in the composer)."""
    if decision not in (AIReviewStatus.REJECTED, AIReviewStatus.USED):
        raise ValidationError({"decision": ["Choose rejected or used."]})
    with transaction.atomic():
        locked = AIRequest.objects.select_for_update().get(pk=row.pk)
        _reviewable(locked, actor)
        locked.review_status = decision
        locked.reviewed_by = actor
        locked.reviewed_at = timezone.now()
        locked.save()
        record_audit_event(
            action=f"ai.draft.{decision}",
            resource=locked,
            actor=actor,
            changes={"feature": locked.feature, "subject_type": locked.subject_type},
        )
    return locked


# --- Housekeeping ---------------------------------------------------------------------------------


def sweep(now: datetime | None = None) -> dict[str, int]:
    """Expire requests that never started or whose worker died; apply retention."""
    now = now or timezone.now()
    never_started = AIRequest.objects.filter(
        status=AIRequestStatus.PENDING, created_at__lt=now - PENDING_EXPIRY
    ).update(
        status=AIRequestStatus.EXPIRED, error_code="not_started", completed_at=now, updated_at=now
    )
    lost = AIRequest.objects.filter(
        status=AIRequestStatus.PROCESSING, started_at__lt=now - PROCESSING_EXPIRY
    ).update(
        status=AIRequestStatus.EXPIRED, error_code="worker_lost", completed_at=now, updated_at=now
    )
    result_days = int(getattr(settings, "MATECART_AI_RESULT_RETENTION_DAYS", 30))
    record_days = int(getattr(settings, "MATECART_AI_RECORD_RETENTION_DAYS", 400))
    cleared = AIRequest.objects.filter(
        result__isnull=False, completed_at__lt=now - timedelta(days=result_days)
    ).update(result=None, result_cleared_at=now, updated_at=now)
    deleted, _ = AIRequest.objects.filter(created_at__lt=now - timedelta(days=record_days)).delete()
    return {"expired": never_started + lost, "cleared": cleared, "deleted": deleted}
