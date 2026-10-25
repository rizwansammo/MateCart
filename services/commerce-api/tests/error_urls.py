from django.http import HttpRequest, HttpResponse
from django.urls import path

from config.urls import handler404, handler500, urlpatterns

__all__ = ["handler404", "handler500", "urlpatterns"]


def explode(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("boom: internal detail that must never reach the client")


urlpatterns = [*urlpatterns, path("boom/", explode)]
