"""Job-Lebenszyklus (Fachentwurf D 7.2, E 10.2 bis 10.4, docs/architektur.md 5.4, 6.10).

Die Datenbank ist Quelle der Wahrheit: enqueue legt die Zeile in processing_jobs an (Idempotenzschluessel), die Queue
transportiert nur die Job-ID. reserve setzt pending nach running per UPDATE mit Statusbedingung; eine doppelt
zugestellte Nachricht findet den Job in running oder done und endet ohne Wirkung. Uebergaenge landen in
processing_job_events. Zeitlimits und Queues je Jobtyp nach B-13 und A-12.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from datetime import timedelta
from functools import wraps

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.config import store
from apps.pipeline.models import JobStatus, JobType, ProcessingJob, ProcessingJobEvent

logger = logging.getLogger(__name__)

QUEUE_FOR = {
    JobType.DISCOVER: "ocr",
    JobType.HASH: "ocr",
    JobType.ANALYZE_PAGES: "ocr",
    JobType.OCR_CHUNK: "ocr",
    JobType.MERGE_PAGES: "ocr",
    JobType.RENDER_PREVIEWS: "ocr",
    JobType.EXTRACT_ENTITIES: "classify",
    JobType.CLASSIFY: "classify",
    JobType.CLASSIFY_AI: "ai",
    JobType.DECIDE: "classify",
    JobType.FILE_TO_DRIVE: "io",
    JobType.ASSIGN_OBJECT: "io",
    JobType.LINK_SEGMENTS: "classify",
    JobType.GENERATE_LISTS: "lists",
    JobType.EVALUATE_COMPLETENESS: "classify",
    JobType.RECONCILE_DRIVE: "io",
    JobType.TRAIN_CLASSIFIER: "classify",
    JobType.SWEEP: "io",
}
# weiche Zeitlimits je Jobtyp in Sekunden (ANNAHME A-12); OCR-Bloecke am laengsten
SOFT_TIME_LIMIT = {
    JobType.OCR_CHUNK: 1800,
    JobType.HASH: 900,
    JobType.RENDER_PREVIEWS: 900,
    JobType.FILE_TO_DRIVE: 900,
}
DEFAULT_SOFT_LIMIT = 600
TERMINAL_STATUSES = {JobStatus.DONE, JobStatus.FAILED, JobStatus.SKIPPED}


class RetryableError(Exception):
    """Vorübergehender Fehler: Job geht mit Backoff zurück nach pending."""


class SkipJob(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DeferJob(Exception):
    """Voraussetzung fehlt (etwa keine Google-Verbindung): Job wartet, ohne einen Versuch zu verbrauchen.
    Anders als RetryableError endet das nie in failed; der Job bleibt pending, bis die Voraussetzung da ist."""

    def __init__(self, reason: str, seconds: int = 600):
        super().__init__(reason)
        self.reason = reason
        self.seconds = seconds


def worker_id() -> str:
    return f"{os.environ.get('SERVICE_NAME', 'worker')}@{socket.gethostname()}:{os.getpid()}"


def _event(job: ProcessingJob, from_status: str | None, to_status: str, message: str | None = None) -> None:
    ProcessingJobEvent.objects.create(
        job=job,
        from_status=from_status,
        to_status=to_status,
        worker_id=worker_id(),
        attempt_no=job.attempt_count,
        message=(message or "")[:500] or None,
    )


def idempotency_key(job_type: str, obj_id: int, *parts) -> str:
    return ":".join([job_type, str(obj_id), *[str(p) for p in parts]])[:160]


def enqueue(
    job_type: str,
    obj,
    *,
    key: str,
    document=None,
    run=None,
    payload: dict | None = None,
    priority: int = 100,
    dispatch: bool = True,
    repeat: bool = True,
) -> tuple[ProcessingJob, bool]:
    """Legt den Job idempotent an und reiht ihn ein; ein zweiter Aufruf mit gleichem Schluessel erzeugt keine Zeile,
    solange der Job offen ist. Ist der Job bereits abgeschlossen (done, failed, skipped) und der Vorgaenger laeuft
    legitim erneut (z. B. Arbeitsverzeichnis geraeumt), entsteht bei repeat ein neuer Job mit Suffix #n."""
    max_attempts = int(store.get("jobs.max_attempts_default", 3))
    existing = ProcessingJob.objects.filter(idempotency_key=key).only("status").first()
    if existing is not None and existing.status in TERMINAL_STATUSES and repeat:
        # Ein offener Wiederholungsjob (#n, pending oder running) gilt als derselbe Job: kein weiterer. 14.09.2026:
        # zuvor legte jeder Aufruf (dispatch_run bei jedem Dokumenteneingang) einen weiteren #n an, solange der
        # vorige in der Warteschlange wartete; je Eingang entstanden so 134 neue Jobs fuer dieselben Dokumente.
        open_repeat = (
            ProcessingJob.objects.filter(idempotency_key__startswith=f"{key}#")
            .exclude(status__in=TERMINAL_STATUSES)
            .order_by("-id")
            .first()
        )
        if open_repeat is not None:
            if run is not None and open_repeat.run_id is None:
                open_repeat.run = run
                open_repeat.save(update_fields=["run", "updated_at"])
            if dispatch and open_repeat.status == JobStatus.PENDING and open_repeat.dispatched_at is None:
                send(open_repeat)
            return open_repeat, False
        n = ProcessingJob.objects.filter(idempotency_key__startswith=f"{key}#").count() + 2
        key = f"{key}#{n}"[:160]
    job, created = ProcessingJob.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "object": obj,
            "document": document,
            "run": run,
            "job_type": job_type,
            "status": JobStatus.PENDING,
            "priority": priority,
            "payload": payload or {},
            "max_attempts": max_attempts,
        },
    )
    if created:
        _event(job, None, JobStatus.PENDING, "angelegt")
    elif run is not None and job.run_id is None:
        job.run = run
        job.save(update_fields=["run", "updated_at"])
    if dispatch and job.status == JobStatus.PENDING:
        send(job)
    return job, created


