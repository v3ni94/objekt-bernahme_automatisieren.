"""Verarbeitungslaeufe und Objektserialitaet (Fachentwurf D 7.1, Beschluss B-26, docs/architektur.md 6.10).

Hoechstens processing.max_parallel_objects Laeufe vom Typ full oder incremental gleichzeitig running, durchgesetzt
ueber Cache-Sperre und Datenbankpruefung; weitere Objekte warten pending mit sichtbarer Warteposition.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.config import store
from apps.documents.models import Document, DocumentPage
from apps.pipeline.jobs import enqueue, idempotency_key, models_q_not_in_flight, send
from apps.pipeline.models import JobStatus, JobType, ProcessingJob, ProcessingRun, RunStatus, RunType
from apps.review.models import CaseStatus, ReviewCase

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


def restart_status(doc: Document) -> str:
    """Wiedereinstieg eines Dokuments mit Status error: so spaet wie moeglich, damit vorhandene Ergebnisse
    (Hash, erkannte Seitentexte) nicht erneut berechnet werden. Seiten vorhanden -> ocr_done, Hash vorhanden ->
    hashed, sonst von vorn."""
    if doc.sha256 and DocumentPage.objects.filter(document=doc).exists():
        return "ocr_done"
    if doc.sha256:
        return "hashed"
    return "registered"


RESET_LIMIT = 3


def reset_failed_documents(run: ProcessingRun) -> int:
    """Dokumente im Status error wieder in die Kette nehmen (Abbruch durch Umgebungsfehler, etwa fehlende
    Datenbankrechte oder Drive nicht erreichbar). Der Fall job_failed im Review Center wird als erledigt
    geschlossen; scheitert der neue Versuch, entsteht mit dem neuen Job ein neuer Fall."""
    reset = 0
    for doc in Document.objects.filter(object=run.object, deleted_at__isnull=True, status="error"):
        # Begrenzung (14.09.2026): nach RESET_LIMIT automatischen Wiederaufnahmen bleibt das Dokument mit offenem
        # Fall job_failed stehen, statt bei jedem Eingang erneut Jobs zu erzeugen, die wieder scheitern
        frueher = ReviewCase.objects.filter(
            document=doc, case_subtype="job_failed", status=CaseStatus.RESOLVED
        ).values_list("resolution", flat=True)
        if (
            sum(1 for res in frueher if isinstance(res, dict) and res.get("action") == "reprocess")
            >= RESET_LIMIT
        ):
            logger.info("Dokument %s bleibt nach %s Wiederaufnahmen im Status error", doc.pk, RESET_LIMIT)
            continue
        doc.status = restart_status(doc)
        doc.error_message = None
        doc.save(update_fields=["status", "error_message", "updated_at"])
        ReviewCase.objects.filter(
            document=doc, case_subtype="job_failed", status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
        ).update(
            status=CaseStatus.RESOLVED,
            resolved_at=timezone.now(),
            resolution={"action": "reprocess", "run_id": run.pk, "restart_status": doc.status},
        )
        reset += 1
    return reset


def dispatch_run(run: ProcessingRun) -> int:
    """Reiht die offenen Jobs des Objekts ein; Dokumente ohne Job erhalten den passenden Startjob.
    Dokumente im Status error werden zuvor zurueckgesetzt (reset_failed_documents)."""
    obj = run.object
    count = 0
    reset_failed_documents(run)
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
    # nur Jobs ohne Nachricht unterwegs; ein fruehere Versand innerhalb des Fensters liegt noch in der Warteschlange
    for job in ProcessingJob.objects.filter(object=obj, status=JobStatus.PENDING, run=run).filter(
        models_q_not_in_flight(timezone.now())
    ):
        send(job)
    run.documents_total = Document.objects.filter(object=obj, deleted_at__isnull=True).count()
    run.save(update_fields=["documents_total", "updated_at"])
    return count


REPAIR_AFTER_MINUTES = 10


def repair_interrupted_runs(min_age_minutes: int = REPAIR_AFTER_MINUTES) -> list[int]:
    """Laufende Laeufe, deren Jobversand nie zu Ende kam (documents_total leer; dispatch_run setzt den Wert erst am
    Ende), erneut versorgen. 14.09.2026: drei Laeufe wurden beim Sammelstart als laufend markiert, dann brach der
    Versand am vollen Redis ab; die Jobs hingen ohne Lauf und ohne Nachricht. Nur Laeufe, die aelter als
    min_age_minutes sind, damit ein gerade laufender Versand nicht doppelt angestossen wird (dispatch_run ist
    idempotent, unterwegs befindliche Jobs werden nicht erneut versandt)."""
    grenze = timezone.now() - timedelta(minutes=min_age_minutes)
    repaired: list[int] = []
    for run in ProcessingRun.objects.filter(
        status=RunStatus.RUNNING,
        run_type__in=SERIAL_TYPES,
        documents_total__isnull=True,
        started_at__lt=grenze,
    ).select_related("object"):
        count = dispatch_run(run)
        logger.warning("Lauf %s (Objekt %s) nachversorgt: %s Jobs", run.pk, run.object.object_number, count)
        repaired.append(run.pk)
    return repaired


def maybe_finish_run(run: ProcessingRun) -> bool:
    """Lauf abschliessen, wenn kein Job des Objekts mehr offen ist; KPIs speichern (D 7.1)."""
    if run.status != RunStatus.RUNNING:
        return False
    open_jobs = ProcessingJob.objects.filter(
        object=run.object, status__in=[JobStatus.PENDING, JobStatus.RUNNING]
    )
    # Ablagejobs, die auf den Objektordner warten (Anschrift unvollstaendig, kein Ordner), halten den Lauf nicht
    # offen (14.09.2026): der Lauf gibt seinen Platz frei, die Jobs laufen nach Anlage des Ordners von selbst weiter
    waiting_for_folder = open_jobs.filter(
        status=JobStatus.PENDING,
        job_type=JobType.FILE_TO_DRIVE,
        next_attempt_at__gt=timezone.now(),
        last_error__contains="Objektordner unbekannt",
    )
    if open_jobs.exclude(pk__in=waiting_for_folder.values("pk")).exists():
        return False
    waiting_count = waiting_for_folder.count()
    if waiting_count:
        logger.warning(
            "Lauf %s Objekt %s: %s Ablagejobs warten auf den Objektordner, Lauf wird abgeschlossen",
            run.pk,
            run.object_id,
            waiting_count,
        )
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

    if getattr(run.object, "is_system_inbox", False):
        return  # Eingangsobjekt der Synchronisation: keine Vollstaendigkeitsbewertung, keine Listen
    trigger_evaluation(run.object_id, "run")  # H 3.5: Bewertung nach jedem Verarbeitungslauf
    if not run.dry_run:
        from apps.lists.services import request_generation

        request_generation(run.object_id, "run")  # CR 12a: Listen nach jedem Verarbeitungslauf (entprellt)
        from apps.drive.auto_cleanup import trigger_auto_cleanup

        # 13.09.2026: leere Altordner und Systemdateien nach der Verteilung in den Papierkorb (nie Struktur, nie Dokumente)
        trigger_auto_cleanup(run.object_id, trigger="run")
    schedule_runs()
    return True


WORK_STATUSES = ("registered", "hashed", "ocr_done", "error")


def objects_with_open_work():
    """Aktive Objekte (ohne Eingangsobjekt) mit Dokumenten, die noch durch die Kette muessen oder auf Fehler stehen,
    und ohne wartenden oder laufenden Objektlauf."""
    from apps.objects.models import ManagedObject

    busy = set(
        ProcessingRun.objects.filter(
            status__in=[RunStatus.PENDING, RunStatus.RUNNING], run_type__in=SERIAL_TYPES
        ).values_list("object_id", flat=True)
    )
    with_work = set(
        Document.objects.filter(deleted_at__isnull=True, status__in=WORK_STATUSES)
        .values_list("object_id", flat=True)
        .distinct()
    )
    return [
        obj
        for obj in ManagedObject.active.filter(is_system_inbox=False).order_by("object_number_numeric", "id")
        if obj.pk in with_work and obj.pk not in busy
    ]


def has_object_folder(obj) -> bool:
    """Objektordner in Drive bekannt (aktiver Knoten object_root aus dem Ordnerabgleich)."""
    from apps.drive.models import DriveNode as DriveNodeRow
    from apps.drive.models import NodeKind, NodeStatus

    return DriveNodeRow.objects.filter(
        object=obj, node_kind=NodeKind.OBJECT_ROOT, status=NodeStatus.ACTIVE
    ).exists()


def request_schedule() -> list[int] | None:
    """Wartende Laeufe starten: im Betrieb (JOB_DISPATCH celery) als Hintergrundtask pipeline.schedule_runs, weil
    dispatch_run fuer ein grosses Objekt Tausende Jobs anlegt und versendet und im Web-Request in den
    Gunicorn-Timeout lief (14.09.2026: HTTP 500 beim Sammelstart). Ohne Celery (Tests, lokaler Runner) sofort.
    Liefert die sofort gestarteten Laeufe oder None, wenn der Start im Hintergrund erfolgt."""
    if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "none":
        return schedule_runs()
    from celery import current_app

    current_app.send_task("pipeline.schedule_runs", queue="control")
    return None


def start_runs_for_all(*, user=None, run_type: str = RunType.INCREMENTAL, dry_run: bool = False) -> dict:
    """„Verarbeitung für alle Objekte starten“: je Objekt mit offener Arbeit ein Nachlauf (wartende Laeufe werden
    nach processing.max_parallel_objects nacheinander gestartet). Objekte mit bereits wartendem oder laufendem
    Lauf und Objekte ohne offene Dokumente werden ausgelassen. Der Start selbst laeuft im Hintergrund
    (request_schedule). Protokoll processing.start_all."""
    from apps.audit.services import record

    candidates = objects_with_open_work()
    summary = {
        "run_type": run_type,
        "dry_run": dry_run,
        "objects": [o.object_number for o in candidates],
        "started": [],
        "runs": [],
        "folders_requested": [],
    }
    without_folder = [o for o in candidates if not has_object_folder(o)]
    summary["without_folder"] = [o.object_number for o in without_folder]
    if dry_run:
        return summary
    if without_folder:
        # Objekte ohne Objektordner (etwa Altbestand mit unvollstaendiger Anschrift): Ordneranlage anstossen, der
        # Abgleich legt bei fehlender Anschrift einen vorlaeufigen Ordner nach drive.object_folder_fallback_pattern an
        from apps.drive.tasks import trigger_object_folders

        for obj in without_folder:
            status = trigger_object_folders(
                obj.pk, user_id=getattr(user, "pk", None), trigger="start_all", force=True
            )
            summary["folders_requested"].append({"object": obj.object_number, "status": status})
    for obj in candidates:
        run = ProcessingRun.objects.create(
            object=obj, run_type=run_type, status=RunStatus.PENDING, dry_run=False, triggered_by=user
        )
        summary["started"].append(obj.object_number)
        summary["runs"].append(run.pk)
    started_now = request_schedule() if summary["runs"] else []
    summary["running_now"] = started_now
    summary["background"] = started_now is None
    if summary["runs"]:
        record(
            "processing.start_all",
            entity_type="processing_run",
            actor=user,
            after={k: v for k, v in summary.items() if k != "objects"} | {"objects": len(candidates)},
        )
    return summary
