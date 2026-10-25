"""Price lists and variant prices (D-053).

Money follows the canonical convention: ``DecimalField(max_digits=19, decimal_places=4)`` with an
ISO 4217 ``currency`` column; never floats, never implicit conversion. Every store has one
default price list in its default currency; further lists (wholesale, another currency) hold
their own prices. A price's currency always equals its list's currency (``services``).
"""

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

from commerce.catalog.models import ProductVariant
from commerce.core.models import TimestampedModel
from commerce.core.standards import validate_currency_code
from commerce.tenancy.constraints import TenantForeignKeyConstraint
from commerce.tenancy.models import TenantOwnedModel

PRICE_LIST_CODE_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class PriceList(TenantOwnedModel, TimestampedModel):
    code = models.CharField(max_length=40, validators=[RegexValidator(PRICE_LIST_CODE_PATTERN)])
    name = models.CharField(max_length=120)
    currency = models.CharField(
        max_length=3, validators=[validate_currency_code], help_text="ISO 4217."
    )
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["tenant", "code"], name="pricing_pricelist_code_unique"
            ),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(is_default=True),
                name="pricing_pricelist_one_default",
            ),
            models.CheckConstraint(
                condition=Q(code__regex=PRICE_LIST_CODE_PATTERN),
                name="pricing_pricelist_code_format",
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"),
                name="pricing_pricelist_currency_format",
            ),
            models.CheckConstraint(
                condition=Q(is_default=False) | Q(is_active=True),
                name="pricing_pricelist_default_is_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.currency})"


class Price(TenantOwnedModel, TimestampedModel):
    """A variant's selling price in one price list.

    ``compare_at_amount`` (the struck-through "was" price) must exceed the selling price.
    ``cost_amount`` is internal: only staff with ``catalog.costs`` can see or change it, and audit
    events record that it changed, never its value.
    """

    price_list = models.ForeignKey(PriceList, on_delete=models.PROTECT, related_name="prices")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="prices")
    currency = models.CharField(max_length=3, validators=[validate_currency_code])
    amount = models.DecimalField(max_digits=19, decimal_places=4)
    compare_at_amount = models.DecimalField(max_digits=19, decimal_places=4, null=True, blank=True)
    cost_amount = models.DecimalField(max_digits=19, decimal_places=4, null=True, blank=True)

    class Meta(TenantOwnedModel.Meta):
        constraints = [
            *TenantOwnedModel.Meta.constraints,
            TenantForeignKeyConstraint(
                field="price_list", name="pricing_price_price_list_same_tenant"
            ),
            TenantForeignKeyConstraint(field="variant", name="pricing_price_variant_same_tenant"),
            models.UniqueConstraint(
                fields=["tenant", "price_list", "variant"], name="pricing_price_one_per_list"
            ),
            models.CheckConstraint(
                condition=Q(currency__regex=r"^[A-Z]{3}$"), name="pricing_price_currency_format"
            ),
            models.CheckConstraint(condition=Q(amount__gte=0), name="pricing_price_amount_gte_0"),
            models.CheckConstraint(
                condition=Q(compare_at_amount__isnull=True) | Q(compare_at_amount__gt=F("amount")),
                name="pricing_price_compare_at_above_amount",
            ),
            models.CheckConstraint(
                condition=Q(cost_amount__isnull=True) | Q(cost_amount__gte=0),
                name="pricing_price_cost_gte_0",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "variant"], name="pricing_price_variant")]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
