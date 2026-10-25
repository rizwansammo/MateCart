from typing import Any

from rest_framework import serializers

CHECK_STATES = ("ok", "error")


class LivenessSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=["ok"])
    service = serializers.CharField()


class ReadinessChecksSerializer(serializers.Serializer[dict[str, Any]]):
    database = serializers.ChoiceField(choices=CHECK_STATES)


class ReadinessSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=["ok", "unavailable"])
    service = serializers.CharField()
    checks = ReadinessChecksSerializer()
