"""Public, read-only storefront configuration under /api/storefront/ (anonymous GET only).

Returns only what the customer-facing store renders: store name and brand texts, the preset,
brand colours and logo URLs, effective (implemented) modules, published pages and enabled
homepage sections. Never staff, audit, draft, module overrides or unassigned media (D-064).
"""

from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from commerce.catalog.serializers import public_media_url
from commerce.core.domain import found
from commerce.identity.auth import CsrfProtectedAPIView
from commerce.inventory.services import store_today
from commerce.stores import appearance, modules

from .models import ContentPage, HomeSection, PageStatus, PageType, SectionType

TAGS = ["storefront"]


class PublicView(CsrfProtectedAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    http_method_names = ("get", "head", "options")


def _url(asset_id: Any) -> str | None:
    return public_media_url(asset_id) if asset_id else None


class StoreInfoSerializer(serializers.Serializer[Any]):
    name = serializers.CharField()
    tagline = serializers.CharField()
    description = serializers.CharField()
    announcement = serializers.CharField()
    footer_text = serializers.CharField()
    contact_email = serializers.CharField()
    contact_phone = serializers.CharField()
    currency = serializers.CharField()
    default_language = serializers.CharField()
    languages = serializers.ListField(child=serializers.CharField())


class ThemeSerializer(serializers.Serializer[Any]):
    primary_color = serializers.CharField(allow_null=True)
    accent_color = serializers.CharField(allow_null=True)
    logo_url = serializers.CharField(allow_null=True)
    logo_dark_url = serializers.CharField(allow_null=True)
    favicon_url = serializers.CharField(allow_null=True)
    show_name_with_logo = serializers.BooleanField()


class FooterPageSerializer(serializers.Serializer[Any]):
    slug = serializers.CharField()
    title = serializers.CharField()
    page_type = serializers.ChoiceField(choices=PageType.choices)
    # Phase 06: footer links follow the customer's language (title only; never the body).
    translations = serializers.SerializerMethodField()

    def get_translations(self, page: ContentPage) -> dict[str, dict[str, str]]:
        return {
            language: {"title": texts["title"]}
            for language, texts in page.translations.items()
            if texts.get("title")
        }


class StorefrontConfigSerializer(serializers.Serializer[Any]):
    store = StoreInfoSerializer()
    preset = serializers.CharField()
    theme = ThemeSerializer()
    modules = serializers.ListField(child=serializers.CharField())
    footer_pages = FooterPageSerializer(many=True)
    translations = serializers.JSONField()
    # Phase 06.1: today's date in the store's time zone (store_today), so storefront labels such
    # as pre-orders use the same calendar day as availability. A date only, never the time.
    local_date = serializers.DateField()


class PublicSectionSerializer(serializers.Serializer[Any]):
    section_type = serializers.ChoiceField(choices=SectionType.choices)
    title = serializers.CharField()
    subtitle = serializers.CharField()
    body = serializers.CharField()
    image_url = serializers.CharField(allow_null=True)
    collection = serializers.DictField(allow_null=True)
    cta_label = serializers.CharField()
    cta_url = serializers.CharField()
    translations = serializers.JSONField()


class PublicPageSerializer(serializers.Serializer[Any]):
    slug = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    page_type = serializers.ChoiceField(choices=PageType.choices)
    published_at = serializers.DateTimeField()
    translations = serializers.JSONField()


def _published() -> Any:
    return ContentPage.objects.filter(status=PageStatus.PUBLISHED)


class StorefrontConfigView(PublicView):
    @extend_schema(
        operation_id="storefront_config", tags=TAGS, responses={200: StorefrontConfigSerializer}
    )
    def get(self, request: Request) -> Response:
        settings = appearance.store_settings()
        theme = appearance.current_theme()
        payload = {
            "store": {
                "name": settings.name,
                "tagline": theme.tagline,
                "description": theme.description,
                "announcement": theme.announcement,
                "footer_text": theme.footer_text,
                "contact_email": settings.contact_email,
                "contact_phone": settings.contact_phone,
                "currency": settings.default_currency,
                "default_language": settings.default_language,
                "languages": list(settings.supported_languages),
            },
            "preset": settings.preset,
            "theme": {
                "primary_color": theme.primary_color or None,
                "accent_color": theme.accent_color or None,
                "logo_url": _url(theme.logo_id),
                "logo_dark_url": _url(theme.logo_dark_id),
                "favicon_url": _url(theme.favicon_id),
                "show_name_with_logo": theme.show_name_with_logo,
            },
            "modules": modules.enabled_modules(),
            "footer_pages": _published()
            .filter(show_in_footer=True)
            .order_by("position", "title", "pk"),
            "translations": theme.translations,
            "local_date": store_today(),
        }
        return Response(StorefrontConfigSerializer(payload).data)


class StorefrontHomeView(PublicView):
    @extend_schema(
        operation_id="storefront_home",
        tags=TAGS,
        responses={200: PublicSectionSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        sections = (
            HomeSection.objects.filter(is_enabled=True)
            .select_related("collection")
            .order_by("position", "pk")
        )
        rows = []
        for section in sections:
            collection = section.collection
            if section.section_type == SectionType.COLLECTION_HIGHLIGHT and (
                collection is None or not collection.is_active
            ):
                continue  # an inactive collection is not advertised
            rows.append(
                {
                    "section_type": section.section_type,
                    "title": section.title,
                    "subtitle": section.subtitle,
                    "body": section.body,
                    "image_url": _url(section.image_id),
                    "collection": {"name": collection.name, "slug": collection.slug}
                    if collection is not None
                    else None,
                    "cta_label": section.cta_label,
                    "cta_url": section.cta_url,
                    "translations": section.translations,
                }
            )
        return Response(PublicSectionSerializer(rows, many=True).data)


class StorefrontPageView(PublicView):
    @extend_schema(operation_id="storefront_page", tags=TAGS, responses={200: PublicPageSerializer})
    def get(self, request: Request, slug: str) -> Response:
        page = found(ContentPage, slug=slug, status=PageStatus.PUBLISHED)
        return Response(PublicPageSerializer(page).data)
