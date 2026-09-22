"""Objektzuordnung fuer Dokumente des Eingangsobjekts: Datei in den Drive-Eingang spiegeln, Kandidaten mit
Belegen bewerten (apps.sync.assignment), bei eindeutiger Evidenz in das Zielobjekt uebernehmen (transfer_document,
Datei wird verschoben, nie kopiert), sonst Fall Objektzuordnung mit Kandidatenliste. Automatische Zuordnungen
sind keine Lernbeispiele; erst die menschliche Bestaetigung zaehlt."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import Document, DocumentPage
from apps.objects.models import ManagedObject
from apps.review.models import CaseStatus, CaseType, ReviewCase
from apps.sync import config, inbox, services
from apps.sync.flows.common import open_case
from apps.sync.models import (
    AssignmentExample,
    ExampleKind,
    OperationKind,
    OperationStatus,
    SyncOperation,
)

logger = logging.getLogger(__name__)

TEXT_LIMIT = 120_000


class AssignmentError(Exception):
    """Fachlicher Fehler der Zuordnung, der dem Bearbeiter als Hinweis angezeigt wird."""


def _ensure_open(case: ReviewCase | None) -> None:
    """Ein erledigter oder verworfener Fall nimmt keine Entscheidung mehr an (Doppelklick, veraltete Seite); sonst
    wuerde eine spaete Ablehnung einen erledigten Fall umstellen und ein Gegenbeispiel fuer das Lernen erzeugen."""
    if case is not None and case.status in (CaseStatus.RESOLVED, CaseStatus.DISMISSED):
        raise AssignmentError("Fall ist bereits erledigt; zum erneuten Entscheiden bitte wiedereröffnen")


def document_text(doc) -> str:
    parts = (
        DocumentPage.objects.filter(document=doc).order_by("page_no").values_list("text_content", flat=True)
    )
    text = "\n\f".join(p or "" for p in parts)
    return text[:TEXT_LIMIT]


def _to_plain(value):
    if dataclasses.is_dataclass(value):
        return {k: _to_plain(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, list | tuple):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    return value


def candidate_objects():
    return ManagedObject.active.filter(is_system_inbox=False).exclude(status="archived")


def mirror_to_inbox_folder(doc, drive) -> str | None:
    """Datei ohne Drive-Kopie (aus Paperless oder Upload) in den geschuetzten Eingangsordner legen; vorhandene Datei
    gleichen Hashs wird wiederverwendet (Idempotenz ueber appProperties.sha256)."""
    folder_id = inbox.inbox_folder_id()
    if not folder_id or doc.drive_file_id:
        return doc.drive_file_id
    try:
        existing = next(
            (
                c
                for c in drive.list_children(folder_id)
                if not c.is_folder and (c.app_properties or {}).get("sha256") == doc.sha256
            ),
            None,
        )
        if existing is None:
            with services.local_copy(doc) as path:
                existing = drive.upload(
                    folder_id,
                    path,
                    doc.current_name or doc.original_name or path.name,
                    doc.mime_type or "application/octet-stream",
                    app_properties={
                        "sha256": doc.sha256 or "",
                        "document_id": str(doc.pk),
                        "object_id": str(doc.object_id),
                        "mhv_uuid": str(doc.uuid),
                    },
                )
    except Exception:  # Spiegelung ist Sicherung, nicht Voraussetzung der Zuordnung
        logger.exception("Spiegelung in den Eingangsordner fehlgeschlagen (Dokument %s)", doc.pk)
        return None
    Document.objects.filter(pk=doc.pk).update(drive_file_id=existing.id, drive_md5=existing.md5)
    doc.drive_file_id = existing.id
    record(
        "drive.upload",
        entity_type="document",
        entity_id=doc.pk,
        object_id=doc.object_id,
        after={"drive_file_id": existing.id, "folder": "eingang"},
    )
    return existing.id


def _hints_for(doc) -> dict:
    """Titel und Korrespondent aus den Paperless-Verknuepfungen als Zusatzsignale."""
    hints = {}
    for link in doc.sync_links.all():
        fields = link.synced_fields or {}
        if fields.get("title"):
            hints["title"] = fields["title"]
        if fields.get("correspondent"):
            hints["correspondent"] = fields["correspondent"]
    return hints


def document_features(doc, *, text: str | None = None) -> dict:
    """Merkmale eines Dokuments fuer Lernbeispiele und Regeln (Lieferant aus dem Korrespondenten, Nummernpaare,
    Adressen). Ohne den Korrespondenten entsteht keine Merkmalskombination und damit nie eine Regel."""
    from apps.sync.assignment import candidates as cand

    text = document_text(doc) if text is None else text
    index = cand.build_index(list(candidate_objects()))
    return _to_plain(
        cand.extract_features(
            text,
            filename=doc.current_name or doc.original_name or "",
            correspondent=_hints_for(doc).get("correspondent"),
            index=index,
        )
    )


def _carry_over_operations(old_doc, new_doc) -> int:
    """Wartende Operationen der Eingangszeile (etwa die Uebertragung nach Paperless) folgen der Nachfolgezeile.
    Sonst wuerden sie am Status moved_out uebersprungen und das Dokument im Zielobjekt nie uebertragen."""
    ids = list(
        SyncOperation.objects.filter(document=old_doc, status=OperationStatus.PENDING).values_list(
            "pk", flat=True
        )
    )
    if ids and not config.writes_allowed(new_doc.object):
        # Zielobjekt ausserhalb von Modus oder Pilotumfang: die Uebertragung entfaellt wie bei einem direkten
        # Upload dorthin; eine blockierte Operation waere kein Fehler, sondern nur Rauschen in der Warteschlange
        SyncOperation.objects.filter(pk__in=ids, kind=OperationKind.PAPERLESS_PUSH).update(
            status=OperationStatus.CANCELLED,
            blocked_reason="Zielobjekt außerhalb von Modus oder Pilotumfang",
            finished_at=timezone.now(),
        )
    return SyncOperation.objects.filter(pk__in=ids).update(document=new_doc) if ids else 0


def build_proposal(doc, *, text: str | None = None):
    from apps.sync.assignment import candidates as cand
    from apps.sync.assignment import decide as dec
    from apps.sync.assignment.learning import active_rules

    text = document_text(doc) if text is None else text
    hints = _hints_for(doc)
    index = cand.build_index(list(candidate_objects()))
    ranked = cand.score_candidates(
        text,
        filename=doc.current_name or doc.original_name or "",
        index=index,
        folder_object_id=None,
        own_addresses=None,
        extra_hints=hints or None,
        rules=list(active_rules()),
    )
    auto_min, gap_min = config.assignment_thresholds()
    proposal = dec.decide(ranked, dec.Thresholds(auto_min=auto_min, gap_min=gap_min))
    if proposal.decision == "auto" and proposal.chosen is not None:
        if proposal.chosen.object_id in _rejected_object_ids(doc):
            proposal.decision = "review"
            proposal.reasons = [r for r in proposal.reasons if r != "automatische Zuordnung"] + [
                f"Objekt {proposal.chosen.object_number} wurde für dieses Dokument bereits manuell verworfen; "
                "keine erneute automatische Zuordnung"
            ]
    return proposal, ranked


def _open_assignment_case(doc) -> ReviewCase | None:
    return (
        ReviewCase.objects.filter(
            document=doc,
            case_type=CaseType.OBJECT_ASSIGNMENT,
            status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        )
        .order_by("-id")
        .first()
    )


def _rejected_object_ids(doc) -> set[int]:
    """Objekte, die fuer dieses Dokument bereits manuell verworfen wurden (Ablehnung im Pruefcenter oder Herausnahme
    ueber den Feldabgleich Paperless). Ein einziger Adresstreffer im Text (Objektadresse als Fahrtziel einer
    Fahrtkostenabrechnung) wuerde sonst dasselbe Objekt sofort wieder automatisch waehlen."""
    return set(
        AssignmentExample.objects.filter(
            document=doc, kind=ExampleKind.REJECT, proposed_object__isnull=False
        ).values_list("proposed_object_id", flat=True)
    )


def _assign_auto(
    doc,
    target: ManagedObject,
    *,
    proposal,
    plain: list[dict],
    mirrored,
    subtype: str,
    reason: str,
    audit_action: str,
    context_extra: dict | None = None,
) -> Document:
    """Uebernahme ohne Menschen (Regelwerk oder KI): Dokument in das Zielobjekt, erledigter Fall mit Belegen und
    Grund, Audit. Automatische Zuordnungen sind keine Lernbeispiele."""
    from apps.documents.transfer import transfer_document

    new_doc = transfer_document(doc, target, reason=reason[:400])
    _carry_over_operations(doc, new_doc)
    score = next(
        (c.score for c in proposal.ranked if c.object_id == target.pk),
        proposal.chosen.score if proposal.chosen else 0.0,
    )
    ReviewCase.objects.create(
        object=target,
        case_type=CaseType.OBJECT_ASSIGNMENT,
        case_subtype=subtype,
        document=new_doc,
        status=CaseStatus.RESOLVED,
        batch_key=f"object_assignment:{subtype}:{new_doc.pk}",
        priority=90,
        candidates=plain,
        context={
            "reasons": proposal.reasons,
            "score": score,
            "from_document_id": doc.pk,
            **(context_extra or {}),
        },
        resolution={
            "action": "assign_object_auto" if subtype == "auto" else "assign_object_ai",
            "object_id": target.pk,
            "at": timezone.now().isoformat(),
        },
        resolved_at=timezone.now(),
    )
    top = next((c for c in plain if c.get("object_id") == target.pk), plain[0] if plain else {})
    record(
        audit_action,
        entity_type="document",
        entity_id=new_doc.pk,
        object_id=target.pk,
        after={
            "from_document_id": doc.pk,
            "score": score,
            "reasons": proposal.reasons[:5],
            "evidence": [e.get("text", "")[:80] for e in (top.get("evidence") or [])][:5],
            **{
                k: v for k, v in (context_extra or {}).get("ai", {}).items() if k in ("confidence", "call_id")
            },
        },
    )
    return new_doc


def run_for_document(doc, *, job=None) -> dict:
    """Ablauf: Datei spiegeln, Kandidaten bewerten. Eindeutig (auto) -> Uebernahme. Sonst greift der
    KI-Schiedsrichter, sobald es Kandidaten gibt (Entscheidung 22.09.2026): sicherer Kandidat -> Uebernahme
    (ai_auto), kein Objektdokument -> Fall ai_not_object im Eingang, sonst Pruefall mit KI-Einschaetzung. Ein fuer
    dieses Dokument manuell verworfenes Objekt wird weder vom Regelwerk noch von der KI automatisch gewaehlt."""
    drive = services.get_drive()
    mirrored = mirror_to_inbox_folder(doc, drive) if drive is not None else None
    text = document_text(doc)
    proposal, ranked = build_proposal(doc, text=text)
    plain = [_to_plain(c) for c in ranked[:5]]
    try:
        features = document_features(doc, text=text)
    except Exception:  # Merkmale dienen dem Lernen, nicht der Entscheidung
        logger.exception("Merkmalsextraktion fehlgeschlagen (Dokument %s)", doc.pk)
        features = {}
    if proposal.decision == "auto" and proposal.chosen is not None:
        target = ManagedObject.active.filter(pk=proposal.chosen.object_id).first()
        if target is not None:
            new_doc = _assign_auto(
                doc,
                target,
                proposal=proposal,
                plain=plain,
                mirrored=mirrored,
                subtype="auto",
                reason=f"automatische Objektzuordnung ({proposal.chosen.score:.2f}): "
                + "; ".join(proposal.reasons)[:200],
                audit_action="inbox.assign_auto",
            )
            return {
                "decision": "auto",
                "object_id": target.pk,
                "document_id": new_doc.pk,
                "mirrored": mirrored,
            }
    ai = None
    existing_case = _open_assignment_case(doc)
    if ranked:
        from apps.ai import assignment as arbiter

        vorhanden = ((existing_case.context or {}).get("ai") if existing_case else None) or {}
        if vorhanden.get("status") == "ok":
            # Einschaetzung aus der Gegenprobe des Feldimports: dasselbe Dokument wird nicht erneut angefragt
            ai = arbiter.ArbiterOutcome.from_context(vorhanden)
        else:
            try:
                ai = arbiter.arbitrate(doc, ranked, text=text, job=job)
            except Exception:  # die KI darf die Zuordnung nie scheitern lassen; der Pruefall bleibt
                logger.exception("KI-Schiedsrichter fehlgeschlagen (Dokument %s)", doc.pk)
                ai = arbiter.ArbiterOutcome("provider_error", message="unerwarteter Fehler, siehe Log")
    ai_context = {"ai": ai.to_context()} if ai is not None else {}
    proposed_id = proposal.chosen.object_id if proposal.chosen else None
    if ai is not None and ai.status == "ok":
        from apps.ai import assignment as arbiter

        if ai.is_object_document and ai.object_id and ai.confidence >= arbiter.min_confidence():
            if ai.object_id in _rejected_object_ids(doc):
                proposal.reasons.append(
                    f"KI wählt Objekt {ai.object_number}, das für dieses Dokument bereits manuell verworfen "
                    "wurde; nur Vorschlag"
                )
                proposed_id = ai.object_id
            else:
                target = ManagedObject.active.filter(pk=ai.object_id).first()
                if target is not None:
                    new_doc = _assign_auto(
                        doc,
                        target,
                        proposal=proposal,
                        plain=plain,
                        mirrored=mirrored,
                        subtype="ai_auto",
                        reason=f"KI-Zuordnung ({ai.confidence:.2f}): {ai.reasoning or ''}",
                        audit_action="inbox.assign_ai",
                        context_extra=ai_context,
                    )
                    return {
                        "decision": "ai_auto",
                        "object_id": target.pk,
                        "document_id": new_doc.pk,
                        "mirrored": mirrored,
                        "ai_call_id": ai.call_id,
                    }
        elif ai.is_object_document is False:
            case = open_case(
                doc.object,
                case_type=CaseType.OBJECT_ASSIGNMENT,
                subtype="ai_not_object",
                key=f"object_assignment:{doc.pk}",
                document=doc,
                context={
                    "reasons": proposal.reasons,
                    "decision": proposal.decision,
                    "mirrored": bool(mirrored),
                    "features": features,
                    **ai_context,
                },
                candidates=plain,
                proposed_action=None,
                priority=50,
            )
            return {
                "decision": "ai_not_object",
                "case_id": case.pk if case else (existing_case.pk if existing_case else None),
                "candidates": len(ranked),
                "mirrored": mirrored,
                "ai_call_id": ai.call_id,
            }
        elif ai.object_id:
            proposed_id = ai.object_id  # KI-Vorschlag unter der Mindestkonfidenz: als Vorschlag im Pruefall
    subtype = "proposal" if ranked else "no_candidate"
    case = open_case(
        doc.object,
        case_type=CaseType.OBJECT_ASSIGNMENT,
        subtype=subtype,
        key=f"object_assignment:{doc.pk}",
        document=doc,
        context={
            "reasons": proposal.reasons,
            "decision": proposal.decision,
            "mirrored": bool(mirrored),
            "features": features,
            **ai_context,
        },
        candidates=plain,
        proposed_action={"action": "assign_object", "object_id": proposed_id} if proposed_id else None,
        priority=70,
    )
    return {
        "decision": proposal.decision,
        "case_id": case.pk if case else None,
        "candidates": len(ranked),
        "mirrored": mirrored,
        **({"ai_status": ai.status} if ai is not None else {}),
    }


def apply_assignment(
    doc, target: ManagedObject, *, user, case: ReviewCase | None = None, request=None, reason: str = ""
) -> Document:
    """Manuelle Zuordnung aus dem Dokumenteneingang: Uebernahme in das Zielobjekt und Lernbeispiel."""
    from apps.documents.transfer import transfer_document
    from apps.sync.assignment import learning

    _ensure_open(case)
    proposed_id = None
    if case is not None and case.proposed_action:
        proposed_id = case.proposed_action.get("object_id")
    kind = "confirm" if proposed_id == target.pk else "correct"
    features = {}
    evidence = []
    if case is not None and case.candidates:
        first = next((c for c in case.candidates if c.get("object_id") == target.pk), None)
        if first:
            evidence = first.get("evidence") or []
        features = (case.context or {}).get("features") or {}
    if not features:
        try:
            features = document_features(doc)
        except Exception:  # Merkmalsextraktion ist fuer das Beispiel hilfreich, aber nicht Voraussetzung
            features = {}
    new_doc = transfer_document(
        doc, target, user=user, request=request, reason=reason or "Zuordnung im Dokumenteneingang"
    )
    _carry_over_operations(doc, new_doc)
    learning.record_example(
        new_doc,
        kind=kind,
        previous_object=doc.object if not doc.object.is_system_inbox else None,
        target_object=target,
        proposed_object=ManagedObject.objects.filter(pk=proposed_id).first() if proposed_id else None,
        user=user,
        features=features,
        evidence=evidence,
        review_case=case,
        source="human",
    )
    if case is not None:
        case.status = CaseStatus.RESOLVED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution = {"action": "assign_object", "object_id": target.pk, "new_document_id": new_doc.pk}
        case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
    record(
        "inbox.assign",
        entity_type="document",
        entity_id=new_doc.pk,
        object_id=target.pk,
        request=request,
        actor=user,
        after={"from_document_id": doc.pk, "kind": kind, "proposed_object_id": proposed_id},
    )
    return new_doc


def reject_proposal(case: ReviewCase, *, user, request=None, reason: str = "") -> None:
    from apps.sync.assignment import learning

    _ensure_open(case)
    doc = case.document
    proposed_id = (case.proposed_action or {}).get("object_id")
    if doc is not None and proposed_id:
        learning.record_example(
            doc,
            kind="reject",
            previous_object=None,
            target_object=None,
            proposed_object=ManagedObject.objects.filter(pk=proposed_id).first(),
            user=user,
            features=(case.context or {}).get("features") or {},
            evidence=[],
            review_case=case,
            source="human",
        )
    case.status = CaseStatus.DISMISSED
    case.resolved_by = user
    case.resolved_at = timezone.now()
    case.resolution = {"action": "reject", "reason": reason}
    case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
    record(
        "inbox.reject",
        entity_type="review_case",
        entity_id=case.pk,
        object_id=case.object_id,
        request=request,
        actor=user,
        after={"proposed_object_id": proposed_id, "reason": reason},
    )


def filename_for(doc) -> str:
    return Path(doc.current_name or doc.original_name or "").name
