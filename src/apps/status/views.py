from __future__ import annotations

import hmac

from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record

from . import checks


def processing_overview() -> dict:
    """Verarbeitung fuer die Statusseite: eine Zeile je Objekt aus object_progress (Auflage Ue18), offene Jobs je
    Queue und wartende Laeufe mit Position."""
    from apps.pipeline.jobs import QUEUE_FOR
    from apps.pipeline.models import JobStatus, ObjectProgress, ProcessingJob, ProcessingRun, RunStatus
    from apps.pipeline.runs import queue_position

    queues: dict[str, dict[str, int]] = {}
    for row in (
        ProcessingJob.objects.filter(status__in=[JobStatus.PENDING, JobStatus.RUNNING])
        .values("job_type", "status")
        .annotate(c=Count("id"))
    ):
        q = QUEUE_FOR.get(row["job_type"], "io")
        queues.setdefault(q, {"pending": 0, "running": 0})[row["status"]] += row["c"]
    runs = ProcessingRun.objects.filter(status__in=[RunStatus.PENDING, RunStatus.RUNNING]).select_related(
        "object"
    )
    from apps.classification.training import cold_start_status

    return {
        "classifier": cold_start_status(),
        "progress": ObjectProgress.objects.select_related("object", "last_run").order_by(
            "object__object_number"
        ),
        "queues": queues,
        "runs": [(r, queue_position(r)) for r in runs.order_by("status", "created_at")],
        "failed_jobs": ProcessingJob.objects.filter(status=JobStatus.FAILED).count(),
    }


@never_cache
def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def _token_ok(request) -> bool:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    token = settings.OBJEKTAKTE.get("READYZ_TOKEN") or ""
    if not header.startswith("Bearer ") or not token:
        return False
    return hmac.compare_digest(header[7:].strip(), token)


@never_cache
def readyz(request):
    user = getattr(request, "user", None)
    if not (_token_ok(request) or (user is not None and user.is_authenticated and user.is_admin)):
        return JsonResponse({"error": "nicht autorisiert"}, status=401)
    ok, report = checks.readiness()
    return JsonResponse({"ok": ok, **report}, status=200 if ok else 503)


@permission_required("status.read")
def status_page(request):
    ok, report = checks.readiness()
    return render(
        request,
        "status/page.html",
        {
            "ok": ok,
            "report": report,
            "heartbeats": checks.heartbeats(),
            "backup": checks.backup_status(),
            "can_operate": user_has_permission(request.user, "status.operate"),
            "image_tag": settings.OBJEKTAKTE.get("IMAGE_TAG", ""),
            "processing": processing_overview(),
        },
    )


@permission_required("status.operate")
@require_POST
def sweep_now(request):
    """Sweeper sofort ausfuehren (Runbook: Worker ohne Fortschritt)."""
    from apps.pipeline.jobs import sweep_stale_jobs
    from apps.pipeline.runs import schedule_runs

    result = sweep_stale_jobs()
    result["runs_started"] = len(schedule_runs())
    record("processing.sweep", entity_type="system", request=request, after=result)
    messages.success(
        request,
        f"Sweeper: {result['reset']} Jobs erneut eingereiht, {result['failed']} fehlgeschlagen, "
        f"{result['runs_started']} Läufe gestartet.",
    )
    return redirect("status_page")
