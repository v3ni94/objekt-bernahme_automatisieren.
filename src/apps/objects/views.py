"""Objektverwaltung: Objekte, Einheiten, Stichtagsabfrage (Umsetzungsplan M2 Schritte 5 und 6)."""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.objects.forms import ObjectForm, UnitForm
from apps.objects.models import ManagedObject, Unit
from apps.parties.models import OwnerFile, OwnerUnitAssignment
from apps.parties.services import owners_at

# Rueckmeldung der Ordneranlage an den Anwender; "disabled" bleibt ohne Hinweis, weil bewusst abgeschaltet.
_FOLDER_HINTS = {
    "queued": "Der Objektordner wird in Drive angelegt. Der Verlauf steht unter Ordnerabgleich.",
    "not_connected": (
        "Ohne Google-Verbindung wurde kein Ordner angelegt. Nach dem Verbinden den Ordnerabgleich starten."
    ),
    "no_root": (
        "Der Wurzelordner ist nicht gesetzt, es wurde kein Ordner angelegt. Einrichtung unter Google Drive."
    ),
    "error": "Die Ordneranlage konnte nicht eingereiht werden. Ordnerabgleich später von Hand starten.",
}


def _changes(before: dict, after: dict) -> tuple[dict, dict]:
    keys = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


@login_required
def object_list(request):
    q = request.GET.get("q", "").strip()
    objects = ManagedObject.active.annotate(
        unit_count=Count("units", filter=Q(units__deleted_at__isnull=True))
    )
    if q:
        objects = objects.filter(Q(object_number__contains=q) | Q(name__icontains=q) | Q(city__icontains=q))
    return render(
        request,
        "objects/list.html",
        {"objects": objects, "q": q, "can_write": user_has_permission(request.user, "objects.write")},
    )


@permission_required("objects.write")
def object_create(request):
    form = ObjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            record(
                "object.create",
                entity_type="object",
                entity_id=obj.pk,
                object_id=obj.pk,
                request=request,
                after=model_to_dict(obj),
            )
        # H 3.5, CR 2: Der Objektordner in Drive entsteht unmittelbar mit dem Objekt. Der Abgleich laeuft als
        # Job, damit die Anlage nicht an der Antwortzeit oder an einem Drive-Fehler haengt.
        from apps.drive.tasks import trigger_object_folders

        outcome = trigger_object_folders(obj.pk, user_id=request.user.pk, trigger="object_create")
        if outcome == "queued":
            record(
                "drive.reconcile",
                entity_type="object",
                entity_id=obj.pk,
                object_id=obj.pk,
                request=request,
                after={"dry_run": False, "trigger": "object_create"},
            )
        messages.success(request, f"Objekt {obj.object_number} angelegt.")
        hint = _FOLDER_HINTS.get(outcome)
        if hint:
            (messages.info if outcome == "queued" else messages.warning)(request, hint)
        return redirect("object_detail", pk=obj.pk)
    return render(request, "objects/form.html", {"form": form, "title": "Objekt anlegen"})


@permission_required("objects.write")
def object_edit(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    before = model_to_dict(obj)
    form = ObjectForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save()
            b, a = _changes(before, model_to_dict(obj))
            record(
                "object.update",
                entity_type="object",
                entity_id=obj.pk,
                object_id=obj.pk,
                request=request,
                before=b,
                after=a,
            )
        from apps.requirements.tasks import trigger_evaluation

        trigger_evaluation(obj.pk, "object_update")  # H 3.5: Stammdatenaenderung
        from apps.lists.services import request_generation

        request_generation(obj.pk, "masterdata_change", user_id=request.user.pk)
        messages.success(request, f"Objekt {obj.object_number} gespeichert.")
        return redirect("object_detail", pk=obj.pk)
    return render(
        request,
        "objects/form.html",
        {"form": form, "title": f"Objekt {obj.object_number} bearbeiten", "object": obj},
    )


@login_required
def object_detail(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    units = list(Unit.active.filter(object=obj).order_by("unit_type", "unit_label_normalized"))
    stichtag_raw = request.GET.get("stichtag", "")
    stichtag = None
    if stichtag_raw:
        try:
            stichtag = date.fromisoformat(stichtag_raw)
        except ValueError:
            messages.error(request, "Stichtag im Format JJJJ-MM-TT eingeben.")
    reference = stichtag or date.today()
    current = {}
    for u in units:
        current[u.pk] = list(owners_at(u, reference))
    assignments = (
        OwnerUnitAssignment.active.filter(unit__object=obj)
        .select_related("owner", "unit")
        .order_by("unit__unit_label_normalized", "valid_from")
    )
    files = OwnerFile.active.filter(object=obj).select_related("unit").order_by("folder_name")
    return render(
        request,
        "objects/detail.html",
        {
            "object": obj,
            "units": units,
            "current": current,
            "reference": reference,
            "stichtag": stichtag,
            "assignments": assignments,
            "owner_files": files,
            "can_write": user_has_permission(request.user, "objects.write"),
            "can_master": user_has_permission(request.user, "masterdata.write"),
        },
    )


@permission_required("masterdata.write")
def unit_create(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    form = UnitForm(request.POST or None, object=obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            unit = form.save()
            record(
                "unit.create",
                entity_type="unit",
                entity_id=unit.pk,
                object_id=obj.pk,
                request=request,
                after=model_to_dict(unit),
            )
        hint = f" (Hinweis: {', '.join(form.parsed.reasons)})" if form.parsed.reasons else ""
        messages.success(request, f"Einheit {unit.unit_label} angelegt{hint}.")
        return redirect("object_detail", pk=obj.pk)
    return render(
        request, "objects/unit_form.html", {"form": form, "object": obj, "title": "Einheit anlegen"}
    )


@permission_required("masterdata.write")
def unit_edit(request, pk: int):
    unit = get_object_or_404(Unit.active.select_related("object"), pk=pk)
    before = model_to_dict(unit)
    form = UnitForm(request.POST or None, instance=unit, object=unit.object)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            unit = form.save()
            b, a = _changes(before, model_to_dict(unit))
            record(
                "unit.update",
                entity_type="unit",
                entity_id=unit.pk,
                object_id=unit.object_id,
                request=request,
                before=b,
                after=a,
            )
        messages.success(request, f"Einheit {unit.unit_label} gespeichert.")
        return redirect("object_detail", pk=unit.object_id)
    return render(
        request,
        "objects/unit_form.html",
        {"form": form, "object": unit.object, "title": f"Einheit {unit.unit_label} bearbeiten"},
    )