def send(job: ProcessingJob, countdown: int | None = None) -> None:
    """Job-ID an die Queue geben. Versandmodus JOB_DISPATCH: celery (Betrieb) oder none (Tests, lokaler Runner).
    Der Versandzeitpunkt (dispatched_at) wird immer gemerkt: Grundlage fuer den Nachversand verlorener Nachrichten
    (redispatch_lost_jobs) und dafuer, dass dispatch_run unterwegs befindliche Jobs nicht doppelt versendet."""
    from celery import current_app
    from django.conf import settings

    ProcessingJob.objects.filter(pk=job.pk).update(dispatched_at=timezone.now())
    if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "none":
        return
    task_name = f"pipeline.{job.job_type}"
    kwargs = {"queue": QUEUE_FOR.get(job.job_type, "io")}
    if countdown:
        kwargs["countdown"] = countdown
    current_app.send_task(task_name, args=[job.pk], **kwargs)


def reserve(job_id: int) -> ProcessingJob | None:
    now = timezone.now()
    updated = (
        ProcessingJob.objects.filter(pk=job_id, status=JobStatus.PENDING)
        .filter(models_q_next_attempt(now))
        .update(
            status=JobStatus.RUNNING,
            locked_by=worker_id(),
            locked_at=now,
            heartbeat_at=now,
            started_at=now,
            attempt_count=F("attempt_count") + 1,
        )
    )
    if updated != 1:
        return None
    job = ProcessingJob.objects.select_related("object", "document", "run").get(pk=job_id)
    _event(job, JobStatus.PENDING, JobStatus.RUNNING)
    return job


def models_q_next_attempt(now):
    from django.db.models import Q

    return Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)


