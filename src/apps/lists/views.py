"""Listen je Objekt: Stand je Format, manueller Knopf „Listen jetzt erzeugen“ (H 5.6 Nr. 5), Download der lokalen
Dateien, Verlauf der Erzeugungen."""

from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import permission_required, user_has_permission
from apps.audit.services import record
from apps.lists import publish, services
from apps.lists.models import ListGeneration
from apps.objects.models import ManagedObject


@login_required
def list_overview(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    if request.method == "POST":
        if not user_has_permission(request.user, "lists.generate"):
            messages.error(request, "Kein Recht zur Listenerzeugung.")
            return redirect("list_overview", pk=obj.pk)
        from apps.drive import oauth

        try:
            drive = oauth.get_adapter()
        except Exception:
            drive = None
        try:
            results = services.generate_lists(
                obj, trigger="manual", user=request.user, drive=drive, publish_files=drive is not None
            )
        except services.ListError as exc:
            messages.error(request, str(exc))
            return redirect("list_overview", pk=obj.pk)
        for r in results:
            label = {"owner_list": "Eigentümerliste", "tenant_list": "Mieterliste"}[r.list_type]
            if r.status == "failed":
                messages.error(request, f"{label}: {r.error}")
            elif r.status == "skipped_unchanged":
                messages.info(request, f"{label}: unverändert, keine neue Version.")
            else:
                messages.success(
                    request,
                    f"{label}: erzeugt"
                    + (
                        " und in Drive veröffentlicht."
                        if drive
                        else ", lokal abgelegt (keine Drive-Verbindung)."
                    ),
                )
            for m in r.messages:
                messages.info(request, m)
        return redirect("list_overview", pk=obj.pk)
    latest = services.latest_status(obj)
    rows = []
    for list_type in services.list_types_for(obj):
        for fmt in ("xlsx", "pdf"):
            gen = latest.get(list_type, {}).get(fmt)
            rows.append(
                {
                    "list_type": list_type,
                    "label": {"owner_list": "Eigentümerliste", "tenant_list": "Mieterliste"}[list_type],
                    "format": fmt,
                    "name": publish.render_name(obj, list_type, fmt),
                    "gen": gen,
                    "local": (services.local_dir(obj) / publish.render_name(obj, list_type, fmt)).exists(),
                }
            )
    history = (
        ListGeneration.objects.filter(object=obj)
        .select_related("triggered_by", "drive_node")
        .order_by("-generated_at")[:50]
    )
    return render(
        request,
        "lists/overview.html",
        {
            "object": obj,
            "rows": rows,
            "history": history,
            "can_generate": user_has_permission(request.user, "lists.generate"),
        },
    )


@permission_required("lists.generate")
def list_download(request, pk: int, list_type: str, fmt: str):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    if list_type not in ("owner_list", "tenant_list") or fmt not in ("xlsx", "pdf"):
        raise Http404("Unbekannte Liste")
    path = services.local_dir(obj) / publish.render_name(obj, list_type, fmt)
    if not path.exists():
        raise Http404("Datei nicht vorhanden")
    record(
        "list.export",
        entity_type="object",
        entity_id=obj.pk,
        object_id=obj.pk,
        request=request,
        after={"list_type": list_type, "format": fmt},
    )
    return FileResponse(open(path, "rb"), as_attachment=True, filename=Path(path).name)
