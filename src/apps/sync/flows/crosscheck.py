"""Gegenprobe der Objektzuordnung fuer Dokumente, die ueber das Feld MHV Objekt aus Paperless in ein Objekt kamen
(Entscheidung 22.09.2026: nur eindeutige Dokumente bleiben automatisch im Objekt). Bisher gewann das Feld ohne
Pruefung; so kamen bei Pilot 82 Fahrtkostenabrechnungen mit der Objektadresse als Fahrtziel in die Akte.

Analyse nach der Texterkennung mit der Kandidatenbewertung des Eingangs:
- eindeutig: nur das Feldobjekt hat einen klaren Bezug im Text (Anschrift als Objekt oder neutral, Objektnummer,
  Regel); weitere Anschriften desselben Gebaeudes (Eckobjekt) zaehlen dazu
- zweiter_bezug: ein anderes bekanntes Objekt kommt im Text vor (Anschrift in jeder Rolle ausser Lieferant,
  Objektnummer, Dateiname)
- schwacher_bezug: das Feldobjekt kommt nur beilaeufig vor (Fahrtziel, Rechnungs- oder Eigentuemeranschrift)
- kein_bezug: der Text nennt das Feldobjekt gar nicht (haeufig schwache Scans); nur Meldung, keine Aktion

Aufloesung fuer zweiter_bezug und schwacher_bezug: KI-Schiedsrichter mit dem Feldobjekt als Kandidat. Sicher
dasselbe Objekt -> bestaetigt; sicher ein anderes -> dorthin uebernommen; kein Objektdokument -> Eingang mit Fall
ai_not_object; sonst bleibt das Dokument, wenn die lokale Regel das Feldobjekt eindeutig waehlen wuerde, andernfalls
Eingang mit Pruefall (die KI-Einschaetzung wandert mit, kein zweiter Aufruf). Jede Pruefung setzt
assignment_checked_at und schreibt Audit sync.field_crosscheck. Live greift die Gegenprobe im Entscheidungsschritt
der Pipeline fuer Dokumente, die ab LIVE_SINCE angelegt wurden; der Bestand laeuft ueber das Kommando
paperless_zuordnung_pruefen (Vorschau ohne KI, echt mit KI)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import Document, DocumentSource
from apps.objects.models import ManagedObject
from apps.review.models import CaseType
from apps.sync import inbox
from apps.sync.assignment.candidates import Candidate
from apps.sync.assignment.evidence import Evidence
from apps.sync.flows import assign
from apps.sync.flows.common import open_case

logger = logging.getLogger(__name__)

LIVE_SINCE = datetime(2026, 9, 22, tzinfo=UTC)
FIELD_WEIGHT = 0.5
OBJECT_KINDS = ("address", "object_number", "filename_number", "filename_address", "folder", "rule")
STRONG_KINDS = ("object_number", "filename_number", "folder", "rule")
STRONG_ADDRESS_ROLES = ("object", "neutral")
KINDS = ("eindeutig", "zweiter_bezug", "schwacher_bezug", "kein_bezug")
AMBIGUOUS = ("zweiter_bezug", "schwacher_bezug")
# KI-Status, die einen technischen Ausfall statt einer fachlichen Einschaetzung bedeuten; ein Sammellauf ueberspringt
# das Dokument dann (bleibt ungeprueft, naechster Lauf), statt es ohne Urteil in den Eingang zu schieben
AI_UNAVAILABLE = ("disabled", "provider_error", "budget_blocked", "blocked_by_mask_check", "error")


@dataclass
class CrossCheck:
    kind: str
    proposal: object
    ranked: list[Candidate]
    own_id: int
    text: str
    others: list[Candidate] = field(default_factory=list)

    @property
    def local_unique(self) -> bool:
        """Die lokale Regel des Eingangs wuerde das Feldobjekt automatisch waehlen."""
        p = self.proposal
        return p.decision == "auto" and p.chosen is not None and p.chosen.object_id == self.own_id

    @property
    def other_numbers(self) -> list[str]:
        return [c.object_number for c in self.others][:5]


def _any_object_evidence(c: Candidate) -> bool:
    return any(e.kind in OBJECT_KINDS and e.weight > 0 and e.role != "supplier" for e in c.evidence)


def _strong_object_evidence(c: Candidate) -> bool:
    for e in c.evidence:
        if e.weight <= 0:
            continue
        if e.kind in STRONG_KINDS or (e.kind == "address" and e.role in STRONG_ADDRESS_ROLES):
            return True
    return False


def applies(doc, *, live: bool = False) -> bool:
    """Nur Dokumente aus Paperless in einem echten Objekt, noch ungeprueft; live nur fuer Dokumente ab LIVE_SINCE
    (der Bestand laeuft ueber das Kommando, damit ein Nachlauf der Pipeline keine Massenaufrufe ausloest)."""
    if doc.source != DocumentSource.PAPERLESS or doc.assignment_checked_at is not None:
        return False
    if doc.status in ("moved_out", "duplicate") or inbox.is_inbox(doc.object):
        return False
    if live and (doc.created_at is None or doc.created_at < LIVE_SINCE):
        return False
    return True


def analyse(doc, *, text: str | None = None) -> CrossCheck:
    text = assign.document_text(doc) if text is None else text
    proposal, ranked = assign.build_proposal(doc, text=text)
    own_id = doc.object_id
    others = [c for c in ranked if c.object_id != own_id and _any_object_evidence(c)]
    own = next((c for c in ranked if c.object_id == own_id), None)
    if others:
        kind = "zweiter_bezug"
    elif own is None or not _any_object_evidence(own):
        kind = "kein_bezug"
    elif not _strong_object_evidence(own):
        kind = "schwacher_bezug"
    else:
        kind = "eindeutig"
    return CrossCheck(kind, proposal, ranked, own_id, text, others)


def candidates_with_field(doc, ranked: list[Candidate]) -> list[Candidate]:
    """Kandidaten fuer die KI: das Feldobjekt steht immer an erster Stelle mit dem Beleg paperless_field."""
    obj = doc.object
    own = next((c for c in ranked if c.object_id == obj.pk), None)
    if own is None:
        own = Candidate(
            object_id=obj.pk,
            object_number=obj.object_number,
            score=FIELD_WEIGHT,
            management_type=obj.management_type,
        )
    if not any(e.kind == "paperless_field" for e in own.evidence):
        own.evidence.append(
            Evidence(
                kind="paperless_field",
                text="Feld MHV Objekt in Paperless",
                weight=FIELD_WEIGHT,
                role="field",
                object_id=obj.pk,
            )
        )
    return [own, *[c for c in ranked if c.object_id != obj.pk]]


def mark(doc, check: CrossCheck, action: str, ai_ctx: dict | None = None, extra: dict | None = None) -> None:
    now = timezone.now()
    Document.objects.filter(pk=doc.pk).update(assignment_checked_at=now)
    doc.assignment_checked_at = now
    ai = (
        {k: ai_ctx.get(k) for k in ("status", "object_number", "is_object_document", "confidence", "call_id")}
        if ai_ctx
        else None
    )
    record(
        "sync.field_crosscheck",
        entity_type="document",
        entity_id=doc.pk,
        object_id=doc.object_id,
        after={
            "kind": check.kind,
            "others": check.other_numbers,
            "action": action,
            "local_unique": check.local_unique,
            "ai": ai,
            **(extra or {}),
        },
    )


def _to_inbox(
    doc, check: CrossCheck, plain: list[dict], ai_ctx: dict, *, subtype: str, proposed, reason: str
):
    from apps.documents.transfer import transfer_document

    ziel = inbox.ensure_inbox_object()
    new = transfer_document(doc, ziel, reason=reason[:400])
    assign._carry_over_operations(doc, new)
    Document.objects.filter(pk=new.pk).update(assignment_checked_at=timezone.now())
    open_case(
        ziel,
        case_type=CaseType.OBJECT_ASSIGNMENT,
        subtype=subtype,
        key=f"object_assignment:{new.pk}",
        document=new,
        context={
            "reasons": [reason],
            "decision": check.proposal.decision,
            "mirrored": False,
            "crosscheck": {
                "kind": check.kind,
                "from_object": doc.object.object_number,
                "others": check.other_numbers,
            },
            "ai": ai_ctx,
        },
        candidates=plain,
        proposed_action={"action": "assign_object", "object_id": proposed} if proposed else None,
        priority=60,
    )
    return new


def resolve(doc, check: CrossCheck, *, job=None, use_ai: bool = True, skip_on_ai_error: bool = False) -> dict:
    """Aufloesung eines nicht eindeutigen Falls. action: bestaetigt | umgehaengt | eingang | belassen; mit
    skip_on_ai_error zusaetzlich uebersprungen (KI technisch nicht verfuegbar, Dokument bleibt ungeprueft)."""
    from apps.ai import assignment as arbiter
    from apps.sync.assignment import decide as dec

    candidates = candidates_with_field(doc, list(check.ranked))
    plain = [assign._to_plain(c) for c in candidates[:5]]
    if use_ai:
        ai = arbiter.arbitrate(doc, candidates, text=check.text, job=job)
    else:
        ai = arbiter.ArbiterOutcome("disabled", message="KI in diesem Lauf abgeschaltet (ohne-ki)")
    ai_ctx = ai.to_context()
    result = {
        "document_id": doc.pk,
        "kind": check.kind,
        "others": check.other_numbers,
        "ai_status": ai.status,
        "local_unique": check.local_unique,
    }
    if use_ai and skip_on_ai_error and ai.status in AI_UNAVAILABLE:
        return {**result, "action": "uebersprungen", "message": ai.message or ai.status}
    grund = f"Gegenprobe Feldimport ({check.kind}): " + (
        ", ".join(f"Objekt {n}" for n in check.other_numbers[:3])
        or "nur beiläufiger Bezug auf das Feldobjekt"
    )
    if (
        ai.status == "ok"
        and ai.is_object_document
        and ai.object_id
        and ai.confidence >= arbiter.min_confidence()
    ):
        if ai.object_id == doc.object_id:
            mark(doc, check, "bestaetigt", ai_ctx)
            return {**result, "action": "bestaetigt"}
        target = ManagedObject.active.filter(pk=ai.object_id, is_system_inbox=False).first()
        if target is not None:
            proposal = dec.Proposal("review", None, candidates, [grund])
            new = assign._assign_auto(
                doc,
                target,
                proposal=proposal,
                plain=plain,
                mirrored=None,
                subtype="ai_auto",
                reason=f"KI-Gegenprobe Feldimport ({ai.confidence:.2f}): {ai.reasoning or ''}",
                audit_action="inbox.assign_ai",
                context_extra={
                    "ai": ai_ctx,
                    "crosscheck": {"kind": check.kind, "from_object": doc.object.object_number},
                },
            )
            Document.objects.filter(pk=new.pk).update(assignment_checked_at=timezone.now())
            mark(
                doc, check, "umgehaengt", ai_ctx, {"target": target.object_number, "new_document_id": new.pk}
            )
            return {
                **result,
                "action": "umgehaengt",
                "target": target.object_number,
                "new_document_id": new.pk,
            }
    if ai.status == "ok" and ai.is_object_document is False:
        new = _to_inbox(
            doc,
            check,
            plain,
            ai_ctx,
            subtype="ai_not_object",
            proposed=None,
            reason=f"{grund}; laut KI kein Objektdokument ({ai.confidence:.2f})",
        )
        mark(doc, check, "eingang", ai_ctx, {"new_document_id": new.pk})
        return {**result, "action": "eingang", "new_document_id": new.pk}
    if check.local_unique:
        # ohne sichere KI-Antwort entscheidet die lokale Regel: sie wuerde das Feldobjekt ohnehin waehlen
        mark(doc, check, "belassen", ai_ctx)
        return {**result, "action": "belassen"}
    if ai.status == "ok" and ai.object_id:
        proposed = ai.object_id
    else:
        proposed = check.proposal.chosen.object_id if check.proposal.chosen else None
    new = _to_inbox(
        doc,
        check,
        plain,
        ai_ctx,
        subtype="proposal",
        proposed=proposed,
        reason=f"{grund}; keine sichere Entscheidung ({ai.summary_text()[:120]})",
    )
    mark(doc, check, "eingang", ai_ctx, {"new_document_id": new.pk})
    return {**result, "action": "eingang", "new_document_id": new.pk}


def run(doc, *, job=None, use_ai: bool = True, text: str | None = None) -> dict:
    """Analyse und Aufloesung fuer ein Dokument; eindeutig und kein_bezug werden nur markiert."""
    check = analyse(doc, text=text)
    if check.kind not in AMBIGUOUS:
        action = "ok" if check.kind == "eindeutig" else "kein_bezug"
        mark(doc, check, action)
        return {"document_id": doc.pk, "kind": check.kind, "others": [], "action": action}
    return resolve(doc, check, job=job, use_ai=use_ai)
