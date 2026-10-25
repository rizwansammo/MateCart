from django.apps import AppConfig


class MediaConfig(AppConfig):
    """Uploaded files (catalogue images and documents), validated and stored per tenant."""

    name = "commerce.media"
    label = "media"
    verbose_name = "Media"
