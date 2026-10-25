"""Page-number pagination for list endpoints: ``?page=1&page_size=25`` (at most 100 per page).

Responses are ``{"count", "page", "page_size", "results"}``. Callers order the queryset first, so
pages are deterministic (every ordering ends with the primary key as a tie-breaker).
"""

from functools import cache
from typing import Any

from django.db import models
from rest_framework import serializers
from rest_framework.request import Request

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class PageQuerySerializer(serializers.Serializer[dict[str, Any]]):
    page = serializers.IntegerField(min_value=1, max_value=100_000, default=1)
    page_size = serializers.IntegerField(
        min_value=1, max_value=MAX_PAGE_SIZE, default=DEFAULT_PAGE_SIZE
    )


def paginate[M: models.Model](
    queryset: models.QuerySet[M], request: Request
) -> tuple[list[M], dict[str, int]]:
    params = PageQuerySerializer(data=request.query_params)
    params.is_valid(raise_exception=True)
    page: int = params.validated_data["page"]
    size: int = params.validated_data["page_size"]
    count = queryset.count()
    start = (page - 1) * size
    return list(queryset[start : start + size]), {"count": count, "page": page, "page_size": size}


@cache
def paginated(item: type[serializers.Serializer[Any]]) -> type[serializers.Serializer[Any]]:
    """The OpenAPI response shape of a page of ``item`` (one named component per item type)."""
    name = item.__name__.removesuffix("Serializer")
    return type(
        f"{name}PageSerializer",
        (serializers.Serializer,),
        {
            "count": serializers.IntegerField(),
            "page": serializers.IntegerField(),
            "page_size": serializers.IntegerField(),
            "results": item(many=True),
            "__module__": item.__module__,
        },
    )
