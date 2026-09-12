"""Datei nach Paperless uebertragen (Upload mit Task-Verfolgung) oder eine dort vorhandene Kopie verknuepfen.
Kein blinder Wiederholungs-Upload: nach einem Verbindungsabbruch wird zuerst geprueft, ob das Dokument mit der
UUID bereits angekommen ist."""

from __future__ import annotations

import re
from pathlib import Path

from django.utils import timezone

from apps.audit.services import record
from apps.sync import config, services
from apps.sync.flows import paperless_meta
from apps.sync.flows.common import PAPERLESS, SYNCED, client_or_defer, link_for, raise_mapped, upsert_link
from apps.sync.models import LinkRole, LinkState, OperationKind, SyncSystem
from apps.sync.operations import Block, Defer, Retry, Skip, enqueue, handler, op_key

PAPERLESS_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".gif",
    ".webp",
    ".bmp",
    ".txt",
    ".docx",
    ".xlsx",
    ".pptx",
    ".doc",
    ".xls",
    ".ppt",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".eml",
    ".csv",
}
DUPLICATE_ID = re.compile(r"#(\d+)")


def find_by_uuid(client, doc) -> dict | None:
    """Sucht ein Paperless-Dokument mit der UUID der Anwendung (Feldsuche), sofern der Client sie anbietet."""
    meta = services.connection_meta()
    field_id = (meta.get("field_ids") or {}).get("uuid")
    names = config.field_names()
    finder = getattr(client, "find_by_custom_field", None)
    if finder is None or not field_id:
        return None
    try:
        return finder(names.uuid, str(doc.uuid))
    except Exception:  # Suche ist eine Optimierung, kein Pflichtschritt
        return None


def _link_existing(doc, remote: dict, client, reason: str) -> dict:
    metadata = {}
    try:
        metadata = client.get_metadata(int(remote["id"])) or {}
    except Exception:  # Metadaten sind optional fuer die Verknuepfung
        metadata = {}
    link = upsert_link(
        doc,
        system=PAPERLESS,
        external_id=str(remote["id"]),
        checksum_sha256=metadata.get("original_checksum") or None,
        mime_type=metadata.get("original_mime_type") or remote.get("mime_type"),
        size_bytes=metadata.get("original_size"),
        remote_modified_at=_parse_dt(remote.get("modified")),
        state=LinkState.LINKED,
        state_reason=reason,
        synced_fields={
            "title": remote.get("title"),
            "tags": remote.get("tags"),
            "modified": remote.get("modified"),
        },
    )
    record(
        "sync.paperless_push",
        entity_type="document",
        entity_id=doc.pk,
        object_id=doc.object_id,
        after={"paperless_id": link.external_id, "linked_existing": True, "reason": reason},
    )
    enqueue(
        OperationKind.PAPERLESS_PUSH_META,
        system=SyncSystem.PAPERLESS,
        key=op_key(OperationKind.PAPERLESS_PUSH_META, doc.uuid, "linked", link.external_id),
        document=doc,
        source_system=SyncSystem.APP,
    )
    return {"paperless_id": link.external_id, "linked_existing": True}


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


@handler(OperationKind.PAPERLESS_PUSH)
def push(op) -> dict:
    doc = op.document
    if doc is None or not doc.sha256:
        raise Skip("kein Dokument mit Hash")
    if doc.status in ("duplicate", "moved_out") or doc.deleted_at:
        raise Skip(f"Status {doc.status}")
    if link_for(doc, PAPERLESS) is not None:
        raise Skip("bereits verknüpft")
    if not config.writes_allowed(doc.object):
        raise Block("Schreiben nach Paperless für dieses Objekt nicht erlaubt (Modus oder Pilotumfang)")
    client = client_or_defer()
    # 1. Kopie schon vorhanden (fruehere Uebertragung mit unklarem Ausgang oder Bestand)?
    remote = find_by_uuid(client, doc)
    if remote is not None:
        return _link_existing(doc, remote, client, "UUID in Paperless gefunden")
    # 2. Uebertragbar?
    suffix = Path(doc.current_name or doc.original_name or "").suffix.lower()
    if (doc.mime_type or "").startswith("application/vnd.google-apps"):
        enqueue(
            OperationKind.DRIVE_EXPORT_SNAPSHOT,
            system=SyncSystem.DRIVE,
            key=op_key(OperationKind.DRIVE_EXPORT_SNAPSHOT, doc.uuid, doc.drive_file_id),
            document=doc,
            source_system=SyncSystem.DRIVE,
        )
        raise Skip("Google-Dokument: Exportfassung statt Original")
    if suffix not in PAPERLESS_SUFFIXES or (doc.size_bytes or 0) > config.max_upload_bytes():
        reason = "Dateityp nicht übertragbar" if suffix not in PAPERLESS_SUFFIXES else "Datei zu groß"
        enqueue(
            OperationKind.PAPERLESS_INDEX_STUB,
            system=SyncSystem.PAPERLESS,
            key=op_key(OperationKind.PAPERLESS_INDEX_STUB, doc.uuid, doc.sha256),
            document=doc,
            source_system=SyncSystem.APP,
            payload={"reason": reason},
        )
        raise Skip(f"{reason}: Indexbeleg vorgemerkt")
    # 3. Upload mit Task-Verfolgung
    meta = services.connection_meta()
    fields = paperless_meta.desired_fields(doc)
    ids = meta.get("field_ids") or {}
    custom = {int(ids[k]): v for k, v in fields.items() if k in ids and v}
    tags = [int(meta["tag_id"])] if meta.get("tag_id") else []
    try:
        with services.local_copy(doc) as path:
            task_id = client.post_document(
                path,
                title=Path(doc.current_name or doc.original_name).stem[:128],
                created=doc.document_date,
                tags=tags,
                custom_fields=custom or None,
                filename=doc.current_name or doc.original_name,
            )
    except (FileNotFoundError, ConnectionError) as exc:
        raise Retry(f"Quelle nicht verfügbar: {exc}") from exc
    except Exception as exc:
        from apps.sync.paperless import errors as e

        if isinstance(exc, e.PaperlessUnavailable):
            # Ausgang unklar: beim naechsten Versuch zuerst per UUID suchen (oben), nie blind erneut hochladen
            raise Retry(f"Upload mit unklarem Ausgang: {exc}") from exc
        raise_mapped(exc)
    enqueue(
        OperationKind.PAPERLESS_AWAIT_TASK,
        system=SyncSystem.PAPERLESS,
        key=op_key(OperationKind.PAPERLESS_AWAIT_TASK, task_id),
        document=doc,
        source_system=SyncSystem.APP,
        payload={"task_id": str(task_id), "fields": fields},
        delay_seconds=5,
    )
    return {"task_id": str(task_id)}


