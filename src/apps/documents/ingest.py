"""Annahme von Dokumenten in die Verarbeitung (Fachentwurf E 1.2 Schritt 1 fuer Uploads, docs/architektur.md 6.1).

Ein Upload wird im Transitbereich abgelegt, als documents-Zeile mit Status registered angelegt und ueber einen
Verarbeitungslauf (incremental) des Objekts in die Pipeline gegeben. Der Hash und die Dublettenpruefung laufen im
Job hash (T29), nicht beim Upload. Laeuft fuer das Objekt bereits ein Lauf, erhaelt er die neuen Jobs; sonst wartet
ein neuer Lauf pending mit sichtbarer Warteposition (Objektserialitaet, B-26).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document, DocumentSource, DocumentStatus
from apps.pipeline import storage
from apps.pipeline.models import ProcessingRun, RunStatus, RunType
from apps.pipeline.runs import SERIAL_TYPES, dispatch_run, schedule_runs

ALLOWED_SUFFIXES = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".webp",
    ".docx",
    ".xlsx",
    ".xlsm",
    ".csv",
    ".txt",
}


class IngestError(Exception):
    pass


def safe_filename(name: str) -> str:
    base = Path(name or "").name.replace("\\", "_").replace("/", "_").strip()
    return base or "dokument"


def register_upload(obj, *, filename: str, data: bytes, user=None, request=None) -> Document:
    """Datei annehmen und als Dokument registrieren; Rueckgabe der documents-Zeile (Status registered)."""
    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise IngestError(f"Dateityp {suffix or 'ohne Endung'} wird nicht angenommen")
    limit = int(store.get("documents.max_download_bytes", 524288000))
    if len(data) > limit:
        raise IngestError(f"Datei größer als {limit} Byte")
    if not data:
        raise IngestError("Datei ist leer")
    storage.ensure_disk_reserve()
    target_dir = storage.upload_dir(obj.pk)
    target = target_dir / name
    target.write_bytes(data)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with transaction.atomic():
        doc = Document.objects.create(
            object=obj,
            size_bytes=len(data),
            mime_type=mime,
            original_name=name,
            current_name=name,
            source=DocumentSource.UPLOAD,
            source_path=str(target),
            status=DocumentStatus.REGISTERED,
            first_seen_at=timezone.now(),
        )
        record(
            "document.ingest",
            entity_type="document",
            entity_id=doc.pk,
            object_id=obj.pk,
            request=request,
            actor=user,
            after={"name": name, "size_bytes": len(data), "mime_type": mime},
        )
    return doc


def ensure_run(obj, *, user=None, run_type: str = RunType.INCREMENTAL) -> ProcessingRun:
    """Liefert den offenen Lauf des Objekts oder legt einen neuen an; ein laufender Lauf erhaelt die neuen Jobs."""
    running = (
        ProcessingRun.objects.filter(object=obj, status=RunStatus.RUNNING, run_type__in=SERIAL_TYPES)
        .order_by("-created_at")
        .first()
    )
    if running is not None:
        dispatch_run(running)
        return running
    pending = (
        ProcessingRun.objects.filter(object=obj, status=RunStatus.PENDING, run_type__in=SERIAL_TYPES)
        .order_by("created_at")
        .first()
    )
    if pending is not None:
        schedule_runs()
        pending.refresh_from_db()
        return pending
    run = ProcessingRun.objects.create(
        object=obj, run_type=run_type, status=RunStatus.PENDING, triggered_by=user
    )
    schedule_runs()
    run.refresh_from_db()
    return run


def ingest_upload(
    obj, *, filename: str, data: bytes, user=None, request=None
) -> tuple[Document, ProcessingRun]:
    doc = register_upload(obj, filename=filename, data=data, user=user, request=request)
    run = ensure_run(obj, user=user)
    return doc, run
