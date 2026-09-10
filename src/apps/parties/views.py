"""Eigentuemer und Zuordnungen in Grundform (M2 Schritt 6). Aenderungen protokolliert (owner.update, assignment.create)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.objects.models import ManagedObject
from apps.parties import services
from apps.parties.forms import AssignmentForm, EndAssignmentForm, OwnerForm
from apps.parties.models import Owner, OwnerUnitAssignment

AUDIT_EXCLUDE = {"iban_hash", "iban_encrypted"}


def _owner_dict(owner: Owner) -> dict:
    return {k: v for k, v in model_to_dict(owner).items() if k not in AUDIT_EXCLUDE}


@login_required
def owner_list(request):
    q = request.GET.get("q", "").strip()
    owners = Owner.active.all()
    if q:
        owners = owners.filter(Q(search_name__icontains=q) | Q(email__icontains=q))
    return render(
        request,
        "parties/owner_list.html",
        {"owners": owners[:500], "q": q, "can_master": user_has_permission(request.user, "masterdata.write")},
    )


@permission_required("masterdata.write")
def owner_create(request):
    form = OwnerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            owner = form.save()
            record(
                "owner.create",
                entity_type="owner",
                entity_id=owner.pk,
                request=request,
                after=_owner_dict(owner),
            )
        messages.success(request, f"Eigentümer {owner} angelegt.")
        nxt = request.GET.get("next")
        return redirect(nxt) if nxt and nxt.startswith("/") else redirect("owner_list")
    return render(request, "parties/owner_form.html", {"form": form, "title": "Eigentümer anlegen"})


@permission_required("masterdata.write")
def owner_edit(request, pk: int):
    owner = get_object_or_404(Owner.active, pk=pk)
    before = _owner_dict(owner)
    form = OwnerForm(request.POST or None, instance=owner)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            owner = form.save()
            after = _owner_dict(owner)
            keys = [k for k in after if before.get(k) != after.get(k)]
            record(
                "owner.update",
                entity_type="owner",
                entity_id=owner.pk,
                request=request,
                before={k: before.get(k) for k in keys},
                after={k: after.get(k) for k in keys},
            )
        messages.success(request, f"Eigentümer {owner} gespeichert.")
        return redirect("owner_list")
    return render(
        request,
        "parties/owner_form.html",
        {"form": form, "title": f"Eigentümer {owner} bearbeiten", "owner": owner},
    )


@permission_required("masterdata.write")
def assignment_create(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    form = AssignmentForm(request.POST or None, object=obj)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            result = services.create_assignment(
                owner=d["owner"],
                unit=d["unit"],
                valid_from=d["valid_from"],
                valid_to=d["valid_to"],
                share=d["share"],
                user=request.user,
                confirmed=d["confirmed"],
                notes=d["notes"] or None,
            )
        except services.OverlapError as exc:
            form.add_error(None, str(exc))
        else:
            a = result.assignment
            record(
                "assignment.create",
                entity_type="owner_unit_assignment",
                entity_id=a.pk,
                object_id=obj.pk,
                request=request,
                after={
                    "owner_id": a.owner_id,
                    "unit_id": a.unit_id,
                    "valid_from": str(a.valid_from),
                    "valid_to": str(a.valid_to),
                    "share": str(a.share),
                },
            )
            if result.share_warning is not None:
                messages.warning(
                    request,
                    f"Summe der Anteile am Beginn beträgt {result.share_warning} und liegt über 1,0 (Warnung, Teildaten).",
                )
            messages.success(request, f"Zuordnung {a.owner} an {a.unit.unit_label} gespeichert.")
            return redirect("object_detail", pk=obj.pk)
    return render(request, "parties/assignment_form.html", {"form": form, "object": obj})


@permission_required("masterdata.write")
def assignment_end(request, pk: int):
    a = get_object_or_404(OwnerUnitAssignment.active.select_related("unit", "owner"), pk=pk)
    form = EndAssignmentForm(request.POST or None, assignment=a)
    if request.method == "POST" and form.is_valid():
        before = str(a.valid_to)
        services.end_assignment(a, form.cleaned_data["valid_to"], user=request.user)
        record(
            "assignment.end",
            entity_type="owner_unit_assignment",
            entity_id=a.pk,
            object_id=a.unit.object_id,
            request=request,
            reason=form.cleaned_data.get("reason") or None,
            before={"valid_to": before},
            after={"valid_to": str(a.valid_to)},
        )
        messages.success(
            request, f"Zuordnung {a.owner} an {a.unit.unit_label} endet am {a.valid_to:%d.%m.%Y}."
        )
        return redirect("object_detail", pk=a.unit.object_id)
    return render(request, "parties/assignment_end.html", {"form": form, "assignment": a})
