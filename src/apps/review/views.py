"""Review Center (H 2.2 bis 2.6, docs/architektur.md 8): Arbeitsliste, Detailansicht in drei Spalten, Aktionen,
Eigentuemer- und Mietersuche, gespeicherte Sichten, Serienmodus. Kein Drive-Aufruf in einer Anfrage."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.classification.context import build_context
from apps.documents.models import (
    Document,
    DocumentCategory,
    DocumentClassification,
    DocumentEntity,
    DocumentOwnerLink,
    DocumentPage,
    DocumentSubfolder,
    DocumentType,
)
from apps.imports.models import ImportKind
from apps.objects.models import ManagedObject, Unit
from apps.pipeline.previews import preview_path
from apps.review import services
from apps.review.models import CaseStatus, CaseType, ReviewCase, ReviewSavedFilter


def _can_view_doc(user, doc: Document | None) -> bool:
    if doc is None:
        return True
    if doc.category_id == "05":
        return user_has_permission(user, "owner_files.read")
    if doc.category_id == "04":
        return user_has_permission(user, "tenant_files.read")
    return True


@login_required
def case_list(request):
    filters = services.ListFilters.from_params(request.GET, request.user)
    cases, next_cursor = services.page(filters, cursor=request.GET.get("cursor"), user=request.user)
    return render(
        request,
        "review/list.html",
        {
            "cases": cases,
            "groups": services.group_sizes(cases),
            "next_cursor": next_cursor,
            "filters": filters,
            "counters": services.counters(filters, request.user),
            "objects": ManagedObject.active.order_by("object_number_numeric"),
            "case_types": CaseType.choices,
            "statuses": CaseStatus.choices,
            "users": User.objects.filter(deleted_at__isnull=True).order_by("display_name"),
            "saved": ReviewSavedFilter.objects.filter(user=request.user).order_by("sort_order", "name"),
            "shared": ReviewSavedFilter.objects.filter(is_shared=True)
            .exclude(user=request.user)
            .order_by("name"),
            "can_decide": user_has_permission(request.user, "review.decide"),
            "query": request.GET.urlencode(),
            "serial": request.GET.get("serie") == "1",
        },
    )


def _neighbors(case: ReviewCase, params, user):
    """Vorheriger und naechster Fall im aktuellen Filter (Fallnavigation, Serienmodus)."""
    filters = services.ListFilters.from_params(params, user)
    rows = list(
        services.case_queryset(filters, user)
        .order_by("priority", "created_at", "id")
        .values_list("id", flat=True)[:2000]
    )
    if case.pk not in rows:
        return None, None, None
    idx = rows.index(case.pk)
    return (
        (rows[idx - 1] if idx > 0 else None),
        (rows[idx + 1] if idx + 1 < len(rows) else None),
        (idx + 1, len(rows)),
    )


@login_required
def case_detail(request, pk: int):
    case = get_object_or_404(
        ReviewCase.objects.select_related("object", "document", "misc_subfolder", "assigned_to"), pk=pk
    )
    doc = case.document
    if not _can_view_doc(request.user, doc):
        return HttpResponseForbidden("Kein Recht auf diese Akte")
    target = services.proposal_target(case)
    prev_id, next_id, position = _neighbors(case, request.GET, request.user)
    pages = list(DocumentPage.objects.filter(document=doc).order_by("page_no")) if doc else []
    ctx_entities = (
        list(
            DocumentEntity.objects.filter(document=doc)
            .select_related("matched_owner", "matched_unit", "matched_tenant")
            .order_by("page_no", "char_from")
        )
        if doc
        else []
    )
    classifications = (
        list(
            DocumentClassification.objects.filter(document=doc)
            .select_related("category", "subfolder", "document_type")
            .order_by("-id")[:10]
        )
        if doc
        else []
    )
    obj = case.object or (doc.object if doc else None)
    period_suggestion = None
    if doc:
        try:
            period_suggestion = build_context(doc, entities=ctx_entities).period
        except Exception:  # Kontext ist Hilfe, kein Muss
            period_suggestion = None
    lease_facts = (case.context or {}).get("lease_facts") if isinstance(case.context, dict) else None
    lease_tenants = list((lease_facts or {}).get("tenants") or [])
    lease_unit_id = None
    if lease_facts and obj is not None:
        lease_unit_id = lease_facts.get("unit_id")
        if not lease_unit_id and lease_facts.get("unit_hint"):
            from apps.objects.units import normalize_label

            lease_unit_id = (
                Unit.active.filter(
                    object=obj, unit_label_normalized=normalize_label(lease_facts["unit_hint"])
                )
                .values_list("pk", flat=True)
                .first()
            )
    return render(
        request,
        "review/detail.html",
        {
            "case": case,
            "document": doc,
            "object": obj,
            "target": target,
            "lease_facts": lease_facts,
            "lease_unit_id": lease_unit_id,
            "lease_tenant": lease_tenants[0] if lease_tenants else None,
            "lease_co_tenant": lease_tenants[1] if len(lease_tenants) > 1 else None,
            "pages": pages,
            "previews": {p.page_no: preview_path(doc.pk, p.page_no).exists() for p in pages} if doc else {},
            "entities": ctx_entities[:200],
            "classifications": classifications,
            "links": DocumentOwnerLink.objects.filter(document=doc, deleted_at__isnull=True).select_related(
                "owner", "unit", "owner_file"
            )
            if doc
            else [],
            "history": services.case_history(case),
            "categories": DocumentCategory.objects.filter(is_active=True).order_by("code"),
            "subfolders": DocumentSubfolder.objects.select_related("category").order_by(
                "category_id", "code"
            ),
            "document_types": DocumentType.objects.filter(is_active=True)
            .select_related("subfolder")
            .order_by("category_id", "subfolder__code", "name"),
            "units": services.units_of(obj) if obj else [],
            "users": User.objects.filter(deleted_at__isnull=True).order_by("display_name"),
            "objects": ManagedObject.active.exclude(pk=obj.pk if obj else None).order_by(
                "object_number_numeric"
            ),
            "prev_id": prev_id,
            "next_id": next_id,
            "position": position,
            "query": request.GET.urlencode(),
            "serial": request.GET.get("serie") == "1",
            "period_suggestion": period_suggestion,
            "can_decide": user_has_permission(request.user, "review.decide"),
            "can_dismiss_object": user_has_permission(request.user, "review.dismiss_object_case"),
            "candidates": case.candidates or [],
            "import_kinds": ImportKind.choices,
            "conflict_choices": _conflict_choices(case),
            "sync_links": list(doc.sync_links.all()) if doc else [],
            "documents_same_object": Document.objects.filter(object=obj, deleted_at__isnull=True)
            .exclude(pk=doc.pk if doc else None)
            .order_by("current_name")[:300]
            if obj
            else [],
        },
    )


def _conflict_choices(case: ReviewCase) -> list[tuple[str, str]]:
    if case.case_type != CaseType.SYNC_CONFLICT:
        return []
    from apps.sync.flows import conflicts

    return conflicts.choices_for(case)


def _next_url(request, case: ReviewCase, next_id):
    query = request.POST.get("query") or ""
    serial = request.POST.get("serie") == "1"
    if serial and next_id:
        return f"/review/{next_id}/?{query}"
    return f"/review/?{query}" if serial else f"/review/{case.pk}/?{query}"


@permission_required("review.decide")
@require_POST
def case_action(request, pk: int):
    case = get_object_or_404(ReviewCase.objects.select_related("object", "document"), pk=pk)
    action = request.POST.get("action")
    from django.http import QueryDict

    _prev, next_id, _pos = _neighbors(case, QueryDict(request.POST.get("query", "")), request.user)
    try:
        if action in ("confirm", "correct"):
            target = services.Target.from_post(request.POST)
            decision = services.apply_decision(case, request.user, target, request=request)
            akte = (decision.after_state.get("physical") or {}).get("owner_file_id")
            warnings = decision.after_state.get("warnings") or []
            messages.success(
                request,
                f"Entscheidung gespeichert: {target.category}/{target.subfolder or ''} {target.document_type or ''}{' Akte ' + str(akte) if akte else ''}. Verschiebung ausstehend.",
            )
            for w in warnings:
                messages.warning(request, w)
        elif action == "defer":
            days = int(request.POST.get("days") or services.DEFAULT_SNOOZE_DAYS)
            services.defer(case, request.user, days=days, reason=request.POST.get("reason"), request=request)
            messages.info(request, f"Fall zurückgestellt um {days} Tage.")
        elif action == "dismiss":
            if case.case_type in services.OBJECT_CASE_TYPES and not user_has_permission(
                request.user, "review.dismiss_object_case"
            ):
                return HttpResponseForbidden(
                    "Verwerfen von Fällen zur Objektzuordnung nur durch Admin (B-17)"
                )
            services.dismiss(case, request.user, reason=request.POST.get("reason", ""), request=request)
            messages.info(request, "Fall verworfen.")
        elif action == "assign":
            assignee = (
                User.objects.filter(pk=request.POST.get("assignee")).first()
                if request.POST.get("assignee")
                else request.user
            )
            services.assign(case, request.user, assignee, request=request)
            messages.info(request, f"Fall zugewiesen an {assignee.display_name}.")
        elif action == "reopen":
            services.reopen(case, request.user, reason=request.POST.get("reason"), request=request)
            messages.info(request, "Fall wiedereröffnet.")
        elif action == "choose_folder":
            result = services.choose_object_folder(
                case, request.user, request.POST.get("folder_id", ""), request=request
            )
            messages.success(
                request,
                f"„{result['folder'].name}“ ist jetzt der Objektordner. "
                + (
                    "Der Ordnerabgleich legt die Struktur an, wartende Ablagen laufen weiter."
                    if result["reconcile"] == "queued"
                    else f"Ordnerabgleich konnte nicht angestoßen werden ({result['reconcile']}); in der Objektansicht ausführen."
                ),
            )
        elif action == "merge":
            original = get_object_or_404(Document, pk=request.POST.get("original_id"))
            services.merge_duplicate(case, request.user, original, request=request)
            messages.info(
                request,
                f"Als Dublette von Dokument {original.pk} zusammengeführt; Verschiebung nach 03_Dubletten ausstehend.",
            )
        elif action == "split":
            segments = json.loads(request.POST.get("segments") or "[]")
            services.split(case, request.user, segments, request=request)
            messages.success(request, f"{len(segments)} Segmente angelegt und entschieden.")
        elif action == "transfer":
            target_obj = get_object_or_404(ManagedObject.active, pk=request.POST.get("target_object"))
            services.transfer(
                case, request.user, target_obj, reason=request.POST.get("reason"), request=request
            )
            messages.success(request, f"Dokument in Objekt {target_obj.object_number} übernommen.")
        elif action == "assign_object":
            from apps.sync.flows import assign as sync_assign

            target_obj = get_object_or_404(ManagedObject.active, pk=request.POST.get("target_object"))
            if case.document is None:
                raise services.ReviewError("Fall ohne Dokument")
            new_doc = sync_assign.apply_assignment(
                case.document,
                target_obj,
                user=request.user,
                case=case,
                request=request,
                reason=request.POST.get("reason", ""),
            )
            messages.success(
                request,
                f"Dokument dem Objekt {target_obj.object_number} zugeordnet (Dokument {new_doc.pk}); Ablage folgt.",
            )
        elif action == "reject_assignment":
            from apps.sync.flows import assign as sync_assign

            sync_assign.reject_proposal(
                case, user=request.user, request=request, reason=request.POST.get("reason", "")
            )
            messages.info(request, "Vorschlag verworfen; das Dokument bleibt im Eingang.")
        elif action == "resolve_conflict":
            from apps.sync.flows import conflicts

            try:
                conflicts.resolve(
                    case,
                    request.POST.get("choice", ""),
                    user=request.user,
                    request=request,
                    reason=request.POST.get("reason", ""),
                )
            except conflicts.ConflictError as exc:
                raise services.ReviewError(str(exc)) from exc
            messages.success(request, "Konflikt entschieden.")
        elif action == "start_import":
            batch = services.start_import(
                case, request.user, import_kind=request.POST.get("import_kind") or None, request=request
            )
            messages.success(request, f"Import {batch.pk} angelegt, die Liste wird eingelesen.")
            return redirect("import_batch", pk=batch.pk)
        else:
            return HttpResponseBadRequest("Unbekannte Aktion")
    except services.ReviewError as exc:
        messages.error(request, str(exc))
        return redirect(f"/review/{case.pk}/?{request.POST.get('query') or ''}")
    except (ValueError, json.JSONDecodeError) as exc:
        messages.error(request, f"Eingabe ungültig: {exc}")
        return redirect(f"/review/{case.pk}/?{request.POST.get('query') or ''}")
    return redirect(_next_url(request, case, next_id))


@login_required
def owner_search(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    return JsonResponse({"results": services.owner_suggestions(request.GET.get("q", ""), obj)})


@login_required
def tenant_search(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    return JsonResponse({"results": services.tenant_suggestions(request.GET.get("q", ""), obj)})


@login_required
def units_json(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    return JsonResponse({"results": services.units_of(obj)})


@login_required
@require_POST
def saved_filter(request):
    action = request.POST.get("action", "save")
    if action == "delete":
        row = get_object_or_404(ReviewSavedFilter, pk=request.POST.get("id"), user=request.user)
        record(
            "review.filter_deleted",
            entity_type="review_saved_filter",
            entity_id=row.pk,
            request=request,
            before={"name": row.name},
        )
        row.delete()
        messages.info(request, "Sicht gelöscht.")
        return redirect("review_list")
    name = (request.POST.get("name") or "").strip()[:80]
    if not name:
        messages.error(request, "Name der Sicht angeben.")
        return redirect("review_list")
    filters = services.ListFilters.from_params(request.POST, request.user).as_dict()
    row, created = ReviewSavedFilter.objects.update_or_create(
        user=request.user,
        name=name,
        defaults={"filters": filters, "is_shared": request.POST.get("shared") == "1"},
    )
    record(
        "review.filter_saved",
        entity_type="review_saved_filter",
        entity_id=row.pk,
        request=request,
        after={"name": name, "filters": filters, "created": created},
    )
    messages.success(request, f"Sicht „{name}“ gespeichert.")
    return redirect(f"/review/?{_filters_query(filters)}")


def _filters_query(filters: dict) -> str:
    from urllib.parse import urlencode

    pairs = []
    for o in filters.get("objects") or []:
        pairs.append(("objekt", o))
    for s in filters.get("status") or []:
        pairs.append(("status", s))
    mapping = {
        "case_type": "fallart",
        "subtype": "unterfall",
        "assigned_to": "bearbeiter",
        "target": "ziel",
        "band": "band",
        "created_from": "von",
        "created_to": "bis",
        "year": "jahr",
        "q": "q",
    }
    for k, param in mapping.items():
        if filters.get(k):
            pairs.append((param, filters[k]))
    if filters.get("grouped_only"):
        pairs.append(("gruppen", "1"))
    if filters.get("include_snoozed"):
        pairs.append(("zurueckgestellt", "1"))
    if filters.get("mine"):
        pairs.append(("meine", "1"))
    return urlencode(pairs)


@login_required
def apply_saved_filter(request, pk: int):
    row = ReviewSavedFilter.objects.filter(pk=pk).filter(models_q_visible(request.user)).first()
    if row is None:
        raise Http404
    return redirect(f"/review/?{_filters_query(row.filters)}")


def models_q_visible(user):
    from django.db.models import Q

    return Q(user=user) | Q(is_shared=True)


# ---------------------------------------------------------------- Massenbearbeitung (H 2.5)
def _bulk_case_ids(request) -> list[int]:
    ids = [int(v) for v in request.POST.getlist("case_id") if str(v).isdigit()]
    if not ids and request.POST.get("batch_key"):
        ids = list(
            ReviewCase.objects.filter(
                batch_key=request.POST["batch_key"], status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
            )
            .order_by("document_id", "page_from", "id")
            .values_list("id", flat=True)[: services.bulk_max()]
        )
    if not ids and request.GET.get("batch_key"):
        ids = list(
            ReviewCase.objects.filter(
                batch_key=request.GET["batch_key"], status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
            )
            .order_by("document_id", "page_from", "id")
            .values_list("id", flat=True)[: services.bulk_max()]
        )
    return ids


def _bulk_overrides(request) -> tuple[dict, dict[int, dict]]:
    overrides = {
        k: request.POST.get(k)
        for k in ("category", "subfolder", "document_type", "period_year")
        if request.POST.get(k)
    }
    row_overrides: dict[int, dict] = {}
    for key, value in request.POST.items():
        if key.startswith("row_") and value:
            _, case_id, field = key.split("_", 2)
            if case_id.isdigit():
                row_overrides.setdefault(int(case_id), {})[field] = value
    return overrides, row_overrides


@permission_required("review.decide")
def bulk_view(request):
    """Vorschau (GET oder POST ohne ausfuehren) und Entwurfsspeicher in der Sitzung; Ausfuehrung ueber bulk_execute."""
    ids = _bulk_case_ids(request)
    if not ids:
        messages.error(request, "Keine Fälle ausgewählt.")
        return redirect("review_list")
    key = f"bulk_draft:{','.join(str(i) for i in ids[:50])}"
    if request.method == "POST":
        overrides, row_overrides = _bulk_overrides(request)
        request.session[key] = {
            "overrides": overrides,
            "row_overrides": {str(k): v for k, v in row_overrides.items()},
            "exclude": [int(v) for v in request.POST.getlist("exclude") if str(v).isdigit()],
        }
        record(
            "review.bulk_preview",
            entity_type="review_bulk",
            request=request,
            after={"cases": ids, "overrides": overrides},
        )
    draft = request.session.get(key) or {}
    overrides = draft.get("overrides") or {}
    row_overrides = {int(k): v for k, v in (draft.get("row_overrides") or {}).items()}
    exclude = set(draft.get("exclude") or [])
    rows = services.bulk_rows(ids, overrides, row_overrides, request.user)
    return render(
        request,
        "review/bulk.html",
        {
            "rows": rows,
            "ids": ids,
            "overrides": overrides,
            "exclude": exclude,
            "green": sum(1 for r in rows if r.state == "gruen" and r.case_id not in exclude),
            "yellow": sum(1 for r in rows if r.state == "gelb" and r.case_id not in exclude),
            "red": sum(1 for r in rows if r.state == "rot" and r.case_id not in exclude),
            "blocking": [r for r in rows if r.errors and r.case_id not in exclude],
            "subfolders": DocumentSubfolder.objects.filter(category_id="05").order_by("code"),
            "document_types": DocumentType.objects.filter(is_active=True).order_by("category_id", "name"),
            "batch_key": request.POST.get("batch_key") or request.GET.get("batch_key") or "",
            "units": services.units_of(rows[0].target and ReviewCase.objects.get(pk=rows[0].case_id).object)
            if rows
            else [],
            "progress_key": None,
        },
    )


@permission_required("review.decide")
@require_POST
def bulk_execute_view(request):
    from apps.review.tasks import bulk_execute_task, progress_key

    ids = _bulk_case_ids(request)
    if not ids:
        return HttpResponseBadRequest("Keine Fälle")
    overrides, row_overrides = _bulk_overrides(request)
    exclude = [int(v) for v in request.POST.getlist("exclude") if str(v).isdigit()]
    rows = services.bulk_rows(ids, overrides, row_overrides, request.user)
    blocking = [r.case_id for r in rows if r.errors and r.case_id not in set(exclude)]
    if blocking:
        messages.error(request, f"{len(blocking)} Zeilen mit Konflikt: korrigieren oder ausschließen.")
        request.session[f"bulk_draft:{','.join(str(i) for i in ids[:50])}"] = {
            "overrides": overrides,
            "row_overrides": {str(k): v for k, v in row_overrides.items()},
            "exclude": exclude,
        }
        return redirect(f"/review/sammel/?{'&'.join(f'case_id={i}' for i in ids)}")
    import uuid

    bulk_key = f"bulk:{uuid.uuid4().hex[:12]}"
    from django.core.cache import cache

    cache.set(progress_key(bulk_key), {"status": "queued", "total": len(ids) - len(exclude)}, timeout=3600)
    bulk_execute_task.delay(
        ids, request.user.pk, overrides, {str(k): v for k, v in row_overrides.items()}, exclude, bulk_key
    )
    request.session.pop(f"bulk_draft:{','.join(str(i) for i in ids[:50])}", None)
    messages.success(request, f"Sammelaktion {bulk_key} gestartet: {len(ids) - len(exclude)} Fälle.")
    return redirect(f"/review/sammel/status/{bulk_key}/")


@login_required
def bulk_status(request, bulk_key: str):
    from django.core.cache import cache

    from apps.review.tasks import progress_key

    state = cache.get(progress_key(bulk_key)) or {"status": "unbekannt"}
    if (
        request.headers.get("Accept", "").startswith("application/json")
        or request.GET.get("format") == "json"
    ):
        return JsonResponse(state)
    decisions = (
        services.ReviewDecision.objects.filter(bulk_key=bulk_key)
        .select_related("review_case", "document")
        .order_by("id")
    )
    return render(
        request, "review/bulk_status.html", {"bulk_key": bulk_key, "state": state, "decisions": decisions}
    )
