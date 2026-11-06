import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from control.accounts.models import AdminUser
from control.core.request_context import get_request_id
from control.core.security import new_token

from .constants import MODULE_BY_KEY, PRESETS
from .models import (
    BackupObservation,
    ConsoleAuditEvent,
    ConsoleNotification,
    Deployment,
    MonitoringCredential,
    MonitoringObservation,
    OperationalAlert,
    StoreConfigurationRevision,
    StoreRegistration,
)


def audit(actor: AdminUser | None, action: str, resource: Any, changes: dict[str, Any]) -> None:
    ConsoleAuditEvent.objects.create(
        actor=actor,
        action=action,
        resource_type=resource._meta.label_lower,
        resource_id=resource.pk,
        request_id=get_request_id() or "",
        changes=changes,
    )


def validate_country(value: str) -> str:
    result = value.strip().upper()
    if len(result) != 2 or not result.isalpha():
        raise ValidationError({"country": "Use a two-letter ISO country code."})
    return result


def validate_currency(value: str) -> str:
    result = value.strip().upper()
    if len(result) != 3 or not result.isalpha():
        raise ValidationError({"default_currency": "Use a three-letter ISO currency code."})
    return result


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError({"time_zone": "Use a valid IANA time zone."}) from exc
    return value


def validate_configuration(
    *, preset: str, theme: str, modules: list[str], languages: list[str], default_language: str
) -> tuple[str, list[str], list[str]]:
    preset_info = PRESETS.get(preset)
    if preset_info is None:
        raise ValidationError({"preset": "Choose one of the five approved store presets."})
    if theme != preset_info[1]:
        raise ValidationError({"desired_theme": "This theme is not compatible with the preset."})
    clean_languages = list(
        dict.fromkeys(item.strip().lower() for item in languages if item.strip())
    )
    if not clean_languages or any(len(item) > 12 for item in clean_languages):
        raise ValidationError({"supported_languages": "Provide at least one valid language code."})
    if default_language.lower() not in clean_languages:
        raise ValidationError({"default_language": "The default language must be supported."})
    clean_modules = list(dict.fromkeys(modules))
    for key in clean_modules:
        spec = MODULE_BY_KEY.get(key)
        if spec is None:
            raise ValidationError({"desired_modules": f"Unknown module: {key}."})
        if not spec.available:
            raise ValidationError({"desired_modules": f"{spec.name} is not available."})
        if preset not in spec.presets:
            raise ValidationError(
                {"desired_modules": f"{spec.name} is incompatible with this preset."}
            )
        missing = [required for required in spec.requires if required not in clean_modules]
        if missing:
            raise ValidationError(
                {"desired_modules": f"{spec.name} requires {', '.join(missing)}."}
            )
    for spec in MODULE_BY_KEY.values():
        if spec.core and spec.key not in clean_modules:
            clean_modules.append(spec.key)
    return theme, clean_modules, clean_languages


def add_revision(store: StoreRegistration, actor: AdminUser) -> StoreConfigurationRevision:
    revision = StoreConfigurationRevision.objects.create(
        store=store,
        revision=store.desired_revision,
        preset=store.preset,
        theme=store.desired_theme,
        modules=store.desired_modules,
        languages=store.supported_languages,
        requested_by=actor,
    )
    audit(actor, "store.configuration.requested", store, {"revision": store.desired_revision})
    return revision


def create_monitoring_credential(
    deployment: Deployment, actor: AdminUser, label: str
) -> tuple[MonitoringCredential, str]:
    raw = new_token()
    with transaction.atomic():
        credential = MonitoringCredential.objects.create(
            deployment=deployment,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            label=label,
            last_four=raw[-4:],
        )
        audit(actor, "monitoring.credential.created", credential, {"label": label})
    return credential, raw


def revoke_monitoring_credential(
    credential: MonitoringCredential, actor: AdminUser
) -> MonitoringCredential:
    with transaction.atomic():
        locked = MonitoringCredential.objects.select_for_update().get(pk=credential.pk)
        if locked.revoked_at is None:
            locked.revoked_at = timezone.now()
            locked.save(update_fields=["revoked_at", "updated_at"])
            audit(
                actor,
                "monitoring.credential.revoked",
                locked,
                {"deployment_id": str(locked.deployment_id), "label": locked.label},
            )
    return locked


