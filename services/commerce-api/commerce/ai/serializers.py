from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import AIFeature, AIProviderKind, AIRequest, AIReviewStatus
from .services import PROBLEM_TEXT

ERROR_TEXT = {
    **PROBLEM_TEXT,
    "requester_not_allowed": "Your role no longer allows this request.",
    "subject_missing": "The record this request was about no longer exists.",
    "context_failed": "MateCart could not prepare the store data for this request.",
    "invalid_output": "The AI answer did not have the expected form. Try again.",
    "output_not_json": "The AI answer did not have the expected form. Try again.",
    "output_not_object": "The AI answer did not have the expected form. Try again.",
    "output_type": "The AI answer did not have the expected form. Try again.",
    "output_empty": "The AI returned an empty answer. Try again with more product details.",
    "not_started": "The request was not started in time (the background worker may be down).",
    "worker_lost": "The background worker stopped while processing this request.",
    "timeout": "The AI provider did not answer in time.",
    "network": "MateCart could not reach the AI provider.",
    "key_missing": "No API key is saved.",
    "key_unreadable": "The saved API key cannot be read; enter it again.",
    "test_provider_disabled": "The test provider is not allowed on this deployment.",
    "model_without_vision": "The chosen model cannot read images.",
    "model_capability_unknown": "MateCart does not know this model's capabilities.",
}


def error_text(code: str) -> str:
    if not code:
        return ""
    if code in ERROR_TEXT:
        return ERROR_TEXT[code]
    if code.startswith("groq_401"):
        return "GroqCloud refused the API key."
    if code.startswith(("groq_429", "groq_498")):
        return "GroqCloud is rate limiting this key. Try again shortly."
    if code.startswith("groq_5"):
        return "GroqCloud is unavailable right now."
    if code.startswith("groq_404"):
        return "The chosen model is not available from GroqCloud."
    if code.startswith("groq_413"):
        return "The request was too large for the provider."
    return "The AI request failed."


class FeatureStateSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    name = serializers.CharField()
    permission = serializers.CharField()
    enabled = serializers.BooleanField()
    available = serializers.BooleanField()
    problem = serializers.CharField()
    problem_text = serializers.CharField()
    needs_vision = serializers.BooleanField()
    uses_customer_data = serializers.BooleanField()


class AIModelSerializer(serializers.Serializer[Any]):
    id = serializers.CharField()
    capabilities = serializers.ListField(child=serializers.CharField())
    context_window = serializers.IntegerField(allow_null=True)
    max_output_tokens = serializers.IntegerField(allow_null=True)
    preview = serializers.BooleanField()


class AISettingsSerializer(serializers.Serializer[Any]):
    module_enabled = serializers.BooleanField()
    provider = serializers.CharField()
    status = serializers.CharField()
    key_set = serializers.BooleanField()
    key_hint = serializers.CharField()
    text_model = serializers.CharField()
    vision_model = serializers.CharField()
    available_models = AIModelSerializer(many=True)
    models_checked_at = serializers.DateTimeField(allow_null=True)
    last_verified_at = serializers.DateTimeField(allow_null=True)
    last_error = serializers.CharField()
    last_error_text = serializers.SerializerMethodField()
    enabled_features = serializers.ListField(child=serializers.CharField())
    monthly_request_limit = serializers.IntegerField()
    monthly_token_limit = serializers.IntegerField()
    max_output_tokens = serializers.IntegerField()
    user_hourly_limit = serializers.IntegerField()
    customer_data_confirmed = serializers.BooleanField()
    customer_data_confirmed_at = serializers.DateTimeField(allow_null=True)
    test_provider_allowed = serializers.BooleanField()
    is_test = serializers.BooleanField()
    features = FeatureStateSerializer(many=True)

    def get_last_error_text(self, obj: dict[str, Any]) -> str:
        return error_text(str(obj.get("last_error") or ""))


class AISettingsInputSerializer(serializers.Serializer[dict[str, Any]]):
    provider = serializers.ChoiceField(choices=AIProviderKind.choices, required=False)
    text_model = serializers.CharField(max_length=120, required=False, allow_blank=True)
    vision_model = serializers.CharField(max_length=120, required=False, allow_blank=True)
    enabled_features = serializers.ListField(
        child=serializers.ChoiceField(choices=AIFeature.choices), required=False, max_length=10
    )
    monthly_request_limit = serializers.IntegerField(min_value=0, max_value=100_000, required=False)
    monthly_token_limit = serializers.IntegerField(
        min_value=0, max_value=500_000_000, required=False
    )
    max_output_tokens = serializers.IntegerField(min_value=64, max_value=8192, required=False)
    user_hourly_limit = serializers.IntegerField(min_value=1, max_value=1000, required=False)


