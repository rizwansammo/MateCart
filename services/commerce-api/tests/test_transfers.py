"""Phase 15 data transfer: CSV import (preview, conflicts, approval, idempotent execution through
the domain services), exports (permissions, formula safety, audited downloads), migrations
(WooCommerce and Shopify adapters with scripted HTTP, the simulator end to end, historical
records, repeat runs, compensation) and tenant isolation."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from commerce.audit.models import AuditEvent
from commerce.catalog.models import Category, Product, ProductStatus, ProductVariant
from commerce.checkout.models import Coupon
from commerce.identity.models import AuthSession
from commerce.inventory.models import StockLevel, StockMovement
from commerce.notifications.models import Notification
from commerce.orders.models import Order, PaymentTransaction
from commerce.pricing.models import Price
from commerce.staff.permissions import StaffRole
from commerce.stores.models import StorePreset
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant
from commerce.transfers import csvio, pipeline
from commerce.transfers.models import (
    DataTransferJob,
    ExternalSourceMapping,
    HistoricalCustomer,
    HistoricalOrder,
    ImportItem,
    ItemAction,
    SourceConnection,
)
from commerce.transfers.sources.base import SourceError
from commerce.transfers.sources.shopify import ShopifySource
from commerce.transfers.sources.woocommerce import WooCommerceSource

from .ai_helpers import Call, fake_http
from .catalog_helpers import set_preset, simple_product
from .comms_helpers import run_on_commit, staff  # noqa: F401 (fixture)
from .factories import make_store_settings

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("run_on_commit")]
DATA = "/api/manage/data/"
HEADER = (
    "handle,name,product_type,status,categories,tags,summary,description,"
    "option1_name,option1_value,sku,price,opening_stock\n"
)


@pytest.fixture
def shop(store: Any, deployment_tenant: Tenant) -> Tenant:
    return deployment_tenant


def upload(client: Any, text: str, strategy: str = "skip", **options: Any) -> Any:
    file = SimpleUploadedFile("catalogue.csv", text.encode("utf-8"), content_type="text/csv")
    return client.post(
        f"{DATA}imports/",
        {"file": file, "strategy": strategy, "options": json.dumps(options)},
        format="multipart",
    )


def job(client: Any, job_id: str) -> dict[str, Any]:
    return dict(client.get(f"{DATA}jobs/{job_id}/").json())


def items(client: Any, job_id: str, **query: Any) -> list[dict[str, Any]]:
    return list(client.get(f"{DATA}jobs/{job_id}/items/", query).json()["results"])


TEE = (
    HEADER
    + 'tee,Cotton Tee,standard,active,Clothing > Tops,"cotton, summer",Soft tee,'
    + "<p>Soft <b>cotton</b></p>,Size,S,TEE-S,450,5\n"
    + "tee,Cotton Tee,standard,active,Clothing > Tops,,,,Size,M,TEE-M,450,7\n"
    + "mug,Mug,standard,draft,Kitchen,,,,,,MUG-1,abc,\n"
    + "dup,Duplicate,standard,draft,,,,,,,TEE-S,100,\n"
)


# --- CSV import -----------------------------------------------------------------------------------


def test_csv_import_previews_before_writing_anything(shop: Tenant) -> None:
    client, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    created = upload(client, TEE)
    assert created.status_code == 202
    detail = job(client, created.json()["id"])
    assert detail["status"] == "review"
    summary = detail["summary"]
    assert summary["total"] == 3
    assert summary["to_create"] == 1
    assert summary["invalid"] == 2
    rows = {row["title"]: row for row in items(client, detail["id"])}
    assert "price not a number" in json.dumps(rows["Mug"]["messages"])
    assert "appears in another record" in json.dumps(rows["Duplicate"]["messages"])
    assert rows["Cotton Tee"]["preview"]["variant_count"] == 2
    with tenant_context(shop):
        assert not Product.objects.exists()
        assert not Category.objects.exists()


def test_approved_import_uses_the_catalogue_and_inventory_ledger(shop: Tenant) -> None:
    client, _ = staff(shop)
    created = upload(client, TEE)
    approved = client.post(f"{DATA}jobs/{created.json()['id']}/approve/")
    assert approved.status_code == 200
    detail = job(client, created.json()["id"])
    assert detail["status"] == "completed_with_errors"
    assert (detail["created_count"], detail["failed_count"]) == (1, 2)
    assert detail["report_file"] is not None
    with tenant_context(shop):
        product = Product.objects.get(name="Cotton Tee")
        assert product.status == ProductStatus.DRAFT  # publish was not requested
        assert product.description == "Soft cotton"  # HTML became plain text
        assert product.tags == ["cotton", "summer"]
        parent = Category.objects.get(name="Tops").parent
        assert parent is not None
        assert parent.name == "Clothing"
        variants = {v.sku: v for v in ProductVariant.objects.filter(product=product)}
        assert set(variants) == {"TEE-S", "TEE-M"}
        assert Price.objects.get(variant=variants["TEE-M"]).amount == Decimal("450")
        receipts = StockMovement.objects.filter(reference_type="data_transfer_job")
        assert receipts.count() == 2
        assert StockLevel.objects.get(variant=variants["TEE-M"]).on_hand == Decimal("7")
        assert AuditEvent.objects.filter(action="catalog.product.created").count() == 1
        assert AuditEvent.objects.filter(action="transfers.job.approved").exists()


def test_publish_option_publishes_only_valid_products(shop: Tenant) -> None:
    client, _ = staff(shop)
    created = upload(client, TEE, publish=True)
    client.post(f"{DATA}jobs/{created.json()['id']}/approve/")
    with tenant_context(shop):
        assert Product.objects.get(name="Cotton Tee").status == ProductStatus.ACTIVE


def test_repeat_import_skips_existing_and_never_duplicates(shop: Tenant) -> None:
    client, _ = staff(shop)
    first = upload(client, TEE)
    client.post(f"{DATA}jobs/{first.json()['id']}/approve/")
    second = upload(client, TEE)
    detail = job(client, second.json()["id"])
    assert detail["summary"]["to_skip"] == 1
    assert detail["summary"]["to_create"] == 0
    client.post(f"{DATA}jobs/{second.json()['id']}/approve/")
    with tenant_context(shop):
        assert Product.objects.filter(name="Cotton Tee").count() == 1
        assert StockMovement.objects.filter(reference_type="data_transfer_job").count() == 2


def test_update_strategy_changes_content_but_never_stock_or_status(shop: Tenant) -> None:
    client, _ = staff(shop)
    first = upload(client, TEE, publish=True)
    client.post(f"{DATA}jobs/{first.json()['id']}/approve/")
    changed = TEE.replace("Soft tee", "Softer tee").replace(",450,5", ",999,50")
    second = upload(client, changed, strategy="update")
    assert job(client, second.json()["id"])["summary"]["to_update"] == 1
    client.post(f"{DATA}jobs/{second.json()['id']}/approve/")
    with tenant_context(shop):
        product = Product.objects.get(name="Cotton Tee")
        assert product.summary == "Softer tee"
        assert product.status == ProductStatus.ACTIVE
        variant = ProductVariant.objects.get(sku="TEE-S")
        assert StockLevel.objects.get(variant=variant).on_hand == Decimal("5")
        assert Price.objects.get(variant=variant).amount == Decimal("450")  # prices not chosen


def test_manual_strategy_needs_decisions_before_approval(shop: Tenant) -> None:
    client, _ = staff(shop)
    simple_product(shop, sku="TEE-S", name="Existing Tee")
    created = upload(
        client, HEADER + "tee,Cotton Tee,standard,draft,,,,,,,TEE-S,450,\n", strategy="manual"
    )
    job_id = created.json()["id"]
    assert job(client, job_id)["summary"]["undecided"] == 1
    refused = client.post(f"{DATA}jobs/{job_id}/approve/")
    assert refused.json()["error"]["code"] == "transfer_undecided"
    conflict = items(client, job_id, action="conflict")[0]
    client.post(
        f"{DATA}jobs/{job_id}/decisions/", {"decisions": {conflict["id"]: "update"}}, format="json"
    )
    assert client.post(f"{DATA}jobs/{job_id}/approve/").status_code == 200
    with tenant_context(shop):
        assert Product.objects.get().name == "Cotton Tee"


def test_sku_owned_by_another_product_is_never_merged_by_name(shop: Tenant) -> None:
    client, _ = staff(shop)
    simple_product(shop, sku="A-1", name="First")
    simple_product(shop, sku="B-1", name="Second")
    text = (
        HEADER
        + "x,Mixed,standard,draft,,,,,Size,S,A-1,10,\nx,Mixed,standard,draft,,,,,Size,M,B-1,10,\n"
    )
    created = upload(client, text, strategy="update")
    rows = items(client, created.json()["id"])
    assert rows[0]["action"] == "invalid"
    assert "different existing products" in json.dumps(rows[0]["messages"])


def test_preset_rules_and_details_are_enforced(shop: Tenant) -> None:
    set_preset(shop, StorePreset.FASHION)
    client, _ = staff(shop)
    created = upload(client, HEADER + "phone,Phone,electronics,draft,,,,,,,PH-1,100,\n")
    row = items(client, created.json()["id"])[0]
    assert row["action"] == "invalid"
    assert "does not offer electronics" in json.dumps(row["messages"])


def test_cancel_and_resume_continue_with_pending_items_only(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staff(shop)
    text = HEADER + "".join(
        f"p{i},Product {i},standard,draft,,,,,,,SKU-{i},10,\n" for i in range(30)
    )
    created = upload(client, text)
    job_id = created.json()["id"]
    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "_enqueue_on_commit", lambda *args, **kwargs: None)
        client.post(f"{DATA}jobs/{job_id}/approve/")
        with tenant_context(shop):
            row = DataTransferJob.objects.get(pk=job_id)
            assert pipeline.run_batch(row.pk) == "more"
            row.refresh_from_db()
            assert row.processed_items == pipeline.BATCH
            DataTransferJob.objects.filter(pk=job_id).update(cancel_requested=True)
            assert pipeline.run_batch(row.pk) == "cancelled"
    assert job(client, job_id)["status"] == "cancelled"
    resumed = client.post(f"{DATA}jobs/{job_id}/resume/")
    assert resumed.status_code == 200
    detail = job(client, job_id)
    assert detail["status"] == "completed"
    assert detail["created_count"] == 30
    with tenant_context(shop):
        assert Product.objects.count() == 30


def test_duplicate_item_processing_is_harmless(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staff(shop)
    created = upload(client, TEE)
    job_id = created.json()["id"]
    monkeypatch.setattr(pipeline, "_enqueue_on_commit", lambda *args, **kwargs: None)
    client.post(f"{DATA}jobs/{job_id}/approve/")
    with tenant_context(shop):
        row = DataTransferJob.objects.get(pk=job_id)
        item = ImportItem.objects.get(job=row, action=ItemAction.CREATE)
        ctx = pipeline._ctx(row)
        pipeline.process_item(row, item.pk, ctx)
        pipeline.process_item(row, item.pk, ctx)  # a duplicate task run
        assert Product.objects.count() == 1
        assert StockMovement.objects.count() == 2


def test_import_permissions(shop: Tenant) -> None:
    handler, _ = staff(shop, StaffRole.ORDER_HANDLER)
    assert upload(handler, TEE).status_code == 403
    editor, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    created = upload(editor, TEE)
    assert handler.get(f"{DATA}jobs/{created.json()['id']}/").status_code == 404
    assert editor.get(f"{DATA}sources/").status_code == 403


def test_bad_files_are_refused_with_a_reason(shop: Tenant) -> None:
    client, _ = staff(shop)
    latin = SimpleUploadedFile("x.csv", "name\nCafé\n".encode("latin-1"), content_type="text/csv")
    response = client.post(f"{DATA}imports/", {"file": latin}, format="multipart")
    assert response.json()["error"]["code"] == "import_encoding"
    empty = SimpleUploadedFile("x.csv", b"", content_type="text/csv")
    assert client.post(f"{DATA}imports/", {"file": empty}, format="multipart").status_code == 400


def test_template_download(shop: Tenant) -> None:
    client, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    response = client.get(f"{DATA}template/")
    assert response.status_code == 200
    assert response.content.decode("utf-8-sig").startswith("handle,name,product_type")


# --- Exports --------------------------------------------------------------------------------------


def test_csv_cells_are_formula_safe() -> None:
    text = "".join(
        csvio.write_csv(["a", "b", "c"], [{"a": "=HYPERLINK(1)", "b": "-12.5", "c": "@SUM"}])
    )
    assert "'=HYPERLINK(1)" in text
    assert ",-12.5," in text
    assert "'@SUM" in text


def test_product_export_round_trips_and_is_downloadable(shop: Tenant) -> None:
    simple_product(shop, sku="EXP-1", name="=cmd|' /C calc'!A0")
    client, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    created = client.post(f"{DATA}exports/", {"entity": "products"}, format="json")
    assert created.status_code == 202
    detail = job(client, created.json()["id"])
    assert detail["status"] == "completed"
    assert detail["total_items"] == 1
    download = client.get(f"{DATA}files/{detail['output_file']['id']}/download/")
    assert download.status_code == 200
    body = b"".join(download).decode("utf-8-sig")
    assert "EXP-1" in body
    assert "'=cmd" in body
    assert download["Cache-Control"].startswith("no-store")


def test_sensitive_exports_need_permissions_and_a_recent_sign_in(shop: Tenant) -> None:
    editor, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    handler, _ = staff(shop, StaffRole.ORDER_HANDLER)
    assert editor.post(f"{DATA}exports/", {"entity": "customers"}, format="json").status_code == 403
    assert handler.post(f"{DATA}exports/", {"entity": "products"}, format="json").status_code == 403
    assert (
        handler.post(f"{DATA}exports/", {"entity": "customers"}, format="json").status_code == 403
    )
    owner, user = staff(shop)
    created = owner.post(f"{DATA}exports/", {"entity": "customers"}, format="json")
    assert created.status_code == 202
    detail = job(owner, created.json()["id"])
    owner.get(f"{DATA}files/{detail['output_file']['id']}/download/")
    with tenant_context(shop):
        assert AuditEvent.objects.filter(action="transfers.file.downloaded").exists()
        AuthSession.objects.filter(user=user).update(
            authenticated_at=timezone.now() - timedelta(hours=3), reauthenticated_at=None
        )
    assert owner.get(f"{DATA}files/{detail['output_file']['id']}/download/").status_code == 403
    assert owner.post(f"{DATA}exports/", {"entity": "orders"}, format="json").status_code == 403


def test_expired_files_cannot_be_downloaded_and_are_swept(shop: Tenant) -> None:
    client, _ = staff(shop)
    created = client.post(f"{DATA}exports/", {"entity": "categories"}, format="json")
    detail = job(client, created.json()["id"])
    with tenant_context(shop):
        from commerce.transfers.models import DataTransferFile

        DataTransferFile.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert client.get(f"{DATA}files/{detail['output_file']['id']}/download/").status_code == 404
    with tenant_context(shop):
        assert pipeline.sweep()["expired_files"] >= 1


# --- Source adapters ------------------------------------------------------------------------------


def woo_handler(call: Call) -> tuple[int, Any, dict[str, str]]:
    path = call.path
    if "/data/currencies/current" in path:
        return 200, {"code": "BDT", "name": "Taka", "symbol": "&#2547;"}, {}
    if "/products/77/variations" in path:
        return (
            200,
            [
                {
                    "id": 771,
                    "sku": "WV-S",
                    "regular_price": "300",
                    "attributes": [{"name": "Size", "option": "S"}],
                    "manage_stock": True,
                    "stock_quantity": 4,
                },
                {
                    "id": 772,
                    "sku": "WV-M",
                    "regular_price": "300",
                    "attributes": [{"name": "Size", "option": "M"}],
                    "manage_stock": True,
                    "stock_quantity": 2,
                },
            ],
            {"X-WP-TotalPages": "1"},
        )
    if path.startswith("/wp-json/wc/v3/products?") and "per_page=1&" not in path + "&":
        return (
            200,
            [
                {
                    "id": 77,
                    "name": "Woo Tee",
                    "slug": "woo-tee",
                    "type": "variable",
                    "status": "publish",
                    "description": "<p>Nice</p>",
                    "short_description": "",
                    "sale_price": "250",
                    "categories": [{"id": 5, "name": "Tops"}],
                    "tags": [{"name": "tee"}],
                    "images": [{"src": "https://cdn.example.com/a.jpg", "alt": "Tee"}],
                    "attributes": [
                        {"name": "Size", "variation": True, "options": ["S", "M"]},
                        {"name": "Material", "variation": False, "options": ["Cotton"]},
                    ],
                    "meta_data": [{"key": "_yoast_wpseo_title", "value": "Woo Tee | Shop"}],
                }
            ],
            {"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    if "customers" in path and "per_page=1" not in path:
        return 200, [], {"X-WP-TotalPages": "1"}
    if "orders" in path and "per_page=1&" in path + "&":
        return 403, {"code": "woocommerce_rest_cannot_view"}, {}
    return 200, [], {"X-WP-Total": "3", "X-WP-TotalPages": "1"}


def test_woocommerce_adapter_is_read_only_and_normalises(monkeypatch: pytest.MonkeyPatch) -> None:
    http = fake_http(monkeypatch, woo_handler)
    source = WooCommerceSource("https://shop.example.com", "ck_test", "cs_test")
    info = source.verify()
    assert info.currency == "BDT"
    assert info.capabilities["products"] is True
    assert info.capabilities["orders"] is False
    page = source.page("products", None)
    product = page.records[0]
    assert page.next_cursor is None
    assert product["options"] == ["Size"]
    assert len(product["variants"]) == 2
    assert product["variants"][0]["sku"] == "WV-S"
    assert product["variants"][0]["stock"] == 4
    assert product["specifications"] == {"Material": "Cotton"}
    assert product["seo_title"] == "Woo Tee | Shop"
    assert any("Sale price" in w for w in product["source_warnings"])
    assert all(call.method == "GET" for call in http.calls)
    auth = http.calls[0].headers["Authorization"]
    assert base64.b64decode(auth.split()[1]).decode() == "ck_test:cs_test"


def test_woocommerce_errors_are_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_http(monkeypatch, lambda call: (401, {"code": "woocommerce_rest_cannot_view"}, {}))
    with pytest.raises(SourceError) as error:
        WooCommerceSource("https://shop.example.com", "ck_x", "cs_x").verify()
    assert error.value.kind == "invalid_credentials"
    fake_http(monkeypatch, lambda call: (301, b"", {"Location": "https://www.shop.example.com/"}))
    with pytest.raises(SourceError) as error:
        WooCommerceSource("https://shop.example.com", "ck_x", "cs_x").verify()
    assert error.value.code == "redirect_refused"


def shopify_handler(call: Call) -> tuple[int, Any, dict[str, str]]:
    if call.path == "/admin/oauth/access_token":
        assert b"grant_type=client_credentials" in (call.body or b"")
        return (
            200,
            {"access_token": "shpat_minted", "scope": "read_products", "expires_in": 86399},
            {},
        )
    assert call.headers["X-Shopify-Access-Token"] == "shpat_minted"
    query = json.loads(call.body or b"{}")["query"]
    if "shop {" in query:
        return (
            200,
            {
                "data": {
                    "shop": {"name": "Shopify Demo", "currencyCode": "BDT"},
                    "currentAppInstallation": {
                        "accessScopes": [{"handle": "read_products"}, {"handle": "read_inventory"}]
                    },
                    "productsCount": {"count": 1},
                }
            },
            {},
        )
    if "productsCount" in query:
        return 200, {"data": {"productsCount": {"count": 1}}}, {}
    return (
        200,
        {
            "data": {
                "products": {
                    "pageInfo": {"hasNextPage": False, "endCursor": "abc"},
                    "nodes": [
                        {
                            "id": "gid://shopify/Product/9",
                            "title": "Shop Mug",
                            "handle": "shop-mug",
                            "status": "ARCHIVED",
                            "descriptionHtml": "<p>Mug</p>",
                            "productType": "Kitchen",
                            "vendor": "MugCo",
                            "tags": ["mug"],
                            "seo": {"title": None, "description": None},
                            "options": [{"name": "Title"}],
                            "collections": {"nodes": [{"title": "Gifts"}]},
                            "media": {"nodes": []},
                            "variants": {
                                "nodes": [
                                    {
                                        "id": "gid://shopify/ProductVariant/91",
                                        "sku": "MUG-9",
                                        "price": "350.00",
                                        "compareAtPrice": "400.00",
                                        "barcode": None,
                                        "inventoryQuantity": 6,
                                        "selectedOptions": [
                                            {"name": "Title", "value": "Default Title"}
                                        ],
                                        "inventoryItem": {"tracked": True},
                                    }
                                ]
                            },
                        }
                    ],
                }
            }
        },
        {},
    )


def test_shopify_adapter_mints_a_token_and_maps_status(monkeypatch: pytest.MonkeyPatch) -> None:
    http = fake_http(monkeypatch, shopify_handler)
    source = ShopifySource("demo.myshopify.com", {"client_id": "cid", "client_secret": "secret"})
    info = source.verify()
    assert info.capabilities["products"]
    assert not info.capabilities["customers"]
    page = source.page("products", None)
    record = page.records[0]
    assert record["status"] == "draft"
    assert "archived" in record["source_warnings"][0]
    assert record["options"] == []
    assert record["variants"][0]["sku"] == "MUG-9"
    assert record["variants"][0]["compare_at"] == "400.00"
    assert record["variants"][0]["stock"] == 6
    assert record["collections"] == ["Gifts"]
    assert record["brand"] == "MugCo"
    assert all(call.host == "demo.myshopify.com" for call in http.calls)
    assert http.calls[1].path == "/admin/api/2026-10/graphql.json"


def test_shopify_throttling_and_domain_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_http(
        monkeypatch,
        lambda call: (
            200,
            {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]},
            {},
        ),
    )
    with pytest.raises(SourceError) as error:
        ShopifySource("demo.myshopify.com", {"access_token": "shpat_x"}).verify()
    assert error.value.kind == "rate_limited"
    with pytest.raises(SourceError):
        ShopifySource("evil.example.com", {"access_token": "shpat_x"})


def test_shopify_bulk_results_are_assembled_by_parent() -> None:
    source = ShopifySource("demo.myshopify.com", {"access_token": "shpat_x"})
    lines = [
        {
            "id": "gid://shopify/Product/1",
            "title": "Bulk Tee",
            "status": "ACTIVE",
            "options": [{"name": "Size"}],
        },
        {
            "id": "gid://shopify/ProductVariant/11",
            "sku": "BT-S",
            "price": "10",
            "selectedOptions": [{"name": "Size", "value": "S"}],
            "__parentId": "gid://shopify/Product/1",
        },
        {
            "id": "gid://shopify/ProductVariant/12",
            "sku": "BT-M",
            "price": "10",
            "selectedOptions": [{"name": "Size", "value": "M"}],
            "__parentId": "gid://shopify/Product/1",
        },
    ]
    records = source.parse_bulk("\n".join(json.dumps(line) for line in lines).encode())
    assert len(records) == 1
    assert [v["sku"] for v in records[0]["variants"]] == ["BT-S", "BT-M"]


# --- Migrations -----------------------------------------------------------------------------------


@pytest.fixture
def simulator(settings: Any) -> None:
    settings.MATECART_MIGRATION_SIMULATOR = True


def connect_simulator(client: Any, platform: str = "woocommerce") -> dict[str, Any]:
    created = client.post(
        f"{DATA}sources/", {"platform": platform, "simulated": True}, format="json"
    )
    assert created.status_code == 201
    verified = client.post(f"{DATA}sources/{created.json()['id']}/verify/").json()
    assert verified["status"] == "connected"
    return dict(verified)


def migrate(client: Any, source: dict[str, Any], scopes: list[str], **options: Any) -> str:
    created = client.post(
        f"{DATA}migrations/",
        {"connection": source["id"], "scopes": scopes, "options": options},
        format="json",
    )
    assert created.status_code == 202, created.json()
    job_id = str(created.json()["id"])
    assert job(client, job_id)["status"] == "review"
    assert client.post(f"{DATA}jobs/{job_id}/approve/").status_code == 200
    return job_id


SCOPES = ["categories", "products", "customers", "orders", "coupons"]


@pytest.mark.usefixtures("simulator")
def test_simulated_migration_end_to_end(shop: Tenant) -> None:
    client, _ = staff(shop)
    source = connect_simulator(client)
    with tenant_context(shop):
        notifications_before = Notification.objects.count()
    job_id = migrate(client, source, SCOPES)
    detail = job(client, job_id)
    assert detail["status"] == "completed_with_errors"
    with tenant_context(shop):
        tee = Product.objects.get(name="Simulated Cotton Tee")
        assert tee.status == ProductStatus.DRAFT
        assert set(ProductVariant.objects.filter(product=tee).values_list("sku", flat=True)) == {
            "SIMW-TEE-S",
            "SIMW-TEE-M",
        }
        parent = Category.objects.get(name="Tops").parent
        assert parent is not None
        assert parent.name == "Simulated"
        assert tee.placements.get().category.name == "Tops"
        assert StockLevel.objects.get(variant__sku="SIMW-TEE-M").on_hand == Decimal("8")
        bottle = Product.objects.get(name="Simulated Steel Bottle")
        assert bottle.track_inventory is False
        assert not Product.objects.filter(name="Simulated Bundle").exists()
        assert HistoricalCustomer.objects.count() == 2
        order = HistoricalOrder.objects.get()
        assert order.grand_total == Decimal("960")
        assert order.customer is not None
        assert order.customer.email == "sim.buyer@example.test"
        assert order.source_financial_status == "paid"
        # Historical orders are never live orders: no order, payment, stock reservation or e-mail.
        assert not Order.objects.exists()
        assert not PaymentTransaction.objects.exists()
        assert Notification.objects.count() == notifications_before
        coupon = Coupon.objects.get(code="SIMWWELCOME")
        assert coupon.is_active is False
        assert coupon.value == Decimal("10")
        assert not Coupon.objects.filter(code="SIMWSHIP").exists()
        assert ExternalSourceMapping.objects.filter(object_type="product").count() == 2
        # Customer and order payloads (personal data) are cleared once the job finishes.
        assert (
            not ImportItem.objects.filter(object_type__in=("customer", "order"))
            .exclude(payload={})
            .exists()
        )


@pytest.mark.usefixtures("simulator")
def test_repeated_migration_creates_no_duplicates(shop: Tenant) -> None:
    client, _ = staff(shop)
    source = connect_simulator(client)
    migrate(client, source, SCOPES)
    second = migrate(client, source, SCOPES)
    summary = job(client, second)["summary"]
    assert summary["to_create"] == 0
    with tenant_context(shop):
        assert Product.objects.count() == 2
        assert HistoricalOrder.objects.count() == 1
        assert Coupon.objects.count() == 1


@pytest.mark.usefixtures("simulator")
def test_compensation_removes_only_what_the_job_created_and_nothing_used(shop: Tenant) -> None:
    client, _ = staff(shop)
    simple_product(shop, sku="KEEP-1", name="Merchant product")
    source = connect_simulator(client)
    job_id = migrate(client, source, SCOPES)
    result = client.post(f"{DATA}jobs/{job_id}/compensate/").json()
    with tenant_context(shop):
        assert Product.objects.filter(name="Merchant product").exists()
        assert not Product.objects.filter(name="Simulated Steel Bottle").exists()  # never stocked
        assert Product.objects.get(name="Simulated Cotton Tee").status == ProductStatus.ARCHIVED
        assert not HistoricalOrder.objects.exists()
        assert not HistoricalCustomer.objects.exists()
        assert not Coupon.objects.filter(code="SIMWWELCOME").exists()
    assert result["archived"] == 1
    assert result["deleted"] >= 4
    assert (
        client.post(f"{DATA}jobs/{job_id}/compensate/").json()["error"]["code"]
        == "transfer_compensated"
    )


@pytest.mark.usefixtures("simulator")
def test_migration_permissions_and_recent_sign_in(shop: Tenant) -> None:
    manager, user = staff(shop, StaffRole.MANAGER)
    source = connect_simulator(manager)
    editor, _ = staff(shop, StaffRole.CONTENT_EDITOR)
    assert (
        editor.post(
            f"{DATA}migrations/",
            {"connection": source["id"], "scopes": ["products"]},
            format="json",
        ).status_code
        == 403
    )
    with tenant_context(shop):
        AuthSession.objects.filter(user=user).update(
            authenticated_at=timezone.now() - timedelta(hours=3), reauthenticated_at=None
        )
    response = manager.post(
        f"{DATA}migrations/", {"connection": source["id"], "scopes": ["customers"]}, format="json"
    )
    assert response.status_code == 403


def test_source_url_must_be_public_https_and_credentials_are_hidden(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staff(shop)
    body = {
        "platform": "woocommerce",
        "consumer_key": "ck_" + "a" * 30,
        "consumer_secret": "cs_" + "b" * 30,
    }
    for url in ("http://shop.example.com", "https://127.0.0.1", "https://localhost"):
        assert (
            client.post(f"{DATA}sources/", {**body, "store_url": url}, format="json").status_code
            == 400
        )
    fake_http(monkeypatch, woo_handler)
    created = client.post(
        f"{DATA}sources/", {**body, "store_url": "https://shop.example.com"}, format="json"
    )
    assert created.status_code == 201
    text = json.dumps(created.json())
    assert "cs_" not in text
    assert "a" * 30 not in text
    with tenant_context(shop):
        stored = SourceConnection.objects.get()
        assert "cs_" not in stored.credentials_encrypted
    refused = client.post(
        f"{DATA}sources/",
        {"platform": "shopify", "store_url": "https://evil.example.com", "access_token": "shpat_1"},
        format="json",
    )
    assert refused.status_code == 400


def test_currency_mismatch_blocks_product_migration(
    shop: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = staff(shop)
    fake_http(
        monkeypatch,
        lambda call: (
            woo_handler(call) if "currencies" not in call.path else (200, {"code": "USD"}, {})
        ),
    )
    body = {
        "platform": "woocommerce",
        "store_url": "https://shop.example.com",
        "consumer_key": "ck_" + "a" * 30,
        "consumer_secret": "cs_" + "b" * 30,
    }
    source = client.post(f"{DATA}sources/", body, format="json").json()
    verified = client.post(f"{DATA}sources/{source['id']}/verify/").json()
    assert verified["source_currency"] == "USD"
    response = client.post(
        f"{DATA}migrations/", {"connection": source["id"], "scopes": ["products"]}, format="json"
    )
    assert response.json()["error"]["code"] == "source_currency_mismatch"


# --- Isolation ------------------------------------------------------------------------------------


@pytest.mark.usefixtures("simulator")
def test_jobs_sources_and_history_are_isolated_between_stores(
    shop: Tenant, tenant_b: Tenant
) -> None:
    make_store_settings(tenant_b, name="Other")
    _, owner_b = staff(tenant_b)
    with tenant_context(tenant_b):
        from commerce.transfers import connections

        source_b = connections.create(platform="shopify", data={"simulated": True}, actor=owner_b)
        other_job = DataTransferJob.objects.create(
            kind="export",
            source="store",
            status="completed",
            entity="products",
            created_by=staff(tenant_b, StaffRole.MANAGER)[1],
        )
        HistoricalCustomer.objects.create(
            platform="shopify",
            source_identity="x",
            external_id="1",
            email="b@example.test",
            job=other_job,
        )
    client, _ = staff(shop)
    assert client.get(f"{DATA}jobs/{other_job.pk}/").status_code == 404
    assert client.post(f"{DATA}sources/{source_b.pk}/verify/").status_code == 404
    assert client.get(f"{DATA}sources/").json() == []
    assert client.get(f"{DATA}history/customers/").json()["count"] == 0


@pytest.mark.django_db(transaction=True)
def test_download_and_reversal_audit_in_their_own_transactions(
    store: Any, deployment_tenant: Tenant, settings: Any
) -> None:
    """Real requests are not wrapped in a test transaction: audited actions must open their own
    (record_audit_event refuses to run outside one)."""
    settings.MATECART_MIGRATION_SIMULATOR = True
    shop = deployment_tenant
    client, _ = staff(shop)
    created = client.post(f"{DATA}exports/", {"entity": "categories"}, format="json").json()
    detail = job(client, created["id"])
    download = client.get(f"{DATA}files/{detail['output_file']['id']}/download/")
    assert download.status_code == 200
    source = connect_simulator(client)
    job_id = migrate(client, source, ["categories", "products"])
    reversed_job = client.post(f"{DATA}jobs/{job_id}/compensate/")
    assert reversed_job.status_code == 200
    with tenant_context(shop):
        assert AuditEvent.objects.filter(action="transfers.file.downloaded").exists()
        assert AuditEvent.objects.filter(action="transfers.job.compensated").exists()
