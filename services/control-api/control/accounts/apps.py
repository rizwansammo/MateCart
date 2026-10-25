from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """NetaMate console administrator identities. AUTH_USER_MODEL = accounts.AdminUser."""

    name = "control.accounts"
    label = "accounts"
    verbose_name = "Accounts"
