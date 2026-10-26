"""Store modules (D-062): a code-owned registry, per-preset defaults and per-store overrides.

A module is *effective* only when it is implemented (``available``), applies to the store's
preset, is switched on (override, or the preset default) and every prerequisite is effective.
Planned modules are listed so merchants and the Central Console (Phase 16) can see what is
coming, but they can never be switched on before their phase ships them. In Phase 05 module
states are configuration for the storefront (Phase 06) and later phases; they do not change any
existing catalogue, pricing or inventory rule.
"""

from dataclasses import dataclass, field

from django.db import transaction

from commerce.audit.services import record_audit_event
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User

from .models import StoreModule, StorePreset, StoreSettings

ALL = frozenset(StorePreset.values)
F, G, E, D, B = (
    StorePreset.FASHION,
    StorePreset.GROCERY,
    StorePreset.ELECTRONICS,
    StorePreset.GENERAL,
    StorePreset.BOOKS,
)


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    name: str
    description: str
    phase: str
    available: bool
    presets: frozenset[str] = ALL
    default_on: frozenset[str] = frozenset()
    core: bool = False
    requires: tuple[str, ...] = field(default=())


REGISTRY: tuple[ModuleSpec, ...] = (
    # Implemented foundations. Core modules are always on.
    ModuleSpec(
        "catalog",
        "Product catalogue",
        "Products, variants, prices and categories.",
        "04",
        True,
        core=True,
    ),
    ModuleSpec(
        "inventory", "Inventory", "Stock levels, movements and locations.", "04", True, core=True
    ),
    ModuleSpec(
        "content_pages",
        "Pages and policies",
        "About, contact and policy pages.",
        "05",
        True,
        core=True,
    ),
    ModuleSpec(
        "customer_accounts",
        "Customer accounts",
        "Customer sign-in and profiles.",
        "03",
        True,
        core=True,
    ),
    ModuleSpec(
        "size_guides",
        "Size guides",
        "Size guide images and notes on apparel.",
        "04",
        True,
        frozenset({F, D}),
        frozenset({F}),
    ),
    ModuleSpec(
        "batch_expiry",
        "Batches and expiry",
        "Lot tracking with expiry dates.",
        "04",
        True,
        frozenset({G, D}),
        frozenset({G}),
        requires=("inventory",),
    ),
    ModuleSpec(
        "serial_numbers",
        "Serial numbers",
        "Per-unit serial or IMEI tracking.",
        "04",
        True,
        frozenset({E, D}),
        frozenset({E}),
        requires=("inventory",),
    ),
    ModuleSpec(
        "book_details",
        "Book details",
        "Authors, publishers, editions and ISBNs.",
        "04",
        True,
        frozenset({B, D}),
        frozenset({B}),
    ),
    ModuleSpec(
        "bilingual_content",
        "Bangla and English content",
        "Translated product and page text.",
        "05",
        True,
        ALL,
        ALL,
    ),
    # Planned: listed, never effective until their phase implements them.
    ModuleSpec(
        "product_comparison",
        "Product comparison",
        "Compare specifications side by side.",
        "06",
        False,
        frozenset({E, D}),
    ),
    ModuleSpec(
        "cart_checkout", "Cart and checkout", "Cart, guest checkout and addresses.", "07", False
    ),
    ModuleSpec(
        "delivery_slots",
        "Delivery slots",
        "Customers choose a delivery window.",
        "07",
        False,
        frozenset({G}),
        requires=("cart_checkout",),
    ),
    ModuleSpec(
        "preorders",
        "Pre-orders",
        "Order titles before their release date.",
        "07",
        False,
        frozenset({B, D}),
        requires=("cart_checkout",),
    ),
    ModuleSpec(
        "cash_on_delivery",
        "Cash on delivery",
        "Pay when the order arrives.",
        "08",
        False,
        requires=("cart_checkout",),
    ),
    ModuleSpec(
        "online_payments",
        "Online payments",
        "Card and mobile wallet payments.",
        "08",
        False,
        requires=("cart_checkout",),
    ),
    ModuleSpec(
        "emi",
        "EMI instalments",
        "Pay for electronics in instalments.",
        "08",
        False,
        frozenset({E, D}),
        requires=("online_payments",),
    ),
    ModuleSpec("courier_booking", "Courier booking", "Book and track couriers.", "09", False),
    ModuleSpec("product_reviews", "Product reviews", "Verified customer reviews.", "10", False),
    ModuleSpec(
        "returns_exchanges", "Returns and exchanges", "Customer return requests.", "10", False
    ),
    ModuleSpec(
        "digital_downloads",
        "Digital downloads",
        "Deliver eBooks and digital files.",
        "10",
        False,
        frozenset({B, D}),
    ),
    ModuleSpec("flash_sales", "Flash sales", "Time-limited deals with countdowns.", "12", False),
    ModuleSpec("loyalty", "Loyalty points", "Rewards for repeat customers.", "12", False),
    ModuleSpec("live_chat", "Live chat", "Website chat and unified inbox.", "14", False),
    ModuleSpec("ai_assistant", "AI assistant", "AI help with product content.", "15", False),
)
BY_KEY = {spec.key: spec for spec in REGISTRY}


