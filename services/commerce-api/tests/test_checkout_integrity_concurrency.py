from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from commerce.checkout import services
from commerce.checkout.models import (
    CheckoutLine,
    CheckoutReservation,
    CheckoutSession,
    CheckoutStatus,
    DeliverySlot,
)
from commerce.inventory import services as inventory
from commerce.inventory.models import StockLevel
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import set_preset
from .test_checkout import prepare_physical_checkout, product
from .test_inventory_concurrency import run_concurrently

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("store")]


def clone_draft(session: CheckoutSession) -> CheckoutSession:
    clone = CheckoutSession.objects.create(
        cart=session.cart,
        customer=session.customer,
        guest_token_hash=session.guest_token_hash,
        expires_at=session.expires_at,
        contact_email=session.contact_email,
        contact_phone=session.contact_phone,
        recipient_name=session.recipient_name,
        address_line_1=session.address_line_1,
        address_line_2=session.address_line_2,
        city=session.city,
        region=session.region,
        postal_code=session.postal_code,
        country=session.country,
        delivery_notes=session.delivery_notes,
        shipping_method=session.shipping_method,
        delivery_slot=session.delivery_slot,
        coupon=session.coupon,
        coupon_code=session.coupon_code,
        requires_shipping=session.requires_shipping,
        currency=session.currency,
        item_subtotal=session.item_subtotal,
        discount_total=session.discount_total,
        shipping_total=session.shipping_total,
        tax_total=session.tax_total,
        grand_total=session.grand_total,
    )
    for line in CheckoutLine.objects.filter(session=session):
        CheckoutLine.objects.create(
            session=clone,
            variant=line.variant,
            product_name=line.product_name,
            variant_title=line.variant_title,
            sku=line.sku,
            kind=line.kind,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_subtotal=line.line_subtotal,
            price_id=line.price_id,
        )
    return clone


def review(session_id: Any, key: str) -> CheckoutSession:
    return services.review_checkout(
        session=CheckoutSession.objects.get(pk=session_id),
        idempotency_key=key,
        actor=None,
    )


def test_concurrent_reviews_of_one_cart_create_exactly_one_hold(
    deployment_tenant: Tenant,
) -> None:
    variant = product(deployment_tenant, tracked=True)
    with tenant_context(deployment_tenant):
        location = inventory.default_location()
        inventory.receive(
            variant=variant,
            location=location,
            quantity=2,
            request=inventory.StockRequest(reason="concurrent cart holds"),
        )
    client = APIClient()
    first_id, _ = prepare_physical_checkout(client, variant)
    with tenant_context(deployment_tenant):
        second = clone_draft(CheckoutSession.objects.get(pk=first_id))

    session_ids = [first_id, second.pk]
    results = run_concurrently(
        deployment_tenant,
        2,
        lambda index: review(session_ids[index], f"same-cart-review-{index}"),
    )

    assert sum(isinstance(result, CheckoutSession) for result in results) == 1
    assert sum(isinstance(result, services.ActiveCheckout) for result in results) == 1
    with tenant_context(deployment_tenant):
        assert CheckoutSession.objects.filter(status=CheckoutStatus.REVIEWED).count() == 1
        assert CheckoutReservation.objects.filter(released_at__isnull=True).count() == 1
        assert StockLevel.objects.get(variant=variant, location=location).reserved == Decimal(1)


def test_concurrent_reviews_cannot_exceed_delivery_slot_capacity(
    deployment_tenant: Tenant,
) -> None:
    set_preset(deployment_tenant, "grocery")
    variant = product(deployment_tenant, tracked=True)
    with tenant_context(deployment_tenant):
        location = inventory.default_location()
        inventory.receive(
            variant=variant,
            location=location,
            quantity=2,
            request=inventory.StockRequest(reason="concurrent delivery slots"),
        )
        slot = DeliverySlot.objects.create(
            label="One place",
            starts_at=timezone.now() + timedelta(hours=2),
            ends_at=timezone.now() + timedelta(hours=4),
            capacity=1,
        )
    clients = [APIClient(), APIClient()]
    session_ids = [
        prepare_physical_checkout(client, variant, delivery_slot=slot)[0] for client in clients
    ]

    results = run_concurrently(
        deployment_tenant,
        2,
        lambda index: review(session_ids[index], f"slot-review-{index}"),
    )

    assert sum(isinstance(result, CheckoutSession) for result in results) == 1
    assert sum(isinstance(result, ValidationError) for result in results) == 1
    with tenant_context(deployment_tenant):
        assert (
            CheckoutSession.objects.filter(
                status=CheckoutStatus.REVIEWED, delivery_slot=slot
            ).count()
            == 1
        )
        assert CheckoutReservation.objects.filter(released_at__isnull=True).count() == 1
        assert StockLevel.objects.get(variant=variant, location=location).reserved == Decimal(1)
