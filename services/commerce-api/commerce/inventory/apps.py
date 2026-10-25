from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """Stock locations, stock levels, the append-only movement ledger, batches and serials."""

    name = "commerce.inventory"
    label = "inventory"
    verbose_name = "Inventory"
