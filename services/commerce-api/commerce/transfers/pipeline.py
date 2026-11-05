"""One import pipeline for files and store migrations (D-105 to D-108).

1. **Stage.** A CSV/JSONL file is parsed, or a source is read page by page (with a checkpoint
   after every page). Each record is normalised and validated without writing business data,
   and stored as an ``ImportItem``.
2. **Classify.** Each valid item is matched against existing records (source mapping first, then
   SKU or code) and given an action under the job's conflict strategy: create, update, skip, or
   "needs a decision". Duplicates inside the source and SKUs owned by another source record are
   invalid. Nothing has been written yet: this is the preview.
3. **Approve.** An authorised staff member confirms (recent sign-in for migrations and
   customer/order data). Conflicts must be decided first.
4. **Run.** Celery processes items in bounded batches, each item in its own transaction through
   the domain services; a failure affects only that item. Progress comes from the item rows.
   Cancellation stops between items; resuming continues with the pending items only.
5. **Report.** Counters, a CSV error report, and a compensation action that removes only what
   the job created and nothing has used since.

Removing records absent from the source is never part of an import.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Iterable, Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from commerce.audit.services import record_audit_event
from commerce.catalog import services as catalog
from commerce.catalog.models import Product, ProductVariant
from commerce.checkout.models import Coupon
from commerce.core.domain import ConflictError, DomainError
from commerce.identity.models import User
from commerce.inventory.models import StockMovement
from commerce.orders.models import OrderItem
from commerce.pricing.services import currency_precision
from commerce.stores.models import StoreSettings
from commerce.tenancy.context import get_active_tenant_id

from . import csvio, importer, normalize
from .models import (
    ConflictStrategy,
    DataTransferFile,
    DataTransferJob,
    ExternalSourceMapping,
    FilePurpose,
    HistoricalCustomer,
    HistoricalOrder,
    ImportItem,
    ItemAction,
    ItemStatus,
    JobKind,
    JobSource,
    JobStatus,
)
from .sources.base import OBJECT_TYPES, SENSITIVE_TYPES

logger = logging.getLogger(__name__)

BATCH = 25
TYPE_OF_SCOPE = {
    "categories": "category",
    "products": "product",
    "customers": "customer",
    "orders": "order",
    "coupons": "coupon",
}
STALE_RUNNING = timedelta(minutes=10)


def _store() -> StoreSettings:
    store = StoreSettings.objects.first()
    if store is None:
        raise DomainError("Store settings are missing.", "store_missing")
    return store


def save_file(
    *, data: bytes, name: str, content_type: str, purpose: str, actor: User, days: int | None = None
) -> DataTransferFile:
    row = DataTransferFile(
        tenant_id=get_active_tenant_id(),
        purpose=purpose,
        display_name=name[:120],
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        created_by=actor,
        expires_at=timezone.now() + timedelta(days=days or settings.MATECART_TRANSFER_FILE_DAYS),
    )
    extension = ".jsonl" if content_type == "application/x-ndjson" else ".csv"
    row.file.save(f"transfer{extension}", ContentFile(data), save=False)
    try:
        row.save()
    except BaseException:
        row.file.delete(save=False)
        raise
    return row


def read_file(row: DataTransferFile) -> bytes:
    with row.file.open("rb") as handle:
        return bytes(handle.read(settings.MATECART_TRANSFER_MAX_UPLOAD_BYTES + 1))


# --- Creating jobs --------------------------------------------------------------------------------


def create_file_import(
    *,
    data: bytes,
    filename: str,
    actor: User,
    strategy: str,
    options: Mapping[str, Any],
) -> DataTransferJob:
    if len(data) > settings.MATECART_TRANSFER_MAX_UPLOAD_BYTES:
        raise DomainError("This file is too large.", "import_file_too_large")
    is_jsonl = filename.lower().endswith((".jsonl", ".ndjson"))
    if is_jsonl:
        csvio.read_jsonl(data)
    else:
        headers, _ = csvio.read_csv(data)
    upload = save_file(
        data=data,
        name=filename or "import.csv",
        content_type="application/x-ndjson" if is_jsonl else "text/csv",
        purpose=FilePurpose.UPLOAD,
        actor=actor,
    )
    mapping = dict(options.get("mapping") or {})
    if not is_jsonl:
        mapping = mapping or normalize.auto_mapping(headers)
        unknown = [target for target in mapping if target not in normalize.CATALOG_FIELDS]
        missing = [header for header in mapping.values() if header not in headers]
        if unknown or missing:
            raise ValidationError({"mapping": ["Map template fields to columns of this file."]})
    with transaction.atomic():
        job = DataTransferJob.objects.create(
            kind=JobKind.IMPORT,
            source=JobSource.JSONL if is_jsonl else JobSource.CSV,
            status=JobStatus.ANALYSING,
            entity="catalog",
            scopes=["products"],
            conflict_strategy=strategy,
            options={
                "mapping": mapping,
                "headers": [] if is_jsonl else headers,
                "include_stock": bool(options.get("include_stock", True)),
                "download_images": bool(options.get("download_images", False)),
                "publish": bool(options.get("publish", False)),
                "update_prices": bool(options.get("update_prices", False)),
            },
            input_file=upload,
            created_by=actor,
        )
        record_audit_event(
            action="transfers.job.created",
            resource=job,
            actor=actor,
            changes={"kind": job.kind, "source": job.source, "strategy": strategy},
        )
        _enqueue_on_commit("analyse", job)
    return job


def create_migration(
    *,
    connection: Any,
    scopes: list[str],
    actor: User,
    permissions: frozenset[str],
    strategy: str,
    options: Mapping[str, Any],
) -> DataTransferJob:
    from .connections import ensure_scopes

    ordered = [scope for scope in OBJECT_TYPES if scope in scopes]
    if not ordered:
        raise ValidationError({"scopes": ["Choose what to migrate."]})
    ensure_scopes(connection, ordered, permissions)
    with transaction.atomic():
        job = DataTransferJob.objects.create(
            kind=JobKind.MIGRATION,
            source=connection.platform,
            status=JobStatus.ANALYSING,
            entity="store",
            scopes=ordered,
            connection=connection,
            conflict_strategy=strategy,
            sensitive=bool(SENSITIVE_TYPES & set(ordered)),
            options={
                "include_stock": bool(options.get("include_stock", True)),
                "download_images": bool(options.get("download_images", True)),
                "publish": bool(options.get("publish", False)),
                "update_prices": bool(options.get("update_prices", False)),
            },
            created_by=actor,
        )
        record_audit_event(
            action="transfers.migration.created",
            resource=job,
            actor=actor,
            changes={"platform": connection.platform, "scopes": ordered, "strategy": strategy},
        )
        _enqueue_on_commit("analyse", job)
    return job


def _enqueue_on_commit(step: str, job: DataTransferJob, countdown: int = 0) -> None:
    tenant_id = str(get_active_tenant_id())
    job_id = str(job.pk)

    def enqueue() -> None:
        from . import tasks

        task = {"analyse": tasks.analyse_job, "run": tasks.run_job, "export": tasks.export_job}[
            step
        ]
        try:
            task.apply_async((job_id,), {"tenant_id": tenant_id}, countdown=countdown)
        except Exception:  # broker down: the sweep resumes the job
            logger.warning("transfers.enqueue_failed", extra={"job": job_id})

    transaction.on_commit(enqueue)


def enqueue(step: str, job: DataTransferJob, countdown: int = 0) -> None:
    with transaction.atomic():
        _enqueue_on_commit(step, job, countdown)


# --- Staging --------------------------------------------------------------------------------------


def _context(job: DataTransferJob) -> dict[str, Any]:
    store = _store()
    currency = store.default_currency
    return {
        "preset": store.preset,
        "currency": currency,
        "places": currency_precision(currency),
        "source_currency": job.connection.source_currency if job.connection else "",
    }


def _check(object_type: str, raw: Mapping[str, Any], ctx: Mapping[str, Any]) -> normalize.Checked:
    if object_type == "product":
        if raw.get("unsupported"):
            return normalize.Checked(dict(raw), [f"Not imported: {raw['unsupported']}."], [])
        checked = normalize.check_product(
            raw,
            preset=ctx["preset"],
            currency=ctx["currency"],
            places=ctx["places"],
            source_currency=ctx["source_currency"],
        )
        checked.warnings = list(raw.get("source_warnings") or []) + checked.warnings
        return checked
    if object_type == "category":
        return normalize.check_category(raw)
    if object_type == "customer":
        return normalize.check_customer(raw)
    if object_type == "order":
        return normalize.check_order(raw)
    return normalize.check_coupon(raw, currency=ctx["currency"], places=ctx["places"])


def _key(object_type: str, record: Mapping[str, Any]) -> str:
    if object_type == "product":
        return ",".join(v.get("sku", "") for v in record.get("variants") or [])[:128]
    if object_type == "coupon":
        return str(record.get("code") or "")
    if object_type == "customer":
        return str(record.get("email") or record.get("external_id") or "")[:128]
    if object_type == "order":
        return str(record.get("number") or "")
    return str(record.get("name") or "")[:128]


def _title(object_type: str, record: Mapping[str, Any]) -> str:
    if object_type == "customer":
        return (
            f"{record.get('first_name', '')} {record.get('last_name', '')}".strip()[:200]
            or "Customer"
        )
    if object_type == "order":
        return f"Order {record.get('number', '')}"[:200]
    if object_type == "coupon":
        return str(record.get("code") or "Coupon")
    return str(record.get("name") or "")[:200]


def stage(
    job: DataTransferJob,
    object_type: str,
    records: Iterable[Mapping[str, Any]],
    start: int,
    lines: list[list[int]] | None = None,
) -> int:
    """Validate records and store them as items from ``start`` (idempotent per position)."""
    ctx = _context(job)
    rows = []
    position = start
    for index, raw in enumerate(records):
        checked = _check(object_type, raw, ctx)
        record = checked.record
        messages = [{"level": "error", "text": text} for text in checked.errors]
        messages += [{"level": "warning", "text": text} for text in checked.warnings]
        if lines is not None:
            messages.insert(
                0, {"level": "info", "text": "File lines " + ", ".join(map(str, lines[index]))}
            )
        rows.append(
            ImportItem(
                job=job,
                position=position,
                object_type=object_type,
                external_id=str(record.get("external_id") or "")[:128],
                key=_key(object_type, record),
                title=_title(object_type, record),
                action=ItemAction.INVALID if checked.errors else ItemAction.CREATE,
                messages=messages[:30],
                payload=record,
            )
        )
        position += 1
    ImportItem.objects.bulk_create(rows, ignore_conflicts=True)
    return position


def analyse_file(job: DataTransferJob) -> None:
    data = read_file(job.input_file) if job.input_file else b""
    if job.source == JobSource.JSONL:
        records = csvio.read_jsonl(data)
        stage(job, "product", records, 0)
    else:
        _, rows = csvio.read_csv(data)
        grouped = normalize.products_from_rows(rows, job.options.get("mapping") or {})
        stage(job, "product", [record for record, _ in grouped], 0, [lines for _, lines in grouped])
    classify(job)


def extract_step(job: DataTransferJob) -> float | None:
    """Read the next source page(s) into items. Returns seconds to wait before the next call, or
    None when extraction is complete."""
    from .connections import source_for

    source = source_for(job.connection)
    checkpoint = dict(job.checkpoint or {})
    scope_index = int(checkpoint.get("scope", 0))
    position = int(checkpoint.get("position", 0))
    pages = 0
    while scope_index < len(job.scopes):
        scope = job.scopes[scope_index]
        page = source.page(scope, checkpoint.get("cursor"))
        if page.records:
            position = stage(job, TYPE_OF_SCOPE[scope], page.records, position)
        if page.wait is not None:
            checkpoint.update(scope=scope_index, cursor=page.next_cursor, position=position)
            _save_checkpoint(job, checkpoint)
            return page.wait
        if page.next_cursor:
            checkpoint.update(scope=scope_index, cursor=page.next_cursor, position=position)
        else:
            scope_index += 1
            checkpoint.update(scope=scope_index, cursor=None, position=position)
        _save_checkpoint(job, checkpoint)
        pages += 1
        if pages >= 20:  # keep tasks short; continue in a new task
            return 0.0
    classify(job)
    return None


def _save_checkpoint(job: DataTransferJob, checkpoint: dict[str, Any]) -> None:
    DataTransferJob.objects.filter(pk=job.pk).update(
        checkpoint=checkpoint, heartbeat_at=timezone.now(), updated_at=timezone.now()
    )
    job.checkpoint = checkpoint


def _ctx(job: DataTransferJob, actor: User | None = None) -> importer.Context:
    store = _store()
    if job.connection is not None:
        platform, identity = job.connection.platform, job.connection.store_identity
    else:
        platform, identity = "file", "catalog"
    return importer.Context(
        job=job,
        actor=actor or job.approved_by or job.created_by,
        platform=platform,
        identity=identity,
        currency=store.default_currency,
    )


def classify(job: DataTransferJob) -> None:
    """Plan an action for every valid item (no business data is written)."""
    ctx = _ctx(job)
    seen_skus: set[str] = set()
    seen_codes: set[str] = set()
    seen_external: set[tuple[str, str]] = set()
    items = ImportItem.objects.filter(job=job).order_by("position")
    for item in items.iterator(chunk_size=500):
        if item.action == ItemAction.INVALID:
            continue
        errors: list[str] = []
        existing: Any = None
        marker = (item.object_type, item.external_id)
        if item.external_id and marker in seen_external:
            errors.append("Appears more than once in the source.")
        seen_external.add(marker)
        if item.object_type == "product":
            skus = [v["sku"] for v in item.payload.get("variants") or []]
            repeated = [sku for sku in skus if sku in seen_skus]
            if repeated:
                errors.append(f"SKU {repeated[0]} appears in another record of this import.")
            seen_skus.update(skus)
            product, problem = importer.existing_product(ctx, item.payload)
            if problem:
                errors.append(problem)
            existing = product.pk if product is not None else None
        elif item.object_type == "category":
            existing = importer.mapped(ctx, "category", item.external_id)
        elif item.object_type == "customer":
            existing = (
                HistoricalCustomer.objects.filter(
                    platform=ctx.platform,
                    source_identity=ctx.identity,
                    external_id=item.external_id,
                )
                .values_list("pk", flat=True)
                .first()
            )
        elif item.object_type == "order":
            existing = (
                HistoricalOrder.objects.filter(
                    platform=ctx.platform,
                    source_identity=ctx.identity,
                    external_id=item.external_id,
                )
                .values_list("pk", flat=True)
                .first()
            )
        elif item.object_type == "coupon":
            code = item.payload.get("code", "")
            if code in seen_codes:
                errors.append("This code appears more than once in the source.")
            seen_codes.add(code)
            existing = Coupon.objects.filter(code=code).values_list("pk", flat=True).first()
        if errors:
            item.action = ItemAction.INVALID
            item.messages = [*({"level": "error", "text": t} for t in errors), *item.messages][:30]
        elif existing is None:
            item.action = ItemAction.CREATE
        elif item.object_type in ("order", "coupon", "category"):
            # History and coupons are never overwritten; categories are reused.
            item.action = ItemAction.SKIP
        elif job.conflict_strategy == ConflictStrategy.UPDATE:
            item.action = ItemAction.UPDATE
        elif job.conflict_strategy == ConflictStrategy.MANUAL:
            item.action = ItemAction.CONFLICT
        else:
            item.action = ItemAction.SKIP
        item.existing_id = existing
        item.save(update_fields=["action", "messages", "existing_id"])
    summarise(job)
    DataTransferJob.objects.filter(pk=job.pk, status=JobStatus.ANALYSING).update(
        status=JobStatus.REVIEW, heartbeat_at=timezone.now(), updated_at=timezone.now()
    )


def summarise(job: DataTransferJob) -> dict[str, Any]:
    rows = ImportItem.objects.filter(job=job)
    by_type: dict[str, dict[str, int]] = {}
    for row in rows.values("object_type", "action").annotate(n=Count("pk")).order_by():
        by_type.setdefault(row["object_type"], {})[row["action"]] = row["n"]
    summary = {
        "total": rows.count(),
        "valid": rows.exclude(action=ItemAction.INVALID).count(),
        "invalid": rows.filter(action=ItemAction.INVALID).count(),
        "to_create": rows.filter(action=ItemAction.CREATE).count(),
        "to_update": rows.filter(action=ItemAction.UPDATE).count()
        + rows.filter(action=ItemAction.CONFLICT, decision="update").count(),
        "to_skip": rows.filter(action=ItemAction.SKIP).count()
        + rows.filter(action=ItemAction.CONFLICT, decision="skip").count(),
        "conflicts": rows.filter(action=ItemAction.CONFLICT).count(),
        "undecided": rows.filter(action=ItemAction.CONFLICT, decision="").count(),
        "with_warnings": rows.filter(messages__contains=[{"level": "warning"}]).count(),
        "by_type": by_type,
    }
    DataTransferJob.objects.filter(pk=job.pk).update(
        summary=summary, total_items=summary["total"], updated_at=timezone.now()
    )
    job.summary = summary
    return summary


# --- Review ---------------------------------------------------------------------------------------


def decide(*, job: DataTransferJob, decisions: Mapping[str, str], actor: User) -> DataTransferJob:
    if job.status != JobStatus.REVIEW:
        raise ConflictError("Decisions can be made only while reviewing.", "transfer_not_in_review")
    for item_id, decision in decisions.items():
        if decision not in ("skip", "update"):
            raise ValidationError({"decisions": ["Choose skip or update."]})
        updated = ImportItem.objects.filter(job=job, pk=item_id, action=ItemAction.CONFLICT).update(
            decision=decision
        )
        if not updated:
            raise ValidationError({"decisions": ["Unknown item."]})
    summarise(job)
    return job


def approve(*, job: DataTransferJob, actor: User) -> DataTransferJob:
    with transaction.atomic():
        locked = DataTransferJob.objects.select_for_update().get(pk=job.pk)
        if locked.status != JobStatus.REVIEW:
            raise ConflictError("This job is not waiting for approval.", "transfer_not_in_review")
        if ImportItem.objects.filter(job=locked, action=ItemAction.CONFLICT, decision="").exists():
            raise ConflictError(
                "Decide every item that needs a decision first.", "transfer_undecided"
            )
        if not ImportItem.objects.filter(job=locked).exclude(action=ItemAction.INVALID).exists():
            raise ConflictError("Nothing valid to import.", "transfer_nothing_valid")
        locked.status = JobStatus.QUEUED
        locked.approved_by = actor
        locked.approved_at = timezone.now()
        locked.save()
        record_audit_event(
            action="transfers.job.approved",
            resource=locked,
            actor=actor,
            changes={
                "kind": locked.kind,
                "summary": {k: v for k, v in locked.summary.items() if k != "by_type"},
            },
        )
        _enqueue_on_commit("run", locked)
    return locked


def cancel(*, job: DataTransferJob, actor: User) -> DataTransferJob:
    with transaction.atomic():
        locked = DataTransferJob.objects.select_for_update().get(pk=job.pk)
        if locked.status in (JobStatus.REVIEW, JobStatus.ANALYSING, JobStatus.QUEUED):
            locked.status = JobStatus.CANCELLED
            locked.finished_at = timezone.now()
        elif locked.status == JobStatus.RUNNING:
            locked.cancel_requested = True  # stops after the current item
        else:
            raise ConflictError("This job has already finished.", "transfer_finished")
        locked.save()
        record_audit_event(
            action="transfers.job.cancelled",
            resource=locked,
            actor=actor,
            changes={"status": locked.status},
        )
    return locked


def resume(*, job: DataTransferJob, actor: User) -> DataTransferJob:
    with transaction.atomic():
        locked = DataTransferJob.objects.select_for_update().get(pk=job.pk)
        if locked.approved_at is None or locked.status not in (
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ):
            raise ConflictError(
                "Only an approved, stopped job can be resumed.", "transfer_not_resumable"
            )
        if not ImportItem.objects.filter(job=locked, status=ItemStatus.PENDING).exists():
            raise ConflictError("Nothing is left to import.", "transfer_nothing_left")
        locked.status = JobStatus.QUEUED
        locked.cancel_requested = False
        locked.error_code = ""
        locked.finished_at = None
        locked.save()
        record_audit_event(action="transfers.job.resumed", resource=locked, actor=actor, changes={})
        _enqueue_on_commit("run", locked)
    return locked


# --- Execution ------------------------------------------------------------------------------------


def _effective_action(item: ImportItem) -> str:
    if item.action == ItemAction.CONFLICT:
        return ItemAction.UPDATE if item.decision == "update" else ItemAction.SKIP
    return item.action


def _finish_item(
    item: ImportItem,
    status: str,
    messages: list[dict[str, str]] | None = None,
    local_id: Any = None,
    created: bool = False,
) -> None:
    ImportItem.objects.filter(pk=item.pk, status=ItemStatus.PENDING).update(
        status=status,
        processed_at=timezone.now(),
        local_id=local_id,
        created_local=created,
        messages=(item.messages + (messages or []))[:40],
    )


def process_item(job: DataTransferJob, item_id: Any, ctx: importer.Context) -> None:
    item = ImportItem.objects.filter(pk=item_id).first()
    if item is None or item.status != ItemStatus.PENDING:
        return
    action = _effective_action(item)
    if action == ItemAction.INVALID:
        _finish_item(item, ItemStatus.FAILED)
        return
    if action == ItemAction.SKIP:
        local = item.existing_id
        if item.object_type == "category" and local:
            pass
        _finish_item(item, ItemStatus.SKIPPED, local_id=local)
        return
    assets: list[Any] = []
    notes: list[str] = []
    if (
        item.object_type == "product"
        and action == ItemAction.CREATE
        and job.options.get("download_images")
        and item.payload.get("images")
    ):
        assets, notes = importer.download_images(item.payload, ctx.actor)
    try:
        with transaction.atomic():
            locked = ImportItem.objects.select_for_update().get(pk=item.pk)
            if locked.status != ItemStatus.PENDING:
                return
            created = False
            if locked.object_type == "product":
                if action == ItemAction.CREATE:
                    product, warnings = importer.create_product(ctx, locked, assets)
                    created = True
                else:
                    product = Product.objects.select_for_update().get(pk=_uuid(locked.existing_id))
                    warnings = importer.update_product(ctx, locked, product)
                importer.remember(ctx, "product", locked.external_id, product.pk, created=created)
                local_id = product.pk
                notes += warnings
            elif locked.object_type == "category":
                local_id, created = importer.apply_category(ctx, locked)
            elif locked.object_type == "customer":
                local_id, created = importer.apply_customer(ctx, locked)
            elif locked.object_type == "order":
                local_id, created = importer.apply_order(ctx, locked)
            else:
                local_id, created = importer.apply_coupon(ctx, locked)
            locked.status = ItemStatus.DONE
            locked.processed_at = timezone.now()
            locked.local_id = local_id
            locked.created_local = created
            locked.messages = (locked.messages + [{"level": "warning", "text": t} for t in notes])[
                :40
            ]
            locked.save()
    except (importer.ItemProblem, ValidationError, DomainError) as exc:
        _finish_item(item, ItemStatus.FAILED, [{"level": "error", "text": _message(exc)}])
    except Exception:
        logger.exception(
            "transfers.item_failed", extra={"job": str(job.pk), "position": item.position}
        )
        _finish_item(
            item,
            ItemStatus.FAILED,
            [{"level": "error", "text": "Unexpected error while importing this record."}],
        )


def _message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        parts = []
        for key, value in detail.items():
            text = value[0] if isinstance(value, list) and value else value
            parts.append(f"{key}: {text}")
        return "; ".join(parts)[:400]
    if isinstance(detail, list) and detail:
        return str(detail[0])[:400]
    return str(detail or exc)[:400]


def run_batch(job_id: Any) -> str:
    """Process the next batch. Returns "more", "done", "cancelled" or the job's status."""
    with transaction.atomic():
        job = DataTransferJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None:
            return "missing"
        if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return job.status
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.RUNNING
            job.started_at = job.started_at or timezone.now()
        job.heartbeat_at = timezone.now()
        job.save()
    ctx = _ctx(job)
    ids = list(
        ImportItem.objects.filter(job=job, status=ItemStatus.PENDING)
        .order_by("position")
        .values_list("pk", flat=True)[:BATCH]
    )
    for item_id in ids:
        if DataTransferJob.objects.filter(pk=job.pk, cancel_requested=True).exists():
            break
        process_item(job, item_id, ctx)
        DataTransferJob.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now())
    refresh_counts(job)
    job.refresh_from_db()
    if job.cancel_requested:
        _close(job, JobStatus.CANCELLED)
        return "cancelled"
    if ImportItem.objects.filter(job=job, status=ItemStatus.PENDING).exists():
        return "more"
    status = JobStatus.COMPLETED_WITH_ERRORS if job.failed_count else JobStatus.COMPLETED
    _close(job, status)
    return "done"