class ModuleError(DomainError):
    default_code = "module_not_allowed"


@dataclass(frozen=True)
class ModuleState:
    spec: ModuleSpec
    applicable: bool
    default_enabled: bool
    configured: bool | None
    enabled: bool
    blocked_by: tuple[str, ...]


def _preset() -> str:
    preset = StoreSettings.objects.values_list("preset", flat=True).first()
    return preset or StorePreset.GENERAL


def module_states() -> list[ModuleState]:
    """Every registry module with its effective state for the active store."""
    preset = _preset()
    overrides = dict(StoreModule.objects.values_list("key", "enabled"))
    wanted: dict[str, bool] = {}
    for spec in REGISTRY:
        applicable = preset in spec.presets
        if spec.core:
            wanted[spec.key] = True
        elif not spec.available or not applicable:
            wanted[spec.key] = False
        else:
            wanted[spec.key] = overrides.get(spec.key, preset in spec.default_on)
    effective: dict[str, bool] = {}

    def resolve(key: str) -> bool:
        if key not in effective:
            effective[key] = wanted[key] and all(resolve(dep) for dep in BY_KEY[key].requires)
        return effective[key]

    states = []
    for spec in REGISTRY:
        blocked = tuple(dep for dep in spec.requires if not resolve(dep))
        states.append(
            ModuleState(
                spec=spec,
                applicable=preset in spec.presets,
                default_enabled=spec.core or (spec.available and preset in spec.default_on),
                configured=overrides.get(spec.key),
                enabled=resolve(spec.key),
                blocked_by=blocked if wanted[spec.key] else (),
            )
        )
    return states


def enabled_modules() -> list[str]:
    return [state.spec.key for state in module_states() if state.enabled]


def set_module(*, key: str, enabled: bool | None, actor: User | None) -> ModuleState:
    """Switch a module on or off, or back to its preset default (``None``)."""
    spec = BY_KEY.get(key)
    if spec is None:
        raise ModuleError("This module does not exist.", "module_unknown")
    if spec.core:
        raise ModuleError("Core modules are always on.", "module_core")
    if not spec.available:
        raise ConflictError(
            f"{spec.name} arrives in Phase {spec.phase}; it cannot be switched on yet.",
            "module_unavailable",
        )
    preset = _preset()
    if preset not in spec.presets:
        raise ModuleError(
            "This module does not apply to the store's preset.", "module_not_applicable"
        )
    with transaction.atomic():
        current = StoreModule.objects.select_for_update().filter(key=key).first()
        before = current.enabled if current else None
        if enabled:
            states = {state.spec.key: state for state in module_states()}
            missing = [dep for dep in spec.requires if not states[dep].enabled]
            if missing:
                raise ModuleError(
                    f"Turn on {', '.join(BY_KEY[dep].name for dep in missing)} first.",
                    "module_requires",
                )
        if enabled is None:
            if current is not None:
                current.delete()
        elif current is None:
            current = StoreModule.objects.create(key=key, enabled=enabled)
        else:
            current.enabled = enabled
            current.save(update_fields=["enabled", "updated_at"])
        record_audit_event(
            action="store.module.updated",
            resource=current if current is not None and enabled is not None else _settings(),
            actor=actor,
            changes={"module": key, "enabled": {"from": before, "to": enabled}},
        )
    return next(state for state in module_states() if state.spec.key == key)


def _settings() -> StoreSettings:
    settings = StoreSettings.objects.first()
    if settings is None:
        raise DomainError("Store settings are missing; provision the store first.", "store_missing")
    return settings
