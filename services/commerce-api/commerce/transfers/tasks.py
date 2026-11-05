"""Background work for imports, migrations and exports (D-108).

Tasks are idempotent and short: staging is checkpointed per source page, execution processes a
bounded batch of items and re-queues itself, and every item is processed at most once (row lock
and status check). A lost worker or broker leaves a stale heartbeat; the sweep re-queues it.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from commerce.tenancy.tasks import TenantTask

logger = logging.getLogger(__name__)


@shared_task(base=TenantTask, name="transfers.analyse_job", acks_late=True)
def analyse_job(job_id: str, *, tenant_id: str) -> str:
    from . import pipeline
    from .models import DataTransferJob, JobKind, JobStatus
    from .sources.base import SourceError

    job = DataTransferJob.objects.filter(pk=job_id).first()
    if job is None or job.status != JobStatus.ANALYSING:
        return "skipped"
    DataTransferJob.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now())
    try:
        if job.kind == JobKind.IMPORT:
            pipeline.analyse_file(job)
            return "review"
        wait = pipeline.extract_step(job)
    except SourceError as exc:
        if exc.kind == "rate_limited":
            pipeline.enqueue("analyse", job, countdown=int(exc.retry_after or 10))
            return "rate_limited"
        _fail(job.pk, exc.code)
        return "failed"
    except APIException as exc:  # a refused file (encoding, size, columns): its rule code
        codes = exc.get_codes()
        _fail(job.pk, codes if isinstance(codes, str) else "import_file_invalid")
        return "failed"
    except Exception:
        logger.exception("transfers.analyse_failed", extra={"job": job_id})
        _fail(job.pk, "analysis_failed")
        return "failed"
    if wait is not None:
        pipeline.enqueue("analyse", job, countdown=int(wait))
        return "continuing"
    return "review"


def _fail(job_id: Any, code: str) -> None:
    from .models import DataTransferJob, JobStatus

    with transaction.atomic():
        DataTransferJob.objects.filter(pk=job_id).update(
            status=JobStatus.FAILED,
            error_code=code[:48],
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )


@shared_task(base=TenantTask, name="transfers.run_job", acks_late=True)
def run_job(job_id: str, *, tenant_id: str) -> str:
    from . import pipeline
    from .models import DataTransferJob

    outcome = pipeline.run_batch(job_id)
    if outcome == "more":
        job = DataTransferJob.objects.get(pk=job_id)
        pipeline.enqueue("run", job)
    return outcome


@shared_task(base=TenantTask, name="transfers.export_job", acks_late=True)
def export_job(job_id: str, *, tenant_id: str) -> str:
    from commerce.audit.services import record_audit_event

    from . import exports, pipeline
    from .models import DataTransferJob, FilePurpose, JobStatus

    with transaction.atomic():
        job = DataTransferJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return "skipped"
        job.status = JobStatus.RUNNING
        job.started_at = job.started_at or timezone.now()
        job.heartbeat_at = timezone.now()
        job.save()
    try:
        data, count = exports.render(job.entity, job.file_format)
        stamp = timezone.now().strftime("%Y%m%d-%H%M")
        row = pipeline.save_file(
            data=data,
            name=f"{job.entity}-{stamp}.{job.file_format}",
            content_type="application/x-ndjson" if job.file_format == "jsonl" else "text/csv",
            purpose=FilePurpose.EXPORT,
            actor=job.created_by,
        )
    except Exception:
        logger.exception("transfers.export_failed", extra={"job": job_id})
        _fail(job.pk, "export_failed")
        return "failed"
    with transaction.atomic():
        DataTransferJob.objects.filter(pk=job.pk).update(
            status=JobStatus.COMPLETED,
            output_file=row,
            total_items=count,
            processed_items=count,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        record_audit_event(
            action="transfers.export.generated",
            resource=job,
            actor=job.created_by,
            changes={
                "entity": job.entity,
                "format": job.file_format,
                "rows": count,
                "sensitive": job.sensitive,
            },
        )
    return "completed"


@shared_task(base=TenantTask, name="transfers.sweep_tenant")
def sweep_tenant(*, tenant_id: str) -> dict[str, int]:
    from . import pipeline

    return pipeline.sweep()


@shared_task(name="transfers.sweep")
def sweep() -> dict[str, int]:
    """Beat entry point (every five minutes)."""
    from commerce.communications.tasks import _tenant

    tenant_id = _tenant()
    return sweep_tenant(tenant_id=tenant_id) if tenant_id else {}
