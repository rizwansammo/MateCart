from django.apps import AppConfig


class PricingConfig(AppConfig):
    """Price lists and variant prices (Decimal amounts in ISO 4217 currencies)."""

    name = "commerce.pricing"
    label = "pricing"
    verbose_name = "Pricing"