REDISPATCH_HOURS = 48


def models_q_not_in_flight(now, hours: int = REDISPATCH_HOURS):
    """Jobs ohne Nachricht unterwegs: nie versandt oder Versand aelter als `hours` Stunden (Warteschlange geleert)."""
    from django.db.models import Q

    return Q(dispatched_at__isnull=True) | Q(dispatched_at__lt=now - timedelta(hours=hours))


def redispatch_lost_jobs(limit: int = 500, hours: int = REDISPATCH_HOURS) -> int:
    """Wartende, faellige Jobs laufender Laeufe erneut versenden, wenn keine Nachricht unterwegs ist: nie versandt
    (etwa waehrend eines laufenden Laufs von der Altbestand-Aufarbeitung angelegt) oder seit `hours` Stunden nicht
    angekommen (Redis geleert). Hoechstens `limit` je Aufruf (Beat-Sweep jede Minute), damit eine tiefe, aber
    intakte Warteschlange nicht durch Doppelte waechst. Doppelte sind unschaedlich: reserve nimmt einen Job nur
    einmal."""
    from apps.pipeline.models import RunStatus

    now = timezone.now()
    jobs = (
        ProcessingJob.objects.filter(status=JobStatus.PENDING, run__status=RunStatus.RUNNING)
        .filter(models_q_next_attempt(now))
        .filter(models_q_not_in_flight(now, hours))
        .order_by("id")[:limit]
    )
    sent = 0
    for job in jobs:
        send(job)
        sent += 1
    return sent


def heartbeat(job: ProcessingJob, pages: int | None = None) -> None:
    fields = {"heartbeat_at": timezone.now()}
    if pages is not None:
        fields["pages_processed"] = pages
    ProcessingJob.objects.filter(pk=job.pk).update(**fields)


def done(job: ProcessingJob, result: dict | None = None, *, pages: int | None = None) -> None:
    now = timezone.now()
    job.status = JobStatus.DONE
    job.finished_at = now
    job.duration_ms = int((now - job.started_at).total_seconds() * 1000) if job.started_at else None
    job.result = result or {}
    if pages is not None:
        job.pages_processed = pages
    job.locked_by = None
    job.save(
        update_fields=[
            "status",
            "finished_at",
            "duration_ms",
            "result",
            "pages_processed",
            "locked_by",
            "updated_at",
        ]
    )
    _event(job, JobStatus.RUNNING, JobStatus.DONE)


def skip(job: ProcessingJob, reason: str) -> None:
    job.status = JobStatus.SKIPPED
    job.skip_reason = reason[:64]
    job.finished_at = timezone.now()
    job.locked_by = None
    job.save(update_fields=["status", "skip_reason", "finished_at", "locked_by", "updated_at"])
    _event(job, JobStatus.RUNNING, JobStatus.SKIPPED, reason)


def defer(job: ProcessingJob, exc: DeferJob) -> None:
    """Zurueck nach pending mit Wartezeit; der Reservierungszaehler wird zurueckgenommen, damit die Wartezeit auf
    eine fehlende Voraussetzung nicht als Fehlversuch zaehlt (max_attempts bleibt fuer echte Fehler)."""
    job.status = JobStatus.PENDING
    job.locked_by = None
    job.next_attempt_at = timezone.now() + timedelta(seconds=exc.seconds)
    job.attempt_count = max(0, job.attempt_count - 1)
    job.last_error = f"wartet: {exc.reason}"[:2000]
    job.save(
        update_fields=["status", "locked_by", "next_attempt_at", "attempt_count", "last_error", "updated_at"]
    )
    _event(job, JobStatus.RUNNING, JobStatus.PENDING, f"wartet {exc.seconds} s: {exc.reason}")
    send(job, countdown=exc.seconds)


