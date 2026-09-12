"""Exportfassung nativer Google-Dokumente: das Original bleibt bearbeitbar in Drive, Paperless erhaelt einen als
Snapshot gekennzeichneten PDF-Export (Rolle snapshot) mit der Drive-Version als Quellrevision. Ein neuer Snapshot
entsteht nur nach einer Aenderung der Quelle; der Snapshot wird beim Rueckabgleich nie als eigenes Quelldokument
importiert (Feld UUID und Rolle sind gesetzt). Eine PDF aus Paperless ueberschreibt das Google-Dokument nie."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.utils import timezone

from apps.audit.services import record
from apps.sync import config, services
from apps.sync.flows import paperless_meta
from apps.sync.flows.common import PAPERLESS, client_or_defer, raise_mapped
from apps.sync.models import ExternalLink, LinkRole, OperationKind, SyncSystem
from apps.sync.operations import Block, Defer, Skip, enqueue, handler, op_key

EXPORT_MIME = "application/pdf"


@handler(OperationKind.DRIVE_EXPORT_SNAPSHOT)
def export_snapshot(op) -> dict:
    doc = op.document
    if doc is None or not doc.drive_file_id:
        raise Skip("kein Google-Dokument")
    if not config.writes_allowed(doc.object):
        raise Block("Schreiben nach Paperless für dieses Objekt nicht erlaubt (Modus oder Pilotumfang)")
    drive = services.get_drive()
    if drive is None:
        raise Defer("keine Google-Verbindung", 600)
    client = client_or_defer()
    node = drive.get(doc.drive_file_id)
    if node is None or node.trashed:
        raise Skip("Google-Dokument nicht (mehr) vorhanden")
    revision = getattr(node, "head_revision_id", None) or getattr(node, "version", None) or node.modified_time
    existing = ExternalLink.objects.filter(document=doc, system=PAPERLESS, role=LinkRole.SNAPSHOT).first()
    if existing is not None and existing.external_version == str(revision):
        raise Skip("Snapshot entspricht der aktuellen Quellversion")
    meta = services.connection_meta()
    fields = paperless_meta.desired_fields(doc)
    fields["status"] = f"Exportfassung (Snapshot) der Drive-Version {revision}; Original bleibt in Drive"
    ids = meta.get("field_ids") or {}
    custom = {int(ids[k]): v for k, v in fields.items() if k in ids and v}
    tags = [int(meta["tag_id"])] if meta.get("tag_id") else []
    tmp = Path(tempfile.mkdtemp(prefix="snap-"))
    try:
        pdf = drive.export(doc.drive_file_id, EXPORT_MIME, tmp / "export.pdf")
        size = pdf.stat().st_size
        if size == 0:
            raise Block("Export lieferte eine leere Datei")
        if size > config.max_upload_bytes():
            raise Block("Export überschreitet die Größengrenze für Paperless")
        title = f"Exportfassung: {Path(doc.current_name or doc.original_name).stem}"[:128]
        try:
            if existing is not None and (meta.get("features") or {}).get("document_versions"):
                task_id = client.update_version(int(existing.external_id), pdf, label=f"Drive {revision}")
            else:
                task_id = client.post_document(
                    pdf,
                    title=title,
                    tags=tags,
                    custom_fields=custom or None,
                    filename=f"{Path(doc.current_name).stem}_Export.pdf",
                )
        except Exception as exc:
            raise_mapped(exc)
    finally:
        for f in tmp.glob("*"):
            f.unlink(missing_ok=True)
        tmp.rmdir()
    enqueue(
        OperationKind.PAPERLESS_AWAIT_TASK,
        system=SyncSystem.PAPERLESS,
        key=op_key(OperationKind.PAPERLESS_AWAIT_TASK, task_id),
        document=doc,
        source_system=SyncSystem.DRIVE,
        payload={
            "task_id": str(task_id),
            "fields": fields,
            "role": LinkRole.SNAPSHOT,
            "external_version": str(revision),
        },
        delay_seconds=5,
    )
    record(
        "sync.drive_snapshot",
        entity_type="document",
        entity_id=doc.pk,
        object_id=doc.object_id,
        after={
            "drive_file_id": doc.drive_file_id,
            "revision": str(revision),
            "task_id": str(task_id),
            "at": timezone.now().isoformat(),
        },
    )
    return {"task_id": str(task_id), "revision": str(revision)}
