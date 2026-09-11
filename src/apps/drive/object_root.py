"""Objektordner festlegen (Befund 11.09.2026, Objekt 82).

Findet der Ordnerabgleich mehrere Ordner mit derselben Objektnummer, legt er wie vorgesehen nichts an und erzeugt den
Fall „Objektnummer doppelt“. Die Entscheidung, welcher Ordner der Objektordner ist, trifft der Anwender: im Fall
(Kandidat waehlen) oder auf der Objektseite (Ordner-ID oder Link). Danach wird der Abgleich erneut angestossen; er
nutzt den festgelegten Ordner und legt die Struktur an, wartende Ablage-Jobs laufen weiter.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.drive.adapter import DriveAdapter
from apps.drive.takeover import parse_folder_ref
from apps.objects.models import ManagedObject
from apps.review.models import CaseStatus, CaseType, ReviewCase


class ObjectRootError(Exception):
    pass


def open_structure_case(obj: ManagedObject) -> ReviewCase | None:
    """Offener Fall zur Objektzuordnung (Objektnummer doppelt oder Ordnerstruktur), der die Ablage aufhaelt."""
    return (
        ReviewCase.objects.filter(
            object=obj,
            case_type__in=[CaseType.DUPLICATE_OBJECT_NUMBER, CaseType.DRIVE_STRUCTURE],
            status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        )
        .order_by("-id")
        .first()
    )


def set_object_root(
    obj: ManagedObject,
    folder_ref: str,
    *,
    drive: DriveAdapter,
    user=None,
    request=None,
    case: ReviewCase | None = None,
) -> dict:
    """Objektordner setzen, offene Faelle „Objektnummer doppelt“ schliessen, Ordnerabgleich anstossen."""
    folder_id = parse_folder_ref(folder_ref or "")
    if not folder_id:
        raise ObjectRootError("Keine Ordner-ID oder kein Drive-Link erkannt.")
    node = drive.get(folder_id)
    if node is None or node.trashed or not node.is_folder:
        raise ObjectRootError(
            "Ordner in Drive nicht gefunden"
            if node is None
            else (
                "Ordner liegt im Papierkorb"
                if node.trashed
                else "Die ID gehört zu einer Datei, nicht zu einem Ordner"
            )
        )
    with transaction.atomic():
        before = {
            "drive_root_folder_id": obj.drive_root_folder_id,
            "drive_root_folder_name": obj.drive_root_folder_name,
        }
        obj.drive_root_folder_id = node.id
        obj.drive_root_folder_name = node.name
        obj.drive_root_verified_at = timezone.now()
        obj.save(
            update_fields=[
                "drive_root_folder_id",
                "drive_root_folder_name",
                "drive_root_verified_at",
                "updated_at",
            ]
        )
        closed = 0
        cases = ReviewCase.objects.select_for_update().filter(
            object=obj,
            case_type=CaseType.DUPLICATE_OBJECT_NUMBER,
            status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        )
        for c in cases:
            c.status = CaseStatus.RESOLVED
            c.resolved_by = user
            c.resolved_at = timezone.now()
            c.resolution = {"decision": "choose_folder", "folder_id": node.id, "folder_name": node.name}
            c.save(update_fields=["status", "resolved_by", "resolved_at", "resolution", "updated_at"])
            closed += 1
        record(
            "object.drive_root_set",
            entity_type="object",
            entity_id=obj.pk,
            object_id=obj.pk,
            request=request,
            actor=user,
            before=before,
            after={
                "drive_root_folder_id": node.id,
                "drive_root_folder_name": node.name,
                "cases_closed": closed,
                "case_id": case.pk if case else None,
            },
        )
    from apps.drive.tasks import trigger_object_folders

    outcome = trigger_object_folders(
        obj.pk, user_id=getattr(user, "pk", None), trigger="root_set", force=True
    )
    return {"folder": node, "cases_closed": closed, "reconcile": outcome}
