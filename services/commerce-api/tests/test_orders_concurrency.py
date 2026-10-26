from decimal import Decimal
from typing import Any

import pytest
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from commerce.checkout.models import CheckoutSession, Coupon, CouponDiscountType
from commerce.core.domain import ConflictError
from commerce.inventory.models import StockLevel
from commerce.orders import payments, services
from commerce.orders.models import Order, PaymentIntent, PaymentTransaction
from commerce.orders.providers import reset_provider_cache
from commerce.stores.modules import set_module
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .fakes import FakePaymentProvider
from .test_checkout import product
from .test_inventory_concurrency import run_concurrently
from .test_orders import digital, digital_checkout, place, reviewed, stock

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("store")]


def place_directly(checkout_id: Any, key: str) -> Order:
    return services.place_order(
        session=CheckoutSession.objects.get(pk=checkout_id),
        payment_method="cod",
        idempotency_key=key,
        actor=None,
    ).order


def test_concurrent_submissions_create_one_order(deployment_tenant: Tenant) -> None:
    variant = product(deployment_tenant, tracked=True)
    stock(deployment_tenant, variant, 2)
    checkout_id = reviewed(APIClient(), variant)

    results = run_concurrently(
        deployment_tenant, 3, lambda index: place_directly(checkout_id, f"parallel-key-{index}")
    )

    orders = [result for result in results if isinstance(result, Order)]
    assert len(orders) == 1
    assert all(isinstance(result, (Order, ConflictError)) for result in results)
    with tenant_context(deployment_tenant):
        assert Order.objects.count() == 1
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(1)


def test_concurrent_orders_cannot_exceed_coupon_usage(deployment_tenant: Tenant) -> None:
    variant = product(deployment_tenant, tracked=True)
    stock(deployment_tenant, variant, 3)
    with tenant_context(deployment_tenant):
        Coupon.objects.create(
            code="SOLO", discount_type=CouponDiscountType.PERCENT, value=10, max_redemptions=1
        )
    ids = [reviewed(APIClient(), variant, coupon_code="SOLO") for _ in range(3)]

    results = run_concurrently(
        deployment_tenant, 3, lambda index: place_directly(ids[index], f"coupon-race-{index}")
    )

    assert sum(isinstance(result, Order) for result in results) == 1
    assert sum(isinstance(result, services.CouponUnavailable) for result in results) == 2
    with tenant_context(deployment_tenant):
        assert Coupon.objects.get(code="SOLO").times_redeemed == 1
        # Only the winning order keeps stock; the blocked checkouts released theirs.
        assert StockLevel.objects.get(variant=variant).reserved == Decimal(1)


def test_concurrent_duplicate_callbacks_record_one_payment(
    deployment_tenant: Tenant, settings: Settings
) -> None:
    settings.MATECART_PAYMENT_PROVIDER = "tests.fakes.FakePaymentProvider"
    reset_provider_cache()
    FakePaymentProvider.reset()
    with tenant_context(deployment_tenant):
        set_module(key="online_payments", enabled=True, actor=None)
    client = APIClient()
    placed = place(client, digital_checkout(client, digital(deployment_tenant)), "online")
    client.post(f"/api/orders/{placed.data['id']}/payment/", {}, format="json")
    with tenant_context(deployment_tenant):
        intent = PaymentIntent.objects.get()
    bodies = [
        FakePaymentProvider.signed(
            {
                "event_id": f"evt-race-{index}",
                "reference": str(intent.pk),
                "provider_ref": intent.provider_ref,
                "transaction_ref": "txn-race",
                "status": "succeeded",
                "amount": f"{intent.amount:.2f}",
                "currency": intent.currency,
            }
        )
        for index in range(3)
    ]

    results = run_concurrently(
        deployment_tenant,
        3,
        lambda index: payments.process_callback(
            provider_code="fakepay",
            body=bodies[index][0],
            headers={"X-Fake-Signature": bodies[index][1]},
        ),
    )

    assert sorted(str(result) for result in results).count("payment_succeeded") == 1
    with tenant_context(deployment_tenant):
        order = Order.objects.get()
        assert PaymentTransaction.objects.count() == 1
        assert order.amount_paid == order.grand_total
        assert order.status == "confirmed"
    reset_provider_cache()