@handler(OperationKind.PAPERLESS_AWAIT_TASK)
def await_task(op) -> dict:
    doc = op.document
    task_id = (op.payload or {}).get("task_id")
    if doc is None or not task_id:
        raise Skip("keine Aufgabe")
    role_wanted = (op.payload or {}).get("role") or LinkRole.ORIGINAL
    if role_wanted == LinkRole.ORIGINAL and link_for(doc, PAPERLESS) is not None:
        raise Skip("bereits verknüpft")
    client = client_or_defer()
    try:
        task = client.get_task(task_id)
    except Exception as exc:
        raise_mapped(exc)
    if task is None:
        raise Defer("Aufgabe in Paperless noch nicht sichtbar", 30)
    status = str(task.get("status") or "").upper()
    if status in ("PENDING", "STARTED", "RETRY"):
        raise Defer(f"Paperless verarbeitet noch ({status})", 30)
    if status == "SUCCESS":
        ids = list(task.get("related_document_ids") or [])
        if not ids:
            raise Block("Aufgabe erfolgreich, aber ohne Dokument-ID")
        remote_id = int(ids[0])
        try:
            metadata = client.get_metadata(remote_id) or {}
        except Exception:
            metadata = {}
        role = (op.payload or {}).get("role") or LinkRole.ORIGINAL
        is_original = role == LinkRole.ORIGINAL
        link = upsert_link(
            doc,
            system=PAPERLESS,
            external_id=str(remote_id),
            role=role,
            external_version=(op.payload or {}).get("external_version"),
            checksum_sha256=metadata.get("original_checksum") or (doc.sha256 if is_original else None),
            mime_type=metadata.get("original_mime_type")
            or (doc.mime_type if is_original else "application/pdf"),
            size_bytes=metadata.get("original_size") or (doc.size_bytes if is_original else None),
            state=SYNCED,
            state_reason={
                "original": "hochgeladen",
                "index_stub": "Indexbeleg",
                "snapshot": "Exportfassung",
            }.get(role, "hochgeladen"),
            last_synced_at=timezone.now(),
            synced_fields={"app_fields": (op.payload or {}).get("fields") or {}},
        )
        record(
            "sync.paperless_push",
            entity_type="document",
            entity_id=doc.pk,
            object_id=doc.object_id,
            after={
                "paperless_id": link.external_id,
                "task_id": task_id,
                "checksum_match": link.checksum_sha256 == doc.sha256,
            },
        )
        if is_original and link.checksum_sha256 and link.checksum_sha256 != doc.sha256:
            from apps.sync.flows.common import conflict

            conflict(
                doc,
                "checksum_mismatch",
                link.external_id,
                {"local": doc.sha256, "remote": link.checksum_sha256},
            )
        if is_original:
            # Ablage oder Zuordnung koennen den Upload ueberholt haben: Sollwerte nachziehen (ohne Aenderung
            # endet die Operation als uebersprungen, kein Schreibzugriff)
            enqueue(
                OperationKind.PAPERLESS_PUSH_META,
                system=SyncSystem.PAPERLESS,
                key=op_key(OperationKind.PAPERLESS_PUSH_META, doc.uuid, "uploaded", remote_id),
                document=doc,
                source_system=SyncSystem.APP,
                payload={"reason": "uploaded"},
            )
        return {"paperless_id": remote_id, "role": role}
    result = str(task.get("result") or "")
    if "duplicate" in result.lower() or "already exists" in result.lower():
        m = DUPLICATE_ID.search(result)
        if m:
            try:
                remote = client.get_document(int(m.group(1)))
            except Exception as exc:
                raise_mapped(exc)
            return _link_existing(doc, remote, client, "Paperless meldet Dublette")
        raise Block(f"Paperless meldet Dublette ohne Kennung: {result[:200]}")
    raise Block(f"Paperless-Aufgabe fehlgeschlagen: {result[:300] or status}")


def link_role_snapshot() -> str:
    return LinkRole.SNAPSHOT
