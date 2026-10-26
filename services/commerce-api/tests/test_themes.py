"""Phase 06.2 (D-069): storefront themes are separate from presets and platform-assigned."""

import logging
from collections.abc import Callable
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.forms.models import model_to_dict
from rest_framework.exceptions import ValidationError

from commerce.audit.models import AuditEvent
from commerce.catalog.models import Product
from commerce.pricing.models import Price
from commerce.staff.permissions import StaffRole
from commerce.stores import services, themes
from commerce.stores.models import StorePreset, StoreSettings, StoreTheme
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import sign_in_staff, simple_product
from .factories import make_store_settings
from .helpers import Api

pytestmark = pytest.mark.usefixtures("store")  # a General store

APPEARANCE = "/api/manage/store/appearance/"
CONFIG = "/api/storefront/config/"


@pytest.fixture
def owner(api: Api, deployment_tenant: Tenant) -> Api:
    sign_in_staff(api, deployment_tenant, StaffRole.OWNER, "owner@example.com")
    return api


def settings_of(tenant: Tenant) -> StoreSettings:
    with tenant_context(tenant):
        return StoreSettings.objects.get()


# --- Catalogue ------------------------------------------------------------------------------------


@pytest.mark.parametrize("preset", StorePreset.values)
def test_every_preset_has_exactly_one_default_theme_of_its_own(preset: str) -> None:
    defaults = [t for t in themes.THEMES if t.preset == preset and t.is_default]
    assert len(defaults) == 1
    assert themes.default_theme(preset) == defaults[0].id
    assert themes.is_compatible(defaults[0].id, preset)


def test_theme_identifiers_are_stable_unique_and_belong_to_known_presets() -> None:
    assert themes.THEME_IDS == (
        "fashion-editorial",
        "grocery-quickshop",
        "electronics-spec-first",
        "general-marketplace",
        "books-literary",
    )
    assert len(set(themes.THEME_IDS)) == len(themes.THEME_IDS)
    for spec in themes.THEMES:
        assert themes.is_valid_id(spec.id)
        assert spec.preset in StorePreset.values
        assert spec.name != spec.id  # display names are never identifiers
    assert not themes.is_compatible("books-literary", "fashion")
    assert not themes.is_compatible("fashion-minimal", "fashion")
    with pytest.raises(LookupError):
        themes.default_theme("casino")


# --- Effective theme ------------------------------------------------------------------------------


def test_existing_stores_use_their_preset_default(make_api: Callable[[], Api]) -> None:
    config = make_api().get(CONFIG).json()
    assert (config["preset"], config["active_theme"]) == ("general", "general-marketplace")


