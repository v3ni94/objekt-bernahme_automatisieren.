"""Oberflaeche der Synchronisation: Webhook (sitzungslos, gemeinsames Geheimnis), Verwaltungsseite (Verbindung,
Eingang, Bestandslaeufe, Operationen), Bestandslauf mit Manifest und der zentrale Dokumenteneingang."""

from __future__ import annotations

import hmac
import json

from allauth.account.decorators import reauthentication_required
from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document
from apps.objects.models import ManagedObject
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import config, inbox, inventory, operations, services
from apps.sync.models import (
    Disposition,
    ExternalLink,
    InventoryRun,
    OperationStatus,
    SyncOperation,
    SyncSystem,
)
from apps.sync.paperless import setup as paperless_setup

WEBHOOK_HEADER = "HTTP_X_MHV_WEBHOOK_TOKEN"


# ---------------------------------------------------------------- Webhook
@csrf_exempt
@require_POST
def webhook_paperless(request):
    """Nimmt ein Ereignis aus einem Paperless-Workflow an (Dokument hinzugefuegt oder geaendert), merkt genau eine
    Operation vor und antwortet sofort. Der Body wird nie als Wahrheit genommen: die Verarbeitung liest das
    Dokument selbst ueber die API."""
    if not config.webhook_enabled():
        return HttpResponseNotFound()
    expected = config.webhook_token()
    provided = (
        request.META.get(WEBHOOK_HEADER)
        or (request.META.get("HTTP_AUTHORIZATION") or "").removeprefix("Bearer ").strip()
    )
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        record("auth.denied", entity_type="webhook", after={"path": request.path, "reason": "webhook token"})
        return JsonResponse({"accepted": False, "error": "nicht autorisiert"}, status=401)
    payload: dict = {}
    if request.content_type and "json" in request.content_type:
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("kein gültiges JSON")
    else:
        payload = request.POST.dict()
    raw = payload.get("doc_id") or payload.get("document_id") or payload.get("doc_pk") or payload.get("id")
    if raw is None and payload.get("doc_url"):
        raw = str(payload["doc_url"]).rstrip("/").rsplit("/", 1)[-1]
    try:
        doc_id = int(str(raw).strip())
    except (TypeError, ValueError):
        return HttpResponseBadRequest("doc_id fehlt")
    if not config.active():
        return JsonResponse({"accepted": True, "ignored": "paperless inaktiv"}, status=202)
    from apps.sync.flows.paperless_pull import enqueue_from_webhook

    op, created = enqueue_from_webhook(doc_id, event=str(payload.get("event") or "webhook")[:24])
    record(
        "sync.webhook",
        entity_type="sync_operation",
        entity_id=op.pk,
        after={"paperless_id": doc_id, "created": created},
    )
    return JsonResponse({"accepted": True, "operation": op.pk, "created": created}, status=202)


# ---------------------------------------------------------------- Verwaltung
def _cursor_view(system: str, name: str) -> dict:
    row = services.get_cursor(system, name)
    return {
        "value": row.value if row else None,
        "meta": row.meta if row else None,
        "updated_at": row.updated_at if row else None,
    }