def refresh_counts(job: DataTransferJob) -> None:
    rows = ImportItem.objects.filter(job=job)
    done = rows.filter(status=ItemStatus.DONE)
    DataTransferJob.objects.filter(pk=job.pk).update(
        processed_items=rows.exclude(status=ItemStatus.PENDING).count(),
        created_count=done.filter(created_local=True).count(),
        updated_count=done.filter(created_local=False).count(),
        skipped_count=rows.filter(status=ItemStatus.SKIPPED).count(),
        failed_count=rows.filter(status=ItemStatus.FAILED).count(),
        updated_at=timezone.now(),
    )


def _close(job: DataTransferJob, status: str) -> None:
    report = error_report(job)
    with transaction.atomic():
        locked = DataTransferJob.objects.select_for_update().get(pk=job.pk)
        locked.status = status
        locked.finished_at = timezone.now()
        locked.cancel_requested = False
        if report is not None:
            locked.report_file = report
        locked.save()
        record_audit_event(
            action="transfers.job.finished",
            resource=locked,
            actor=locked.approved_by,
            changes={
                "status": status,
                "created": locked.created_count,
                "updated": locked.updated_count,
                "skipped": locked.skipped_count,
                "failed": locked.failed_count,
            },
        )
    if status in (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS):
        clear_personal_payloads(job)


