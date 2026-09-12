"""Technische Kennzeichen an Drive-Dateien (appProperties: mhv_uuid, sha256, document_id, object_id, mhv_gen) und
die Drive-Verknuepfung des Dokuments (System drive, Rolle original). Keine Volltexte, keine Zugangsdaten."""

from __future__ import annotations

from django.utils import timezone

from apps.audit.services import record
from apps.sync import services
from apps.sync.flows.common import DRIVE, SYNCED, upsert_link
from apps.sync.models import OperationKind
from apps.sync.operations import Defer, Retry, Skip, handler

GENERATION = "1"


def wanted_props(doc) -> dict[str, str]:
    props = {
        "mhv_uuid": str(doc.uuid),
        "document_id": str(doc.pk),
        "object_id": str(doc.object_id),
        "mhv_gen": GENERATION,
    }
    if doc.sha256:
        props["sha256"] = doc.sha256
    return props


@handler(OperationKind.DRIVE_SET_PROPS)
def set_props(op) -> dict:
    doc = op.document
    if doc is None or not doc.drive_file_id:
        raise Skip("keine Drive-Datei")
    drive = services.get_drive()
    if drive is None:
        raise Defer("keine Google-Verbindung", 600)
    from apps.drive.adapter import DriveError, NotFound

    try:
        node = drive.get(doc.drive_file_id)
    except NotFound:
        node = None
    except DriveError as exc:
        raise Retry(f"Drive nicht erreichbar: {exc}") from exc
    if node is None:
        raise Skip("Drive-Datei nicht gefunden")
    current = dict(node.app_properties or {})
    wanted = wanted_props(doc)
    changed = {k: v for k, v in wanted.items() if current.get(k) != v}
    if changed:
        try:
            node = drive.set_app_properties(doc.drive_file_id, changed)
        except DriveError as exc:
            raise Retry(f"Kennzeichen nicht gesetzt: {exc}") from exc
    upsert_link(
        doc,
        system=DRIVE,
        external_id=doc.drive_file_id,
        external_version=getattr(node, "head_revision_id", None) or getattr(node, "version", None),
        parent_external_id=node.parent_id,
        checksum_md5=node.md5,
        checksum_sha256=getattr(node, "sha256", None) or doc.sha256,
        mime_type=node.mime_type,
        size_bytes=node.size,
        remote_modified_at=None,
        state=SYNCED,
        state_reason="Kennzeichen gesetzt" if changed else "Kennzeichen vorhanden",
        last_synced_at=timezone.now(),
        synced_fields={"name": node.name, "parent": node.parent_id, "props": {**current, **changed}},
    )
    if changed:
        record(
            "sync.drive_props_set",
            entity_type="document",
            entity_id=doc.pk,
            object_id=doc.object_id,
            after={"drive_file_id": doc.drive_file_id, "props": sorted(changed)},
        )
    return {"changed": sorted(changed)}
