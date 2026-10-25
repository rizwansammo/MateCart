import pytest
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.tenancy.models import Tenant


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
