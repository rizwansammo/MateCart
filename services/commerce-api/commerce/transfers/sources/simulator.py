"""Development migration simulator (D-107): a small sample store that answers locally.

It exists so the migration workflow can be accepted on localhost without a real WooCommerce or
Shopify store. It never opens a network connection, its connections are labelled "Simulator"
everywhere, and production settings refuse it (``MATECART_MIGRATION_SIMULATOR``). Records use
the same normalised shapes as the real adapters, so the real pipeline processes them.
"""

from __future__ import annotations

from typing import Any

from .base import Page, SourceInfo

PAGE = 3


def _products(prefix: str) -> list[dict[str, Any]]:
    return [
        {
            "external_id": "101",
            "handle": "sim-cotton-tee",
            "name": "Simulated Cotton Tee",
            "product_type": "standard",
            "status": "active",
            "brand": "Sim Basics",
            "categories": [["Simulated", "Tops"]],
            "category_refs": ["11"],
            "collections": ["Simulated picks"],
            "tags": ["cotton", "simulated"],
            "summary": "A plain tee from the migration simulator.",
            "description": "<p>Soft <strong>cotton</strong> tee.</p><ul><li>Regular fit</li></ul>",
            "seo_title": "Simulated Cotton Tee",
            "seo_description": "",
            "options": ["Size"],
            "variants": [
                {
                    "external_id": "1011",
                    "sku": f"{prefix}-TEE-S",
                    "options": {"Size": "S"},
                    "price": "450",
                    "stock": 5,
                    "track_inventory": True,
                },
                {
                    "external_id": "1012",
                    "sku": f"{prefix}-TEE-M",
                    "options": {"Size": "M"},
                    "price": "450",
                    "stock": 8,
                    "track_inventory": True,
                },
            ],
            "images": [],
            "specifications": {"Material": "Cotton"},
            "details": {},
        },
        {
            "external_id": "102",
            "handle": "sim-steel-bottle",
            "name": "Simulated Steel Bottle",
            "product_type": "standard",
            "status": "draft",
            "brand": "",
            "categories": [["Simulated"]],
            "category_refs": ["10"],
            "collections": [],
            "tags": ["bottle"],
            "summary": "",
            "description": "750 ml insulated bottle.",
            "seo_title": "",
            "seo_description": "",
            "options": [],
            "variants": [
                {
                    "external_id": "102",
                    "sku": f"{prefix}-BOTTLE",
                    "options": {},
                    "price": "990",
                    "compare_at": "1200",
                    "stock": None,
                    "track_inventory": False,
                },
            ],
            "images": [],
            "details": {},
        },
        {
            "external_id": "103",
            "handle": "sim-gift-card",
            "name": "Simulated Bundle",
            "product_type": "standard",
            "status": "draft",
            "categories": [],
            "options": [],
            "variants": [],
            "images": [],
            "unsupported": "grouped products are not sellable items in MateCart",
        },
    ]


def _customers() -> list[dict[str, Any]]:
    return [
        {
            "external_id": "201",
            "first_name": "Simulated",
            "last_name": "Buyer",
            "email": "sim.buyer@example.test",
            "phone": "+8801712000001",
            "city": "Dhaka",
            "country": "BD",
            "orders_count": 1,
            "created_at": "2025-03-01T10:00:00Z",
        },
        {
            "external_id": "202",
            "first_name": "Second",
            "last_name": "Buyer",
            "email": "",
            "phone": "",
            "city": "Chattogram",
            "country": "BD",
            "orders_count": 0,
            "created_at": "2025-04-02T10:00:00Z",
        },
    ]


def _orders(currency: str, prefix: str) -> list[dict[str, Any]]:
    return [
        {
            "external_id": "301",
            "number": "#1001",
            "placed_at": "2025-03-02T09:30:00Z",
            "status": "completed",
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "currency": currency,
            "subtotal": "900",
            "shipping_total": "60",
            "tax_total": "0",
            "discount_total": "0",
            "grand_total": "960",
            "customer_external_id": "201",
            "customer_name": "Simulated Buyer",
            "lines": [
                {
                    "name": "Simulated Cotton Tee - M",
                    "sku": f"{prefix}-TEE-M",
                    "quantity": 2,
                    "unit_price": "450",
                    "total": "900",
                }
            ],
        }
    ]


class SimulatedSource:
    def __init__(self, platform: str, identity: str, currency: str) -> None:
        self.platform = platform
        self.identity = identity
        self.currency = currency
        self.prefix = "SIMW" if platform == "woocommerce" else "SIMS"

    def verify(self) -> SourceInfo:
        counts: dict[str, int | None] = {
            "categories": 2,
            "products": 3,
            "customers": 2,
            "orders": 1,
            "coupons": 2,
        }
        return SourceInfo(
            name=f"Simulated {self.platform.title()} store",
            currency=self.currency,
            capabilities=dict.fromkeys(counts, True),
            counts=counts,
        )

    def _all(self, object_type: str) -> list[dict[str, Any]]:
        if object_type == "categories":
            return [
                {"external_id": "10", "name": "Simulated", "parent_external_id": ""},
                {"external_id": "11", "name": "Tops", "parent_external_id": "10"},
            ]
        if object_type == "products":
            return _products(self.prefix)
        if object_type == "customers":
            return _customers()
        if object_type == "orders":
            return _orders(self.currency, self.prefix)
        return [
            {
                "external_id": "401",
                "code": f"{self.prefix}WELCOME",
                "discount_type": "percent",
                "amount": "10",
                "minimum_subtotal": "500",
                "unsupported": [],
            },
            {
                "external_id": "402",
                "code": f"{self.prefix}SHIP",
                "discount_type": None,
                "unsupported": ["free shipping"],
            },
        ]

    def page(self, object_type: str, cursor: str | None) -> Page:
        rows = self._all(object_type)
        start = int(cursor or 0)
        end = start + PAGE
        return Page(records=rows[start:end], next_cursor=str(end) if end < len(rows) else None)
