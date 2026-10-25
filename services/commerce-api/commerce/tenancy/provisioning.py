"""Creating a store's tenant: the only supported way to add a Tenant row.

Single-tenant mode allows exactly one tenant per database, and it must be the one this
deployment is configured to serve (MATECART_TENANT_ID). The tenant, its store settings and the
audit event are written in one transaction: provisioning either completes or leaves nothing.
"""

import uuid
from dataclasses import asdict, dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from commerce.audit.services import field_changes, record_audit_event
from commerce.core.contact import normalize_email, normalize_phone
from commerce.stores.models import StoreSettings

from .context import tenant_context
from .exceptions import TenantAlreadyProvisioned, TenantProvisioningError
from .models import Tenant
from .resolution import configured_tenant_id

# What the provisioning audit event records: operational facts about the store, never its
# contact details or legal identity (data minimisation, D-035).
PROVISIONING_AUDIT_FIELDS = (
    "handle",
    "name",
    "preset",
    "country",
    "default_currency",
    "timezone",
    "default_language",
    "supported_languages",
)


@dataclass(frozen=True)
class StoreProfile:
    handle: str
    name: str
    preset: str
    country: str
    default_currency: str
    timezone: str
    default_language: str
    supported_languages: tuple[str, ...]
    legal_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""


def provision_tenant(profile: StoreProfile, *, tenant_id: uuid.UUID | None = None) -> Tenant:
    configured = configured_tenant_id()
    if configured is None:
        raise TenantProvisioningError(
            "Set MATECART_TENANT_ID before provisioning; it is this deployment's tenant id."
        )
    tenant_id = tenant_id or configured
    if tenant_id != configured:
        raise TenantProvisioningError(
            f"Tenant {tenant_id} does not match this deployment's MATECART_TENANT_ID {configured}."
        )
    if Tenant.objects.filter(pk=tenant_id).exists():
        raise TenantAlreadyProvisioned(f"Tenant {tenant_id} is already provisioned.")
    if Tenant.objects.exists():
        raise TenantProvisioningError(
            "This single-tenant database already contains a different tenant."
        )

    try:
        with transaction.atomic():
            tenant = Tenant(id=tenant_id, handle=profile.handle)
            tenant.full_clean()
            tenant.save(force_insert=True)
            with tenant_context(tenant):
                store = StoreSettings(
                    tenant=tenant,
                    name=profile.name,
                    legal_name=profile.legal_name,
                    preset=profile.preset,
                    country=profile.country,
                    default_currency=profile.default_currency,
                    timezone=profile.timezone,
                    default_language=profile.default_language,
                    supported_languages=list(profile.supported_languages),
                    contact_email=normalize_email(profile.contact_email) or "",
                )
                store.full_clean()
                # Normalised against the country validated above (national formats allowed).
                store.contact_phone = normalize_phone(profile.contact_phone, store.country) or ""
                store.save(force_insert=True)
                record_audit_event(
                    action="tenant.provisioned",
                    resource=tenant,
                    actor_label="provision_tenant",
                    changes=field_changes({}, asdict(profile), fields=PROVISIONING_AUDIT_FIELDS),
                )
    except ValidationError as exc:
        raise TenantProvisioningError(_describe(exc)) from exc
    except IntegrityError as exc:
        # A concurrent provisioning of the same tenant or handle won the race. The database
        # message is not echoed: it can quote submitted values.
        raise TenantProvisioningError(
            "Provisioning conflicted with existing data (tenant id or handle already in use)."
        ) from exc
    return tenant


def _describe(error: ValidationError) -> str:
    if hasattr(error, "error_dict"):
        return "; ".join(
            f"{field}: {' '.join(messages)}" for field, messages in error.message_dict.items()
        )
    return " ".join(error.messages)
