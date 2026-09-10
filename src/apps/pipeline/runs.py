"""Verarbeitungslaeufe und Objektserialitaet (Fachentwurf D 7.1, Beschluss B-26, docs/architektur.md 6.10).

Hoechstens processing.max_parallel_objects Laeufe vom Typ full oder incremental gleichzeitig running, durchgesetzt
ueber Cache-Sperre und Datenbankpruefung; weitere Objekte warten pending mit sichtbarer Warteposition.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.config import store
from apps.documents.models import Document
from apps.pipeline.jobs import enqueue, idempotency_key, send
from apps.pipeline.models import JobStatus, JobType, ProcessingJob, ProcessingRun, RunStatus, RunType
from apps.review.models import ReviewCase

logger = logging.getLogger(__name__)
SERIAL_TYPES = (RunType.FULL, RunType.INCREMENTAL)
LOCK_KEY = "processing:object-slots"


def start_run(obj, *, run_type: str = RunType.FULL, dry_run: bool = False, user=None) -> ProcessingRun:
    run = ProcessingRun.objects.create(
        object=obj, run_type=run_type, status=RunStatus.PENDING, dry_run=dry_run, triggered_by=user
    )
    schedule_runs()
    run.refresh_from_db()
    return run


def queue_position(run: ProcessingRun) -> int | None:
    if run.status != RunStatus.PENDING:
        return None
    return (
        ProcessingRun.objects.filter(
            status=RunStatus.PENDING, run_type__in=SERIAL_TYPES, created_at__lt=run.created_at
        ).count()
        + 1
    )


def schedule_runs() -> list[int]:
    """Startet wartende Laeufe, solange Plaetze frei sind (Redis-Sperre plus Datenbankpruefung)."""
    max_parallel = int(store.get("processing.max_parallel_objects", 1))
    started: list[int] = []
    if not cache.add(LOCK_KEY, "1", timeout=30):
        return started
    try:
        with transaction.atomic():
            running = (
                ProcessingRun.objects.select_for_update()
                .filter(status=RunStatus.RUNNING, run_type__in=SERIAL_TYPES)
                .count()
            )
            if running >= max_parallel:
                return started
            for run in (
                ProcessingRun.objects.select_for_update()
                .filter(status=RunStatus.PENDING, run_type__in=SERIAL_TYPES)
                .order_by("created_at")[: max_parallel - running]
            ):
                if ProcessingRun.objects.filter(
                    object=run.object, status=RunStatus.RUNNING, run_type__in=SERIAL_TYPES
                ).exists():
                    continue
                run.status = RunStatus.RUNNING
                run.started_at = timezone.now()
                run.worker_count = int(store.get("processing.max_parallel_objects", 1))
                run.save(update_fields=["status", "started_at", "worker_count", "updated_at"])
                started.append(run.pk)
    finally:
        cache.delete(LOCK_KEY)
    for run_id in started:
        dispatch_run(ProcessingRun.objects.get(pk=run_id))
    return started


def dispatch_run(run: ProcessingRun) -> int:
    """Reiht die offenen Jobs des Objekts ein; Dokumente ohne Job erhalten den passenden Startjob."""
    obj = run.object
    count = 0
    for doc in Document.objects.filter(
        object=obj, deleted_at__isnull=True, status__in=["registered", "hashed", "ocr_done"]
    ):
        if doc.status == "registered" and doc.source == "drive_existing":
            key = idempotency_key(JobType.DISCOVER, obj.pk, doc.drive_file_id, doc.drive_md5 or "")
            job, _ = enqueue(
                JobType.DISCOVER,
                obj,
                key=key,
                document=doc,
                run=run,
                payload={"drive_file_id": doc.drive_file_id, "md5": doc.drive_md5},
                dispatch=False,
            )
        elif doc.status == "registered":
            key = idempotency_key(JobType.HASH, obj.pk, doc.drive_file_id or f"upload-{doc.pk}")
            job, _ = enqueue(
                JobType.HASH,
                obj,
                key=key,
                document=doc,
                run=run,
                payload={"source_path": doc.source_path},
                dispatch=False,
            )
        elif doc.status == "hashed":
            key = idempotency_key(JobType.ANALYZE_PAGES, obj.pk, doc.sha256)
            job, _ = enqueue(JobType.ANALYZE_PAGES, obj, key=key, document=doc, run=run, dispatch=False)
        else:
            key = idempotency_key(JobType.EXTRACT_ENTITIES, obj.pk, doc.sha256)
            job, _ = enqueue(JobType.EXTRACT_ENTITIES, obj, key=key, document=doc, run=run, dispatch=False)
        count += 1
    pending = ProcessingJob.objects.filter(object=obj, status=JobStatus.PENDING, run__isnull=True)
    pending.update(run=run)
    for job in ProcessingJob.objects.filter(object=obj, status=JobStatus.PENDING, run=run):
        send(job)
    run.documents_total = Document.objects.filter(object=obj, deleted_at__isnull=True).count()
    run.save(update_fields=["documents_total", "updated_at"])
    return count


def maybe_finish_run(run: ProcessingRun) -> bool:
    """Lauf abschliessen, wenn kein Job des Objekts mehr offen ist; KPIs speichern (D 7.1)."""
    if run.status != RunStatus.RUNNING:
        return False
    open_jobs = ProcessingJob.objects.filter(
        object=run.object, status__in=[JobStatus.PENDING, JobStatus.RUNNING]
    ).exists()
    if open_jobs:
        return False
    with transaction.atomic():
        run = ProcessingRun.objects.select_for_update().get(pk=run.pk)
        if run.status != RunStatus.RUNNING:
            return False
        docs = Document.objects.filter(object=run.object, deleted_at__isnull=True)
        counts = {r["status"]: r["c"] for r in docs.values("status").annotate(c=Count("id"))}
        run.documents_total = sum(counts.values())
        run.documents_failed = counts.get("error", 0)
        run.documents_skipped = counts.get("duplicate", 0)
        run.documents_done = (
            run.documents_total
            - run.documents_failed
            - run.documents_skipped
            - counts.get("registered", 0)
            - counts.get("hashed", 0)
        )
        run.documents_misc = docs.filter(category_id="06").count()
        run.misc_share_pct = (
            Decimal(str(round(100 * run.documents_misc / run.documents_total, 2)))
            if run.documents_total
            else None
        )
        # bereinigt (B-31): ohne 03_Dubletten, 04_Nicht_objektbezogen und Faelle mit fachlichem Grund in 02
        from apps.documents.models import DocumentClassification

        adjusted = DocumentClassification.objects.filter(
            document__in=docs, is_final=True, features__kpi_misc_adjusted=True
        ).count()
        run.misc_share_adjusted_pct = (
            Decimal(str(round(100 * adjusted / run.documents_total, 2))) if run.documents_total else None
        )
        from apps.documents.models import DocumentPage

        run.pages_done = DocumentPage.objects.filter(document__object=run.object).count()
        run.pages_total = run.pages_done
        if run.started_at:
            minutes = max((timezone.now() - run.started_at).total_seconds() / 60, 0.001)
            run.pages_per_minute = Decimal(str(round(run.pages_done / minutes, 2)))
        run.review_cases_created = ReviewCase.objects.filter(
            object=run.object, created_at__gte=run.started_at or run.created_at
        ).count()
        run.status = (
            RunStatus.DONE
            if not ProcessingJob.objects.filter(run=run, status=JobStatus.FAILED).exists()
            else RunStatus.FAILED
        )
        run.finished_at = timezone.now()
        run.save()
    logger.info("Lauf %s Objekt %s abgeschlossen: %s", run.pk, run.object_id, run.status)
    from apps.requirements.tasks import trigger_evaluation

    trigger_evaluation(run.object_id, "run")  # H 3.5: Bewertung nach jedem Verarbeitungslauf
    schedule_runs()
    return True
