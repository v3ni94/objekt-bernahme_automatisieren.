"""Berichte (CR 13): Objektuebersicht mit Kennzahlen, CSV-Export, Objektdetail mit Kacheln, offenen Punkten und Faellen."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.audit.services import record
from apps.config import store
from apps.objects.models import ManagedObject
from apps.reporting import services
from apps.requirements import engine
from apps.review.models import CaseStatus, ReviewCase


@login_required
def overview(request):
    statuses = (
        ("takeover", "active", "new", "archived")
        if request.GET.get("alle") == "1"
        else ("takeover", "active")
    )
    rows = services.overview(statuses=statuses)
    if request.GET.get("format") == "csv":
        record(
            "list.export",
            entity_type="report",
            entity_id=None,
            request=request,
            after={"report": "objektuebersicht", "rows": len(rows)},
        )
        resp = HttpResponse(services.overview_csv(rows), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="objektuebersicht.csv"'
        return resp
    return render(
        request,
        "reporting/overview.html",
        {
            "rows": rows,
            "target_misc": store.get("reports.misc_share_target_pct", 5),
            "warning_days": store.get("reports.review_age_warning_days", 10),
            "alle": request.GET.get("alle") == "1",
        },
    )


@login_required
def object_report(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    kpi = services.object_kpi(obj)
    cases = (
        ReviewCase.objects.filter(object=obj, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS])
        .select_related("document", "assigned_to")
        .order_by("created_at")[:200]
    )
    return render(
        request,
        "reporting/object.html",
        {
            "object": obj,
            "kpi": kpi,
            "open_items": engine.open_items(obj)[:300],
            "cases": cases,
            "target_misc": store.get("reports.misc_share_target_pct", 5),
        },
    )