def clear_personal_payloads(job: DataTransferJob) -> None:
    """Customer and order payloads are personal data; the historical tables keep what is needed."""
    ImportItem.objects.filter(job=job, object_type__in=("customer", "order")).exclude(
        status=ItemStatus.PENDING
    ).update(payload={})


def error_report(job: DataTransferJob) -> DataTransferFile | None:
    rows = (
        ImportItem.objects.filter(job=job)
        .filter(Q(status=ItemStatus.FAILED) | Q(messages__contains=[{"level": "warning"}]))
        .order_by("position")
    )
    if not rows.exists():
        return None
    header = ["position", "type", "record", "key", "status", "messages"]

    def lines() -> Iterable[dict[str, Any]]:
        for row in rows.iterator(chunk_size=500):
            yield {
                "position": row.position + 1,
                "type": row.object_type,
                "record": row.title,
                "key": row.key,
                "status": row.status,
                "messages": " | ".join(
                    m.get("text", "") for m in row.messages if m.get("level") != "info"
                ),
            }

    data = "".join(csvio.write_csv(header, lines())).encode("utf-8-sig")
    return save_file(
        data=data,
        name=f"import-report-{str(job.pk)[:8]}.csv",
        content_type="text/csv",
        purpose=FilePurpose.ERROR_REPORT,
        actor=job.created_by,
        days=30,
    )


