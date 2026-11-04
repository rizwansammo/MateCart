"""Search engine settings (D-085).

Indexing needs all three: the deployment opts in (``STOREFRONT_INDEXING=allow`` on the web app),
the deployment has a trusted public HTTPS address (``STOREFRONT_PUBLIC_URL``), and the merchant
has not switched indexing off here. The request's Host header is never used for any of it.

Metadata is plain text with length limits; the storefront escapes it and builds JSON-LD with
``<`` escaped, so merchant text can never become markup. Sitemaps list only published, active
records, never accounts, carts, checkout, orders, the dashboard or the API.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Model
from rest_framework.exceptions import PermissionDenied, ValidationError

from commerce.audit.services import field_changes, record_audit_event
from commerce.catalog.models import Category, Collection, Product, ProductStatus
from commerce.content.models import ContentPage, PageStatus
from commerce.core.domain import ConflictError
from commerce.identity.models import User
from commerce.media.models import MediaAsset

from .models import SeoSettings

SETTINGS_FIELDS = ["home_title", "home_description", "social_image", "allow_indexing"]
SITEMAP_PAGE_SIZE = 5000
OVERRIDE_MODELS: dict[str, type[Model]] = {
    "category": Category,
    "collection": Collection,
    "page": ContentPage,
}


def current() -> SeoSettings:
    found = SeoSettings.objects.select_related("social_image").first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return SeoSettings.objects.create()
    except IntegrityError:
        existing = SeoSettings.objects.first()
        if existing is None:
            raise
        return existing


def _clean(value: Any, field: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        raise ValidationError({field: [f"Use at most {limit} characters."]})
    if "<" in text or ">" in text:
        raise ValidationError({field: ["Use plain text without < or >."]})
    return text


def save(*, data: Mapping[str, Any], actor: User, can_change_indexing: bool) -> SeoSettings:
    with transaction.atomic():
        row = SeoSettings.objects.select_for_update().get(pk=current().pk)
        before = {
            "home_title": row.home_title,
            "home_description": row.home_description,
            "social_image": str(row.social_image_id or ""),
            "allow_indexing": row.allow_indexing,
        }
        if "home_title" in data:
            row.home_title = _clean(data["home_title"], "home_title", 70)
        if "home_description" in data:
            row.home_description = _clean(data["home_description"], "home_description", 320)
        if "social_image" in data:
            asset_id = data["social_image"]
            if asset_id is None:
                row.social_image = None
            else:
                asset = MediaAsset.objects.filter(pk=asset_id).first()
                if asset is None or asset.width is None:
                    raise ValidationError({"social_image": ["Choose an uploaded image."]})
                row.social_image = asset
        if "allow_indexing" in data and data["allow_indexing"] != row.allow_indexing:
            if not can_change_indexing:
                raise PermissionDenied("Changing search indexing needs store settings access.")
            row.allow_indexing = bool(data["allow_indexing"])
        row.save()
        after = {
            "home_title": row.home_title,
            "home_description": row.home_description,
            "social_image": str(row.social_image_id or ""),
            "allow_indexing": row.allow_indexing,
        }
        record_audit_event(
            action="marketing.seo.updated",
            resource=row,
            actor=actor,
            changes=field_changes(before, after, fields=SETTINGS_FIELDS),
        )
        return row


def set_override(*, kind: str, pk: Any, data: Mapping[str, Any], actor: User) -> Model:
    model = OVERRIDE_MODELS.get(kind)
    if model is None:
        raise ValidationError({"kind": ["Unknown kind."]})
    with transaction.atomic():
        target = model._default_manager.select_for_update().filter(pk=pk).first()
        if target is None:
            raise ConflictError("This item does not exist.", "not_found")
        before = {"seo_title": target.seo_title, "seo_description": target.seo_description}  # type: ignore[attr-defined]
        if "seo_title" in data:
            target.seo_title = _clean(data["seo_title"], "seo_title", 70)  # type: ignore[attr-defined]
        if "seo_description" in data:
            target.seo_description = _clean(  # type: ignore[attr-defined]
                data["seo_description"], "seo_description", 320
            )
        target.save(update_fields=["seo_title", "seo_description", "updated_at"])
        record_audit_event(
            action=f"marketing.seo.{kind}_updated",
            resource=target,
            actor=actor,
            changes=field_changes(
                before,
                {"seo_title": target.seo_title, "seo_description": target.seo_description},  # type: ignore[attr-defined]
                fields=["seo_title", "seo_description"],
            ),
        )
        return target


def sitemap(section: str, page: int) -> dict[str, Any]:
    """One bounded slice of published, public records with their last change."""
    if section == "products":
        rows = (
            Product.objects.filter(status=ProductStatus.ACTIVE)
            .order_by("published_at", "pk")
            .values_list("slug", "updated_at")
        )
        count = rows.count()
        start = (page - 1) * SITEMAP_PAGE_SIZE
        products = [
            {"type": "product", "slug": slug, "updated_at": updated}
            for slug, updated in rows[start : start + SITEMAP_PAGE_SIZE]
        ]
        return {"count": count, "page_size": SITEMAP_PAGE_SIZE, "entries": products}
    if section != "pages":
        raise ValidationError({"section": ["Use pages or products."]})
    entries: list[dict[str, Any]] = []
    for kind, queryset in (
        ("category", Category.objects.filter(is_active=True)),
        ("collection", Collection.objects.filter(is_active=True)),
        ("page", ContentPage.objects.filter(status=PageStatus.PUBLISHED)),
    ):
        for slug, updated in queryset.order_by("slug").values_list("slug", "updated_at")[
            :SITEMAP_PAGE_SIZE
        ]:
            entries.append({"type": kind, "slug": slug, "updated_at": updated})
    published = Product.objects.filter(status=ProductStatus.ACTIVE).count()
    return {"count": published, "page_size": SITEMAP_PAGE_SIZE, "entries": entries}
