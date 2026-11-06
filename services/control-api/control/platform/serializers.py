from rest_framework import serializers

from .models import (
    BackupObservation,
    Client,
    ClientContact,
    Deployment,
    DomainRecord,
    OperationalRequest,
    Server,
    StoreRegistration,
)
from .services import validate_configuration, validate_country, validate_currency, validate_timezone

HEALTH_STATES = ["healthy", "degraded", "unavailable"]


class PlatformSerializer(serializers.Serializer[dict[str, object]]):
    """Named fallback for bespoke response envelopes documented by individual operations."""


class ClientSerializer(serializers.ModelSerializer[Client]):
    stores_count = serializers.IntegerField(read_only=True, required=False)
    servers_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Client
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_country(self, value: str) -> str:
        return validate_country(value)

    def validate_time_zone(self, value: str) -> str:
        return validate_timezone(value) if value else ""


class ContactSerializer(serializers.ModelSerializer[ClientContact]):
    class Meta:
        model = ClientContact
        fields = "__all__"
        read_only_fields = ("id", "client", "created_at", "updated_at")


class StoreSerializer(serializers.ModelSerializer[StoreRegistration]):
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    configuration_state = serializers.SerializerMethodField()

    class Meta:
        model = StoreRegistration
        fields = "__all__"
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "applied_theme",
            "applied_modules",
            "desired_revision",
            "applied_revision",
            "applied_at",
        )

    def get_configuration_state(self, obj: StoreRegistration) -> str:
        return "applied" if obj.desired_revision == obj.applied_revision else "pending_application"

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        current = self.instance
        preset = str(attrs.get("preset", current.preset if current else ""))
        theme = str(attrs.get("desired_theme", current.desired_theme if current else ""))
        module_value = attrs.get("desired_modules", current.desired_modules if current else [])
        language_value = attrs.get(
            "supported_languages", current.supported_languages if current else []
        )
        modules = module_value if isinstance(module_value, list) else []
        languages = language_value if isinstance(language_value, list) else []
        default_language = str(
            attrs.get("default_language", current.default_language if current else "")
        )
        clean_theme, clean_modules, clean_languages = validate_configuration(
            preset=preset,
            theme=theme,
            modules=[str(item) for item in modules],
            languages=[str(item) for item in languages],
            default_language=default_language,
        )
        attrs["desired_theme"] = clean_theme
        attrs["desired_modules"] = clean_modules
        attrs["supported_languages"] = clean_languages
        if "country" in attrs:
            attrs["country"] = validate_country(str(attrs["country"]))
        if "default_currency" in attrs:
            attrs["default_currency"] = validate_currency(str(attrs["default_currency"]))
        if "time_zone" in attrs:
            attrs["time_zone"] = validate_timezone(str(attrs["time_zone"]))
        return attrs


class ServerSerializer(serializers.ModelSerializer[Server]):
    owner_name = serializers.SerializerMethodField()
    deployments_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Server
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")

    def get_owner_name(self, obj: Server) -> str:
        return obj.owner_client.display_name if obj.owner_client else "NetaMate"

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        current = self.instance
        ownership = str(attrs.get("ownership", current.ownership if current else ""))
        owner_client = attrs.get(
            "owner_client", current.owner_client if current is not None else None
        )
        if ownership == "client" and owner_client is None:
            raise serializers.ValidationError(
                {"owner_client": "Choose the client that owns this server."}
            )
        if ownership == "netamate" and owner_client is not None:
            raise serializers.ValidationError(
                {"owner_client": "NetaMate-owned servers cannot be assigned to a client."}
            )
        if attrs.get("country"):
            attrs["country"] = validate_country(str(attrs["country"]))
        return attrs


class DeploymentSerializer(serializers.ModelSerializer[Deployment]):
    store_name = serializers.CharField(source="store.name", read_only=True)
    server_name = serializers.CharField(source="server.name", read_only=True)
    health_state = serializers.CharField(read_only=True, required=False)

    class Meta:
        model = Deployment
        fields = "__all__"
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "observed_version",
            "last_observed_at",
            "last_healthy_at",
            "configuration_revision",
        )


class OperationSerializer(serializers.ModelSerializer[OperationalRequest]):
    requested_by_name = serializers.CharField(source="requested_by.full_name", read_only=True)

    class Meta:
        model = OperationalRequest
        fields = "__all__"
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "requested_by",
            "decided_by",
            "decided_at",
        )


class DomainSerializer(serializers.ModelSerializer[DomainRecord]):
    store_name = serializers.CharField(source="store.name", read_only=True)

    class Meta:
        model = DomainRecord
        fields = "__all__"
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "dns_state",
            "tls_state",
            "certificate_expires_at",
            "last_verified_at",
        )


class BackupSerializer(serializers.ModelSerializer[BackupObservation]):
    store_name = serializers.CharField(source="deployment.store.name", read_only=True)

    class Meta:
        model = BackupObservation
        fields = "__all__"
        read_only_fields = ("id", "source")


class TelemetrySerializer(serializers.Serializer[dict[str, object]]):
    deployment_id = serializers.UUIDField()
    store_id = serializers.UUIDField()
    report_id = serializers.UUIDField()
    observed_at = serializers.DateTimeField()
    store_status = serializers.ChoiceField(choices=HEALTH_STATES)
    api_status = serializers.ChoiceField(choices=HEALTH_STATES)
    response_ms = serializers.IntegerField(
        min_value=0, max_value=120000, required=False, allow_null=True
    )
    cpu_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    ram_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    disk_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    visitor_count_24h = serializers.IntegerField(
        min_value=0, max_value=1000000000, required=False, allow_null=True
    )
    application_version = serializers.CharField(max_length=80, required=False, allow_blank=True)
    backup_status = serializers.ChoiceField(
        choices=["successful", "failed"], required=False, allow_null=True
    )
    backup_type = serializers.CharField(max_length=32, required=False, allow_blank=True)
    backup_at = serializers.DateTimeField(required=False, allow_null=True)
