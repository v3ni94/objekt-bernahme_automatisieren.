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

logger = logging.getLogger(__name__)

TEXT_LIMIT = 120_000


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


def build_proposal(doc, *, text: str | None = None):
    from apps.sync.assignment import candidates as cand
    from apps.sync.assignment import decide as dec
    from apps.sync.assignment.learning import active_rules

    text = document_text(doc) if text is None else text
    hints = {}
    for link in doc.sync_links.all():
        fields = link.synced_fields or {}
        if fields.get("title"):
            hints["title"] = fields["title"]
        if fields.get("correspondent"):
            hints["correspondent"] = fields["correspondent"]
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
    return proposal, ranked


def run_for_document(doc, *, job=None) -> dict:
    drive = services.get_drive()
    mirrored = mirror_to_inbox_folder(doc, drive) if drive is not None else None
    proposal, ranked = build_proposal(doc)
    plain = [_to_plain(c) for c in ranked[:5]]
    if proposal.decision == "auto" and proposal.chosen is not None:
        target = ManagedObject.active.filter(pk=proposal.chosen.object_id).first()
        if target is not None:
            from apps.documents.transfer import transfer_document

            new_doc = transfer_document(
                doc,
                target,
                reason=f"automatische Objektzuordnung ({proposal.chosen.score:.2f}): "
                + "; ".join(proposal.reasons)[:200],
            )
            ReviewCase.objects.create(
                object=target,
                case_type=CaseType.OBJECT_ASSIGNMENT,
                case_subtype="auto",
                document=new_doc,
                status=CaseStatus.RESOLVED,
                batch_key=f"object_assignment:auto:{new_doc.pk}",
                priority=90,
                candidates=plain,
                context={
                    "reasons": proposal.reasons,
                    "score": proposal.chosen.score,
                    "from_document_id": doc.pk,
                },
                resolution={
                    "action": "assign_object_auto",
                    "object_id": target.pk,
                    "at": timezone.now().isoformat(),
                },
                resolved_at=timezone.now(),
            )
            record(
                "inbox.assign_auto",
                entity_type="document",
                entity_id=new_doc.pk,
                object_id=target.pk,
                after={
                    "from_document_id": doc.pk,
                    "score": proposal.chosen.score,
                    "reasons": proposal.reasons[:5],
                    "evidence": [e.get("text", "")[:80] for e in (plain[0].get("evidence") or [])][:5],
                },
            )
            return {
                "decision": "auto",
                "object_id": target.pk,
                "document_id": new_doc.pk,
                "mirrored": mirrored,
            }
    subtype = "proposal" if ranked else "no_candidate"
    case = open_case(
        doc.object,
        case_type=CaseType.OBJECT_ASSIGNMENT,
        subtype=subtype,
        key=f"object_assignment:{doc.pk}",
        document=doc,
        context={"reasons": proposal.reasons, "decision": proposal.decision, "mirrored": bool(mirrored)},
        candidates=plain,
        proposed_action={"action": "assign_object", "object_id": proposal.chosen.object_id}
        if proposal.chosen
        else None,
        priority=70,
    )
    return {
        "decision": proposal.decision,
        "case_id": case.pk if case else None,
        "candidates": len(ranked),
        "mirrored": mirrored,
    }


def apply_assignment(
    doc, target: ManagedObject, *, user, case: ReviewCase | None = None, request=None, reason: str = ""
) -> Document:
    """Manuelle Zuordnung aus dem Dokumenteneingang: Uebernahme in das Zielobjekt und Lernbeispiel."""
    from apps.documents.transfer import transfer_document
    from apps.sync.assignment import learning

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
            from apps.sync.assignment import candidates as cand

            features = _to_plain(cand.extract_features(document_text(doc), filename=doc.current_name or ""))
        except Exception:  # Merkmalsextraktion ist fuer das Beispiel hilfreich, aber nicht Voraussetzung
            features = {}
    new_doc = transfer_document(
        doc, target, user=user, request=request, reason=reason or "Zuordnung im Dokumenteneingang"
    )
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
