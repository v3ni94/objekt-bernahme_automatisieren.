"""Objektverwaltung: Objekte, Einheiten, Stichtagsabfrage (Umsetzungsplan M2 Schritte 5 und 6)."""

from __future__ import annotations

from datetime import date

from allauth.account.decorators import reauthentication_required
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.config import store
from apps.objects.forms import ArchiveForm, ObjectForm, UnitForm
from apps.objects.models import ManagedObject, ObjectStatus, Unit
from apps.parties.models import OwnerFile, OwnerUnitAssignment, TenantFile
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
_UNIT_FOLDER_HINTS = {
    "queued": "Die Aktenordner werden in Drive angelegt und benannt. Der Verlauf steht im Protokoll.",
    "debounced": "Die Aktenordner werden bereits nachgezogen.",
    "not_connected": "Ohne Google-Verbindung bleiben die Akten vorerst ohne Drive-Ordner.",
    "no_root": "Der Wurzelordner ist nicht gesetzt; die Akten bleiben vorerst ohne Drive-Ordner.",
    "error": "Die Aktenordner konnten nicht eingereiht werden. Später „Akten anlegen“ erneut ausführen.",
}


def _prepare_unit_files(obj: ManagedObject, *, user, force: bool = False) -> tuple[str | None, dict]:
    """Akten-Vorlage (11.09.2026): Einheiten aus der Sollzahl als Platzhalter WE 1 bis WE n, je Einheit eine
    Eigentuemer- und eine Mieterakte. Ohne force nur mit Schalter owner_file.create_folders_eagerly.
    Rueckgabe: Hinweis fuer den Anwender (None, wenn nichts entstand) und Zahlen fuer das Protokoll."""
    if not force and not store.get("owner_file.create_folders_eagerly", False):
        return None, {}
    from apps.parties.unit_files import ensure_placeholder_units, ensure_unit_files

    created = ensure_placeholder_units(obj, obj.expected_unit_count or 0, user=user)
    stats = ensure_unit_files(obj)
    counts = {"units_created": len(created), **stats}
    parts = []
    if created:
        parts.append(f"{len(created)} Einheiten WE 1 bis WE {len(created)} als Platzhalter angelegt")
    if stats["owner_files"] or stats["tenant_files"]:
        parts.append(
            f"{stats['owner_files']} Eigentümerakten und {stats['tenant_files']} Mieterakten vorbereitet"
        )
    if not parts:
        return None, counts
    return (
        ", ".join(parts)
        + ". Die Aktennamen folgen den Eigentümern und Mietern, sobald diese zugeordnet sind.",
        counts,
    )


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
        # Akten-Vorlage vor der Ordneranlage: der Abgleich legt danach auch die Aktenordner an
        file_hint, _ = _prepare_unit_files(obj, user=request.user)
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
        if file_hint:
            messages.info(request, file_hint)
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
        file_hint, _ = _prepare_unit_files(
            obj, user=request.user
        )  # Sollzahl nachgetragen: Platzhalter folgen
        if file_hint:
            from apps.drive.tasks import trigger_unit_folders

            messages.info(request, file_hint)
            folder_hint = _UNIT_FOLDER_HINTS.get(trigger_unit_folders(obj.pk, user_id=request.user.pk))
            if folder_hint:
                messages.info(request, folder_hint)
        return redirect("object_detail", pk=obj.pk)
    return render(
        request,
        "objects/form.html",
        {"form": form, "title": f"Objekt {obj.object_number} bearbeiten", "object": obj},
    )


@permission_required("objects.write")
@require_POST
def object_unit_files(request, pk: int):
    """Knopf „Akten anlegen“: Einheiten aus der Sollzahl, je Einheit Eigentuemer- und Mieterakte, Ordner in Drive
    nachziehen. Wirkt unabhaengig vom Schalter, weil ausdruecklich angefordert; nichts wird geloescht."""
    from apps.drive.tasks import trigger_unit_folders

    obj = get_object_or_404(ManagedObject.active, pk=pk)
    if not Unit.active.filter(object=obj).exists() and not obj.expected_unit_count:
        messages.warning(
            request, "Keine Einheiten vorhanden. Sollzahl Einheiten eintragen oder Einheiten anlegen."
        )
        return redirect("object_detail", pk=obj.pk)
    file_hint, counts = _prepare_unit_files(obj, user=request.user, force=True)
    outcome = trigger_unit_folders(obj.pk, user_id=request.user.pk, force=True)
    record(
        "unit_files.prepare",
        entity_type="object",
        entity_id=obj.pk,
        object_id=obj.pk,
        request=request,
        after={**counts, "drive": outcome},
    )
    messages.success(
        request, file_hint or "Alle Einheiten haben bereits eine Eigentümer- und eine Mieterakte."
    )
    hint = _UNIT_FOLDER_HINTS.get(outcome)
    if hint:
        (messages.info if outcome in ("queued", "debounced") else messages.warning)(request, hint)
    return redirect("object_detail", pk=obj.pk)


