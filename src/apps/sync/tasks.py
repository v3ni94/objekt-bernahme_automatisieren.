"""Celery-Tasks der Synchronisation (Queue io: einziger Worker mit Egress). Beat: sync.dispatch_due jede Minute,
sync.paperless_poll und sync.drive_changes alle fuenf Minuten (Intervall aus der Konfiguration wird im Task
geprueft), sync.derive_rules naechtlich. Der Job pipeline.assign_object laeuft im Pipeline-Rahmen."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.pipeline.jobs import SkipJob, job_task
from apps.pipeline.models import JobType
from apps.sync import config, operations

logger = logging.getLogger(__name__)


@shared_task(name="sync.run_operation", queue="io", acks_late=True, soft_time_limit=1500, time_limit=1620)
def run_operation_task(op_id: int) -> dict:
    return operations.run(op_id, worker="celery")


@shared_task(name="sync.dispatch_due", queue="io")
def dispatch_due_task() -> dict:
    return {"released": operations.release_stale(), "dispatched": operations.dispatch_due()}


@shared_task(name="sync.paperless_poll", queue="io", soft_time_limit=1500, time_limit=1620)
def paperless_poll_task(force: bool = False) -> dict:
    if not config.active():
        return {"skipped": "paperless inaktiv"}
    from apps.sync.flows import paperless_pull

    return paperless_pull.poll(force=force)


@shared_task(name="sync.drive_changes", queue="io", soft_time_limit=1500, time_limit=1620)
def drive_changes_task(force: bool = False) -> dict:
    if not config.drive_changes_enabled():
        return {"skipped": "drive changes inaktiv"}
    from apps.sync import drive_changes

    return drive_changes.poll(force=force)


@shared_task(name="sync.inventory_step", queue="io", soft_time_limit=1500, time_limit=1620)
def inventory_step_task(run_id: int) -> dict:
    from apps.sync import inventory

    result = inventory.step(run_id)
    if result.get("continue"):
        inventory_step_task.apply_async(args=[run_id], countdown=1, queue="io")
    return result


@shared_task(name="sync.derive_rules", queue="io")
def derive_rules_task() -> dict:
    from apps.sync.assignment import learning

    rules = learning.derive_rules(min_confirmations=int(config.store.get("sync.rule_min_confirmations", 2)))
    return {"rules_created": len(rules)}


@job_task(JobType.ASSIGN_OBJECT)
def assign_object(job) -> dict:
    """Objektzuordnung fuer Dokumente des Eingangsobjekts: Datei in den Drive-Eingang spiegeln, Kandidaten
    bewerten, bei eindeutiger Evidenz uebernehmen, sonst Fall Objektzuordnung."""
    from apps.sync.flows import assign

    doc = job.document
    if doc is None or not doc.sha256 or doc.status not in ("classified", "review", "ocr_done"):
        raise SkipJob("not_ready")
    if not getattr(job.object, "is_system_inbox", False):
        raise SkipJob("kein Eingangsobjekt")
    return assign.run_for_document(doc, job=job)
