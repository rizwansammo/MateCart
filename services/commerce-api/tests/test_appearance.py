"""Phase 05: store appearance, modules, content, public storefront config and isolation."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction

from commerce.audit.models import AuditEvent
from commerce.content.models import ContentPage, HomeSection
from commerce.staff.permissions import StaffRole
from commerce.stores import colors, modules
from commerce.stores.models import StoreModule, StoreSettings, StoreTheme
from commerce.tenancy.context import tenant_context
from commerce.tenancy.exceptions import CrossTenantAccessError
from commerce.tenancy.models import Tenant

from .catalog_helpers import png_bytes, set_preset, sign_in_staff, simple_product, stored_asset
from .factories import make_store_settings
from .helpers import Api

pytestmark = pytest.mark.usefixtures("store")

APPEARANCE = "/api/manage/store/appearance/"
MODULES = "/api/manage/store/modules/"
PAGES = "/api/manage/content/pages/"
SECTIONS = "/api/manage/content/sections/"


@pytest.fixture
def editor(api: Api, deployment_tenant: Tenant) -> Api:
    sign_in_staff(api, deployment_tenant, StaffRole.CONTENT_EDITOR, "editor@example.com")
    return api


@pytest.fixture
def manager(make_api: Callable[[], Api], deployment_tenant: Tenant) -> Api:
    browser = make_api()
    sign_in_staff(browser, deployment_tenant, StaffRole.MANAGER, "manager@example.com")
    return browser


def upload(api: Api, size: tuple[int, int]) -> str:
    response = api.upload(
        "/api/manage/media/assets/upload/",
        {"file": SimpleUploadedFile("logo.png", png_bytes(size), content_type="image/png")},
    )
    assert response.status_code == 201, response.json()
    return str(response.json()["id"])


# --- Colours --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("color", "expected_on", "readable"),
    [
        ("#2A2620", colors.WHITE, True),
        ("#00B050", colors.INK, True),
        ("#FFF3DC", colors.INK, True),
        ("#777777", colors.WHITE, False),
    ],
)
def test_brand_colours_get_a_readable_text_colour(
    color: str, expected_on: str, readable: bool
) -> None:
    assert colors.on_color(color) == expected_on
    assert colors.readable(color) is readable


# --- Appearance -----------------------------------------------------------------------------------


def test_appearance_defaults_follow_the_preset(editor: Api) -> None:
    body = editor.get(APPEARANCE).json()
    assert (body["preset"], body["preset_label"], body["primary_color"]) == (
        "general",
        "General / Multi-category",
        "",
    )
    assert body["logo"] is None
    assert body["can_rename"] is False


def test_editors_update_branding_and_reset_colours(editor: Api, deployment_tenant: Tenant) -> None:
    logo = upload(editor, (120, 40))
    favicon = upload(editor, (64, 64))
    saved = editor.patch(
        APPEARANCE,
        {
            "primary_color": "#1f6f4a",
            "accent_color": "#C8312B",
            "logo": logo,
            "favicon": favicon,
            "tagline": "  Everyday   essentials ",
            "translations": {"bn": {"tagline": "প্রতিদিনের প্রয়োজন"}},
        },
    )
    assert saved.status_code == 200, saved.json()
    body = saved.json()
    assert (body["primary_color"], body["tagline"]) == ("#1F6F4A", "Everyday essentials")
    assert body["logo"]["id"] == logo
    reset = editor.post("/api/manage/store/appearance/reset/").json()
    assert (reset["primary_color"], reset["accent_color"], reset["logo"]["id"]) == ("", "", logo)
    with tenant_context(deployment_tenant):
        actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"store.theme.created", "store.theme.reset"} <= actions


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"primary_color": "red"}, "primary_color"),
        ({"primary_color": "#777777"}, "primary_color"),
        ({"accent_color": "#12345"}, "accent_color"),
        ({"translations": {"fr": {"tagline": "x"}}}, "translations"),
        ({"tagline": "x" * 161}, "tagline"),
    ],
)
def test_invalid_overrides_are_refused(editor: Api, payload: dict[str, Any], field: str) -> None:
    response = editor.patch(APPEARANCE, payload)
    assert response.status_code == 400
    assert field in response.json()["error"]["details"]


def test_logo_and_favicon_shapes_are_checked(editor: Api) -> None:
    assert (
        "favicon"
        in editor.patch(APPEARANCE, {"favicon": upload(editor, (64, 32))}).json()["error"][
            "details"
        ]
    )
    assert (
        "logo"
        in editor.patch(APPEARANCE, {"logo": upload(editor, (120, 20))}).json()["error"]["details"]
    )


def test_renaming_the_store_needs_store_settings(
    editor: Api, manager: Api, deployment_tenant: Tenant
) -> None:
    assert editor.patch(APPEARANCE, {"store_name": "Renamed"}).status_code == 403
    assert manager.get(APPEARANCE).json()["can_rename"] is True
    assert manager.patch(APPEARANCE, {"store_name": "Renamed"}).json()["store_name"] == "Renamed"
    with tenant_context(deployment_tenant):
        assert StoreSettings.objects.get().name == "Renamed"


def test_appearance_needs_content_permission(
    api: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    assert api.get(APPEARANCE).status_code == 401
    handler = make_api()
    sign_in_staff(handler, deployment_tenant, StaffRole.ORDER_HANDLER, "orders@example.com")
    assert handler.get(APPEARANCE).status_code == 403
    assert handler.get(PAGES).status_code == 403
    assert handler.get(MODULES).status_code == 403


def test_branding_images_are_public_only_while_assigned(
    editor: Api, make_api: Callable[[], Api]
) -> None:
    logo = upload(editor, (120, 40))
    visitor = make_api()
    url = f"/api/catalog/media/{logo}/"
    assert visitor.get(url).status_code == 404
    editor.patch(APPEARANCE, {"logo": logo})
    assert visitor.get(url).status_code == 200
    config = visitor.get("/api/storefront/config/").json()
    assert config["theme"]["logo_url"] == url
    editor.patch(APPEARANCE, {"logo": None})
    assert visitor.get(url).status_code == 404


# --- Modules --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset", "on", "off"),
    [
        ("fashion", {"size_guides"}, {"batch_expiry", "serial_numbers", "book_details"}),
        ("grocery", {"batch_expiry"}, {"size_guides", "serial_numbers"}),
        ("electronics", {"serial_numbers"}, {"batch_expiry", "book_details"}),
        ("books", {"book_details"}, {"size_guides", "serial_numbers"}),
        ("general", set(), {"size_guides", "batch_expiry", "serial_numbers", "book_details"}),
    ],
)
def test_module_defaults_follow_the_preset(
    deployment_tenant: Tenant, preset: str, on: set[str], off: set[str]
) -> None:
    set_preset(deployment_tenant, preset)
    with tenant_context(deployment_tenant):
        enabled = set(modules.enabled_modules())
    assert {
        "catalog",
        "inventory",
        "content_pages",
        "customer_accounts",
        "bilingual_content",
    } <= enabled
    assert on <= enabled
    assert not off & enabled
    planned = {spec.key for spec in modules.REGISTRY if not spec.available}
    assert not planned & enabled


def test_module_overrides_and_their_limits(manager: Api) -> None:
    assert manager.put(f"{MODULES}size_guides/", {"enabled": True}).json()["enabled"] is True
    assert manager.put(f"{MODULES}size_guides/", {"enabled": None}).json()["enabled"] is False
    refusals = {
        "cart_checkout": "module_unavailable",
        "delivery_slots": "module_unavailable",
        "catalog": "module_core",
        "nonexistent": "module_unknown",
    }
    for key, code in refusals.items():
        response = manager.put(f"{MODULES}{key}/", {"enabled": True})
        assert response.json()["error"]["code"] == code, key
    rows = {row["key"]: row for row in manager.get(MODULES).json()}
    assert rows["cart_checkout"]["available"] is False
    assert rows["emi"]["enabled"] is False


def test_modules_not_for_the_preset_cannot_be_enabled(
    manager: Api, deployment_tenant: Tenant
) -> None:
    set_preset(deployment_tenant, "fashion")
    response = manager.put(f"{MODULES}serial_numbers/", {"enabled": True})
    assert response.json()["error"]["code"] == "module_not_applicable"


def test_missing_prerequisites_block_a_module(
    deployment_tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = modules.ModuleSpec("gift_wrap", "Gift wrap", "", "05", True, requires=("size_guides",))
    monkeypatch.setattr(modules, "REGISTRY", (*modules.REGISTRY, spec))
    monkeypatch.setitem(modules.BY_KEY, "gift_wrap", spec)
    with tenant_context(deployment_tenant):
        with pytest.raises(modules.ModuleError, match="Size guides"):
            modules.set_module(key="gift_wrap", enabled=True, actor=None)
        StoreModule.objects.create(
            key="gift_wrap", enabled=True
        )  # e.g. set before a prerequisite was turned off
        state = next(s for s in modules.module_states() if s.spec.key == "gift_wrap")
    assert (state.enabled, state.blocked_by) == (False, ("size_guides",))


# --- Content --------------------------------------------------------------------------------------


def test_pages_publish_and_only_published_pages_are_public(
    editor: Api, make_api: Callable[[], Api]
) -> None:
    created = editor.post(PAGES, {"title": "Return & refund policy", "page_type": "policy"})
    assert created.status_code == 201
    page = created.json()
    assert (page["slug"], page["status"]) == ("return-refund-policy", "draft")
    visitor = make_api()
    assert visitor.get("/api/storefront/pages/return-refund-policy/").status_code == 404
    refused = editor.post(f"{PAGES}{page['id']}/status/", {"status": "published"})
    assert refused.json()["error"]["details"] == {"body": ["Write the page before publishing it."]}
    editor.patch(f"{PAGES}{page['id']}/", {"body": "Return unused items within 7 days."})
    assert (
        editor.post(f"{PAGES}{page['id']}/status/", {"status": "published"}).json()["status"]
        == "published"
    )
    public = visitor.get("/api/storefront/pages/return-refund-policy/").json()
    assert public["body"] == "Return unused items within 7 days."
    footer = visitor.get("/api/storefront/config/").json()["footer_pages"]
    assert footer == [
        {
            "slug": "return-refund-policy",
            "title": "Return & refund policy",
            "page_type": "policy",
            "translations": {},
        }
    ]
    # Footer links carry translated titles only (Phase 06), never page bodies.
    editor.patch(
        f"{PAGES}{page['id']}/",
        {"translations": {"bn": {"title": "ফেরত নীতি", "body": "অব্যবহৃত পণ্য ফেরত দিন।"}}},
    )
    footer = visitor.get("/api/storefront/config/").json()["footer_pages"]
    assert footer[0]["translations"] == {"bn": {"title": "ফেরত নীতি"}}


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "About", "body": "<script>alert(1)</script>"},
        {"title": "<b>About</b>"},
        {"title": "About", "slug": "About Us"},
        {"title": "About", "slug": "../admin"},
        {"title": "About", "translations": {"bn": {"body": "<img src=x onerror=alert(1)>"}}},
    ],
)
def test_unsafe_content_and_slugs_are_refused(editor: Api, payload: dict[str, Any]) -> None:
    assert editor.post(PAGES, payload).status_code == 400


def test_page_slugs_are_unique(editor: Api) -> None:
    editor.post(PAGES, {"title": "About", "slug": "about"})
    duplicate = editor.post(PAGES, {"title": "About again", "slug": "about"})
    assert duplicate.json()["error"]["details"] == {"slug": ["Another page uses this address."]}


def test_homepage_sections_default_order_and_publish(
    editor: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    set_preset(deployment_tenant, "fashion")
    created = editor.post(f"{SECTIONS}defaults/")
    assert created.status_code == 201
    sections = created.json()
    assert [s["section_type"] for s in sections] == [
        "hero",
        "category_showcase",
        "collection_highlight",
        "brand_story",
        "trust_strip",
    ]
    assert editor.post(f"{SECTIONS}defaults/").json()["error"]["code"] == "sections_exist"
    highlight = sections[2]
    assert highlight["is_enabled"] is False
    enable = editor.patch(f"{SECTIONS}{highlight['id']}/", {"is_enabled": True})
    assert "collection" in enable.json()["error"]["details"]

    reordered = [sections[4]["id"], *[s["id"] for s in sections[:4]]]
    after = editor.put(f"{SECTIONS}order/", {"sections": reordered}).json()
    assert [s["id"] for s in after] == reordered
    assert [s["position"] for s in after] == [0, 1, 2, 3, 4]
    partial = editor.put(f"{SECTIONS}order/", {"sections": reordered[:2]})
    assert partial.status_code == 400

    editor.patch(f"{SECTIONS}{sections[0]['id']}/", {"is_enabled": False})
    public = make_api().get("/api/storefront/home/").json()
    assert [s["section_type"] for s in public] == [
        "trust_strip",
        "category_showcase",
        "brand_story",
    ]


def test_section_links_and_media_are_checked(editor: Api, deployment_tenant: Tenant) -> None:
    hero = {"section_type": "hero", "title": "Hero"}
    for cta_url in ("https://evil.example", "//evil.example", "javascript:alert(1)"):
        response = editor.post(SECTIONS, {**hero, "cta_label": "Shop", "cta_url": cta_url})
        assert response.json()["error"]["details"].keys() == {"cta_url"}, cta_url
    linked = editor.post(SECTIONS, {**hero, "cta_label": "Shop", "cta_url": "/collections/new-in"})
    assert linked.status_code == 201
    document = stored_asset(deployment_tenant, kind="document")
    refused = editor.post(SECTIONS, {**hero, "image": str(document.pk)})
    assert refused.json()["error"]["details"] == {"image": ["Choose an image."]}


# --- Public configuration -------------------------------------------------------------------------


def test_public_config_exposes_only_storefront_data(
    editor: Api, manager: Api, make_api: Callable[[], Api]
) -> None:
    manager.put(f"{MODULES}size_guides/", {"enabled": True})
    editor.patch(APPEARANCE, {"primary_color": "#1F6F4A", "tagline": "Hello"})
    editor.post(PAGES, {"title": "Draft page", "body": "Not yet."})
    config = make_api().get("/api/storefront/config/").json()
    assert set(config) == {
        "store",
        "preset",
        "active_theme",
        "theme",
        "modules",
        "footer_pages",
        "translations",
        "local_date",
    }
    assert config["theme"]["primary_color"] == "#1F6F4A"
    assert config["store"]["tagline"] == "Hello"
    assert "size_guides" in config["modules"]
    assert config["footer_pages"] == []
    text = str(config)
    for private in ("editor@example.com", "configured", "can_rename", "updated_at", "audit"):
        assert private not in text


# --- Business preset changes ----------------------------------------------------------------------


def test_preset_changes_keep_products_valid(deployment_tenant: Tenant) -> None:
    simple_product(deployment_tenant, sku="BOOK-1", name="A book", product_type="book")
    with pytest.raises(CommandError, match="book"):
        call_command("change_store_preset", "--preset", "fashion")
    out = StringIO()
    call_command("change_store_preset", "--preset", "books", stdout=out)
    assert "changed from general to books" in out.getvalue()
    with tenant_context(deployment_tenant):
        assert StoreSettings.objects.get().preset == "books"
        assert AuditEvent.objects.filter(action="store.preset.changed").count() == 1


# --- Isolation ------------------------------------------------------------------------------------


def test_appearance_and_content_are_isolated_per_store(
    editor: Api, deployment_tenant: Tenant, tenant_b: Tenant
) -> None:
    make_store_settings(tenant_b, name="Other store")
    foreign_logo = stored_asset(tenant_b)
    with tenant_context(tenant_b):
        theme = StoreTheme.objects.create(
            primary_color="#123456", tagline="Theirs", logo=foreign_logo
        )
        page = ContentPage.objects.create(slug="about", title="Theirs", body="x")
        section = HomeSection.objects.create(section_type="hero", title="Theirs")
        module = StoreModule.objects.create(key="size_guides", enabled=True)

    assert editor.get(APPEARANCE).json()["tagline"] == ""
    assert editor.patch(APPEARANCE, {"logo": str(foreign_logo.pk)}).status_code == 400
    assert editor.get(f"{PAGES}{page.pk}/").status_code == 404
    assert editor.patch(f"{SECTIONS}{section.pk}/", {"title": "Mine"}).status_code == 404
    with tenant_context(deployment_tenant):
        assert "size_guides" not in modules.enabled_modules()
        for row in (theme, page, section, module):
            assert not type(row).objects.filter(pk=row.pk).exists()
            with pytest.raises(CrossTenantAccessError):
                row.refresh_from_db()
        own = StoreTheme.objects.create()

        def point_logo_at_other_store() -> None:
            StoreTheme.objects.filter(pk=own.pk).update(logo_id=foreign_logo.pk)
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

        with pytest.raises(IntegrityError), transaction.atomic():
            point_logo_at_other_store()


# --- Phase 05.1 corrections -----------------------------------------------------------------------


def _published_page(editor: Api) -> dict[str, Any]:
    page = editor.post(PAGES, {"title": "Shipping", "body": "Delivery in 2 days."}).json()
    editor.post(f"{PAGES}{page['id']}/status/", {"status": "published"})
    return dict(page)


@pytest.mark.parametrize("body", ["", "   \n\t  "])
def test_published_pages_cannot_be_emptied(
    editor: Api, make_api: Callable[[], Api], body: str
) -> None:
    page = _published_page(editor)
    refused = editor.patch(f"{PAGES}{page['id']}/", {"body": body, "title": "Changed"})
    assert refused.status_code == 400
    assert "body" in refused.json()["error"]["details"]
    stored = editor.get(f"{PAGES}{page['id']}/").json()
    assert (stored["body"], stored["title"], stored["status"]) == (
        "Delivery in 2 days.",
        "Shipping",
        "published",
    )
    public = make_api().get("/api/storefront/pages/shipping/").json()
    assert public["body"] == "Delivery in 2 days."


def test_drafts_may_be_empty_and_published_pages_still_edit(editor: Api) -> None:
    draft = editor.post(PAGES, {"title": "About"}).json()
    assert editor.patch(f"{PAGES}{draft['id']}/", {"body": ""}).status_code == 200
    page = _published_page(editor)
    updated = editor.patch(f"{PAGES}{page['id']}/", {"body": "Delivery in 1 day."})
    assert (updated.status_code, updated.json()["status"]) == (200, "published")


def test_sections_need_a_title_to_be_shown(editor: Api, make_api: Callable[[], Api]) -> None:
    hidden = editor.post(SECTIONS, {"section_type": "brand_story", "is_enabled": False})
    assert hidden.status_code == 201
    blank_shown = editor.post(SECTIONS, {"section_type": "hero", "title": "   "})
    assert blank_shown.json()["error"]["details"] == {
        "title": ["Give the section a title before showing it."]
    }
    section_id = hidden.json()["id"]
    refused = editor.patch(f"{SECTIONS}{section_id}/", {"is_enabled": True})
    assert "title" in refused.json()["error"]["details"]
    assert make_api().get("/api/storefront/home/").json() == []

    shown = editor.patch(f"{SECTIONS}{section_id}/", {"title": "Our story", "is_enabled": True})
    assert shown.json()["is_enabled"] is True
    cleared = editor.patch(f"{SECTIONS}{section_id}/", {"title": ""})
    assert "title" in cleared.json()["error"]["details"]
    public = make_api().get("/api/storefront/home/").json()
    assert [(s["section_type"], s["title"]) for s in public] == [("brand_story", "Our story")]


@pytest.mark.parametrize("preset", ["fashion", "grocery", "electronics", "general", "books"])
def test_preset_default_sections_are_valid_to_show(
    editor: Api, deployment_tenant: Tenant, preset: str
) -> None:
    set_preset(deployment_tenant, preset)
    sections = editor.post(f"{SECTIONS}defaults/").json()
    for section in sections:
        if section["section_type"] != "collection_highlight":
            response = editor.patch(f"{SECTIONS}{section['id']}/", {"is_enabled": True})
            assert response.status_code == 200, (preset, section["section_type"])


@pytest.mark.parametrize(("default", "other"), [("en", "bn"), ("bn", "en")])
def test_translations_round_trip_for_either_default_language(
    editor: Api, deployment_tenant: Tenant, default: str, other: str
) -> None:
    with tenant_context(deployment_tenant):
        StoreSettings.objects.update(default_language=default, supported_languages=[default, other])
    saved = editor.patch(APPEARANCE, {"translations": {other: {"tagline": "Translated"}}})
    assert saved.status_code == 200, saved.json()
    assert editor.get(APPEARANCE).json()["translations"] == {other: {"tagline": "Translated"}}
    assert (
        editor.patch(APPEARANCE, {"translations": {default: {"tagline": "x"}}}).status_code == 400
    )
    page = editor.post(
        PAGES, {"title": "About", "translations": {other: {"title": "T", "body": "B"}}}
    )
    assert editor.get(f"{PAGES}{page.json()['id']}/").json()["translations"] == {
        other: {"title": "T", "body": "B"}
    }


@pytest.mark.parametrize(
    ("zone", "instant", "local"),
    [
        # 00:30 on 27 October in Dhaka (UTC+6) while UTC is still 26 October.
        ("Asia/Dhaka", datetime(2026, 10, 26, 18, 30, tzinfo=UTC), date(2026, 10, 27)),
        # 22:00 on 26 October in New York (UTC-4) while UTC is already 27 October.
        ("America/New_York", datetime(2026, 10, 27, 2, 0, tzinfo=UTC), date(2026, 10, 26)),
    ],
)
def test_public_config_gives_the_store_local_date(
    editor: Api,
    make_api: Callable[[], Api],
    deployment_tenant: Tenant,
    monkeypatch: pytest.MonkeyPatch,
    zone: str,
    instant: datetime,
    local: date,
) -> None:
    """Phase 06.1: pre-order labels use the same calendar day as availability (store_today)."""
    assert instant.date() != local  # the case the storefront previously got wrong
    with tenant_context(deployment_tenant):
        StoreSettings.objects.update(timezone=zone)
    for sku, release in (("ON-DAY", local), ("NEXT-DAY", local + timedelta(days=1))):
        simple_product(
            deployment_tenant, sku=sku, name=sku, publish=True, variant={"release_date": release}
        )
    simple_product(deployment_tenant, sku="PLAIN", name="Plain", publish=True)
    monkeypatch.setattr("django.utils.timezone.now", lambda: instant)
    visitor = make_api()

    assert visitor.get("/api/storefront/config/").json()["local_date"] == local.isoformat()
    listing = visitor.get("/api/catalog/products/").json()["results"]
    in_stock = {row["slug"]: row["in_stock"] for row in listing}
    # Released today (store time): no longer a pre-order, and without stock it is unavailable.
    # Releasing tomorrow: still a pre-order. Plain unstocked products are unchanged.
    assert in_stock == {"on-day": False, "next-day": True, "plain": False}
