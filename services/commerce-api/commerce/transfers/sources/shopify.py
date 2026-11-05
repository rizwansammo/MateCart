"""Shopify Admin GraphQL source (D-107), read-only.

Written against shopify.dev/docs/api/admin-graphql, version ``2026-10`` (checked 2026-11-05):

- Endpoint ``https://<shop>.myshopify.com/admin/api/2026-10/graphql.json``, header
  ``X-Shopify-Access-Token``. Only ``*.myshopify.com`` shop domains are accepted.
- Credentials, never a password: either the access token of an existing custom app created in
  the Shopify admin (new ones can no longer be created there), or the client id and secret of an
  app the merchant creates in their own Dev Dashboard, from which MateCart obtains short-lived
  (24 hour) tokens with the client credentials grant. Read scopes needed: ``read_products``,
  ``read_inventory``; optionally ``read_customers``, ``read_orders`` (+ ``read_all_orders``
  for orders older than 60 days) and ``read_discounts``.
- Cursor pagination (``first``/``after``) with small pages so a query stays under the 1,000
  point cost limit; ``THROTTLED`` errors and HTTP 429 back off and retry later.
- Large catalogues (more than ``BULK_THRESHOLD`` products) use a bulk operation
  (``bulkOperationRunQuery``, polled with ``bulkOperation(id:)``), whose JSONL result is read
  with ``__parentId`` links. Bulk queries are not charged against rate limits.
- Mapping notes: Shopify product status ``ACTIVE`` becomes a published candidate, ``DRAFT``,
  ``ARCHIVED`` and ``UNLISTED`` stay drafts; compare-at prices are kept, sale logic is not;
  inventory is the variant's total ``inventoryQuantity`` (locations are not split).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from typing import Any

from commerce.core import outbound

from .base import Page, SourceError, SourceInfo

API_VERSION = "2026-10"
BULK_THRESHOLD = 1000
SHOP = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
MAX_RESPONSE = 8 * 1024 * 1024
MAX_BULK_RESULT = 64 * 1024 * 1024

PRODUCT_FIELDS = """
id title handle status descriptionHtml productType vendor tags
seo { title description }
options { name }
collections(first: 5) { nodes { title } }
media(first: 5) { nodes { ... on MediaImage { image { url altText } } } }
variants(first: 50) { nodes { id sku price compareAtPrice barcode inventoryQuantity
  selectedOptions { name value } inventoryItem { tracked } } }