# --- Compensation ---------------------------------------------------------------------------------


def compensate(*, job: DataTransferJob, actor: User) -> dict[str, int]:
    """Remove what this job created and nothing has depended on since.

    Products: deleted when never sold, never published and without stock history beyond the
    job's own opening balance... which itself is history, so stocked products are archived
    instead. Sold products are kept. Historical customers and orders created by the job are
    deleted; imported coupons are deleted unless redeemed. Records the job only matched or
    updated are never touched.
    """
    if job.status not in (
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_ERRORS,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    ):
        raise ConflictError("Wait until the job has stopped.", "transfer_running")
    if job.compensated_at is not None:
        raise ConflictError("This job was already reversed.", "transfer_compensated")
    result = {"deleted": 0, "archived": 0, "kept": 0}
    created = ImportItem.objects.filter(job=job, status=ItemStatus.DONE, created_local=True)
    for item in created.filter(object_type="product").iterator():
        product = Product.objects.filter(pk=_uuid(item.local_id)).first()
        if product is None:
            continue
        variants = ProductVariant.objects.filter(product=product)
        if OrderItem.objects.filter(variant__in=variants).exists():
            result["kept"] += 1
            continue
        try:
            if (
                product.published_at is None
                and not StockMovement.objects.filter(variant__in=variants).exists()
            ):
                with transaction.atomic():
                    ExternalSourceMapping.objects.filter(
                        local_id__in=[product.pk, *variants.values_list("pk", flat=True)]
                    ).delete()
                    catalog.delete_product(product=product, actor=actor)
                result["deleted"] += 1
            else:
                catalog.set_status(product=product, status="archived", actor=actor)
                result["archived"] += 1
        except (DomainError, ValidationError):
            result["kept"] += 1
    orders = HistoricalOrder.objects.filter(
        job=job, pk__in=created.filter(object_type="order").values("local_id")
    )
    result["deleted"] += orders.delete()[0]
    customers = HistoricalCustomer.objects.filter(
        job=job, pk__in=created.filter(object_type="customer").values("local_id")
    )
    HistoricalOrder.objects.filter(customer__in=customers).update(customer=None)
    result["deleted"] += customers.delete()[0]
    for item in created.filter(object_type="coupon").iterator():
        coupon = Coupon.objects.filter(pk=_uuid(item.local_id), times_redeemed=0).first()
        if coupon is None:
            result["kept"] += 1
            continue
        ExternalSourceMapping.objects.filter(local_id=coupon.pk).delete()
        coupon.delete()
        result["deleted"] += 1
    with transaction.atomic():
        DataTransferJob.objects.filter(pk=job.pk).update(
            compensated_at=timezone.now(), updated_at=timezone.now()
        )
        record_audit_event(
            action="transfers.job.compensated", resource=job, actor=actor, changes=result
        )
    return result


