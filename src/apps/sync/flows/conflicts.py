"""Aufloesung von Abgleichskonflikten (Review-Faelle sync_conflict) durch Sachbearbeiter oder Admin. Keine Regel
„der neueste Zeitstempel gewinnt“: jede Entscheidung ist ausdruecklich, protokolliert und wirkt nur auf die
Verknuepfung oder auf eine neue Operation. Loeschungen werden nie gespiegelt; eine bestaetigte Loeschung setzt
eine dauerhafte Loeschmarkierung (Tombstone), damit die Gegenkopie nicht erneut importiert wird."""

from __future__ import annotations

from django.utils import timezone

from apps.audit.services import record
from apps.review.models import CaseStatus, ReviewCase
from apps.sync import config
from apps.sync.flows.common import DRIVE, PAPERLESS, SYNCED, link_for, mark_link
from apps.sync.models import LinkState, OperationKind, SyncSystem
from apps.sync.operations import enqueue, op_key

CHOICES = {
    "keep_local": "lokalen Stand behalten (Gegenseite als bekannt übernehmen)",
    "apply_remote_object": "Objektzuordnung aus Paperless übernehmen",
    "push_local_object": "eigene Zuordnung nach Paperless zurückschreiben",
    "tombstone": "Löschung bestätigen (keine erneute Übernahme)",
    "reupload": "Datei erneut nach Paperless übertragen",
}


class ConflictError(Exception):
    pass


def resolve(case: ReviewCase, choice: str, *, user, request=None, reason: str = "") -> dict:
    if case.status in (CaseStatus.RESOLVED, CaseStatus.DISMISSED):
        raise ConflictError("Fall ist bereits erledigt")
    if choice not in CHOICES:
        raise ConflictError("unbekannte Entscheidung")
    if choice not in {key for key, _ in choices_for(case)}:
        raise ConflictError("Entscheidung für diese Konfliktart nicht zulässig")
    doc = case.document
    ctx = case.context or {}
    subtype = case.case_subtype or ""
    result: dict = {"choice": choice}
    if doc is None:
        raise ConflictError("Fall ohne Dokument")
    system = DRIVE if subtype.startswith("drive_") else PAPERLESS
    link = link_for(doc, system)
    if choice == "keep_local":
        if link is not None:
            fields = dict(link.synced_fields or {})
            if ctx.get("new_remote_checksum"):
                fields["accepted_remote_checksum"] = ctx["new_remote_checksum"]
                mark_link(
                    link,
                    SYNCED,
                    "Konflikt: lokaler Stand behalten",
                    synced_fields=fields,
                    checksum_sha256=ctx["new_remote_checksum"]
                    if system == PAPERLESS
                    else link.checksum_sha256,
                    last_synced_at=timezone.now(),
                )
            elif link.state in (LinkState.TRASHED, LinkState.MISSING):
                mark_link(link, link.state, "Konflikt gesehen, lokal behalten", last_synced_at=timezone.now())
            else:
                mark_link(link, SYNCED, "Konflikt: lokaler Stand behalten", last_synced_at=timezone.now())
    elif choice == "tombstone":
        if link is None:
            raise ConflictError("keine Verknüpfung, nichts zu bestätigen")
        mark_link(
            link,
            LinkState.TOMBSTONE,
            reason or "Löschung bestätigt",
            tombstone_at=timezone.now(),
            tombstone_by_id=getattr(user, "pk", None),
        )
        record(
            "sync.tombstone",
            entity_type="document",
            entity_id=doc.pk,
            object_id=doc.object_id,
            request=request,
            actor=user,
            after={"system": system, "external_id": link.external_id, "reason": reason},
        )
    elif choice == "apply_remote_object":
        from apps.objects.models import ManagedObject
        from apps.sync.flows.assign import apply_assignment

        target = ManagedObject.active.filter(pk=ctx.get("remote_object_id")).first()
        if target is None:
            raise ConflictError("Zielobjekt aus Paperless nicht gefunden")
        new_doc = apply_assignment(
            doc, target, user=user, case=None, request=request, reason="Zuordnung aus Paperless übernommen"
        )
        result["new_document_id"] = new_doc.pk
        if link is not None:
            mark_link(link, SYNCED, "Objektzuordnung aus Paperless übernommen")
    elif choice == "push_local_object":
        if not config.writes_allowed(doc.object):
            raise ConflictError(
                "Schreiben nach Paperless für dieses Objekt nicht erlaubt (Modus oder Pilotumfang)"
            )
        if link is not None:
            fields = dict(link.synced_fields or {})
            fields["app_fields"] = {}  # erzwingt das Zurueckschreiben
            mark_link(link, SYNCED, "eigene Zuordnung zurückgeschrieben", synced_fields=fields)
        enqueue(
            OperationKind.PAPERLESS_PUSH_META,
            system=SyncSystem.PAPERLESS,
            key=op_key(OperationKind.PAPERLESS_PUSH_META, doc.uuid, "conflict", case.pk),
            document=doc,
            source_system=SyncSystem.APP,
            priority=50,
        )
    elif choice == "reupload":
        if link is not None:
            link.delete()
        enqueue(
            OperationKind.PAPERLESS_PUSH,
            system=SyncSystem.PAPERLESS,
            key=op_key(OperationKind.PAPERLESS_PUSH, doc.uuid, doc.sha256, "again", case.pk),
            document=doc,
            source_system=SyncSystem.APP,
            priority=50,
        )
    case.status = CaseStatus.RESOLVED
    case.resolved_by = user
    case.resolved_at = timezone.now()
    case.resolution = {"action": "resolve_conflict", **result, "reason": reason}
    case.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
    record(
        "sync.conflict_resolved",
        entity_type="review_case",
        entity_id=case.pk,
        object_id=case.object_id,
        request=request,
        actor=user,
        after={"subtype": subtype, **result, "reason": reason},
    )
    return result


def choices_for(case: ReviewCase) -> list[tuple[str, str]]:
    subtype = case.case_subtype or ""
    if subtype in ("deleted_remote", "trashed_remote", "drive_removed", "drive_trashed"):
        keys = ["keep_local", "tombstone"] + (["reupload"] if subtype in ("deleted_remote",) else [])
    elif subtype == "object_changed_remote":
        keys = ["apply_remote_object", "push_local_object"]
    elif subtype in ("content_changed_remote", "drive_content_changed", "checksum_mismatch"):
        keys = ["keep_local"] + (["reupload"] if subtype == "checksum_mismatch" else [])
    else:
        keys = ["keep_local"]
    return [(k, CHOICES[k]) for k in keys]