"""
QUERIES = {
    "products": "query($after: String) { products(first: 10, after: $after, sortKey: ID) { "
    "pageInfo { hasNextPage endCursor } nodes { " + PRODUCT_FIELDS + " } } }",
    "customers": "query($after: String) { customers(first: 50, after: $after, sortKey: ID) { "
    "pageInfo { hasNextPage endCursor } nodes { id firstName lastName numberOfOrders createdAt "
    "defaultEmailAddress { emailAddress } defaultPhoneNumber { phoneNumber } "
    "defaultAddress { city countryCodeV2 } } } }",
    "orders": "query($after: String) { orders(first: 10, after: $after, sortKey: CREATED_AT) { "
    "pageInfo { hasNextPage endCursor } nodes { id name createdAt cancelledAt "
    "displayFinancialStatus displayFulfillmentStatus currencyCode customer { id displayName } "
    "subtotalPriceSet { shopMoney { amount } } totalShippingPriceSet { shopMoney { amount } } "
    "totalTaxSet { shopMoney { amount } } totalDiscountsSet { shopMoney { amount } } "
    "totalPriceSet { shopMoney { amount } } lineItems(first: 50) { nodes { name sku quantity "
    "originalUnitPriceSet { shopMoney { amount } } "
    "originalTotalSet { shopMoney { amount } } } } } } }",
    "coupons": "query($after: String) { codeDiscountNodes(first: 25, after: $after) { "
    "pageInfo { hasNextPage endCursor } nodes { id codeDiscount { __typename "
    "... on DiscountCodeBasic { title startsAt endsAt usageLimit appliesOncePerCustomer "
    "codes(first: 1) { nodes { code } } "
    "customerGets { items { __typename } value { __typename "
    "... on DiscountPercentage { percentage } "
    "... on DiscountAmount { amount { amount currencyCode } } } } "
    "minimumRequirement { __typename ... on DiscountMinimumSubtotal { "
    "greaterThanOrEqualToSubtotal { amount } } } } } } } }",
}
CONNECTION = {
    "products": "products",
    "customers": "customers",
    "orders": "orders",
    "coupons": "codeDiscountNodes",
}
BULK_QUERY = (
    "{ products { edges { node { id title handle status descriptionHtml productType vendor tags "
    "seo { title description } options { name } "
    "variants { edges { node { id sku price compareAtPrice barcode inventoryQuantity "
    "selectedOptions { name value } inventoryItem { tracked } } } } } } } }"
)


def shop_domain(value: str) -> str:
    text = value.strip().lower()
    if "://" in text:
        text = urllib.parse.urlsplit(text).hostname or ""
    text = text.rstrip("/").rstrip(".")
    if not SHOP.match(text):
        raise SourceError("refused", "shopify_domain_invalid")
    return text


def _gid(value: Any) -> str:
    text = str(value or "")
    return text.rsplit("/", 1)[-1] if text.startswith("gid://") else text


def _money(value: Any) -> Any:
    if isinstance(value, Mapping):
        shop = value.get("shopMoney") if isinstance(value.get("shopMoney"), Mapping) else value
        return shop.get("amount") if isinstance(shop, Mapping) else None
    return value


class ShopifySource:
    platform = "shopify"

    def __init__(self, shop: str, credentials: Mapping[str, str]) -> None:
        self.shop = shop_domain(shop)
        self.identity = self.shop
        self._credentials = dict(credentials)
        self._token = self._credentials.get("access_token", "")

    def _post(self, path: str, body: bytes, headers: dict[str, str]) -> outbound.Response:
        try:
            return outbound.request(
                "POST",
                f"https://{self.shop}{path}",
                headers=headers,
                body=body,
                timeout=30.0,
                max_bytes=MAX_RESPONSE,
                policy=outbound.Policy(
                    max_redirects=0, host_allowed=lambda host: host == self.shop
                ),
            )
        except outbound.OutboundError as exc:
            kind = (
                "refused"
                if exc.code.startswith(("host_", "https_", "url_", "port_"))
                else "unreachable"
            )
            raise SourceError(kind, exc.code) from None

    def token(self) -> str:
        """The access token, minted with the client credentials grant when needed."""
        if self._token:
            return self._token
        client_id = self._credentials.get("client_id", "")
        secret = self._credentials.get("client_secret", "")
        if not client_id or not secret:
            raise SourceError("invalid_credentials", "shopify_credentials_missing")
        body = urllib.parse.urlencode(
            {"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}
        ).encode()
        response = self._post(
            "/admin/oauth/access_token",
            body,
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        if response.status in (400, 401, 403):
            raise SourceError("invalid_credentials", f"shopify_token_{response.status}")
        if response.status != 200:
            raise SourceError("unreachable", f"shopify_token_{response.status}")
        try:
            token = json.loads(response.body).get("access_token")
        except (ValueError, AttributeError):
            token = None
        if not isinstance(token, str) or not token:
            raise SourceError("bad_response", "shopify_token_missing")
        self._token = token
        return token

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": dict(variables or {})}).encode()
        response = self._post(
            f"/admin/api/{API_VERSION}/graphql.json",
            body,
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Shopify-Access-Token": self.token(),
            },
        )
        if response.status == 401:
            raise SourceError("invalid_credentials", "shopify_401")
        if response.status == 403:
            raise SourceError("permission", "shopify_403")
        if response.status == 429:
            raise SourceError("rate_limited", "shopify_429", retry_after=2.0)
        if response.status != 200:
            raise SourceError("unreachable", f"shopify_{response.status}")
        try:
            payload = json.loads(response.body)
        except ValueError:
            raise SourceError("bad_response", "shopify_not_json") from None
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            codes = {
                str((error.get("extensions") or {}).get("code") or "")
                for error in errors
                if isinstance(error, Mapping)
            }
            if "THROTTLED" in codes:
                raise SourceError("rate_limited", "shopify_throttled", retry_after=2.0)
            if "ACCESS_DENIED" in codes:
                raise SourceError("permission", "shopify_access_denied")
            raise SourceError("bad_response", "shopify_graphql_error")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise SourceError("bad_response", "shopify_no_data")
        return data

    def verify(self) -> SourceInfo:
        data = self.graphql(
            "{ shop { name currencyCode } currentAppInstallation { accessScopes { handle } } "
            "productsCount { count } }"
        )
        shop = data.get("shop") or {}
        scopes = {
            str(scope.get("handle"))
            for scope in ((data.get("currentAppInstallation") or {}).get("accessScopes") or [])
            if isinstance(scope, Mapping)
        }
        if "read_products" not in scopes and "write_products" not in scopes:
            raise SourceError("permission", "shopify_read_products_missing")
        capabilities = {
            "categories": False,  # Shopify collections are imported with products
            "products": True,
            "customers": bool(scopes & {"read_customers", "write_customers"}),
            "orders": bool(scopes & {"read_orders", "write_orders"}),
            "coupons": bool(scopes & {"read_discounts", "write_discounts"}),
            "all_orders": "read_all_orders" in scopes,
        }
        count = (data.get("productsCount") or {}).get("count")
        return SourceInfo(
            name=str(shop.get("name") or self.shop)[:200],
            currency=str(shop.get("currencyCode") or "")[:3],
            capabilities=capabilities,
            counts={"products": count if isinstance(count, int) else None},
        )

    def page(self, object_type: str, cursor: str | None) -> Page:
        if object_type == "categories":
            return Page(records=[], next_cursor=None)
        if object_type == "products":
            if cursor is None:
                count = (
                    self.graphql("{ productsCount { count } }").get("productsCount") or {}
                ).get("count")
                if isinstance(count, int) and count > BULK_THRESHOLD:
                    return self._start_bulk()
            elif cursor.startswith("bulk:"):
                return self._poll_bulk(cursor[5:])
        after = cursor[6:] if cursor and cursor.startswith("after:") else None
        data = self.graphql(QUERIES[object_type], {"after": after})
        connection = data.get(CONNECTION[object_type]) or {}
        info = connection.get("pageInfo") or {}
        records = [
            self.normalize(object_type, node)
            for node in connection.get("nodes") or []
            if isinstance(node, Mapping)
        ]
        if object_type == "coupons":
            records = [record for record in records if record]
        next_cursor = (
            f"after:{info['endCursor']}"
            if info.get("hasNextPage") and info.get("endCursor")
            else None
        )
        return Page(records=records, next_cursor=next_cursor)

    # --- Bulk operations --------------------------------------------------------------------------

    def _start_bulk(self) -> Page:
        data = self.graphql(
            "mutation($q: String!) { bulkOperationRunQuery(query: $q) { "
            "bulkOperation { id status } userErrors { field message } } }",
            {"q": BULK_QUERY},
        )
        result = data.get("bulkOperationRunQuery") or {}
        if result.get("userErrors"):
            raise SourceError("bad_response", "shopify_bulk_refused")
        operation = (result.get("bulkOperation") or {}).get("id")
        if not operation:
            raise SourceError("bad_response", "shopify_bulk_missing")
        return Page(records=[], next_cursor=f"bulk:{operation}", wait=10.0)

    def _poll_bulk(self, operation: str) -> Page:
        data = self.graphql(
            "query($id: ID!) { bulkOperation(id: $id) { status errorCode url objectCount } }",
            {"id": operation},
        )
        state = data.get("bulkOperation") or {}
        status = state.get("status")
        if status in ("CREATED", "RUNNING"):
            return Page(records=[], next_cursor=f"bulk:{operation}", wait=10.0)
        if status != "COMPLETED":
            raise SourceError("bad_response", "shopify_bulk_failed")
        url = state.get("url")
        if not url:
            return Page(records=[], next_cursor=None)
        try:
            # A signed download URL returned by Shopify: HTTPS, public address, no credentials.
            response = outbound.request(
                "GET",
                str(url),
                timeout=120.0,
                max_bytes=MAX_BULK_RESULT,
                policy=outbound.Policy(max_redirects=2),
            )
        except outbound.OutboundError as exc:
            raise SourceError("unreachable", exc.code) from None
        if response.status != 200:
            raise SourceError("unreachable", f"shopify_bulk_{response.status}")
        return Page(records=self.parse_bulk(response.body), next_cursor=None)

    def parse_bulk(self, data: bytes) -> list[dict[str, Any]]:
        products: dict[str, dict[str, Any]] = {}
        variants: dict[str, list[dict[str, Any]]] = {}
        for line in data.decode("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            parent = row.get("__parentId")
            if parent:
                variants.setdefault(str(parent), []).append(row)
            elif str(row.get("id", "")).startswith("gid://shopify/Product/"):
                products[str(row["id"])] = row
        records = []
        for gid, product in products.items():
            product = {**product, "variants": {"nodes": variants.get(gid, [])}}
            records.append(self.normalize("products", product))
        return records

    # --- Normalisation ----------------------------------------------------------------------------

    def normalize(self, object_type: str, node: Mapping[str, Any]) -> dict[str, Any]:
        if object_type == "products":
            return self._product(node)
        if object_type == "customers":
            email = (node.get("defaultEmailAddress") or {}).get("emailAddress") or ""
            phone = (node.get("defaultPhoneNumber") or {}).get("phoneNumber") or ""
            address = node.get("defaultAddress") or {}
            count = node.get("numberOfOrders")
            try:
                orders = int(count) if count is not None else None
            except (TypeError, ValueError):
                orders = None
            return {
                "external_id": _gid(node.get("id")),
                "first_name": node.get("firstName") or "",
                "last_name": node.get("lastName") or "",
                "email": email,
                "phone": phone,
                "city": address.get("city") or "",
                "country": address.get("countryCodeV2") or "",
                "accepts_marketing": None,
                "orders_count": orders,
                "created_at": node.get("createdAt"),
            }
        if object_type == "orders":
            customer = node.get("customer") or {}
            return {
                "external_id": _gid(node.get("id")),
                "number": node.get("name") or _gid(node.get("id")),
                "placed_at": node.get("createdAt"),
                "status": "cancelled" if node.get("cancelledAt") else "open",
                "financial_status": str(node.get("displayFinancialStatus") or "").lower(),
                "fulfillment_status": str(node.get("displayFulfillmentStatus") or "").lower(),
                "currency": node.get("currencyCode") or "",
                "subtotal": _money(node.get("subtotalPriceSet")),
                "shipping_total": _money(node.get("totalShippingPriceSet")),
                "tax_total": _money(node.get("totalTaxSet")),
                "discount_total": _money(node.get("totalDiscountsSet")),
                "grand_total": _money(node.get("totalPriceSet")),
                "customer_external_id": _gid(customer.get("id")) if customer else "",
                "customer_name": customer.get("displayName") or "",
                "lines": [
                    {
                        "name": line.get("name"),
                        "sku": line.get("sku"),
                        "quantity": line.get("quantity"),
                        "unit_price": _money(line.get("originalUnitPriceSet")),
                        "total": _money(line.get("originalTotalSet")),
                    }
                    for line in (node.get("lineItems") or {}).get("nodes") or []
                    if isinstance(line, Mapping)
                ],
            }
        return self._coupon(node)

    def _coupon(self, node: Mapping[str, Any]) -> dict[str, Any]:
        discount = node.get("codeDiscount") or {}
        if discount.get("__typename") != "DiscountCodeBasic":
            return {
                "external_id": _gid(node.get("id")),
                "code": "",
                "discount_type": None,
                "unsupported": [f"{discount.get('__typename') or 'unknown'} discounts"],
            }
        codes = (discount.get("codes") or {}).get("nodes") or []
        gets = discount.get("customerGets") or {}
        value = gets.get("value") or {}
        unsupported = []
        if (gets.get("items") or {}).get("__typename") != "AllDiscountItems":
            unsupported.append("product or collection restrictions")
        if discount.get("appliesOncePerCustomer"):
            unsupported.append("once per customer")
        kind, amount, currency = None, None, ""
        if value.get("__typename") == "DiscountPercentage":
            kind = "percent"
            try:
                amount = str(round(float(value.get("percentage") or 0) * 100, 2))
            except (TypeError, ValueError):
                amount = None
        elif value.get("__typename") == "DiscountAmount":
            kind = "fixed"
            amount = (value.get("amount") or {}).get("amount")
            currency = (value.get("amount") or {}).get("currencyCode") or ""
        minimum = discount.get("minimumRequirement") or {}
        if minimum.get("__typename") not in (None, "DiscountMinimumSubtotal"):
            unsupported.append("minimum quantity requirement")
        return {
            "external_id": _gid(node.get("id")),
            "code": (codes[0] or {}).get("code") if codes else "",
            "discount_type": kind,
            "amount": amount,
            "currency": currency,
            "minimum_subtotal": (minimum.get("greaterThanOrEqualToSubtotal") or {}).get("amount"),
            "starts_at": discount.get("startsAt"),
            "ends_at": discount.get("endsAt"),
            "usage_limit": discount.get("usageLimit"),
            "unsupported": unsupported,
        }

    def _product(self, node: Mapping[str, Any]) -> dict[str, Any]:
        warnings: list[str] = []
        status = str(node.get("status") or "").upper()
        if status in ("ARCHIVED", "UNLISTED"):
            warnings.append(f"Shopify status {status.lower()}: imported as a draft.")
        options = [
            str(option.get("name"))
            for option in node.get("options") or []
            if isinstance(option, Mapping) and option.get("name") != "Title"
        ][:3]
        variants = []
        for variant in (node.get("variants") or {}).get("nodes") or []:
            if not isinstance(variant, Mapping):
                continue
            tracked = bool((variant.get("inventoryItem") or {}).get("tracked"))
            variants.append(
                {
                    "external_id": _gid(variant.get("id")),
                    "sku": variant.get("sku") or "",
                    "options": {
                        str(option.get("name")): str(option.get("value"))
                        for option in variant.get("selectedOptions") or []
                        if isinstance(option, Mapping) and option.get("name") in options
                    },
                    "price": variant.get("price"),
                    "compare_at": variant.get("compareAtPrice"),
                    "barcode": variant.get("barcode") or "",
                    "stock": variant.get("inventoryQuantity") if tracked else None,
                    "track_inventory": tracked,
                }
            )
        seo = node.get("seo") or {}
        images = []
        for media in (node.get("media") or {}).get("nodes") or []:
            image = (media or {}).get("image") if isinstance(media, Mapping) else None
            if isinstance(image, Mapping) and image.get("url"):
                images.append({"url": image["url"], "alt": image.get("altText") or ""})
        return {
            "external_id": _gid(node.get("id")),
            "handle": node.get("handle") or "",
            "name": node.get("title") or "",
            "product_type": "standard",
            "status": "active" if status == "ACTIVE" else "draft",
            "brand": node.get("vendor") or "",
            "categories": [[str(node["productType"])]] if node.get("productType") else [],
            "collections": [
                str(c.get("title"))
                for c in (node.get("collections") or {}).get("nodes") or []
                if isinstance(c, Mapping)
            ],
            "tags": [str(tag) for tag in node.get("tags") or []],
            "summary": "",
            "description": node.get("descriptionHtml") or "",
            "seo_title": seo.get("title") or "",
            "seo_description": seo.get("description") or "",
            "options": options,
            "variants": variants,
            "images": images,
            "details": {},
            "source_warnings": warnings,
        }