class AIKeyInputSerializer(serializers.Serializer[dict[str, Any]]):
    provider = serializers.ChoiceField(choices=[AIProviderKind.GROQ], default=AIProviderKind.GROQ)
    api_key = serializers.CharField(max_length=200, trim_whitespace=True)


class ConfirmInputSerializer(serializers.Serializer[dict[str, Any]]):
    confirmed = serializers.BooleanField()


class AIRequestInputSerializer(serializers.Serializer[dict[str, Any]]):
    feature = serializers.ChoiceField(choices=AIFeature.choices)
    subject_id = serializers.UUIDField(required=False, allow_null=True)
    options = serializers.DictField(required=False)
    client_key = serializers.RegexField(
        r"^[A-Za-z0-9_-]{8,64}$", required=False, allow_blank=True, default=""
    )


class ApplyInputSerializer(serializers.Serializer[dict[str, Any]]):
    values = serializers.DictField(help_text="Field name → staff-reviewed value.")
    overwrite = serializers.BooleanField(default=False)


class AIReviewInputSerializer(serializers.Serializer[dict[str, Any]]):
    decision = serializers.ChoiceField(
        choices=[(AIReviewStatus.REJECTED, "Rejected"), (AIReviewStatus.USED, "Used")]
    )


class AIRequestSerializer(serializers.ModelSerializer[AIRequest]):
    requested_by_name = serializers.SerializerMethodField()
    error_text = serializers.SerializerMethodField()
    result = serializers.SerializerMethodField()
    result_available = serializers.SerializerMethodField()

    class Meta:
        model = AIRequest
        fields = (
            "id",
            "feature",
            "status",
            "review_status",
            "provider",
            "model",
            "is_test",
            "subject_type",
            "subject_id",
            "options",
            "context_sources",
            "result",
            "result_available",
            "error_code",
            "error_text",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "latency_ms",
            "applied_fields",
            "requested_by_name",
            "created_at",
            "started_at",
            "completed_at",
            "reviewed_at",
        )

    def _can_see_result(self, obj: AIRequest) -> bool:
        viewer = self.context.get("viewer")
        return viewer is not None and obj.requested_by_id == viewer.pk

    def get_requested_by_name(self, obj: AIRequest) -> str:
        user = obj.requested_by
        return user.full_name or user.email or ""

    def get_error_text(self, obj: AIRequest) -> str:
        return error_text(obj.error_code)

    def get_result(self, obj: AIRequest) -> dict[str, Any] | None:
        result: dict[str, Any] | None = obj.result if self._can_see_result(obj) else None
        return result

    def get_result_available(self, obj: AIRequest) -> bool:
        return obj.result is not None


class UsageFeatureSerializer(serializers.Serializer[Any]):
    feature = serializers.CharField()
    requests = serializers.IntegerField()
    completed = serializers.IntegerField()
    failed = serializers.IntegerField()
    tokens = serializers.IntegerField()


class UsageDaySerializer(serializers.Serializer[Any]):
    date = serializers.DateField()
    requests = serializers.IntegerField()
    tokens = serializers.IntegerField()


class AIUsageSerializer(serializers.Serializer[Any]):
    month_start = serializers.DateTimeField()
    month_end = serializers.DateTimeField()
    requests = serializers.IntegerField()
    request_limit = serializers.IntegerField()
    tokens_input = serializers.IntegerField()
    tokens_output = serializers.IntegerField()
    tokens_total = serializers.IntegerField()
    token_limit = serializers.IntegerField()
    requests_without_usage = serializers.IntegerField()
    failed = serializers.IntegerField()
    test_requests = serializers.IntegerField()
    by_status = serializers.DictField(child=serializers.IntegerField())
    by_feature = UsageFeatureSerializer(many=True)
    by_day = UsageDaySerializer(many=True)


class AIOverviewSerializer(serializers.Serializer[Any]):
    module_enabled = serializers.BooleanField()
    provider = serializers.CharField()
    status = serializers.CharField()
    text_model = serializers.CharField()
    vision_model = serializers.CharField()
    is_test = serializers.BooleanField()
    features = FeatureStateSerializer(many=True)
    can_manage_settings = serializers.BooleanField()
    usage = AIUsageSerializer(allow_null=True)
    recent = AIRequestSerializer(many=True)
    provider_data_notice = serializers.CharField()


class RequestQuerySerializer(serializers.Serializer[dict[str, Any]]):
    feature = serializers.ChoiceField(choices=AIFeature.choices, required=False)
    subject_id = serializers.UUIDField(required=False)
    scope = serializers.ChoiceField(choices=[("mine", "Mine"), ("all", "All")], default="mine")
