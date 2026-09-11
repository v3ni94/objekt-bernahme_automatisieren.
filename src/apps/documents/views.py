"""Dokumente je Objekt: Upload in die Verarbeitung, Laeufe mit Warteposition, Dokumentliste, Dokumentansicht mit
maskierten Seitentexten und Entitaeten, Seitenbilder nur ueber rechtegepruefte Route (B-15)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.config import store
from apps.documents import ingest
from apps.documents.forms import DocumentUploadForm
from apps.documents.models import Document, DocumentEntity, DocumentPage
from apps.objects.models import ManagedObject
from apps.pipeline.models import JobStatus, ProcessingJob, ProcessingRun, RunStatus, RunType
from apps.pipeline.previews import preview_path
from apps.pipeline.progress import refresh_progress
from apps.pipeline.runs import queue_position, start_run


def _may_view(user, doc: Document) -> bool:
    """Eigentuemer- und Mieterakten nur mit dem jeweiligen Recht; alles andere fuer angemeldete Nutzer."""
    if doc.category_id == "05":
        return user_has_permission(user, "owner_files.read")
    if doc.category_id == "04":
        return user_has_permission(user, "tenant_files.read")
    return True


@login_required
def document_list(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    status_filter = request.GET.get("status", "")
    docs = (
        Document.objects.filter(object=obj, deleted_at__isnull=True)
        .select_related("category", "duplicate_of")
        .order_by("-created_at")
    )
    if status_filter:
        docs = docs.filter(status=status_filter)
    counts = {
        r["status"]: r["c"]
        for r in Document.objects.filter(object=obj, deleted_at__isnull=True)
        .values("status")
        .annotate(c=Count("id"))
    }
    progress = refresh_progress(obj)
    runs = ProcessingRun.objects.filter(object=obj).order_by("-created_at")[:10]
    from apps.drive.object_root import open_structure_case
    from apps.objects.views import waiting_job_reasons

    structure_case = open_structure_case(obj)
    waiting_jobs = waiting_job_reasons(obj)
    open_jobs = (
        ProcessingJob.objects.filter(object=obj, status__in=[JobStatus.PENDING, JobStatus.RUNNING])
        .values("job_type", "status")
        .annotate(c=Count("id"))
        .order_by("job_type")
    )
    from apps.documents.models import DocumentClassification

    plans = {
        c.document_id: c
        for c in DocumentClassification.objects.filter(document__object=obj)
        .order_by("document_id", "-id")
        .select_related("category", "subfolder", "document_type")
    }
    for c in DocumentClassification.objects.filter(document__object=obj, is_final=True).select_related(
        "category", "subfolder", "document_type"
    ):
        plans[c.document_id] = c
    return render(
        request,
        "documents/list.html",
        {
            "object": obj,
            "plans": plans,
            "documents": docs[:500],
            "counts": counts,
            "status_filter": status_filter,
            "progress": progress,
            "runs": [(r, queue_position(r)) for r in runs],
            "open_jobs": open_jobs,
            "structure_case": structure_case,
            "waiting_jobs": waiting_jobs,
            "can_ingest": user_has_permission(request.user, "documents.ingest"),
        },
    )


@permission_required("documents.ingest")
def document_upload(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    form = DocumentUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        accepted, errors = 0, []
        run = None
        for f in form.cleaned_data["files"]:
            try:
                ingest.register_upload(
                    obj, filename=f.name, data=f.read(), user=request.user, request=request
                )
                accepted += 1
            except ingest.IngestError as exc:
                errors.append(f"{f.name}: {exc}")
        if accepted:
            run = ingest.ensure_run(obj, user=request.user)
            pos = queue_position(run)
            if run.status == RunStatus.RUNNING:
                messages.success(request, f"{accepted} Datei(en) angenommen, Lauf {run.pk} verarbeitet.")
            else:
                messages.success(
                    request, f"{accepted} Datei(en) angenommen, Lauf {run.pk} wartet an Position {pos}."
                )
        for err in errors:
            messages.error(request, err)
        if accepted or not errors:
            return redirect("document_list", pk=obj.pk)
    return render(request, "documents/upload.html", {"form": form, "object": obj})


@permission_required("documents.ingest")
@require_POST
def processing_start(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    run_type = request.POST.get("run_type", RunType.INCREMENTAL)
    if run_type not in (RunType.FULL, RunType.INCREMENTAL):
        run_type = RunType.INCREMENTAL
    dry_run = request.POST.get("dry_run") == "1"
    existing = ProcessingRun.objects.filter(
        object=obj,
        status__in=[RunStatus.PENDING, RunStatus.RUNNING],
        run_type__in=(RunType.FULL, RunType.INCREMENTAL),
    ).first()
    if existing is not None:
        messages.info(
            request, f"Für dieses Objekt ist bereits Lauf {existing.pk} {existing.get_status_display()}."
        )
        return redirect("document_list", pk=obj.pk)
    failed_before = Document.objects.filter(object=obj, deleted_at__isnull=True, status="error").count()
    run = start_run(obj, run_type=run_type, dry_run=dry_run, user=request.user)
    if failed_before:
        messages.info(
            request,
            f"{failed_before} zuvor fehlgeschlagene(s) Dokument(e) werden erneut verarbeitet; "
            "die zugehörigen Fälle im Review Center sind als erledigt geschlossen.",
        )
    record(
        "processing.start",
        entity_type="processing_run",
        entity_id=run.pk,
        object_id=obj.pk,
        request=request,
        after={"run_type": run_type, "status": run.status, "dry_run": dry_run},
    )
    pos = queue_position(run)
    if run.status == RunStatus.RUNNING:
        messages.success(request, f"Lauf {run.pk} gestartet.")
    else:
        messages.success(request, f"Lauf {run.pk} wartet an Position {pos} (Objektserialität).")
    return redirect("document_list", pk=obj.pk)


@login_required
def document_detail(request, pk: int):
    doc = get_object_or_404(
        Document.objects.filter(deleted_at__isnull=True).select_related(
            "object", "category", "subfolder", "document_type", "duplicate_of"
        ),
        pk=pk,
    )
    if not _may_view(request.user, doc):
        return HttpResponseForbidden("Kein Recht auf diese Akte")
    if doc.category_id in ("04", "05") and store.get("security.log_document_views", False):
        record(
            "document.view",
            entity_type="document",
            entity_id=doc.pk,
            object_id=doc.object_id,
            request=request,
        )
    pages = DocumentPage.objects.filter(document=doc).order_by("page_no")
    entities = (
        DocumentEntity.objects.filter(document=doc)
        .select_related("matched_owner", "matched_unit", "matched_tenant")
        .order_by("page_no", "char_from")
    )
    jobs = ProcessingJob.objects.filter(document=doc).order_by("id")
    previews = {p.page_no: preview_path(doc.pk, p.page_no).exists() for p in pages}
    from apps.documents.models import DocumentClassification, DocumentOwnerLink, DocumentTenantLink

    classifications = (
        DocumentClassification.objects.filter(document=doc)
        .select_related("category", "subfolder", "document_type")
        .order_by("-id")[:12]
    )
    owner_links = DocumentOwnerLink.objects.filter(document=doc, deleted_at__isnull=True).select_related(
        "owner", "unit", "owner_file", "subfolder"
    )
    tenant_links = DocumentTenantLink.objects.filter(document=doc, deleted_at__isnull=True).select_related(
        "tenant"
    )
    cases = doc.review_cases.all().order_by("-id")
    return render(
        request,
        "documents/detail.html",
        {
            "document": doc,
            "object": doc.object,
            "pages": pages,
            "entities": entities,
            "jobs": jobs,
            "previews": previews,
            "classifications": classifications,
            "owner_links": owner_links,
            "tenant_links": tenant_links,
            "cases": cases,
        },
    )


@login_required
def document_preview(request, pk: int, page_no: int):
    doc = get_object_or_404(Document.objects.filter(deleted_at__isnull=True), pk=pk)
    if not _may_view(request.user, doc):
        return HttpResponseForbidden("Kein Recht auf diese Akte")
    path = preview_path(doc.pk, page_no)
    if not path.exists():
        raise Http404("Seitenbild nicht vorhanden")
    return FileResponse(path.open("rb"), content_type="image/jpeg")
