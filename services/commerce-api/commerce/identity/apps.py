from django.apps import AppConfig


class IdentityConfig(AppConfig):
    """Store-level identities (customers and merchant staff). AUTH_USER_MODEL = identity.User."""

    name = "commerce.identity"
    label = "identity"
    verbose_name = "Identity"
