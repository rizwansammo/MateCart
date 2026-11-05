from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import (
    ConflictStrategy,
    DataTransferFile,
    DataTransferJob,
    HistoricalCustomer,
    HistoricalOrder,
    ImportItem,
    SourcePlatform,
)
from .sources.base import OBJECT_TYPES

ERROR_TEXT = {
    "import_encoding": "Save the file as UTF-8 (CSV UTF-8) and upload it again.",
    "import_no_header": "The file needs a header row.",
    "import_csv_invalid": "The file is not valid CSV.",
    "import_too_many_rows": "The file has too many rows.",
    "analysis_failed": "MateCart could not read this file or source.",
    "export_failed": "The export could not be generated.",
    "woo_401": "WooCommerce refused the consumer key or secret.",
    "woo_403": "The WooCommerce key does not have read permission.",
    "woo_api_not_found": "The WooCommerce REST API was not found at this address.",
    "shopify_401": "Shopify refused the access token.",
    "shopify_access_denied": "The Shopify app is missing a read scope.",
    "shopify_read_products_missing": "The Shopify app needs the read_products scope.",
    "timeout": "The store did not answer in time.",
    "redirect_refused": "The store address redirects; enter its final https:// address.",
    "host_not_public": "This address is not a public internet address.",
    "certificate_invalid": "The store's TLS certificate is not valid.",
}


def error_text(code: str) -> str:
    if not code:
        return ""
    return ERROR_TEXT.get(code, "The operation failed (" + code + ").")


class OptionsSerializer(serializers.Serializer[dict[str, Any]]):
    include_stock = serializers.BooleanField(default=True)
    download_images = serializers.BooleanField(default=False)
    publish = serializers.BooleanField(default=False)
    update_prices = serializers.BooleanField(default=False)
    mapping = serializers.DictField(child=serializers.CharField(max_length=80), required=False)


class ImportInputSerializer(serializers.Serializer[dict[str, Any]]):
    file = serializers.FileField()
    strategy = serializers.ChoiceField(
        choices=ConflictStrategy.choices, default=ConflictStrategy.SKIP
    )
    options = serializers.JSONField(required=False, help_text="OptionsSerializer as JSON text.")


class MigrationInputSerializer(serializers.Serializer[dict[str, Any]]):
    connection = serializers.UUIDField()
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=list(OBJECT_TYPES)), max_length=5
    )
    strategy = serializers.ChoiceField(
        choices=ConflictStrategy.choices, default=ConflictStrategy.SKIP
    )
    options = OptionsSerializer(required=False)


class ExportInputSerializer(serializers.Serializer[dict[str, Any]]):
    entity = serializers.ChoiceField(
        choices=["products", "categories", "inventory", "customers", "orders"]
    )
    format = serializers.ChoiceField(choices=["csv", "jsonl"], default="csv")


class DecisionsInputSerializer(serializers.Serializer[dict[str, Any]]):
    decisions = serializers.DictField(child=serializers.ChoiceField(choices=["skip", "update"]))


class SourceInputSerializer(serializers.Serializer[dict[str, Any]]):
    platform = serializers.ChoiceField(choices=SourcePlatform.choices)
    name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    store_url = serializers.CharField(max_length=255, required=False, allow_blank=True)
    consumer_key = serializers.CharField(max_length=120, required=False, allow_blank=True)
    consumer_secret = serializers.CharField(max_length=120, required=False, allow_blank=True)
    access_token = serializers.CharField(max_length=200, required=False, allow_blank=True)
    client_id = serializers.CharField(max_length=200, required=False, allow_blank=True)
    client_secret = serializers.CharField(max_length=200, required=False, allow_blank=True)
    simulated = serializers.BooleanField(default=False)


class SourceConnectionSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    platform = serializers.CharField()
    name = serializers.CharField()
    store_url = serializers.CharField()
    credential_kind = serializers.CharField()
    credential_hint = serializers.CharField()
    credential_set = serializers.BooleanField()
    status = serializers.CharField()
    last_verified_at = serializers.DateTimeField(allow_null=True)
    last_error = serializers.CharField()
    last_error_text = serializers.SerializerMethodField()
    source_name = serializers.CharField()
    source_currency = serializers.CharField()
    capabilities = serializers.DictField(child=serializers.BooleanField())
    counts = serializers.DictField(child=serializers.IntegerField(allow_null=True))
    is_simulated = serializers.BooleanField()
    created_at = serializers.DateTimeField()

    def get_last_error_text(self, obj: dict[str, Any]) -> str:
        return error_text(str(obj.get("last_error") or ""))


class TransferFileSerializer(serializers.ModelSerializer[DataTransferFile]):
    class Meta:
        model = DataTransferFile
        fields = (
            "id",
            "purpose",
            "display_name",
            "content_type",
            "size_bytes",
            "expires_at",
            "created_at",
        )


