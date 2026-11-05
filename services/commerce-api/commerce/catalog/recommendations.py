"""Related products (Phase 15, D-110): deterministic, explainable and independent of AI.

Candidates are other *published* products that share the product's primary category, any
category, a collection, the brand or tags; they are ranked by that overlap. Merchants can pin
products (always first, in their order) or hide them. Only products a customer could buy now are
shown: published, with an active priced variant, sellable stock (or untracked, digital or
pre-order, D-060) and, for digital-only products, the digital downloads module switched on.
Nothing here uses visitor behaviour or another customer's purchases, and a failure here never
affects the product page or checkout (the storefront treats it as optional).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.db import transaction
from rest_framework.exceptions import ValidationError

from commerce.audit.services import record_audit_event
from commerce.identity.models import User
from commerce.stores.modules import enabled_modules

from .models import (
    CollectionProduct,
    Product,
    ProductCategory,
    ProductRecommendation,
    ProductStatus,
    ProductVariant,
    RecommendationKind,
    VariantKind,
)

MAX_PINNED = 12
MAX_RESULTS = 12
POOL = 60


def _reasons(product: Product) -> dict[Any, list[str]]:
    """Candidate id → why it is related (ranked by how much it shares)."""
    reasons: dict[Any, list[str]] = {}

    def add(ids: Sequence[Any], reason: str) -> None:
        for pk in ids:
            if pk != product.pk:
                reasons.setdefault(pk, []).append(reason)

    placements = list(
        ProductCategory.objects.filter(product=product).values_list("category_id", "is_primary")
    )
    primary = [cat for cat, is_primary in placements if is_primary]
    categories = [cat for cat, _ in placements]
    if primary:
        add(
            list(
                ProductCategory.objects.filter(
                    category_id__in=primary, is_primary=True
                ).values_list("product_id", flat=True)[:POOL]
            ),
            "same_primary_category",
        )
    if categories:
        add(
            list(
                ProductCategory.objects.filter(category_id__in=categories)
                .values_list("product_id", flat=True)
                .distinct()[:POOL]
            ),
            "same_category",
        )
    collections = CollectionProduct.objects.filter(product=product).values("collection_id")
    add(
        list(
            CollectionProduct.objects.filter(collection_id__in=collections)
            .values_list("product_id", flat=True)
            .distinct()[:POOL]
        ),
        "same_collection",
    )
    if product.brand_id:
        add(
            list(
                Product.objects.filter(brand_id=product.brand_id).values_list("pk", flat=True)[
                    :POOL
                ]
            ),
            "same_brand",
        )
    if product.tags:
        add(
            list(
                Product.objects.filter(tags__overlap=product.tags).values_list("pk", flat=True)[
                    :POOL
                ]
            ),
            "shared_tags",
        )
    return reasons


WEIGHTS = {
    "same_primary_category": 5,
    "same_category": 3,
    "same_collection": 2,
    "same_brand": 2,
    "shared_tags": 1,
}


def _eligible(products: list[Product]) -> list[Product]:
    from .public import _summaries

    if not products:
        return []
    digital_on = "digital_downloads" in enabled_modules()
    physical = set(
        ProductVariant.objects.filter(
            product__in=products, is_active=True, kind=VariantKind.PHYSICAL
        ).values_list("product_id", flat=True)
    )
    summarised = _summaries(products)
    return [
        product
        for product in summarised
        if getattr(product, "in_stock", False)
        and getattr(product, "price_range", None) is not None
        and (digital_on or product.pk in physical)
    ]


def related(product: Product, *, limit: int = 8) -> list[Product]:
    """Published related products for the storefront, pinned first."""
    overrides = list(
        ProductRecommendation.objects.filter(product=product).order_by("position", "pk")
    )
    hidden = {row.recommended_id for row in overrides if row.kind == RecommendationKind.HIDDEN}
    pinned_ids = [row.recommended_id for row in overrides if row.kind == RecommendationKind.PINNED]
    reasons = _reasons(product)
    ranked = sorted(
        (pk for pk in reasons if pk not in hidden and pk not in pinned_ids),
        key=lambda pk: (-sum(WEIGHTS[r] for r in set(reasons[pk])), str(pk)),
    )
    ordered = [*pinned_ids, *ranked][: limit * 3]
    rows = {
        row.pk: row
        for row in Product.objects.filter(
            pk__in=ordered, status=ProductStatus.ACTIVE
        ).select_related("brand")
    }
    candidates = [rows[pk] for pk in ordered if pk in rows]
    return _eligible(candidates)[: min(limit, MAX_RESULTS)]


def merchant_view(product: Product) -> dict[str, Any]:
    overrides = list(
        ProductRecommendation.objects.filter(product=product)
        .select_related("recommended")
        .order_by("position", "pk")
    )
    reasons = _reasons(product)
    hidden = {row.recommended_id for row in overrides if row.kind == RecommendationKind.HIDDEN}
    automatic = sorted(
        (pk for pk in reasons if pk not in hidden),
        key=lambda pk: (-sum(WEIGHTS[r] for r in set(reasons[pk])), str(pk)),
    )[:MAX_RESULTS]
    names = {row.pk: row for row in Product.objects.filter(pk__in=automatic)}
    return {
        "pinned": [
            _entry(row.recommended, [])
            for row in overrides
            if row.kind == RecommendationKind.PINNED
        ],
        "hidden": [
            _entry(row.recommended, [])
            for row in overrides
            if row.kind == RecommendationKind.HIDDEN
        ],
        "automatic": [
            _entry(names[pk], sorted(set(reasons[pk]))) for pk in automatic if pk in names
        ],
        "storefront": [_entry(row, []) for row in related(product)],
    }


def _entry(product: Product, reasons: list[str]) -> dict[str, Any]:
    return {
        "id": product.pk,
        "name": product.name,
        "slug": product.slug,
        "status": product.status,
        "reasons": reasons,
    }


def set_overrides(
    *, product: Product, pinned: Sequence[Product], hidden: Sequence[Product], actor: User
) -> None:
    if len(pinned) > MAX_PINNED:
        raise ValidationError({"pinned": [f"Pin at most {MAX_PINNED} products."]})
    ids = [p.pk for p in pinned] + [p.pk for p in hidden]
    if product.pk in ids:
        raise ValidationError({"pinned": ["A product cannot recommend itself."]})
    if len(set(ids)) != len(ids):
        raise ValidationError({"pinned": ["A product can be either pinned or hidden, once."]})
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=product.pk)
        ProductRecommendation.objects.filter(product=product).delete()
        ProductRecommendation.objects.bulk_create(
            [
                ProductRecommendation(
                    product=product, recommended=row, kind=RecommendationKind.PINNED, position=index
                )
                for index, row in enumerate(pinned)
            ]
            + [
                ProductRecommendation(
                    product=product, recommended=row, kind=RecommendationKind.HIDDEN, position=index
                )
                for index, row in enumerate(hidden)
            ]
        )
        record_audit_event(
            action="catalog.product.recommendations_set",
            resource=product,
            actor=actor,
            changes={"pinned": len(pinned), "hidden": len(hidden)},
        )
