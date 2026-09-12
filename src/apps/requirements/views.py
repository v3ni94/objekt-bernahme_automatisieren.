"""Oberflaeche der Vollstaendigkeitspruefung und der Nachforderung (H 3.4, H 4.5): Bewertung anstossen, Offene Punkte
mit manueller Uebersteuerung, Nachforderungsentwuerfe mit Pruefung, Freigabe (Step-up), Versandvermerk, Download."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from allauth.account.decorators import reauthentication_required
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import permission_required, user_has_permission
from apps.objects.models import ManagedObject
from apps.requirements import engine
from apps.requirements import requests as request_services
from apps.requirements.models import CompletenessCheck, CompletenessFinding, DocumentRequest, RequestStatus


def _parse_date(raw: str | None) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        parts = raw.split(".")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
        raise ValueError("Datum im Format TT.MM.JJJJ oder JJJJ-MM-TT eingeben") from None


@login_required
def completeness_view(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    if request.method == "POST":
        if request.POST.get("action") != "evaluate":
            return HttpResponseBadRequest("Unbekannte Aktion")
        result = engine.evaluate_object(obj, trigger="manual", user=request.user)
        messages.success(
            request,
            f"Bewertung abgeschlossen: {result['findings']} Prüfpositionen, {result['created']} neu, {result['changed']} geändert.",
        )
        return redirect("completeness", pk=obj.pk)
    period = engine.period_for(obj, timezone.localdate())
    summary = engine.summary(obj)
    checks = {c.code: c for c in CompletenessCheck.objects.all()}
    findings = (
        CompletenessFinding.objects.filter(object=obj)
        .select_related("unit", "assignment__owner", "evidence_document", "manual_by")
        .order_by("scope_type", "unit__unit_label_normalized", "check_code", "period_year")
    )
    rows = []
    for f in findings:
        check = checks.get(f.check_code)
        rows.append(
            {
                "f": f,
                "name": check.name if check else f.check_code,
                "effective": engine.effective_status(f),
                "consequential": bool((f.details or {}).get("consequential")),
                "hint": (f.details or {}).get("hint") or "",
                "owner": (f.assignment.owner if f.assignment_id else None)
                or (f.details or {}).get("tenant")
                or "",
            }
        )
    return render(
        request,
        "requirements/completeness.html",
        {
            "object": obj,
            "period": period,
            "summary": summary,
            "open_items": engine.open_items(obj),
            "rows": rows,
            "unit_rows": sorted(summary.get("units", {}).values(), key=lambda u: u["label"]),
            "can_write": user_has_permission(request.user, "demands.write"),
            "statuses": [(str(st), engine.MANUAL_LABELS[st]) for st in engine.MANUAL_STATUSES],
        },
    )


@permission_required("demands.write")
@require_POST
def finding_override(request, pk: int):
    finding = get_object_or_404(CompletenessFinding.objects.select_related("object"), pk=pk)
    try:
        engine.set_manual(
            finding,
            request.user,
            status=request.POST.get("manual_status") or None,
            reason=request.POST.get("reason"),
            include_in_request=request.POST.get("include_in_request") == "1"
            if "include_in_request" in request.POST
            else None,
            request=request,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Prüfposition aktualisiert.")
    return redirect("completeness", pk=finding.object_id)


@permission_required("demands.write")
def request_list(request, pk: int):
    obj = get_object_or_404(ManagedObject.active, pk=pk)
    if request.method == "POST":
        try:
            deadline = _parse_date(request.POST.get("deadline"))
            req = request_services.create_request(
                obj,
                request.user,
                deadline=deadline,
                reminder=request.POST.get("reminder") == "1",
                request=request,
            )
        except (request_services.RequestError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("request_list", pk=obj.pk)
        messages.success(request, f"Nachforderung Version {req.version} als Entwurf erzeugt.")
        return redirect("request_detail", pk=req.pk)
    reqs = DocumentRequest.objects.filter(object=obj).order_by("-version")
    return render(
        request,
        "requirements/requests.html",
        {
            "object": obj,
            "requests": reqs,
            "open_count": sum(1 for i in engine.open_items(obj) if i["include_in_request"]),
        },
    )


@permission_required("demands.write")
def request_detail(request, pk: int):
    req = get_object_or_404(
        DocumentRequest.objects.select_related("object", "created_by", "approved_by"), pk=pk
    )
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "update":
                recipient = {
                    f: request.POST.get(f)
                    for f in (
                        "recipient_name",
                        "recipient_street",
                        "recipient_house_number",
                        "recipient_postal_code",
                        "recipient_city",
                        "recipient_contact_person",
                        "reference",
                    )
                }
                request_services.update_request(
                    req,
                    request.user,
                    deadline=_parse_date(request.POST.get("deadline")),
                    recipient=recipient,
                    request=request,
                )
                messages.success(request, "Entwurf aktualisiert.")
            elif action == "review":
                request_services.mark_reviewed(req, request.user, request=request)
                messages.success(
                    request, "Schreiben geprüft. Freigabe durch die Geschäftsführung erforderlich."
                )
            elif action == "mark_sent":
                request_services.mark_sent(req, request.user, note=request.POST.get("note"), request=request)
                messages.success(
                    request, "Versand vermerkt. Der Versand selbst erfolgt außerhalb der Anwendung."
                )
            elif action == "withdraw":
                if not user_has_permission(request.user, "demands.approve"):
                    return HttpResponseForbidden("Zurückziehen nur mit Freigaberecht")
                request_services.withdraw(
                    req, request.user, reason=request.POST.get("reason", ""), request=request
                )
                messages.info(request, "Schreiben zurückgezogen.")
            else:
                return HttpResponseBadRequest("Unbekannte Aktion")
        except (request_services.RequestError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("request_detail", pk=req.pk)
    return render(
        request,
        "requirements/request_detail.html",
        {
            "object": req.object,
            "req": req,
            "positions": req.findings_snapshot or [],
            "editable": req.status in (RequestStatus.DRAFT, RequestStatus.REVIEWED),
            "can_approve": user_has_permission(request.user, "demands.approve"),
            "recipient_complete": request_services.recipient_complete(req),
        },
    )


@permission_required("demands.approve")
@reauthentication_required
@require_POST
def request_approve(request, pk: int):
    """Freigabe durch die Geschaeftsfuehrung mit Step-up (erneute Authentisierung); Versand ausserhalb der Anwendung."""
    req = get_object_or_404(DocumentRequest.objects.select_related("object"), pk=pk)
    try:
        request_services.approve(req, request.user, request=request)
    except request_services.RequestError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Nachforderung freigegeben. Dateien ohne Wasserzeichen erzeugt.")
    return redirect("request_detail", pk=req.pk)


@permission_required("demands.write")
def request_file(request, pk: int, fmt: str):
    req = get_object_or_404(DocumentRequest, pk=pk)
    path = {"pdf": req.pdf_path, "docx": req.docx_path}.get(fmt)
    if not path or not Path(path).exists():
        raise Http404("Datei nicht vorhanden")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=Path(path).name)
