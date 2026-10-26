"""Verified payment gated customer digital entitlements and private downloads."""

from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from commerce.audit.services import record_audit_event
from commerce.catalog.models import ProductVariant
from commerce.core.domain import ConflictError
from commerce.identity.models import User
from commerce.media.models import MediaAsset, MediaKind
from commerce.orders.models import Order, OrderItem, OrderPaymentStatus, OrderStatus
from commerce.stores.modules import enabled_modules

from .models import DigitalEntitlement, DigitalProductAsset, EntitlementStatus


def paid_access(order: Order) -> bool:
    return (
        order.customer_id is not None
        and order.status != OrderStatus.CANCELLED
        and order.payment_status == OrderPaymentStatus.PAID
        and order.amount_paid >= order.grand_total
        and order.amount_refunded == 0
    )


def issue_for_order(order: Order) -> int:
    """Called in a verified payment transaction, never from browser payment status."""
    if not paid_access(order) or "digital_downloads" not in enabled_modules():
        return 0
    count = 0
    for item in OrderItem.objects.filter(order=order, kind="digital").select_related("variant"):
        mapping = DigitalProductAsset.objects.filter(variant=item.variant, enabled=True).first()
        if mapping is None:
            continue
        _, created = DigitalEntitlement.objects.get_or_create(
            order_item=item,
            defaults={
                "customer": order.customer,
                "asset": mapping.asset,
                "download_limit": mapping.download_limit,
            },
        )
        if created:
            count += 1
    return count


def assign_asset(*, variant_id: Any, asset_id: Any, limit: int, actor: User) -> DigitalProductAsset:
    if "digital_downloads" not in enabled_modules():
        raise ConflictError("Digital delivery is unavailable.", "digital_unavailable")
    with transaction.atomic():
        variant = (
            ProductVariant.objects.select_for_update().filter(pk=variant_id, kind="digital").first()
        )
        asset = MediaAsset.objects.filter(pk=asset_id, kind=MediaKind.DOCUMENT).first()
        if variant is None or asset is None:
            raise ValidationError({"asset": ["Select a digital product and a stored document."]})
        # Never repurpose a catalogue-visible document as a paid download.
        # Other catalogue attachment operations must likewise reject private assets.
        from commerce.catalog.public import is_public_asset

        if is_public_asset(asset):
            raise ConflictError("Select a private document.", "digital_asset_public")
        mapping, _ = DigitalProductAsset.objects.update_or_create(
            variant=variant,
            defaults={"asset": asset, "enabled": True, "download_limit": limit},
        )
        # Issue only for verified payments. Existing bindings retain their original asset.
        orders = Order.objects.filter(items__variant=variant).distinct()
        for order in orders:
            issue_for_order(order)
        record_audit_event(action="digital.asset_assigned", resource=mapping, actor=actor)
        return mapping


def revoke_for_order(order: Order) -> None:
    """Conservative access policy: any successful refund revokes downloads on the order."""
    entitlements = DigitalEntitlement.objects.filter(
        order_item__order=order,
        status=EntitlementStatus.ACTIVE,
    )
    entitlements.update(status=EntitlementStatus.REVOKED, revoked_at=timezone.now())


def library(customer: User) -> list[dict[str, Any]]:
    items = (
        OrderItem.objects.filter(
            order__customer=customer,
            kind="digital",
        )
        .select_related("order")
        .order_by("-order__placed_at", "-pk")[:100]
    )
    entitlements = {
        row.order_item_id: row
        for row in DigitalEntitlement.objects.filter(
            customer=customer,
            order_item_id__in=[item.pk for item in items],
        )
    }
    results = []
    for item in items:
        entitlement = entitlements.get(item.pk)
        available = (
            entitlement is not None
            and paid_access(item.order)
            and entitlement.status == EntitlementStatus.ACTIVE
            and entitlement.access_count < entitlement.download_limit
        )
        state = (
            "ready"
            if available
            else "payment_required"
            if not paid_access(item.order)
            else "awaiting_resource"
            if entitlement is None
            else "unavailable"
        )
        results.append(
            {
                "order_item": str(item.pk),
                "order_number": item.order.number,
                "product_name": item.product_name,
                "variant_title": item.variant_title,
                "purchased_at": item.order.placed_at,
                "state": state,
                "entitlement_id": str(entitlement.pk) if entitlement is not None else None,
                "remaining_downloads": max(entitlement.download_limit - entitlement.access_count, 0)
                if available
                else 0,
            }
        )
    return results


def download_for_customer(*, entitlement_id: Any, customer: User) -> Any:
    """Acquire access atomically, then stream through the existing checked media response."""
    from commerce.media.views import file_response

    with transaction.atomic():
        entitlement = (
            DigitalEntitlement.objects.select_for_update()
            .filter(
                pk=entitlement_id,
                customer=customer,
            )
            .select_related("order_item__order", "asset")
            .first()
        )
        if entitlement is None:
            raise NotFound("Digital purchase not found.")
        if (
            entitlement.status != EntitlementStatus.ACTIVE
            or entitlement.access_count >= entitlement.download_limit
            or not paid_access(entitlement.order_item.order)
        ):
            raise ConflictError(
                "This digital download is not available.", "digital_access_unavailable"
            )
        response = file_response(entitlement.asset, public=False)
        DigitalEntitlement.objects.filter(pk=entitlement.pk).update(
            access_count=F("access_count") + 1
        )
        return response
