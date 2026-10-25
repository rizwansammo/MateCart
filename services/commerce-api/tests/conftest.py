from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.core.cache import caches
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.core import delivery
from commerce.stores.models import StoreSettings
from commerce.tenancy.models import Tenant

from .factories import make_store_settings
from .fakes import FakeSmsProvider
from .helpers import Api


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def tenant_a(db: None) -> Tenant:
    return Tenant.objects.create(handle="store-a")


@pytest.fixture
def tenant_b(db: None) -> Tenant:
    return Tenant.objects.create(handle="store-b")


@pytest.fixture
def deployment_tenant(settings: Settings, tenant_a: Tenant) -> Tenant:
    """tenant_a, configured as the tenant this deployment serves (MATECART_TENANT_ID)."""
    settings.MATECART_TENANT_ID = tenant_a.pk
    return tenant_a


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    caches["ratelimit"].clear()
    FakeSmsProvider.outbox.clear()
    delivery._provider.cache_clear()
    yield
    delivery._provider.cache_clear()


@pytest.fixture
def store(deployment_tenant: Tenant) -> StoreSettings:
    """The deployment's store, provisioned with settings (country BD for phone numbers)."""
    return make_store_settings(deployment_tenant, name="Rahim Fashion")


@pytest.fixture
def sms(settings: Settings) -> list[Any]:
    """Phone sign-in enabled with a controlled fake provider; returns its outbox."""
    settings.MATECART_SMS_PROVIDER = "tests.fakes.FakeSmsProvider"
    delivery._provider.cache_clear()
    return FakeSmsProvider.outbox


@pytest.fixture
def api(django_capture_on_commit_callbacks: Callable[..., AbstractContextManager[Any]]) -> Api:
    return Api(APIClient(), django_capture_on_commit_callbacks)


@pytest.fixture
def make_api(
    django_capture_on_commit_callbacks: Callable[..., AbstractContextManager[Any]],
) -> Callable[[], Api]:
    """A second, independent browser."""
    return lambda: Api(APIClient(), django_capture_on_commit_callbacks)
