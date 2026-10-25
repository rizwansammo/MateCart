"""Price rules: Decimal precision per currency, currency consistency, cost-price privacy."""

import json
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from commerce.audit.models import AuditEvent
from commerce.core.domain import ConflictError
from commerce.pricing import services as pricing
from commerce.pricing.models import Price, PriceList
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import first_variant, simple_product

pytestmark = pytest.mark.usefixtures("store")


def test_the_default_price_list_uses_the_store_currency_once(deployment_tenant: Tenant) -> None:
    with tenant_context(deployment_tenant):
        first = pricing.default_price_list()
        second = pricing.default_price_list()
        assert (first.pk, first.currency, first.is_default) == (second.pk, "BDT", True)
        assert PriceList.objects.count() == 1


@pytest.mark.parametrize(
    ("currency", "amount", "ok"),
    [
        ("BDT", "10.50", True),
        ("BDT", "10.505", False),
        ("JPY", "100", True),
        ("JPY", "100.5", False),
        ("KWD", "1.125", True),
        ("KWD", "1.1255", False),
    ],
)
def test_amounts_respect_the_currency_minor_unit(
    deployment_tenant: Tenant, currency: str, amount: str, ok: bool
) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant, amount=None))
    with tenant_context(deployment_tenant):
        price_list = PriceList.objects.create(
            code=currency.lower(), name=currency, currency=currency
        )
        entry = {"price_list": price_list, "amount": Decimal(amount)}
        if ok:
            [price] = pricing.set_prices(
                variant=variant, entries=[entry], actor=None, allow_cost=False
            )
            assert (price.currency, price.amount) == (currency, Decimal(amount))
        else:
            with pytest.raises(ValidationError, match="decimal places"):
                pricing.set_prices(variant=variant, entries=[entry], actor=None, allow_cost=False)


def test_prices_take_their_list_currency(deployment_tenant: Tenant) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant))
    with tenant_context(deployment_tenant):
        usd = PriceList.objects.create(code="export", name="Export", currency="USD")
        pricing.set_prices(
            variant=variant,
            entries=[{"price_list": usd, "amount": Decimal("4.99")}],
            actor=None,
            allow_cost=False,
        )
        currencies = dict(
            Price.objects.filter(variant=variant).values_list("price_list__code", "currency")
        )
        assert currencies == {"default": "BDT", "export": "USD"}
        with pytest.raises(ConflictError, match="currency is fixed"):
            pricing.save_price_list(price_list=usd, data={"currency": "EUR"}, actor=None)
        default = pricing.default_price_list()
        with pytest.raises(ConflictError):
            pricing.save_price_list(price_list=default, data={"is_active": False}, actor=None)


def test_compare_at_prices_must_exceed_the_price_and_zero_is_free(
    deployment_tenant: Tenant,
) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant))
    with tenant_context(deployment_tenant):
        for compare_at in (Decimal("500"), Decimal("499")):
            with pytest.raises(ValidationError, match="higher"):
                pricing.set_prices(
                    variant=variant,
                    entries=[{"amount": Decimal("500"), "compare_at_amount": compare_at}],
                    actor=None,
                    allow_cost=False,
                )
        [price] = pricing.set_prices(
            variant=variant,
            entries=[{"amount": Decimal("0"), "compare_at_amount": Decimal("100")}],
            actor=None,
            allow_cost=False,
        )
        assert (price.amount, price.compare_at_amount) == (Decimal("0"), Decimal("100"))


def test_omitted_fields_keep_and_null_clears(deployment_tenant: Tenant) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant))
    with tenant_context(deployment_tenant):
        pricing.set_prices(
            variant=variant,
            entries=[
                {
                    "amount": Decimal("450"),
                    "compare_at_amount": Decimal("500"),
                    "cost_amount": Decimal("300"),
                }
            ],
            actor=None,
            allow_cost=True,
        )
        [kept] = pricing.set_prices(
            variant=variant, entries=[{"amount": Decimal("460")}], actor=None, allow_cost=False
        )
        assert (kept.compare_at_amount, kept.cost_amount) == (Decimal("500"), Decimal("300"))
        [cleared] = pricing.set_prices(
            variant=variant,
            entries=[{"amount": Decimal("460"), "compare_at_amount": None}],
            actor=None,
            allow_cost=False,
        )
        assert cleared.compare_at_amount is None


def test_cost_prices_need_permission_and_never_reach_audit(deployment_tenant: Tenant) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant))
    with tenant_context(deployment_tenant):
        with pytest.raises(PermissionDenied):
            pricing.set_prices(
                variant=variant,
                entries=[{"amount": Decimal("500"), "cost_amount": Decimal("321.77")}],
                actor=None,
                allow_cost=False,
            )
        pricing.set_prices(
            variant=variant,
            entries=[{"amount": Decimal("500"), "cost_amount": Decimal("321.77")}],
            actor=None,
            allow_cost=True,
        )
        event = AuditEvent.objects.filter(action="pricing.price.updated").latest("occurred_at")
        assert event.changes["cost_changed"] is True
        everything = json.dumps(list(AuditEvent.objects.values_list("changes", flat=True)))
        assert "321.77" not in everything


def test_active_products_keep_their_default_price(deployment_tenant: Tenant) -> None:
    product = simple_product(deployment_tenant, publish=True)
    variant = first_variant(deployment_tenant, product)
    with tenant_context(deployment_tenant):
        with pytest.raises(ConflictError, match="need a price"):
            pricing.remove_price(
                variant=variant, price_list=pricing.default_price_list(), actor=None
            )
        quote = pricing.quote(variant)
        assert quote is not None
        assert (quote.amount, quote.currency) == (Decimal("500.0000"), "BDT")


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"amount": Decimal("-1")}, "pricing_price_amount_gte_0"),
        ({"amount": Decimal("5"), "compare_at_amount": Decimal("5")}, "compare_at_above_amount"),
        ({"amount": Decimal("5"), "cost_amount": Decimal("-1")}, "pricing_price_cost_gte_0"),
        ({"amount": Decimal("5"), "currency": "bdt"}, "pricing_price_currency_format"),
    ],
)
def test_the_database_refuses_invalid_prices(
    deployment_tenant: Tenant, values: dict[str, object], constraint: str
) -> None:
    variant = first_variant(deployment_tenant, simple_product(deployment_tenant, amount=None))
    with tenant_context(deployment_tenant):
        price_list = pricing.default_price_list()
        with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
            Price.objects.create(
                price_list=price_list, variant=variant, **{"currency": "BDT", **values}
            )
