"""Builders for Phase 04 catalogue, pricing, inventory and media tests."""

import io
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image

from commerce.catalog import services as catalog
from commerce.catalog.models import (
    ApparelDetails,
    AttributeDefinition,
    AttributeOption,
    AttributeScope,
    AttributeValueType,
    BookDetails,
    Brand,
    Category,
    Collection,
    CollectionProduct,
    ElectronicsDetails,
    GroceryDetails,
    Product,
    ProductAttributeValue,
    ProductCategory,
    ProductMedia,
    ProductOption,
    ProductVariant,
    VariantOptionValue,
    WarrantyPolicy,
)
from commerce.identity import sessions
from commerce.identity.models import AccountType, User
from commerce.inventory.models import (
    Batch,
    BatchLevel,
    MovementType,
    SerialNumber,
    SerialStatus,
    StockLevel,
    StockLocation,
    StockMovement,
)
from commerce.media.models import MediaAsset, MediaKind
from commerce.pricing.models import Price, PriceList
from commerce.staff.models import StaffMembership
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .factories import make_user
from .helpers import Api

STAFF_COOKIE = "mc_staff"


def sign_in_staff(api: Api, tenant: Tenant, role: str, email: str) -> User:
    """A staff account with ``role``, signed in on ``api`` (session cookie set directly)."""
    user = make_user(
        tenant, account_type=AccountType.STAFF, email=email, password="staff-passphrase-1234"
    )
    with tenant_context(tenant):
        StaffMembership.objects.create(user=user, role=role)
        _, token = sessions.start(user)
    api.client.cookies[STAFF_COOKIE] = token
    return user


def set_preset(tenant: Tenant, preset: str) -> None:
    with tenant_context(tenant):
        StoreSettings.objects.update(preset=preset)


def png_bytes(size: tuple[int, int] = (4, 3), mode: str = "RGB", fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color=0 if mode == "1" else (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


def stored_asset(tenant: Tenant, *, kind: str = MediaKind.IMAGE) -> MediaAsset:
    """An asset row with a real stored file, created without the upload service."""
    with tenant_context(tenant):
        asset = MediaAsset(
            tenant=tenant,
            kind=kind,
            content_type="image/png" if kind == MediaKind.IMAGE else "application/pdf",
            size_bytes=10,
            checksum_sha256="a" * 64,
            width=4 if kind == MediaKind.IMAGE else None,
            height=3 if kind == MediaKind.IMAGE else None,
        )
        asset.file.save("x.png", ContentFile(png_bytes()), save=False)
        asset.save()
        return asset


def size_attribute(tenant: Tenant, codes: tuple[str, ...] = ("s", "m", "l")) -> AttributeDefinition:
    with tenant_context(tenant):
        attribute = AttributeDefinition.objects.create(
            code="size",
            name="Size",
            value_type=AttributeValueType.OPTION,
            scope=AttributeScope.VARIANT,
        )
        for position, code in enumerate(codes):
            AttributeOption.objects.create(
                attribute=attribute, code=code, value=code.upper(), position=position
            )
        return attribute


def simple_product(
    tenant: Tenant,
    *,
    sku: str = "TEE-1",
    name: str = "Plain Tee",
    amount: str | None = "500.00",
    publish: bool = False,
    **fields: Any,
) -> Product:
    variant: dict[str, Any] = {"sku": sku, **fields.pop("variant", {})}
    if amount is not None:
        variant["price"] = {"amount": Decimal(amount)}
    with tenant_context(tenant):
        product = catalog.create_product(
            data={"name": name, "variants": [variant], **fields}, actor=None, allow_cost=True
        )
        if publish:
            product = catalog.set_status(product=product, status="active", actor=None)
        return product


def first_variant(tenant: Tenant, product: Product) -> ProductVariant:
    with tenant_context(tenant):
        return ProductVariant.objects.filter(product=product).order_by("position").get()


def full_catalogue(tenant: Tenant) -> dict[str, Any]:
    """One row of every Phase 04 model in ``tenant``, linked together."""
    asset = stored_asset(tenant)
    user = make_user(tenant, account_type=AccountType.STAFF, email=f"actor@{tenant.handle}.test")
    with tenant_context(tenant):
        brand = Brand.objects.create(name="Brand", slug="brand")
        category = Category.objects.create(name="Root", slug="root", image=asset)
        child = Category.objects.create(name="Child", slug="child", parent=category)
        collection = Collection.objects.create(name="Picks", slug="picks", image=asset)
        attribute = AttributeDefinition.objects.create(
            code="size",
            name="Size",
            value_type=AttributeValueType.OPTION,
            scope=AttributeScope.VARIANT,
        )
        option = AttributeOption.objects.create(attribute=attribute, code="m", value="M")
        spec = AttributeDefinition.objects.create(
            code="material",
            name="Material",
            value_type=AttributeValueType.TEXT,
            scope=AttributeScope.SPECIFICATION,
        )
        warranty = WarrantyPolicy.objects.create(
            name="1 year", duration_months=12, provider="seller"
        )
        product = Product.objects.create(name="Thing", slug="thing", brand=brand)
        variant = ProductVariant.objects.create(product=product, sku="THING-M", title="M")
        price_list = PriceList.objects.create(
            code="default", name="Default", currency="BDT", is_default=True
        )
        location = StockLocation.objects.create(code="MAIN", name="Main", is_default=True)
        batch = Batch.objects.create(variant=variant, code="B1", received_on=timezone.localdate())
        serial = SerialNumber.objects.create(
            variant=variant, serial="SN1", status=SerialStatus.AVAILABLE, location=location
        )
        level = StockLevel.objects.create(variant=variant, location=location, on_hand=1)
        movement = StockMovement.objects.create(
            operation_id=variant.pk,
            movement_type=MovementType.RECEIPT,
            variant=variant,
            location=location,
            on_hand_delta=1,
            on_hand_after=1,
            reserved_after=0,
            batch=batch,
            serial=serial,
            actor_user=user,
        )
        rows: dict[str, Any] = {
            "user": user,
            "asset": asset,
            "brand": brand,
            "category": category,
            "child": child,
            "collection": collection,
            "attribute": attribute,
            "option": option,
            "warranty": warranty,
            "product": product,
            "variant": variant,
            "price_list": price_list,
            "location": location,
            "batch": batch,
            "serial": serial,
            "level": level,
            "movement": movement,
            "collection_product": CollectionProduct.objects.create(
                collection=collection, product=product
            ),
            "placement": ProductCategory.objects.create(product=product, category=child),
            "product_option": ProductOption.objects.create(product=product, attribute=attribute),
            "option_value": VariantOptionValue.objects.create(
                variant=variant, attribute=attribute, option=option
            ),
            "spec": ProductAttributeValue.objects.create(
                product=product, attribute=spec, value_text="Cotton"
            ),
            "media": ProductMedia.objects.create(product=product, asset=asset, variant=variant),
            "apparel": ApparelDetails.objects.create(product=product, size_guide=asset),
            "grocery": GroceryDetails.objects.create(product=product),
            "electronics": ElectronicsDetails.objects.create(product=product, warranty=warranty),
            "book": BookDetails.objects.create(product=product, authors=["A. Writer"]),
            "price": Price.objects.create(
                price_list=price_list, variant=variant, currency="BDT", amount=Decimal("10")
            ),
            "batch_level": BatchLevel.objects.create(batch=batch, location=location, on_hand=1),
        }
        return rows


ApiFactory = Callable[[], Api]
