"""Lokaler Job-Runner ohne Celery-Worker: fuehrt wartende Jobs in der Reihenfolge Prioritaet, Anlage aus.

Verwendung in Tests (JOB_DISPATCH none) und im Messlauf perf_run --local. Jeder Aufruf durchlaeuft dieselben
Tasks wie ein Worker (Reservierung, Heartbeat, Folgezustand); nur der Transport ueber die Queue entfaellt.
"""

from __future__ import annotations

from django.utils import timezone

from apps.pipeline.models import JobStatus, ProcessingJob


def _task_for(job_type: str):
    import apps.pipeline.tasks  # noqa: F401  registriert die Tasks
    from objektakte.celery import app

    return app.tasks[f"pipeline.{job_type}"]


def run_pending_jobs(obj=None, *, max_jobs: int = 10_000, job_types: list[str] | None = None) -> int:
    """Fuehrt wartende Jobs aus, bis keiner mehr faellig ist; Rueckgabe Anzahl ausgefuehrter Jobs."""
    ran = 0
    while ran < max_jobs:
        now = timezone.now()
        qs = ProcessingJob.objects.filter(status=JobStatus.PENDING).filter(models_next_attempt_due(now))
        if obj is not None:
            qs = qs.filter(object=obj)
        if job_types:
            qs = qs.filter(job_type__in=job_types)
        job = qs.order_by("priority", "id").first()
        if job is None:
            break
        _task_for(job.job_type)(job.pk)
        ran += 1
    return ran


def models_next_attempt_due(now):
    from django.db.models import Q

    return Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)


def run_one(job_id: int) -> dict:
    job = ProcessingJob.objects.get(pk=job_id)
    return _task_for(job.job_type)(job.pk)
