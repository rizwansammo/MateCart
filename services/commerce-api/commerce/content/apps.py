from django.apps import AppConfig


class ContentConfig(AppConfig):
    """Store content: informational and policy pages, and homepage sections."""

    name = "commerce.content"
    label = "content"
    verbose_name = "Content"