class TransferJobSerializer(serializers.ModelSerializer[DataTransferJob]):
    output_file = TransferFileSerializer(allow_null=True)
    report_file = TransferFileSerializer(allow_null=True)
    input_file = TransferFileSerializer(allow_null=True)
    error_text = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    connection_name = serializers.SerializerMethodField()
    connection_simulated = serializers.SerializerMethodField()

    class Meta:
        model = DataTransferJob
        fields = (
            "id",
            "kind",
            "source",
            "status",
            "entity",
            "file_format",
            "scopes",
            "conflict_strategy",
            "options",
            "summary",
            "total_items",
            "processed_items",
            "created_count",
            "updated_count",
            "skipped_count",
            "failed_count",
            "error_code",
            "error_text",
            "sensitive",
            "cancel_requested",
            "input_file",
            "output_file",
            "report_file",
            "created_by_name",
            "approved_by_name",
            "approved_at",
            "started_at",
            "finished_at",
            "compensated_at",
            "created_at",
            "connection_name",
            "connection_simulated",
        )

    def get_error_text(self, obj: DataTransferJob) -> str:
        return error_text(obj.error_code)

    def get_created_by_name(self, obj: DataTransferJob) -> str:
        return obj.created_by.full_name or obj.created_by.email or ""

    def get_approved_by_name(self, obj: DataTransferJob) -> str:
        user = obj.approved_by
        return (user.full_name or user.email or "") if user else ""

    def get_connection_name(self, obj: DataTransferJob) -> str:
        row = obj.connection
        return (row.name or row.source_name or row.store_identity) if row else ""

    def get_connection_simulated(self, obj: DataTransferJob) -> bool:
        return bool(obj.connection and obj.connection.is_simulated)


class ImportItemSerializer(serializers.ModelSerializer[ImportItem]):
    preview = serializers.SerializerMethodField()

    class Meta:
        model = ImportItem
        fields = (
            "id",
            "position",
            "object_type",
            "external_id",
            "key",
            "title",
            "action",
            "status",
            "decision",
            "messages",
            "existing_id",
            "local_id",
            "created_local",
            "preview",
        )

    def get_preview(self, obj: ImportItem) -> dict[str, Any]:
        """A short, non-personal view of the record (products and coupons only)."""
        payload = obj.payload or {}
        if obj.object_type == "product":
            return {
                "name": payload.get("name"),
                "product_type": payload.get("product_type"),
                "status": payload.get("status"),
                "variants": [
                    {
                        "sku": v.get("sku"),
                        "options": v.get("options"),
                        "price": v.get("price"),
                        "stock": v.get("stock"),
                    }
                    for v in (payload.get("variants") or [])[:10]
                ],
                "variant_count": len(payload.get("variants") or []),
                "categories": [" > ".join(path) for path in payload.get("categories") or []],
                "images": len(payload.get("images") or []),
            }
        if obj.object_type == "coupon":
            return {
                k: payload.get(k) for k in ("code", "discount_type", "amount", "minimum_subtotal")
            }
        if obj.object_type == "category":
            return {"name": payload.get("name")}
        return {}


class HistoricalCustomerSerializer(serializers.ModelSerializer[HistoricalCustomer]):
    class Meta:
        model = HistoricalCustomer
        fields = (
            "id",
            "platform",
            "external_id",
            "first_name",
            "last_name",
            "email",
            "phone",
            "city",
            "country",
            "source_orders_count",
            "source_created_at",
            "created_at",
        )


class HistoricalOrderSerializer(serializers.ModelSerializer[HistoricalOrder]):
    class Meta:
        model = HistoricalOrder
        fields = (
            "id",
            "platform",
            "external_id",
            "number",
            "placed_at",
            "source_status",
            "source_financial_status",
            "source_fulfillment_status",
            "currency",
            "subtotal",
            "shipping_total",
            "tax_total",
            "discount_total",
            "grand_total",
            "lines",
            "customer_name",
            "created_at",
        )


class TemplateFieldSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    name = serializers.CharField()
    aliases = serializers.ListField(child=serializers.CharField())


class ExportKindSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    name = serializers.CharField()
    sensitive = serializers.BooleanField()
    formats = serializers.ListField(child=serializers.CharField())
    allowed = serializers.BooleanField()


class DataOverviewSerializer(serializers.Serializer[Any]):
    can_import = serializers.BooleanField()
    can_migrate = serializers.BooleanField()
    can_view_customers = serializers.BooleanField()
    can_view_orders = serializers.BooleanField()
    simulator_allowed = serializers.BooleanField()
    template_fields = TemplateFieldSerializer(many=True)
    exports = ExportKindSerializer(many=True)
    store_currency = serializers.CharField()
    max_upload_bytes = serializers.IntegerField()


class CompensationSerializer(serializers.Serializer[Any]):
    deleted = serializers.IntegerField()
    archived = serializers.IntegerField()
    kept = serializers.IntegerField()


class ItemQuerySerializer(serializers.Serializer[dict[str, Any]]):
    action = serializers.CharField(required=False, max_length=12)
    status = serializers.CharField(required=False, max_length=12)
    object_type = serializers.CharField(required=False, max_length=16)