@permission_required("sync.manage")
def sync_admin(request):
    state = paperless_setup.state_from_cursor()
    ops_summary = operations.summary()
    failed_ops = (
        SyncOperation.objects.filter(status__in=[OperationStatus.FAILED, OperationStatus.BLOCKED])
        .select_related("document")
        .order_by("-updated_at")[:50]
    )
    recent_ops = (
        SyncOperation.objects.exclude(status__in=[OperationStatus.FAILED, OperationStatus.BLOCKED])
        .select_related("document")
        .order_by("-updated_at")[:20]
    )
    inbox_obj = inbox.get_inbox_object()
    inbox_counts = {}
    if inbox_obj is not None:
        inbox_counts = {
            r["status"]: r["n"]
            for r in Document.objects.filter(object=inbox_obj, deleted_at__isnull=True)
            .values("status")
            .annotate(n=Count("id"))
        }
    link_counts = {
        f"{r['system']}:{r['state']}": r["n"]
        for r in ExternalLink.objects.values("system", "state").annotate(n=Count("id"))
    }
    return render(
        request,
        "sync/admin.html",
        {
            "NAV_ACTIVE": "sync",
            "enabled": config.enabled(),
            "configured": config.configured(),
            "mode": config.mode(),
            "base_url": config.base_url(),
            "pilot": sorted(config.pilot_object_numbers()),
            "field_names": config.field_names(),
            "state": state,
            "ops": ops_summary,
            "failed_ops": failed_ops,
            "recent_ops": recent_ops,
            "paperless_cursor": _cursor_view(SyncSystem.PAPERLESS, "modified_cursor"),
            "paperless_poll": _cursor_view(SyncSystem.PAPERLESS, "last_poll"),
            "drive_cursor": _cursor_view(SyncSystem.DRIVE, "page_token"),
            "drive_poll": _cursor_view(SyncSystem.DRIVE, "last_changes_poll"),
            "drive_changes_enabled": config.drive_changes_enabled(),
            "inbox_obj": inbox_obj,
            "inbox_folder_id": inbox.inbox_folder_id(),
            "inbox_counts": inbox_counts,
            "link_counts": link_counts,
            "runs": InventoryRun.objects.order_by("-created_at")[:10],
            "webhook_ready": bool(config.webhook_token()) and config.webhook_enabled(),
            "objects": ManagedObject.active.filter(is_system_inbox=False).order_by("object_number_numeric"),
        },
    )


@permission_required("sync.manage")
@require_POST
def sync_check(request):
    create = request.POST.get("einrichten") == "1"
    state = paperless_setup.check(user=request.user, create=create, request=request)
    (messages.success if state.ok else messages.warning)(
        request,
        f"Paperless: {state.message}"
        + (f" (Server {state.server_version}, API {state.api_version})" if state.server_version else ""),
    )
    if state.missing:
        messages.info(
            request,
            "Fehlt in Paperless: "
            + ", ".join(state.missing)
            + ". Mit „Kennzeichnung einrichten“ anlegen (nicht im Modus readonly).",
        )
    return redirect("sync_admin")


@permission_required("sync.manage")
@reauthentication_required
@require_POST
def sync_inbox_setup(request):
    drive = services.get_drive()
    try:
        obj = inbox.ensure_inbox_object(user=request.user)
        if drive is None:
            messages.warning(
                request,
                f"Eingangsobjekt {obj.object_number} vorhanden; Eingangsordner braucht die Google-Verbindung.",
            )
        else:
            folder_id = inbox.ensure_inbox_folder(drive, user=request.user)
            messages.success(
                request, f"Eingang eingerichtet: Objekt {obj.object_number}, Ordner {folder_id}."
            )
    except Exception as exc:  # Einrichtung meldet jeden Fehler, statt still zu scheitern
        messages.error(request, f"Einrichtung fehlgeschlagen: {exc}")
    return redirect("sync_admin")


@permission_required("sync.manage")
@require_POST
def sync_run_now(request):
    what = request.POST.get("was")
    from apps.sync import tasks

    if what == "paperless":
        result = (
            tasks.paperless_poll_task.apply_async(kwargs={"force": True}, queue="io")
            if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "celery"
            else tasks.paperless_poll_task(force=True)
        )
    elif what == "drive":
        result = (
            tasks.drive_changes_task.apply_async(kwargs={"force": True}, queue="io")
            if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "celery"
            else tasks.drive_changes_task(force=True)
        )
    elif what == "operationen":
        result = operations.dispatch_due()
    else:
        return HttpResponseBadRequest("unbekannt")
    messages.info(
        request, f"Abgleich angestoßen ({what}): {result if isinstance(result, dict | int) else 'eingereiht'}"
    )
    return redirect("sync_admin")


