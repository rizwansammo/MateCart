from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Categories, collections, brands, attributes, products, variants and preset details."""

    name = "commerce.catalog"
    label = "catalog"
    verbose_name = "Catalogue"