@pytest.mark.parametrize("stored", ["books-literary", "retired-theme"])
def test_unusable_assignments_fall_back_within_the_preset(
    stored: str,
    deployment_tenant: Tenant,
    make_api: Callable[[], Api],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A row changed outside the supported path (or a theme removed in a release) never selects
    another preset's theme; operators see a warning, customers see the default design."""
    with tenant_context(deployment_tenant):
        StoreSettings.objects.update(active_theme=stored)
    with caplog.at_level(logging.WARNING, logger="commerce.stores.themes"):
        config = make_api().get(CONFIG).json()
    assert config["active_theme"] == "general-marketplace"
    assert stored not in str(config)
    assert any(stored in record.getMessage() for record in caplog.records)


# --- Trusted assignment ---------------------------------------------------------------------------


def test_assignment_validates_audits_and_changes_nothing_else(deployment_tenant: Tenant) -> None:
    product = simple_product(deployment_tenant, sku="TEE-1", publish=True)
    with tenant_context(deployment_tenant):
        StoreTheme.objects.create(primary_color="#1F6F4A", tagline="Hello")
        before_settings = model_to_dict(StoreSettings.objects.get())
        before_theme = model_to_dict(StoreTheme.objects.get())
        before_prices = list(Price.objects.values_list("variant_id", "amount"))

        for bad in ("fashion-editorial", "fashion-minimal", "Fashion Editorial"):
            with pytest.raises(ValidationError):
                services.assign_theme(theme_id=bad, actor_label="test")
        assert StoreSettings.objects.get().active_theme == ""
        assert not AuditEvent.objects.filter(action="store.theme.assigned").exists()

        services.assign_theme(theme_id="general-marketplace", actor_label="assign_store_theme")
        services.assign_theme(theme_id=None, actor_label="assign_store_theme")
        events = AuditEvent.objects.filter(action="store.theme.assigned").order_by("occurred_at")
        assert [(e.actor_label, e.changes) for e in events] == [
            ("assign_store_theme", {"active_theme": {"from": "", "to": "general-marketplace"}}),
            ("assign_store_theme", {"active_theme": {"from": "general-marketplace", "to": ""}}),
        ]
        after = model_to_dict(StoreSettings.objects.get())
        assert {k: v for k, v in after.items() if k != "active_theme"} == {
            k: v for k, v in before_settings.items() if k != "active_theme"
        }
        assert model_to_dict(StoreTheme.objects.get()) == before_theme  # branding kept
        assert list(Price.objects.values_list("variant_id", "amount")) == before_prices
        assert Product.objects.get(pk=product.pk).status == "active"


def test_malformed_theme_ids_cannot_be_stored(deployment_tenant: Tenant) -> None:
    with tenant_context(deployment_tenant):
        with pytest.raises(IntegrityError), transaction.atomic():
            StoreSettings.objects.update(active_theme="Fashion Editorial!")
        assert StoreSettings.objects.get().active_theme == ""


def test_assignment_command(deployment_tenant: Tenant) -> None:
    out = StringIO()
    call_command("assign_store_theme", "--theme", "general-marketplace", stdout=out)
    assert "general-marketplace" in out.getvalue()
    assert settings_of(deployment_tenant).active_theme == "general-marketplace"
    with pytest.raises(CommandError, match=r"books preset|books theme"):
        call_command("assign_store_theme", "--theme", "books-literary")
    with pytest.raises(CommandError):
        call_command("assign_store_theme", "--theme", "not-a-theme")
    call_command("assign_store_theme", "--default", stdout=StringIO())
    assert settings_of(deployment_tenant).active_theme == ""


# --- Merchants and customers cannot assign themes -------------------------------------------------


def test_merchant_appearance_cannot_change_the_theme(
    owner: Api, deployment_tenant: Tenant, make_api: Callable[[], Api]
) -> None:
    current = owner.get(APPEARANCE).json()
    assert current["active_theme"] == "general-marketplace"
    crafted = {
        "active_theme": "books-literary",
        "preset": "books",
        "theme": "fashion-editorial",
        "settings": {"active_theme": "books-literary"},
        "tagline": "Still editable",
    }
    response = owner.patch(APPEARANCE, crafted)
    assert response.status_code == 200, response.json()
    assert response.json()["tagline"] == "Still editable"  # branding keeps working
    stored = settings_of(deployment_tenant)
    assert (stored.preset, stored.active_theme) == ("general", "")
    assert make_api().get(CONFIG).json()["active_theme"] == "general-marketplace"
    assert owner.post(f"{APPEARANCE}reset/").status_code == 200
    assert settings_of(deployment_tenant).active_theme == ""


def test_public_configuration_is_read_only(owner: Api, make_api: Callable[[], Api]) -> None:
    visitor = make_api()
    for client in (visitor, owner):
        for method in (client.post, client.put, client.patch):
            assert method(CONFIG, {"active_theme": "books-literary"}).status_code in (403, 405)
    assert visitor.get(CONFIG).json()["active_theme"] == "general-marketplace"


# --- Preset changes -------------------------------------------------------------------------------


def test_preset_change_never_keeps_an_incompatible_theme(
    deployment_tenant: Tenant, make_api: Callable[[], Api]
) -> None:
    services_theme = "general-marketplace"
    with tenant_context(deployment_tenant):
        services.assign_theme(theme_id=services_theme, actor_label="test")
    call_command("change_store_preset", "--preset", "books", stdout=StringIO())
    stored = settings_of(deployment_tenant)
    assert (stored.preset, stored.active_theme) == ("books", "")
    with tenant_context(deployment_tenant):
        event = AuditEvent.objects.get(action="store.preset.changed")
    assert event.changes == {
        "preset": {"from": "general", "to": "books"},
        "active_theme": {"from": services_theme, "to": ""},
    }
    assert make_api().get(CONFIG).json()["active_theme"] == "books-literary"
    # Without an assignment there is nothing to clear.
    call_command("change_store_preset", "--preset", "general", stdout=StringIO())
    with tenant_context(deployment_tenant):
        latest = AuditEvent.objects.filter(action="store.preset.changed").latest("occurred_at")
    assert "active_theme" not in latest.changes


# --- Isolation ------------------------------------------------------------------------------------


def test_theme_assignment_is_isolated_per_store(
    deployment_tenant: Tenant, tenant_b: Tenant
) -> None:
    other = make_store_settings(tenant_b, name="Other books", preset="books")
    with tenant_context(tenant_b):
        services.assign_theme(theme_id="books-literary", actor_label="test")
    assert settings_of(tenant_b).active_theme == "books-literary"
    assert settings_of(deployment_tenant).active_theme == ""
    with tenant_context(deployment_tenant):
        # Validated against this store's own preset, never the other store's.
        with pytest.raises(ValidationError):
            services.assign_theme(theme_id="books-literary", actor_label="test")
        assert StoreSettings.objects.filter(pk=other.pk).update(active_theme="") == 0
        assert not AuditEvent.objects.filter(action="store.theme.assigned").exists()
    with tenant_context(tenant_b):
        assert AuditEvent.objects.filter(action="store.theme.assigned").count() == 1
    assert settings_of(tenant_b).active_theme == "books-literary"