@permission_required("sync.manage")
@require_POST
def sync_operation_action(request, pk: int):
    op = get_object_or_404(SyncOperation, pk=pk)
    action = request.POST.get("action")
    if action == "retry":
        if operations.retry_now(op, user=request.user):
            messages.info(request, f"Operation {op.pk} erneut eingereiht.")
        else:
            messages.warning(request, f"Operation {op.pk} läuft gerade und wurde nicht erneut eingereiht.")
    elif action == "cancel":
        if operations.cancel(op, user=request.user, reason=request.POST.get("reason", "")):
            messages.info(request, f"Operation {op.pk} verworfen.")
        else:
            messages.warning(request, f"Operation {op.pk} ist erledigt oder läuft gerade; nichts verworfen.")
    else:
        return HttpResponseBadRequest("unbekannt")
    return redirect(request.POST.get("next") or "sync_admin")


# ---------------------------------------------------------------- Bestandslauf
@permission_required("sync.manage")
@require_POST
def inventory_start(request):
    kind = request.POST.get("art")
    dry_run = request.POST.get("echt") != "1"
    numbers = [
        n.strip() for n in (request.POST.get("objekte") or "").replace(";", ",").split(",") if n.strip()
    ]
    if not dry_run and not user_has_permission(request.user, "sync.manage"):
        return HttpResponseBadRequest("nur Admin")
    try:
        run = inventory.start(
            kind,
            dry_run=dry_run,
            scope={"object_numbers": numbers} if numbers else {},
            user=request.user,
            request=request,
        )
    except inventory.InventoryError as exc:
        messages.error(request, str(exc))
        return redirect("sync_admin")
    messages.success(
        request, f"Bestandslauf {run.pk} ({kind}, {'Trockenlauf' if dry_run else 'echter Lauf'}) gestartet."
    )
    return redirect("sync_inventory", pk=run.pk)


@permission_required("sync.manage")
def inventory_detail(request, pk: int):
    run = get_object_or_404(InventoryRun, pk=pk)
    inventory.refresh_dispositions(run)
    disp = request.GET.get("stand") or ""
    rows = inventory.manifest_rows(run, limit=1000)
    if disp:
        rows = [r for r in rows if r.disposition == disp]
    return render(
        request,
        "sync/inventory.html",
        {
            "NAV_ACTIVE": "sync",
            "run": run,
            "summary": inventory.summary(run),
            "rows": rows[:500],
            "dispositions": Disposition.choices,
            "filter": disp,
            "can_step": settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") != "celery",
        },
    )


@permission_required("sync.manage")
@require_POST
def inventory_action(request, pk: int):
    run = get_object_or_404(InventoryRun, pk=pk)
    action = request.POST.get("action")
    if action == "pause":
        inventory.pause(run, user=request.user, request=request)
    elif action == "resume":
        inventory.resume(run, user=request.user, request=request)
    elif action == "abort":
        inventory.abort(run, user=request.user, request=request)
    elif action == "step":
        result = inventory.step(run.pk)
        messages.info(request, f"Schritt ausgeführt: {result}")
    else:
        return HttpResponseBadRequest("unbekannt")
    return redirect("sync_inventory", pk=run.pk)


# ---------------------------------------------------------------- Dokumenteneingang
def document_sync_status(doc) -> dict:
    """Getrennte Statusangaben je Dokument (Auftrag Abschnitt 9)."""
    links = {link.system: link for link in doc.sync_links.all()}
    p = links.get(SyncSystem.PAPERLESS)
    d = links.get(SyncSystem.DRIVE)
    assigned = not getattr(doc.object, "is_system_inbox", False) and doc.status not in ("moved_out",)
    text_done = doc.status in ("ocr_done", "classified", "review", "filed")
    in_drive = bool(doc.drive_file_id)
    complete = (
        assigned
        and in_drive
        and (p is not None and p.state == "synced")
        and (d is None or d.state == "synced")
    )
    return {
        "eingegangen": True,
        "paperless": p is not None,
        "paperless_state": p.get_state_display() if p else None,
        "text": text_done,
        "zugeordnet": assigned,
        "drive": in_drive,
        "vollstaendig": complete,
    }


