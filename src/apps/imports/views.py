"""Importansicht (H 6.7, M3 Schritt 8): Kopf mit Zaehlern, Spaltenzuordnung, Zeilen mit Vorschlag und Konfidenz,
gesammelte Bestaetigung sicherer Zeilen mit Vorschau, Einzelentscheidung unsicherer Zeilen, Protokoll."""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import permission_required
from apps.imports import services
from apps.imports.forms import ReparseForm, RowDecisionForm, UploadForm, mapping_form_class
from apps.imports.mapping import TARGET_FIELDS, apply_mapping_overrides
from apps.imports.models import ImportBatch, ImportRow, RowStatus
from apps.imports.services import Decision
from apps.imports.tasks import commit_rows_task, normalize_batch_task, parse_batch_task, write_protocol_task
from apps.objects.models import ManagedObject
from apps.parties.models import Owner


@permission_required("imports.write")
def batch_upload(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        f = form.cleaned_data["file"]
        try:
            batch, created = services.create_batch(
                obj,
                filename=f.name,
                data=f.read(),
                user=request.user,
                import_kind=form.cleaned_data["import_kind"],
            )
        except services.ImportError_ as exc:
            form.add_error("file", str(exc))
        else:
            if created:
                parse_batch_task.delay(batch.pk)
                messages.success(
                    request, f"Datei {batch.source_file_name} angenommen, Import {batch.pk} wird eingelesen."
                )
            else:
                messages.info(request, f"Diese Datei wurde bereits als Import {batch.pk} angenommen.")
            return redirect("import_batch", pk=batch.pk)
    return render(request, "imports/upload.html", {"form": form, "object": obj})


@login_required
def batch_list(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    batches = ImportBatch.objects.filter(object=obj).order_by("-created_at")
    return render(request, "imports/list.html", {"object": obj, "batches": batches})


@login_required
def batch_detail(request, pk: int):
    batch = get_object_or_404(ImportBatch.objects.select_related("object"), pk=pk)
    mapping = batch.column_mapping or {}
    columns = mapping.get("columns", [])
    MappingForm = mapping_form_class(columns)
    mapping_form = MappingForm(prefix="map")
    status_filter = request.GET.get("status", "")
    rows = (
        ImportRow.objects.filter(batch=batch)
        .select_related("review_case", "matched_unit")
        .order_by("row_no", "sub_index")
    )
    if status_filter:
        rows = rows.filter(status=status_filter)
    counts = {s: ImportRow.objects.filter(batch=batch, status=s).count() for s, _ in RowStatus.choices}
    protocol_exists = (services.import_dir(batch) / "protokoll.xlsx").exists()
    return render(
        request,
        "imports/detail.html",
        {
            "batch": batch,
            "object": batch.object,
            "mapping": mapping,
            "columns": columns,
            "mapping_form": mapping_form,
            "reparse_form": ReparseForm(
                initial={"profile": batch.parser_profile, "sheet": mapping.get("sheet") or ""}
            ),
            "rows": rows[:2000],
            "counts": counts,
            "status_filter": status_filter,
            "targets": TARGET_FIELDS,
            "protocol_exists": protocol_exists,
            "confidence_min": mapping.get("confidence_min", 0.8),
        },
    )


@permission_required("imports.write")
@require_POST
def batch_reparse(request, pk: int):
    batch = get_object_or_404(ImportBatch, pk=pk)
    form = ReparseForm(request.POST)
    if form.is_valid():
        parse_batch_task.delay(batch.pk, form.cleaned_data["profile"], form.cleaned_data.get("sheet") or None)
        messages.success(request, "Datei wird mit dem gewählten Profil neu eingelesen.")
    return redirect("import_batch", pk=batch.pk)


@permission_required("imports.write")
@require_POST
def batch_mapping(request, pk: int):
    batch = get_object_or_404(ImportBatch, pk=pk)
    mapping = batch.column_mapping or {}
    MappingForm = mapping_form_class(mapping.get("columns", []))
    form = MappingForm(request.POST, prefix="map")
    if not form.is_valid():
        messages.error(request, "Zuordnung ungültig.")
        return redirect("import_batch", pk=batch.pk)
    overrides = {
        int(name[4:]): (value or None) for name, value in form.cleaned_data.items() if name.startswith("col_")
    }
    try:
        mapping = apply_mapping_overrides(mapping, overrides)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("import_batch", pk=batch.pk)
    batch.column_mapping = mapping
    batch.save(update_fields=["column_mapping", "updated_at"])
    normalize_batch_task.delay(batch.pk, request.user.pk)
    messages.success(request, "Zuordnung übernommen, Zeilen werden erkannt.")
    return redirect("import_batch", pk=batch.pk)


def _selected_rows(request, batch) -> list[ImportRow]:
    ids = [int(x) for x in request.POST.getlist("row") if x.isdigit()]
    if request.POST.get("all_parsed") == "1":
        ids += list(
            ImportRow.objects.filter(batch=batch, status=RowStatus.PARSED).values_list("pk", flat=True)
        )
    return list(ImportRow.objects.filter(batch=batch, pk__in=set(ids)).order_by("row_no", "sub_index"))


@permission_required("imports.write")
@require_POST
def batch_preview(request, pk: int):
    """Vorschau vor der Ausfuehrung (H 2.5): was wird angelegt, was ergaenzt."""
    batch = get_object_or_404(ImportBatch.objects.select_related("object"), pk=pk)
    action = request.POST.get("action", "accept")
    rows = _selected_rows(request, batch)
    preview = []
    for r in rows:
        pf = r.parsed_fields or {}
        person = pf.get("person") or {}
        shared = pf.get("shared") or {}
        match = pf.get("match") or {}
        preview.append(
            {
                "row": r,
                "unit": (shared.get("unit_label") or {}).get("value"),
                "unit_exists": pf.get("unit_exists"),
                "person": person.get("company_name")
                or " ".join(p for p in [person.get("first_name"), person.get("last_name")] if p),
                "owner_action": "bestehend"
                if match.get("decision") == "auto"
                else ("neu" if person else "keine"),
                "valid_from": (shared.get("valid_from") or {}).get("value"),
            }
        )
    return render(
        request,
        "imports/preview.html",
        {
            "batch": batch,
            "object": batch.object,
            "preview": preview,
            "action": action,
            "row_ids": [r.pk for r in rows],
        },
    )


@permission_required("imports.write")
@require_POST
def batch_commit(request, pk: int):
    batch = get_object_or_404(ImportBatch, pk=pk)
    action = request.POST.get("action", "accept")
    if action not in ("accept", "reject"):
        raise Http404
    ids = [int(x) for x in request.POST.getlist("row") if x.isdigit()]
    rows = ImportRow.objects.filter(
        batch=batch, pk__in=ids, status__in=[RowStatus.PARSED, RowStatus.UNCERTAIN, RowStatus.CONFIRMED]
    )
    if action == "accept":
        rows = rows.filter(status=RowStatus.PARSED)  # unsichere Zeilen nur einzeln
    decisions = {
        str(r.pk): {"action": action, "reason": "gesammelt im Review" if action == "reject" else None}
        for r in rows
    }
    if not decisions:
        messages.error(request, "Keine passenden Zeilen ausgewählt.")
        return redirect("import_batch", pk=batch.pk)
    commit_rows_task.delay(batch.pk, decisions, request.user.pk)
    messages.success(
        request,
        f"{len(decisions)} Zeilen werden {'übernommen' if action == 'accept' else 'abgelehnt'} (Hintergrundjob).",
    )
    return redirect("import_batch", pk=batch.pk)


@permission_required("imports.write")
def row_decide(request, pk: int):
    row = get_object_or_404(ImportRow.objects.select_related("batch", "batch__object", "matched_unit"), pk=pk)
    pf = row.parsed_fields or {}
    shared = pf.get("shared") or {}
    person = pf.get("person") or {}
    options = pf.get("options") or ["accept", "reject"]
    initial = {
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "company_name": person.get("company_name"),
        "unit_label": (shared.get("unit_label") or {}).get("value"),
        "valid_from": (shared.get("valid_from") or {}).get("value"),
        "action": options[0],
    }
    form = RowDecisionForm(request.POST or None, options=options, initial=initial)
    candidates = (pf.get("match") or {}).get("candidates") or []
    current_ids = pf.get("current_owner_assignment_ids") or []
    from apps.parties.models import OwnerUnitAssignment

    current = OwnerUnitAssignment.objects.filter(pk__in=current_ids).select_related("owner")
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        overrides = {}
        for f in ("first_name", "last_name", "company_name"):
            if d.get(f) and d[f] != (person.get(f) or ""):
                overrides[f] = d[f]
        if d.get("valid_from") and d["valid_from"].isoformat() != initial.get("valid_from"):
            overrides["valid_from"] = d["valid_from"].isoformat()
        owner_id = d.get("owner_id")
        if d["action"] == "duplicate" and not owner_id and current:
            owner_id = current[0].owner_id
        if d["action"] == "use_candidate" and not owner_id and candidates:
            owner_id = candidates[0]["owner_id"]
        if owner_id and not Owner.active.filter(pk=owner_id).exists():
            form.add_error("owner_id", "Eigentümer nicht gefunden.")
        else:
            decision = Decision(
                action=d["action"] if d["action"] != "new_owner" else "accept",
                owner_id=owner_id,
                overrides=overrides,
                reason=d.get("reason") or None,
            )
            if d["action"] == "new_owner":
                decision.owner_id = None
            result = services.commit_rows(row.batch, {row.pk: decision}, user=request.user)
            if result["failed"]:
                row.refresh_from_db()
                messages.error(request, f"Übernahme fehlgeschlagen: {row.notes}")
            else:
                write_protocol_task.delay(row.batch_id)
                messages.success(
                    request,
                    f"Zeile {row.row_no}.{row.sub_index}: {RowDecisionForm.ACTION_LABELS.get(d['action'], d['action'])}.",
                )
                return redirect("import_batch", pk=row.batch_id)
    return render(
        request,
        "imports/row.html",
        {
            "row": row,
            "batch": row.batch,
            "object": row.batch.object,
            "form": form,
            "shared": shared,
            "person": person,
            "candidates": candidates,
            "current": current,
            "today": date.today(),
        },
    )


@login_required
def batch_protocol(request, pk: int):
    batch = get_object_or_404(ImportBatch, pk=pk)
    path = services.import_dir(batch) / "protokoll.xlsx"
    if not path.exists():
        from apps.imports.protocol import write_protocol

        path = write_protocol(batch)
    if not str(path.resolve()).startswith(str(settings.OBJEKTAKTE["DATA_DIR"].resolve())):
        raise Http404
    return FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename=f"importprotokoll_{batch.object.object_number}_{batch.pk}.xlsx",
    )
