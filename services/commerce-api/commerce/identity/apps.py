from django.apps import AppConfig


class IdentityConfig(AppConfig):
    """Store-level identities (customers and merchant staff). AUTH_USER_MODEL = identity.User."""

    name = "commerce.identity"
    label = "identity"
    verbose_name = "Identity"

    def ready(self) -> None:
        from . import schema  # noqa: F401  (registers the OpenAPI security schemes)
