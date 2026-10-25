from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .health import run_readiness_checks
from .serializers import LivenessSerializer, ReadinessSerializer


@method_decorator(never_cache, name="dispatch")
class LivenessView(APIView):
    """The process is up and serving requests. Never touches dependencies."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(operation_id="health_live", tags=["health"], responses={200: LivenessSerializer})
    def get(self, request: Request) -> Response:
        return Response({"status": "ok", "service": settings.SERVICE_NAME})


@method_decorator(never_cache, name="dispatch")
class ReadinessView(APIView):
    """The service can do useful work: the database is reachable."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="health_ready",
        tags=["health"],
        responses={200: ReadinessSerializer, 503: ReadinessSerializer},
    )
    def get(self, request: Request) -> Response:
        checks = run_readiness_checks()
        ready = all(state == "ok" for state in checks.values())
        return Response(
            {
                "status": "ok" if ready else "unavailable",
                "service": settings.SERVICE_NAME,
                "checks": checks,
            },
            status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse({"error": {"code": "not_found", "message": "Not found."}}, status=404)


def server_error(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": "server_error", "message": "Internal server error."}}, status=500
    )