@permission_required("inbox.work")
def inbox_list(request):
    art = request.GET.get("art") or ""
    objekt = request.GET.get("objekt") or ""
    cases = ReviewCase.objects.filter(
        status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        case_type__in=[CaseType.OBJECT_ASSIGNMENT, CaseType.SYNC_CONFLICT],
    ).select_related("object", "document")
    inbox_obj = inbox.get_inbox_object()
    format_cases = ReviewCase.objects.filter(
        status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        case_type=CaseType.UNCLEAR,
        case_subtype__in=["unsupported_format", "file_too_large", "duplicate"],
    )
    if inbox_obj is not None:
        format_cases = format_cases.filter(Q(object=inbox_obj) | Q(document__source="paperless"))
    if art == "zuordnung":
        cases = cases.filter(case_type=CaseType.OBJECT_ASSIGNMENT)
        format_cases = format_cases.none()
    elif art == "konflikt":
        cases = cases.filter(case_type=CaseType.SYNC_CONFLICT)
        format_cases = format_cases.none()
    elif art == "format":
        cases = cases.none()
        format_cases = format_cases.filter(case_subtype__in=["unsupported_format", "file_too_large"])
    elif art == "dublette":
        cases = cases.none()
        format_cases = format_cases.filter(case_subtype="duplicate")
    if objekt:
        cases = cases.filter(object__object_number=objekt)
        format_cases = format_cases.filter(object__object_number=objekt)
    rows = list(cases.order_by("-priority", "created_at")[:300]) + list(
        format_cases.select_related("object", "document").order_by("created_at")[:200]
    )
    processing = []
    if inbox_obj is not None:
        processing = list(
            Document.objects.filter(object=inbox_obj, deleted_at__isnull=True)
            .exclude(status__in=["moved_out"])
            .prefetch_related("sync_links")
            .order_by("-first_seen_at")[:200]
        )
    auto = (
        ReviewCase.objects.filter(
            case_type=CaseType.OBJECT_ASSIGNMENT,
            case_subtype="auto",
            resolved_at__gte=timezone.now() - timezone.timedelta(days=30),
        )
        .select_related("object", "document")
        .order_by("-resolved_at")[:100]
    )
    failed_ops = (
        SyncOperation.objects.filter(status__in=[OperationStatus.FAILED, OperationStatus.BLOCKED])
        .select_related("document")
        .order_by("-updated_at")[:100]
        if art in ("", "fehler")
        else []
    )
    counters = {
        "zuordnung": ReviewCase.objects.filter(
            status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS], case_type=CaseType.OBJECT_ASSIGNMENT
        ).count(),
        "konflikt": ReviewCase.objects.filter(
            status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS], case_type=CaseType.SYNC_CONFLICT
        ).count(),
        "fehler": SyncOperation.objects.filter(
            status__in=[OperationStatus.FAILED, OperationStatus.BLOCKED]
        ).count(),
        "verarbeitung": len(processing),
    }
    return render(
        request,
        "sync/inbox.html",
        {
            "NAV_ACTIVE": "eingang",
            "art": art,
            "objekt": objekt,
            "rows": rows,
            "processing": [(d, document_sync_status(d)) for d in processing],
            "auto": auto,
            "failed_ops": failed_ops if art in ("", "fehler") else [],
            "counters": counters,
            "inbox_obj": inbox_obj,
            "objects": ManagedObject.active.filter(is_system_inbox=False).order_by("object_number_numeric"),
            "active": config.active(),
            "can_manage": user_has_permission(request.user, "sync.manage"),
        },
    )


@permission_required("inbox.work")
def objects_json(request):
    """Objektsuche fuer den Dokumenteneingang: Nummer, Bezeichnung, Strasse, Ort (ab zwei Zeichen)."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    qs = (
        ManagedObject.active.filter(is_system_inbox=False)
        .filter(
            Q(object_number__startswith=q)
            | Q(name__icontains=q)
            | Q(street__icontains=q)
            | Q(city__icontains=q)
        )
        .order_by("object_number_numeric")[:20]
    )
    return JsonResponse(
        {
            "results": [
                {
                    "id": o.pk,
                    "label": f"{o.object_number} {o.name or ''}".strip(),
                    "address": o.address,
                    "management_type": o.management_type,
                }
                for o in qs
            ]
        }
    )


def store_value(key: str, default=None):
    return store.get(key, default)