# --- Housekeeping ---------------------------------------------------------------------------------


def sweep(now: Any = None) -> dict[str, int]:
    """Resume jobs whose worker or broker was lost; remove expired files."""
    now = now or timezone.now()
    resumed = 0
    stale = DataTransferJob.objects.filter(
        status__in=(JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.ANALYSING)
    ).filter(
        Q(heartbeat_at__lt=now - STALE_RUNNING)
        | Q(heartbeat_at__isnull=True, updated_at__lt=now - STALE_RUNNING)
    )
    for job in stale[:20]:
        DataTransferJob.objects.filter(pk=job.pk).update(heartbeat_at=now)
        if job.kind == JobKind.EXPORT:
            enqueue("export", job)
        elif job.status == JobStatus.ANALYSING:
            enqueue("analyse", job)
        else:
            enqueue("run", job)
        resumed += 1
    expired = 0
    for row in DataTransferFile.objects.filter(expires_at__lt=now)[:200]:
        name, storage = row.file.name, row.file.storage
        row.delete()
        _delete_after_commit(storage, name)
        expired += 1
    return {"resumed": resumed, "expired_files": expired}


def money_text(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def upload_bytes(stream: io.BufferedIOBase | Any, limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise DomainError(
            f"Files can be at most {limit // (1024 * 1024)} MB.", "import_file_too_large"
        )
    if not data:
        raise DomainError("The file is empty.", "import_file_empty")
    return bytes(data)


def _uuid(value: Any) -> Any:
    """A required id; a missing one cannot match any row."""
    if value is None:
        raise importer.ItemProblem("The matched record is missing.")
    return value


def _delete_after_commit(storage: Any, name: str) -> None:
    def remove() -> None:
        storage.delete(name)

    transaction.on_commit(remove)
