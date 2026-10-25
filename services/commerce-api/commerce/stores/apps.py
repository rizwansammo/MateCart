from django.apps import AppConfig


class StoresConfig(AppConfig):
    """Store-level business configuration: preset, locale, currency, contact details."""

    name = "commerce.stores"
    label = "stores"
    verbose_name = "Stores"
