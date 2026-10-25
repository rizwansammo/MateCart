import django_stubs_ext
from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Cross-cutting foundations: health checks, request ids, logging and error handling."""

    name = "control.core"
    label = "core"
    verbose_name = "Core"

    def ready(self) -> None:
        from rest_framework import serializers

        # Lets typed code subclass generics such as Serializer[T] at runtime.
        django_stubs_ext.monkeypatch(extra_classes=[serializers.Serializer])
