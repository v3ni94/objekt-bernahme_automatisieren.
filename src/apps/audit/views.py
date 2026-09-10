"""Audit-Ansicht mit Filtern und CSV-Export (docs/architektur.md 9.5)."""

from __future__ import annotations

import csv
import json

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.dateparse import parse_date

from apps.accounts.permissions import user_has_permission

from .models import AuditEvent
from .services import record


def _filtered(request):
    qs = AuditEvent.objects.all()
    if not user_has_permission(request.user, "audit.read_all"):
        qs = qs.filter(user_id=request.user.pk)  # audit.read_own
    action = request.GET.get("action", "").strip()
    email = request.GET.get("user", "").strip()
    entity = request.GET.get("entity_type", "").strip()
    since = parse_date(request.GET.get("since", "") or "")
    until = parse_date(request.GET.get("until", "") or "")
    if action:
        qs = qs.filter(action__startswith=action)
    if email:
        qs = qs.filter(user_email__icontains=email)
    if entity:
        qs = qs.filter(entity_type=entity)
    if since:
        qs = qs.filter(occurred_at__date__gte=since)
    if until:
        qs = qs.filter(occurred_at__date__lte=until)
    return qs


@login_required
def audit_list(request):
    if not (
        user_has_permission(request.user, "audit.read_all")
        or user_has_permission(request.user, "audit.read_own")
    ):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    page = Paginator(_filtered(request), 50).get_page(request.GET.get("seite"))
    return render(request, "audit/list.html", {"page": page, "filters": request.GET})


@login_required
def audit_export(request):
    if not user_has_permission(request.user, "audit.read_all"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="audit.csv"'
    writer = csv.writer(response, delimiter=";")
    writer.writerow(
        [
            "Zeitpunkt",
            "Akteur",
            "Nutzer",
            "Aktion",
            "Entität",
            "ID",
            "Objekt",
            "Grund",
            "Vorher",
            "Nachher",
            "Request",
        ]
    )
    for e in _filtered(request).iterator(chunk_size=500):
        writer.writerow(
            [
                e.occurred_at.strftime("%d.%m.%Y %H:%M:%S"),
                e.actor_type,
                e.user_email or "",
                e.action,
                e.entity_type,
                e.entity_id or "",
                e.object_id or "",
                e.reason or "",
                json.dumps(e.before_state, ensure_ascii=False) if e.before_state is not None else "",
                json.dumps(e.after_state, ensure_ascii=False) if e.after_state is not None else "",
                e.request_id or "",
            ]
        )
    record("audit.export", entity_type="audit_events", request=request, after={"rows": "export"})
    return response
