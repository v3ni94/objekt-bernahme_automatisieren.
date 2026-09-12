"""Indexbeleg fuer nicht uebertragbare Dateien (Format, Groesse, verschluesselt): eine ausdruecklich gekennzeichnete
PDF-Seite mit Dateiname, Objektbezug, Quell-ID und geschuetztem Drive-Link wird in Paperless abgelegt (Rolle
index_stub). Der Beleg ersetzt weder das Original noch eine Volltextindexierung des Inhalts."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.utils import timezone

from apps.audit.services import record
from apps.sync import config, services
from apps.sync.flows import paperless_meta
from apps.sync.flows.common import PAPERLESS, client_or_defer, raise_mapped, upsert_link
from apps.sync.models import ExternalLink, LinkRole, LinkState, OperationKind, SyncSystem
from apps.sync.operations import Block, Skip, enqueue, handler, op_key


def render_stub(doc, reason: str, target: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(target), pagesize=A4)
    y = 270 * mm
    c.setFont("Helvetica-Bold", 14)
    c.drawString(25 * mm, y, "Indexbeleg (kein Original)")
    y -= 12 * mm
    c.setFont("Helvetica", 10.5)
    rows = [
        ("Grund", reason),
        ("Dateiname", doc.current_name or doc.original_name or ""),
        ("Objekt", services.object_label(doc.object)),
        ("Dokument-UUID", str(doc.uuid)),
        ("Quell-ID (Drive)", doc.drive_file_id or "nicht in Drive"),
        ("Drive-Link", services.drive_link(doc.drive_file_id) or ""),
        ("Größe", f"{doc.size_bytes or 0} Byte"),
        ("Typ", doc.mime_type or ""),
        ("Erstellt", timezone.now().strftime("%d.%m.%Y %H:%M")),
    ]
    for label, value in rows:
        c.drawString(25 * mm, y, f"{label}: {value}"[:110])
        y -= 7 * mm
    y -= 5 * mm
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(
        25 * mm,
        y,
        "Dieser Beleg macht die Datei im DMS auffindbar. Das Original liegt unverändert in Google Drive.",
    )
    c.showPage()
    c.save()
    return target


@handler(OperationKind.PAPERLESS_INDEX_STUB)
def index_stub(op) -> dict:
    doc = op.document
    if doc is None:
        raise Skip("kein Dokument")
    if ExternalLink.objects.filter(
        document=doc, system=PAPERLESS, role__in=[LinkRole.ORIGINAL, LinkRole.INDEX_STUB]
    ).exists():
        raise Skip("bereits in Paperless (Original oder Indexbeleg)")
    if not config.writes_allowed(doc.object):
        raise Block("Schreiben nach Paperless für dieses Objekt nicht erlaubt (Modus oder Pilotumfang)")
    client = client_or_defer()
    reason = (op.payload or {}).get("reason") or "nicht übertragbar"
    meta = services.connection_meta()
    fields = paperless_meta.desired_fields(doc)
    fields["status"] = f"Indexbeleg: {reason}"
    ids = meta.get("field_ids") or {}
    custom = {int(ids[k]): v for k, v in fields.items() if k in ids and v}
    tags = [int(meta["tag_id"])] if meta.get("tag_id") else []
    tmp = Path(tempfile.mkdtemp(prefix="stub-"))
    try:
        pdf = render_stub(doc, reason, tmp / "indexbeleg.pdf")
        try:
            task_id = client.post_document(
                pdf,
                title=f"Indexbeleg: {Path(doc.current_name or doc.original_name).stem}"[:128],
                tags=tags,
                custom_fields=custom or None,
                filename=f"Indexbeleg_{doc.uuid}.pdf",
            )
        except Exception as exc:
            raise_mapped(exc)
    finally:
        for f in tmp.glob("*"):
            f.unlink(missing_ok=True)
        tmp.rmdir()
    # Verknuepfung entsteht nach Abschluss der Aufgabe (Rolle index_stub)
    enqueue(
        OperationKind.PAPERLESS_AWAIT_TASK,
        system=SyncSystem.PAPERLESS,
        key=op_key(OperationKind.PAPERLESS_AWAIT_TASK, task_id),
        document=doc,
        source_system=SyncSystem.APP,
        payload={"task_id": str(task_id), "fields": fields, "role": LinkRole.INDEX_STUB},
        delay_seconds=5,
    )
    record(
        "sync.paperless_index_stub",
        entity_type="document",
        entity_id=doc.pk,
        object_id=doc.object_id,
        after={"reason": reason, "task_id": str(task_id)},
    )
    return {"task_id": str(task_id), "reason": reason}


def stub_link(doc, external_id: str, checksum: str | None) -> ExternalLink:
    return upsert_link(
        doc,
        system=PAPERLESS,
        external_id=external_id,
        role=LinkRole.INDEX_STUB,
        checksum_sha256=checksum,
        state=LinkState.SYNCED,
        state_reason="Indexbeleg",
        last_synced_at=timezone.now(),
    )