def ingest_observation(token: str, data: dict[str, Any]) -> MonitoringObservation:
    if not token or len(token) > 256:
        raise ValidationError({"credential": "Invalid telemetry credential."})
    digest = hashlib.sha256(token.encode()).hexdigest()
    observed_at = data["observed_at"]
    now = timezone.now()
    if observed_at < now - timedelta(minutes=10) or observed_at > now + timedelta(minutes=2):
        raise ValidationError(
            {"observed_at": "The telemetry timestamp is outside the accepted window."}
        )
    for field in ("cpu_percent", "ram_percent", "disk_percent"):
        value = data.get(field)
        if value is not None and not Decimal("0") <= value <= Decimal("100"):
            raise ValidationError({field: "Use a value between 0 and 100."})
    with transaction.atomic():
        credential = (
            MonitoringCredential.objects.select_for_update()
            .filter(token_hash=digest, revoked_at__isnull=True)
            .first()
        )
        if credential is None:
            raise ValidationError({"credential": "Invalid telemetry credential."})
        deployment = (
            Deployment.objects.select_for_update()
            .select_related("store")
            .get(pk=credential.deployment_id)
        )
        if str(data["deployment_id"]) != str(deployment.pk) or str(data["store_id"]) != str(
            deployment.store_id
        ):
            raise ValidationError({"deployment_id": "The report is bound to another deployment."})
        observation = MonitoringObservation.objects.create(
            deployment=deployment,
            credential=credential,
            report_id=data["report_id"],
            observed_at=observed_at,
            store_status=data["store_status"],
            api_status=data["api_status"],
            response_ms=data.get("response_ms"),
            cpu_percent=data.get("cpu_percent"),
            ram_percent=data.get("ram_percent"),
            disk_percent=data.get("disk_percent"),
            visitor_count_24h=data.get("visitor_count_24h"),
            application_version=data.get("application_version", ""),
        )
        MonitoringCredential.objects.filter(pk=credential.pk).update(last_used_at=now)
        authoritative = (
            deployment.last_observed_at is None or observed_at > deployment.last_observed_at
        )
        if authoritative:
            deployment.observed_version = data.get("application_version", "")
            deployment.last_observed_at = observed_at
            if data["store_status"] == "healthy" and data["api_status"] == "healthy":
                deployment.last_healthy_at = observed_at
            deployment.save(
                update_fields=[
                    "observed_version",
                    "last_observed_at",
                    "last_healthy_at",
                    "updated_at",
                ]
            )
        if data.get("backup_status"):
            BackupObservation.objects.create(
                deployment=deployment,
                backup_type=data.get("backup_type") or "database",
                observed_at=observed_at,
                backup_at=data.get("backup_at"),
                status=data["backup_status"],
            )
        if authoritative:
            evaluate_alerts(observation)
    return observation


def evaluate_alerts(observation: MonitoringObservation) -> None:
    rules: list[tuple[bool | None, str, str, str]] = [
        (
            observation.store_status == "unavailable",
            "store_unavailable",
            "critical",
            "Store unavailable",
        ),
        (observation.api_status == "unavailable", "api_unavailable", "critical", "API unavailable"),
        (
            None if observation.response_ms is None else observation.response_ms > 2000,
            "high_response_time",
            "warning",
            "High response time",
        ),
        (
            None if observation.cpu_percent is None else observation.cpu_percent >= 90,
            "cpu_high",
            "warning",
            "CPU usage high",
        ),
        (
            None if observation.ram_percent is None else observation.ram_percent >= 90,
            "ram_high",
            "warning",
            "RAM usage high",
        ),
        (
            None if observation.disk_percent is None else observation.disk_percent >= 85,
            "disk_high",
            "critical",
            "Disk usage high",
        ),
    ]
    for active, category, severity, title in rules:
        key = f"deployment:{observation.deployment_id}:{category}"
        alert = OperationalAlert.objects.filter(deduplication_key=key).first()
        if active is True:
            if alert is None:
                alert = OperationalAlert.objects.create(
                    deployment=observation.deployment,
                    category=category,
                    severity=severity,
                    deduplication_key=key,
                    title=title,
                    source_observation=observation,
                    details={
                        "source": "reported",
                        "observed_at": observation.observed_at.isoformat(),
                    },
                )
                notify_alert(alert)
            elif alert.status == "resolved":
                alert.status = "open"
                alert.resolved_at = None
                alert.resolved_by = None
                alert.acknowledged_at = None
                alert.acknowledged_by = None
                alert.silenced_until = None
                alert.source_observation = observation
                alert.details = {
                    "source": "reported",
                    "observed_at": observation.observed_at.isoformat(),
                    "reactivated": True,
                }
                alert.save(
                    update_fields=[
                        "status",
                        "resolved_at",
                        "resolved_by",
                        "acknowledged_at",
                        "acknowledged_by",
                        "silenced_until",
                        "source_observation",
                        "details",
                        "updated_at",
                    ]
                )
                notify_alert(alert, incident_at=observation.observed_at)
        elif active is False and alert is not None and alert.status in {"open", "acknowledged"}:
            alert.status = "resolved"
            alert.resolved_at = observation.observed_at
            alert.save(update_fields=["status", "resolved_at", "updated_at"])


def notify_alert(alert: OperationalAlert, *, incident_at: Any = None) -> None:
    from control.accounts.permissions import permissions_for

    for user in AdminUser.objects.filter(is_active=True):
        if "alerts.read" in permissions_for(user.role):
            deduplication_key = f"alert:{alert.pk}"
            if incident_at is not None:
                deduplication_key = f"{deduplication_key}:{incident_at.isoformat()}"
            ConsoleNotification.objects.get_or_create(
                user=user,
                deduplication_key=deduplication_key,
                defaults={
                    "kind": "alert",
                    "title": alert.title,
                    "body": "A new fleet alert needs review.",
                    "link": "/alerts",
                },
            )
