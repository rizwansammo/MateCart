"""WooCommerce REST API v3 source (D-107), read-only.

Written against developer.woocommerce.com/docs/apis/rest-api (checked 2026-11-05):
``/wp-json/wc/v3`` over HTTPS with a consumer key and secret sent as HTTP Basic credentials.
The merchant creates a key with **Read** permission (WooCommerce → Settings → Advanced → REST
API); MateCart never asks for a WordPress password, installs nothing and never writes.

- Pagination: ``page`` and ``per_page`` (at most 100), ``X-WP-TotalPages`` decides the next page.
- Store currency: ``GET data/currencies/current``.
- Variable products: variations come from ``products/<id>/variations``.
- Redirects are refused (credentials must never follow a redirect to another host); the
  merchant enters the final ``https://`` address of the store.
- Sale prices are not imported as permanent prices (they may be scheduled); grouped and external
  products are not sellable items and are reported, not imported. Weights are not imported
  because the source's weight unit is a store setting MateCart cannot confirm.
- SEO: Yoast's ``_yoast_wpseo_title`` and ``_yoast_wpseo_metadesc`` are used when the API
  returns them in ``meta_data`` (not documented by WooCommerce; absent is fine).
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from collections.abc import Mapping
from typing import Any

from commerce.core import outbound

from .base import Page, SourceError, SourceInfo

API = "/wp-json/wc/v3"
PAGE_SIZES = {"categories": 100, "products": 25, "customers": 100, "orders": 50, "coupons": 100}
ENDPOINTS = {
    "categories": "products/categories",
    "products": "products",
    "customers": "customers",
    "orders": "orders",
    "coupons": "coupons",
}
MAX_RESPONSE = 8 * 1024 * 1024


def _classify(response: outbound.Response) -> SourceError:
    status = response.status
    if status == 401:
        return SourceError("invalid_credentials", "woo_401")
    if status == 403:
        return SourceError("permission", "woo_403")
    if status == 404:
        return SourceError("unreachable", "woo_api_not_found")
    if status == 429:
        try:
            wait = float(response.header("retry-after") or 10)
        except ValueError:
            wait = 10.0
        return SourceError("rate_limited", "woo_429", retry_after=min(wait, 120.0))
    if status >= 500:
        return SourceError("unreachable", f"woo_{status}")
    return SourceError("bad_response", f"woo_{status}")


class WooCommerceSource:
    platform = "woocommerce"

    def __init__(self, store_url: str, key: str, secret: str) -> None:
        target = outbound.parse_url(store_url)
        self.host = target.host
        self.identity = target.host
        self._auth = "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()
        self._variation_cache: dict[str, list[dict[str, Any]]] = {}

    def _get(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> tuple[Any, outbound.Response]:
        url = f"https://{self.host}{API}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            response = outbound.request(
                "GET",
                url,
                headers={"Authorization": self._auth, "Accept": "application/json"},
                timeout=30.0,
                max_bytes=MAX_RESPONSE,
                policy=outbound.Policy(max_redirects=0),
            )
        except outbound.OutboundError as exc:
            kind = (
                "refused"
                if exc.code.startswith(("host_", "https_", "url_", "port_"))
                else "unreachable"
            )
            raise SourceError(kind, exc.code) from None
        if response.status != 200:
            raise _classify(response)
        try:
            return json.loads(response.body), response
        except ValueError:
            raise SourceError("bad_response", "woo_not_json") from None

    def _count(self, path: str) -> int | None:
        try:
            _, response = self._get(path, {"per_page": 1})
        except SourceError as exc:
            if exc.kind == "permission":
                return None
            raise
        try:
            return int(response.header("x-wp-total"))
        except ValueError:
            return 0

    def verify(self) -> SourceInfo:
        currency, _ = self._get("data/currencies/current")
        code = str(currency.get("code") or "") if isinstance(currency, dict) else ""
        counts: dict[str, int | None] = {}
        capabilities: dict[str, bool] = {}
        for object_type, path in ENDPOINTS.items():
            counts[object_type] = self._count(path)
            capabilities[object_type] = counts[object_type] is not None
        if not capabilities.get("products"):
            raise SourceError("permission", "woo_products_unreadable")
        return SourceInfo(
            name=self.host, currency=code.upper()[:3], capabilities=capabilities, counts=counts
        )

    def page(self, object_type: str, cursor: str | None) -> Page:
        number = int(cursor or 1)
        params: dict[str, Any] = {"per_page": PAGE_SIZES[object_type], "page": number}
        if object_type in ("products", "orders", "customers", "coupons", "categories"):
            params["orderby"] = "id"
            params["order"] = "asc"
        if object_type == "products":
            params["status"] = "any"
        if object_type == "customers":
            params["role"] = "all"
        rows, response = self._get(ENDPOINTS[object_type], params)
        try:
            pages = int(response.header("x-wp-totalpages") or 1)
        except ValueError:
            pages = 1
        records = (
            [self.normalize(object_type, row) for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else []
        )
        return Page(records=records, next_cursor=str(number + 1) if number < pages else None)

    # --- Normalisation ----------------------------------------------------------------------------

    def normalize(self, object_type: str, row: Mapping[str, Any]) -> dict[str, Any]:
        if object_type == "categories":
            return {
                "external_id": str(row.get("id")),
                "name": row.get("name") or "",
                "parent_external_id": str(row.get("parent") or "") if row.get("parent") else "",
                "description": row.get("description") or "",
            }
        if object_type == "products":
            return self._product(row)
        if object_type == "customers":
            billing = _map(row.get("billing"))
            return {
                "external_id": str(row.get("id")),
                "first_name": row.get("first_name") or billing.get("first_name") or "",
                "last_name": row.get("last_name") or billing.get("last_name") or "",
                "email": row.get("email") or "",
                "phone": billing.get("phone") or "",
                "city": billing.get("city") or "",
                "country": billing.get("country") or "",
                "accepts_marketing": None,
                "orders_count": None,
                "created_at": row.get("date_created_gmt") and f"{row['date_created_gmt']}Z",
            }
        if object_type == "orders":
            billing = _map(row.get("billing"))
            lines = [
                {
                    "name": item.get("name"),
                    "sku": item.get("sku"),
                    "quantity": item.get("quantity"),
                    "unit_price": item.get("price"),
                    "total": item.get("total"),
                }
                for item in row.get("line_items") or []
                if isinstance(item, Mapping)
            ]
            total_items = sum(_decimal(line["total"]) for line in lines)
            return {
                "external_id": str(row.get("id")),
                "number": str(row.get("number") or row.get("id")),
                "placed_at": row.get("date_created_gmt") and f"{row['date_created_gmt']}Z",
                "status": row.get("status") or "",
                "financial_status": "paid" if row.get("date_paid_gmt") else "not paid",
                "fulfillment_status": "",
                "currency": row.get("currency") or "",
                "subtotal": str(total_items),
                "shipping_total": row.get("shipping_total"),
                "tax_total": row.get("total_tax"),
                "discount_total": row.get("discount_total"),
                "grand_total": row.get("total"),
                "customer_external_id": str(row.get("customer_id") or "")
                if row.get("customer_id")
                else "",
                "customer_name": " ".join(
                    part for part in (billing.get("first_name"), billing.get("last_name")) if part
                ),
                "lines": lines,
            }
        return self._coupon(row)

    def _coupon(self, row: Mapping[str, Any]) -> dict[str, Any]:
        unsupported = []
        kind = {"percent": "percent", "fixed_cart": "fixed"}.get(str(row.get("discount_type")))
        if row.get("discount_type") == "fixed_product":
            unsupported.append("per-product fixed discounts")
        for key, reason in (
            ("product_ids", "product restrictions"),
            ("excluded_product_ids", "product exclusions"),
            ("product_categories", "category restrictions"),
            ("email_restrictions", "customer e-mail restrictions"),
            ("usage_limit_per_user", "per-customer limits"),
            ("maximum_amount", "maximum spend"),
        ):
            if row.get(key):
                unsupported.append(reason)
        if row.get("free_shipping"):
            unsupported.append("free shipping")
        if row.get("individual_use"):
            unsupported.append("individual use only")
        return {
            "external_id": str(row.get("id")),
            "code": row.get("code") or "",
            "discount_type": kind,
            "amount": row.get("amount"),
            "minimum_subtotal": row.get("minimum_amount") or None,
            "starts_at": None,
            "ends_at": row.get("date_expires_gmt") and f"{row['date_expires_gmt']}Z",
            "usage_limit": row.get("usage_limit"),
            "unsupported": unsupported,
        }

    def _product(self, row: Mapping[str, Any]) -> dict[str, Any]:
        product_id = str(row.get("id"))
        kind = row.get("type")
        warnings: list[str] = []
        meta = {
            str(entry.get("key")): entry.get("value")
            for entry in row.get("meta_data") or []
            if isinstance(entry, Mapping)
        }
        attributes = [a for a in row.get("attributes") or [] if isinstance(a, Mapping)]
        option_names = [str(a.get("name")) for a in attributes if a.get("variation")][:3]
        specifications = {
            str(a.get("name")): ", ".join(str(o) for o in a.get("options") or [])
            for a in attributes
            if not a.get("variation")
        }
        variants: list[dict[str, Any]] = []
        if kind == "variable":
            for variation in self._variations(product_id):
                variants.append(
                    {
                        "external_id": str(variation.get("id")),
                        "sku": variation.get("sku") or "",
                        "options": {
                            str(a.get("name")): str(a.get("option"))
                            for a in variation.get("attributes") or []
                            if isinstance(a, Mapping)
                        },
                        "price": variation.get("regular_price") or None,
                        "stock": variation.get("stock_quantity")
                        if variation.get("manage_stock") is True
                        else None,
                        "track_inventory": bool(variation.get("manage_stock")),
                    }
                )
        elif kind in ("simple", None, ""):
            variants.append(
                {
                    "external_id": product_id,
                    "sku": row.get("sku") or "",
                    "options": {},
                    "price": row.get("regular_price") or None,
                    "stock": row.get("stock_quantity") if row.get("manage_stock") else None,
                    "track_inventory": bool(row.get("manage_stock")),
                }
            )
            option_names = []
        if row.get("sale_price"):
            warnings.append("Sale price not imported; create a MateCart campaign for it.")
        status = "active" if row.get("status") == "publish" else "draft"
        record = {
            "external_id": product_id,
            "handle": row.get("slug") or "",
            "name": row.get("name") or "",
            "product_type": "standard",
            "status": status,
            "brand": "",
            "categories": [
                [str(c.get("name"))] for c in row.get("categories") or [] if isinstance(c, Mapping)
            ],
            "category_refs": [
                str(c.get("id")) for c in row.get("categories") or [] if isinstance(c, Mapping)
            ],
            "collections": [],
            "tags": [str(t.get("name")) for t in row.get("tags") or [] if isinstance(t, Mapping)],
            "summary": row.get("short_description") or "",
            "description": row.get("description") or "",
            "seo_title": meta.get("_yoast_wpseo_title") or "",
            "seo_description": meta.get("_yoast_wpseo_metadesc") or "",
            "options": option_names,
            "variants": variants,
            "images": [
                {"url": image.get("src") or "", "alt": image.get("alt") or ""}
                for image in row.get("images") or []
                if isinstance(image, Mapping)
            ],
            "specifications": specifications,
            "details": {},
            "source_warnings": warnings,
        }
        if kind in ("grouped", "external"):
            record["unsupported"] = f"{kind} products are not sellable items in MateCart"
        return record

    def _variations(self, product_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        number = 1
        while number <= 3:  # at most 300 variations; MateCart allows 250 per product
            page, response = self._get(
                f"products/{urllib.parse.quote(product_id)}/variations",
                {"per_page": 100, "page": number},
            )
            rows.extend(row for row in page if isinstance(row, dict))
            try:
                pages = int(response.header("x-wp-totalpages") or 1)
            except ValueError:
                pages = 1
            if number >= pages:
                break
            number += 1
        return rows


def _decimal(value: Any) -> Any:
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal(0)


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