@permission_required("objects.write")
@reauthentication_required
def object_archive(request, pk: int):
    """Objekt archivieren: Soft-Delete (deleted_at, deleted_by, delete_reason), Status archiviert. Einheiten,
    Eigentuemer, Dokumente und Protokoll bleiben; in Drive wird nichts geloescht oder verschoben (D 1 Nr. 6).
    Waehrend eines laufenden oder wartenden Verarbeitungslaufs nicht moeglich, damit kein Job ins Leere schreibt."""
    from apps.pipeline.models import ProcessingRun, RunStatus

    obj = get_object_or_404(ManagedObject.active, pk=pk)
    offen = ProcessingRun.objects.filter(
        object=obj, status__in=[RunStatus.PENDING, RunStatus.RUNNING]
    ).count()
    form = ArchiveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if offen:
            messages.error(
                request, f"Objekt {obj.object_number} hat {offen} offene(n) Verarbeitungslauf; erst abwarten."
            )
            return redirect("object_detail", pk=obj.pk)
        before = {"status": obj.status, "deleted_at": None}
        with transaction.atomic():
            obj.deleted_at = timezone.now()
            obj.deleted_by = request.user
            obj.delete_reason = form.cleaned_data["reason"]
            obj.status = ObjectStatus.ARCHIVED
            obj.save(update_fields=["deleted_at", "deleted_by", "delete_reason", "status", "updated_at"])
            record(
                "object.archive",
                entity_type="object",
                entity_id=obj.pk,
                object_id=obj.pk,
                request=request,
                before=before,
                after={
                    "status": obj.status,
                    "deleted_at": obj.deleted_at.isoformat(),
                    "reason": obj.delete_reason,
                    "drive_root_folder_id": obj.drive_root_folder_id,
                },
            )
        messages.success(
            request,
            f"Objekt {obj.object_number} archiviert. Es erscheint nicht mehr in den Listen; "
            "Daten und Drive-Ordner bleiben erhalten.",
        )
        return redirect("object_archive_list")
    return render(
        request,
        "objects/archive_form.html",
        {"object": obj, "form": form, "open_runs": offen, "title": f"Objekt {obj.object_number} archivieren"},
    )


@permission_required("objects.write")
def object_archive_list(request):
    objects = (
        ManagedObject.objects.filter(deleted_at__isnull=False)
        .select_related("deleted_by")
        .order_by("-deleted_at")
    )
    return render(request, "objects/archive_list.html", {"objects": objects})


@permission_required("objects.write")
@reauthentication_required
@require_POST
def object_restore(request, pk: int):
    """Archiviertes Objekt zurueckholen: deleted_at leeren, Status aktiv. Die Objektnummer darf inzwischen nicht
    an ein anderes aktives Objekt vergeben sein (Eindeutigkeit ueber active_key)."""
    obj = get_object_or_404(ManagedObject.objects.filter(deleted_at__isnull=False), pk=pk)
    if ManagedObject.active.filter(object_number_numeric=obj.object_number_numeric).exists():
        messages.error(
            request,
            f"Objektnummer {obj.object_number} ist inzwischen an ein aktives Objekt vergeben; "
            "Wiederherstellung nicht möglich.",
        )
        return redirect("object_archive_list")
    before = {"status": obj.status, "deleted_at": obj.deleted_at.isoformat(), "reason": obj.delete_reason}
    with transaction.atomic():
        obj.deleted_at = None
        obj.deleted_by = None
        obj.delete_reason = None
        obj.status = ObjectStatus.ACTIVE
        obj.save(update_fields=["deleted_at", "deleted_by", "delete_reason", "status", "updated_at"])
        record(
            "object.restore",
            entity_type="object",
            entity_id=obj.pk,
            object_id=obj.pk,
            request=request,
            before=before,
            after={"status": obj.status, "deleted_at": None},
        )
    messages.success(
        request, f"Objekt {obj.object_number} wiederhergestellt. Status steht auf aktiv, bitte prüfen."
    )
    return redirect("object_detail", pk=obj.pk)


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
    tenant_files = TenantFile.active.filter(object=obj).select_related("unit").order_by("folder_name")
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
            "tenant_files": tenant_files,
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