def fail(job: ProcessingJob, exc: BaseException, *, retryable: bool) -> None:
    job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
    job.error_class = type(exc).__name__[:80]
    job.locked_by = None
    if retryable and job.attempt_count < job.max_attempts:
        delay = min(600, 30 * (2 ** (job.attempt_count - 1)))
        job.status = JobStatus.PENDING
        job.next_attempt_at = timezone.now() + timedelta(seconds=delay)
        job.save(
            update_fields=[
                "status",
                "next_attempt_at",
                "last_error",
                "error_class",
                "locked_by",
                "updated_at",
            ]
        )
        _event(job, JobStatus.RUNNING, JobStatus.PENDING, f"erneut in {delay} s: {job.last_error}")
        send(job, countdown=delay)
        return
    job.status = JobStatus.FAILED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "last_error", "error_class", "locked_by", "updated_at"])
    _event(job, JobStatus.RUNNING, JobStatus.FAILED, job.last_error)
    _mark_document_error(job)


def _mark_document_error(job: ProcessingJob) -> None:
    if job.document_id:
        from apps.documents.models import Document

        Document.objects.filter(pk=job.document_id).exclude(
            status__in=["filed", "duplicate", "moved_out"]
        ).update(status="error", error_message=job.last_error)
    from apps.review.models import CaseStatus, CaseType, ReviewCase

    key = f"job_failed:{job.pk}"
    if not ReviewCase.objects.filter(
        batch_key=key, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists():
        ReviewCase.objects.create(
            object=job.object,
            case_type=CaseType.UNCLEAR,
            case_subtype="job_failed",
            document_id=job.document_id,
            batch_key=key,
            priority=70,
            context={
                "job_id": job.pk,
                "job_type": job.job_type,
                "error": job.last_error,
                "attempts": job.attempt_count,
            },
        )


def job_task(job_type: str) -> Callable:
    """Dekorator: Celery-Task pipeline.<job_type>, der die Zeile reserviert, die Arbeit ausfuehrt und den Folgezustand schreibt."""

    def decorator(fn: Callable[[ProcessingJob], dict | None]):
        soft = SOFT_TIME_LIMIT.get(job_type, DEFAULT_SOFT_LIMIT)

        @shared_task(
            name=f"pipeline.{job_type}",
            queue=QUEUE_FOR.get(job_type, "io"),
            soft_time_limit=soft,
            time_limit=soft + 120,
            acks_late=True,
        )
        @wraps(fn)
        def wrapper(job_id: int):
            job = reserve(job_id)
            if job is None:
                logger.info("Job %s nicht reservierbar (bereits laufend oder erledigt)", job_id)
                return {"job_id": job_id, "reserved": False}
            try:
                result = fn(job)
            except SkipJob as exc:
                skip(job, exc.reason)
                return {"job_id": job_id, "skipped": exc.reason}
            except DeferJob as exc:
                logger.info("Job %s wartet auf Voraussetzung: %s", job_id, exc.reason)
                defer(job, exc)
                return {"job_id": job_id, "deferred": exc.reason}
            except RetryableError as exc:
                logger.warning("Job %s vorübergehend fehlgeschlagen: %s", job_id, exc)
                fail(job, exc, retryable=True)
                return {"job_id": job_id, "retry": True}
            except Exception as exc:
                logger.exception("Job %s fehlgeschlagen", job_id)
                fail(job, exc, retryable=False)
                return {"job_id": job_id, "failed": True}
            done(job, result or {})
            _after_done(job)
            return {"job_id": job_id, "done": True}

        wrapper.job_type = job_type
        return wrapper

    return decorator


def _after_done(job: ProcessingJob) -> None:
    from apps.pipeline.progress import refresh_progress
    from apps.pipeline.runs import maybe_finish_run

    try:
        refresh_progress(job.object)
        if job.run_id:
            maybe_finish_run(job.run)
    except Exception:  # Zaehler und Laufabschluss duerfen den Job nicht scheitern lassen
        logger.exception("Nachlauf zu Job %s fehlgeschlagen", job.pk)


def stale_minutes(job_type: str) -> int:
    cfg = store.get("jobs.stale_minutes", {}) or {}
    return int(cfg.get(job_type, cfg.get("default", 15)))


def dedupe_repeat_jobs(*, dry_run: bool = True) -> dict:
    """Ueberzaehlige wartende Wiederholungsjobs (#n) zum selben Basisschluessel auf skipped setzen; der aelteste
    offene Job je Schluessel bleibt. Die Nachrichten der uebrigen liegen weiter in der Warteschlange und enden
    dort ohne Arbeit (reserve nimmt nur wartende Jobs). Protokoll processing.jobs_dedupe."""
    from apps.audit.services import record

    now = timezone.now()
    kept: dict[str, int] = {}
    extra: list[tuple[int, str]] = []
    rows = (
        ProcessingJob.objects.filter(status=JobStatus.PENDING, idempotency_key__contains="#")
        .order_by("id")
        .values_list("id", "idempotency_key", "job_type")
    )
    for pk, key, job_type in rows:
        base = key.split("#", 1)[0]
        if base in kept:
            extra.append((pk, job_type))
        else:
            kept[base] = pk
    by_type: dict[str, int] = {}
    for _, job_type in extra:
        by_type[job_type] = by_type.get(job_type, 0) + 1
    result = {"dry_run": dry_run, "extra": len(extra), "kept": len(kept), "by_type": by_type}
    if dry_run or not extra:
        return result
    ids = [pk for pk, _ in extra]
    for start in range(0, len(ids), 1000):
        chunk = ids[start : start + 1000]
        updated = ProcessingJob.objects.filter(pk__in=chunk, status=JobStatus.PENDING).update(
            status=JobStatus.SKIPPED,
            skip_reason="doppelter Wiederholungsjob",
            finished_at=now,
            updated_at=now,
        )
        ProcessingJobEvent.objects.bulk_create(
            [
                ProcessingJobEvent(
                    job_id=pk,
                    from_status=JobStatus.PENDING,
                    to_status=JobStatus.SKIPPED,
                    worker_id=worker_id(),
                    message="doppelter Wiederholungsjob bereinigt",
                )
                for pk in chunk
            ]
        )
        result["updated"] = result.get("updated", 0) + updated
    record("processing.jobs_dedupe", entity_type="processing_job", after=result)
    return result


def sweep_stale_jobs() -> dict:
    """Jobs in running ohne Heartbeat je Jobtyp zuruecksetzen; nach max_attempts failed mit Review-Fall (E 10.4)."""
    now = timezone.now()
    reset, failed = 0, 0
    for job in ProcessingJob.objects.filter(status=JobStatus.RUNNING).select_related("object"):
        limit = now - timedelta(minutes=stale_minutes(job.job_type))
        if (job.heartbeat_at or job.locked_at or job.updated_at) > limit:
            continue
        with transaction.atomic():
            job = ProcessingJob.objects.select_for_update().get(pk=job.pk)
            if job.status != JobStatus.RUNNING:
                continue
            if job.attempt_count >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.last_error = f"Heartbeat veraltet nach {job.attempt_count} Versuchen"
                job.finished_at = now
                job.locked_by = None
                job.save(update_fields=["status", "last_error", "finished_at", "locked_by", "updated_at"])
                _event(job, JobStatus.RUNNING, JobStatus.FAILED, job.last_error)
                _mark_document_error(job)
                failed += 1
            else:
                job.status = JobStatus.PENDING
                job.locked_by = None
                job.next_attempt_at = None
                job.save(update_fields=["status", "locked_by", "next_attempt_at", "updated_at"])
                _event(job, JobStatus.RUNNING, JobStatus.PENDING, "Heartbeat veraltet, erneut eingereiht")
                send(job)
                reset += 1
    return {"reset": reset, "failed": failed}
