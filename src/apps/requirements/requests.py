"""Nachforderungsgenerator (Fachentwurf H 4): aus den offenen Punkten entsteht ein Schreiben an die Vorverwaltung im
HVM-CI, immer als Entwurf. Statuskette draft, reviewed, approved, sent (manuell vermerkt), withdrawn; jede Aenderung im
Audit. Die Anwendung versendet nichts. Textbausteine kommen aus request_text_blocks; ein Schreiben speichert die
verwendeten Versionen und den Positionsbestand als Snapshot, spaetere Bausteinaenderungen aendern es nicht."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from string import Formatter

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.objects.models import ManagedObject
from apps.pipeline import storage
from apps.requirements import engine
from apps.requirements.models import CompletenessFinding, DocumentRequest, RequestStatus, RequestTextBlock

DRAFT_HINT = "Entwurf, Freigabe durch die Geschäftsführung erforderlich"
DEADLINE_PLACEHOLDER = "[FRIST]"


class RequestError(Exception):
    pass


class _Safe(dict):
    def __missing__(self, key):
        return ""


def _fmt(template: str, values: dict) -> str:
    try:
        return Formatter().vformat(template or "", (), _Safe(values)).strip()
    except (ValueError, IndexError):
        return template or ""


def _blocks() -> dict[str, RequestTextBlock]:
    return {b.code: b for b in RequestTextBlock.objects.filter(is_active=True)}


def _fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


@dataclass
class Letter:
    subject: str
    salutation: str
    paragraphs: list[str]
    groups: list[dict]  # {"title": str, "lines": [str]}
    attachment: bool
    deadline_text: str
    closing: str
    positions: list[dict]
    hints: list[str] = field(default_factory=list)

    def body_text(self) -> str:
        parts = [self.salutation, "", *self.paragraphs, ""]
        for g in self.groups:
            parts.append(g["title"])
            if not self.attachment:
                parts.extend(f"- {line}" for line in g["lines"])
            parts.append("")
        parts.extend([self.deadline_text, "", self.closing])
        return "\n".join(p for p in parts if p is not None).strip()


def compose(
    obj: ManagedObject, items: list[dict], *, deadline: date | None, user, reminder: bool = False
) -> tuple[Letter, dict[str, int]]:
    blocks = _blocks()
    versions = {code: b.version for code, b in blocks.items()}
    values = {
        "objekt_nr": obj.object_number,
        "objekt_bezeichnung": obj.name or "",
        "verwaltungsart": obj.get_management_type_display(),
        "uebernahmedatum": _fmt_date(obj.takeover_to) or "[Übernahmedatum]",
        "vorverwaltung_name": obj.previous_manager_name or "[Vorverwaltung]",
        "frist": _fmt_date(deadline) or DEADLINE_PLACEHOLDER,
        "sachbearbeiter": getattr(user, "display_name", "") or "die Hausverwaltung Müller GmbH",
        "anzahl_positionen": str(len(items)),
    }
    threshold = int(store.get("requests.attachment_threshold", 25))
    attachment = len(items) > threshold
    groups: dict[str, dict] = {}
    positions = []
    for it in items:
        cat = it["category"]
        title_block = blocks.get(f"group.{cat}")
        title = title_block.text if title_block else cat
        line_block = blocks.get(f"line.{it['check_code']}")
        line_values = {
            "einheit": it["unit_label"] or "Objekt",
            "eigentuemer": it["owner"] or "Eigentümer unbekannt",
            "jahr": str(it["period_year"] or ""),
            "hinweis": f", {it['hint']}" if it.get("hint") and it["status"] == "partial" else "",
        }
        line = (
            _fmt(line_block.text, line_values)
            if line_block
            else f"{it['check_name']} {it['unit_label']} {it['period_year'] or ''}".strip()
        )
        g = groups.setdefault(cat, {"title": title, "lines": [], "sort": min(it["sort_order"], 999)})
        g["lines"].append(line)
        positions.append({**it, "line": line, "group": title})
    ordered = sorted(groups.values(), key=lambda g: g["sort"])
    intro_code = "intro_reminder" if reminder else "intro"
    list_intro = blocks.get("list_intro_attachment" if attachment else "list_intro")
    letter = Letter(
        subject=_fmt(blocks["subject"].text, values)
        if "subject" in blocks
        else f"Übernahme der Verwaltung {obj.object_number}, Nachforderung fehlender Unterlagen",
        salutation=blocks["salutation"].text if "salutation" in blocks else "Sehr geehrte Damen und Herren,",
        paragraphs=[
            p
            for p in (
                _fmt(blocks[intro_code].text, values) if intro_code in blocks else "",
                _fmt(list_intro.text, values) if list_intro else "",
            )
            if p
        ],
        groups=[{"title": g["title"], "lines": g["lines"]} for g in ordered],
        attachment=attachment,
        deadline_text=_fmt(blocks["deadline"].text, values)
        if "deadline" in blocks
        else f"Wir bitten um Übersendung bis zum {values['frist']}.",
        closing=_fmt(blocks["closing"].text, values) if "closing" in blocks else "",
        positions=positions,
    )
    return letter, versions


def recipient_from_object(obj: ManagedObject) -> dict:
    return {
        "recipient_name": obj.previous_manager_name or "",
        "recipient_street": obj.previous_manager_street,
        "recipient_house_number": obj.previous_manager_house_number,
        "recipient_postal_code": obj.previous_manager_postal_code,
        "recipient_city": obj.previous_manager_city,
        "recipient_contact_person": obj.previous_manager_contact_person,
        "reference": obj.previous_manager_reference,
    }


def recipient_complete(req: DocumentRequest) -> bool:
    return all(
        (getattr(req, f) or "").strip()
        for f in ("recipient_name", "recipient_street", "recipient_postal_code", "recipient_city")
    )


def request_dir(req: DocumentRequest) -> Path:
    return storage.data_dir() / "requests" / str(req.object_id) / str(req.version)


def _content_hash(req: DocumentRequest) -> str:
    payload = {
        "subject": req.subject,
        "body": req.body,
        "deadline": req.deadline_date.isoformat() if req.deadline_date else None,
        "recipient": [
            req.recipient_name,
            req.recipient_street,
            req.recipient_house_number,
            req.recipient_postal_code,
            req.recipient_city,
            req.recipient_contact_person,
        ],
        "positions": [(p["finding_id"], p["line"]) for p in req.findings_snapshot or []],
        "version": req.version,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def render_files(req: DocumentRequest, *, final: bool, user=None) -> None:
    from apps.requirements import render

    target = request_dir(req)
    target.mkdir(parents=True, exist_ok=True)
    letter = _letter_from_request(req, user=user)
    signature = None
    if final:
        from django.conf import settings

        candidate = Path(str(settings.OBJEKTAKTE.get("HVM_SIGNATURE_PATH") or ""))
        signature = candidate if candidate.is_file() else None
    stamp = req.approved_at.date() if (final and req.approved_at) else timezone.localdate()
    pdf = target / f"nachforderung_{req.object.object_number}_v{req.version}.pdf"
    docx = target / f"nachforderung_{req.object.object_number}_v{req.version}.docx"
    render.render_pdf(req, letter, pdf, final=final, signature=signature, letter_date=stamp)
    render.render_docx(req, letter, docx, final=final, signature=signature, letter_date=stamp)
    req.pdf_path, req.docx_path = str(pdf), str(docx)
    req.file_path = str(pdf)
    req.content_hash = _content_hash(req)
    req.save(update_fields=["pdf_path", "docx_path", "file_path", "content_hash", "updated_at"])


def _letter_from_request(req: DocumentRequest, *, user=None) -> Letter:
    """Brief aus dem Snapshot: der Positionsbestand des Schreibens aendert sich nach der Erzeugung nicht (H 4.3)."""
    snap = req.findings_snapshot or []
    groups: dict[str, dict] = {}
    for p in snap:
        groups.setdefault(p["group"], {"title": p["group"], "lines": []})["lines"].append(p["line"])
    body = (req.custom_text_blocks or {}).get("body") or req.body
    lines = body.split("\n")
    # Absaetze vor der ersten Gruppe, Fristsatz und Schluss aus dem gespeicherten Text
    meta = req.custom_text_blocks or {}
    return Letter(
        subject=req.subject,
        salutation=meta.get("salutation") or (lines[0] if lines else "Sehr geehrte Damen und Herren,"),
        paragraphs=meta.get("paragraphs") or [],
        groups=list(groups.values()),
        attachment=bool(meta.get("attachment")),
        deadline_text=(meta.get("deadline_text") or "").replace(
            DEADLINE_PLACEHOLDER, _fmt_date(req.deadline_date) or DEADLINE_PLACEHOLDER
        ),
        closing=meta.get("closing") or "",
        positions=snap,
    )


def create_request(
    obj: ManagedObject, user, *, deadline: date | None = None, reminder: bool = False, request=None
) -> DocumentRequest:
    items = [i for i in engine.open_items(obj) if i["include_in_request"]]
    if not items:
        raise RequestError("Keine offenen Punkte für eine Nachforderung")
    letter, versions = compose(obj, items, deadline=deadline, user=user, reminder=reminder)
    with transaction.atomic():
        version = (DocumentRequest.objects.filter(object=obj).aggregate(m=Max("version"))["m"] or 0) + 1
        req = DocumentRequest.objects.create(
            object=obj,
            version=version,
            status=RequestStatus.DRAFT,
            subject=letter.subject,
            body=letter.body_text(),
            finding_ids=[p["finding_id"] for p in letter.positions],
            findings_snapshot=letter.positions,
            deadline_date=deadline,
            custom_text_blocks={
                "salutation": letter.salutation,
                "paragraphs": letter.paragraphs,
                "deadline_text": letter.deadline_text,
                "closing": letter.closing,
                "attachment": letter.attachment,
                "reminder": reminder,
            },
            text_block_versions=versions,
            created_by=user,
            **recipient_from_object(obj),
        )
        record(
            "request.create",
            entity_type="document_request",
            entity_id=req.pk,
            object_id=obj.pk,
            request=request,
            actor=user,
            after={
                "version": version,
                "positions": len(letter.positions),
                "deadline": _fmt_date(deadline) or None,
            },
        )
    render_files(req, final=False, user=user)
    return req


def update_request(
    req: DocumentRequest, user, *, deadline: date | None = ..., recipient: dict | None = None, request=None
) -> DocumentRequest:
    if req.status not in (RequestStatus.DRAFT, RequestStatus.REVIEWED):
        raise RequestError("Nur Entwürfe können geändert werden")
    before = {"deadline": _fmt_date(req.deadline_date) or None, "status": req.status}
    if deadline is not ...:
        req.deadline_date = deadline
    for f, v in (recipient or {}).items():
        if f in (
            "recipient_name",
            "recipient_street",
            "recipient_house_number",
            "recipient_postal_code",
            "recipient_city",
            "recipient_contact_person",
            "reference",
        ):
            setattr(req, f, (v or "").strip() or None if f != "recipient_name" else (v or "").strip())
    req.status = RequestStatus.DRAFT  # jede Aenderung setzt die Pruefung zurueck
    req.reviewed_by, req.reviewed_at = None, None
    req.save()
    render_files(req, final=False, user=user)
    record(
        "request.update",
        entity_type="document_request",
        entity_id=req.pk,
        object_id=req.object_id,
        request=request,
        actor=user,
        before=before,
        after={"deadline": _fmt_date(req.deadline_date) or None},
    )
    return req


def mark_reviewed(req: DocumentRequest, user, *, request=None) -> DocumentRequest:
    if req.status != RequestStatus.DRAFT:
        raise RequestError("Nur Entwürfe können geprüft werden")
    if req.deadline_date is None:
        raise RequestError("Frist fehlt; ohne Frist bleibt das Schreiben Entwurf mit Wasserzeichen")
    if not recipient_complete(req):
        raise RequestError("Anschrift der Vorverwaltung unvollständig (Name, Straße, PLZ, Ort)")
    req.status = RequestStatus.REVIEWED
    req.reviewed_by, req.reviewed_at = user, timezone.now()
    req.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    render_files(req, final=False, user=user)
    record(
        "request.review",
        entity_type="document_request",
        entity_id=req.pk,
        object_id=req.object_id,
        request=request,
        actor=user,
    )
    return req


def approve(req: DocumentRequest, user, *, request=None) -> DocumentRequest:
    """Freigabe durch die Geschaeftsfuehrung (Recht demands.approve, Step-up im View): Dateien ohne Wasserzeichen,
    mit Unterschriftsbild soweit hinterlegt, Datum gleich Freigabedatum, content_hash eingefroren."""
    if req.status != RequestStatus.REVIEWED:
        raise RequestError("Freigabe setzt den Status geprüft voraus")
    req.status = RequestStatus.APPROVED
    req.approved_by, req.approved_at = user, timezone.now()
    req.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    render_files(req, final=True, user=user)
    record(
        "request.approve",
        entity_type="document_request",
        entity_id=req.pk,
        object_id=req.object_id,
        request=request,
        actor=user,
        after={"content_hash": req.content_hash, "version": req.version},
    )
    return req


def mark_sent(req: DocumentRequest, user, *, note: str | None = None, request=None) -> DocumentRequest:
    if req.status != RequestStatus.APPROVED:
        raise RequestError("Als versendet kann nur eine freigegebene Fassung vermerkt werden")
    now = timezone.now()
    with transaction.atomic():
        req.status = RequestStatus.SENT
        req.sent_at = now
        req.marked_sent_by = user
        req.sent_channel_note = (note or "").strip() or None
        req.save(update_fields=["status", "sent_at", "marked_sent_by", "sent_channel_note", "updated_at"])
        for f in CompletenessFinding.objects.filter(pk__in=req.finding_ids or []):
            f.details = {
                **(f.details or {}),
                "requested_at": now.date().isoformat(),
                "requested_deadline": req.deadline_date.isoformat() if req.deadline_date else None,
                "request_version": req.version,
            }
            f.save(update_fields=["details", "updated_at"])
        record(
            "request.mark_sent",
            entity_type="document_request",
            entity_id=req.pk,
            object_id=req.object_id,
            request=request,
            actor=user,
            after={"note": req.sent_channel_note, "deadline": _fmt_date(req.deadline_date) or None},
        )
    return req


def withdraw(req: DocumentRequest, user, *, reason: str, request=None) -> DocumentRequest:
    if req.status == RequestStatus.WITHDRAWN:
        raise RequestError("Schreiben ist bereits zurückgezogen")
    if not (reason or "").strip():
        raise RequestError("Grund ist Pflicht")
    req.status = RequestStatus.WITHDRAWN
    req.withdrawn_by, req.withdrawn_at = user, timezone.now()
    req.save(update_fields=["status", "withdrawn_by", "withdrawn_at", "updated_at"])
    record(
        "request.withdraw",
        entity_type="document_request",
        entity_id=req.pk,
        object_id=req.object_id,
        request=request,
        actor=user,
        reason=reason,
    )
    return req
