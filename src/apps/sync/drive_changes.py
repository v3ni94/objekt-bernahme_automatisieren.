"""Drive-Aenderungsprotokoll (Changes-API) mit dauerhaftem Cursor in sync_cursors (drive, page_token, meta driveId).
Ein Cursor ohne Vorgeschichte wird zuerst als Ausgangspunkt gesetzt (keine Rueckschau); danach werden neue oder
geaenderte Dateien in bekannten Objektordnern und im Eingangsordner registriert, Papierkorb- und Loeschereignisse
an den Verknuepfungen vermerkt (nie lokal geloescht), Inhaltsaenderungen als Konflikt vorgelegt. Verschobene
Ordner werden beim naechsten Bestandslauf nachgezogen (Hinweis im Ergebnis)."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document, DocumentSource, DocumentStatus
from apps.drive.adapter import FOLDER_MIME, DriveCursorInvalid, DriveError
from apps.drive.models import DriveNode as DriveNodeRow
from apps.objects.models import ManagedObject
from apps.pipeline.jobs import JobType, enqueue, idempotency_key
from apps.sync import config, inbox, services
from apps.sync.flows.common import conflict, mark_link
from apps.sync.models import ExternalLink, LinkRole, LinkState, SyncSystem

logger = logging.getLogger(__name__)

CURSOR_PAGE = "page_token"
CURSOR_LAST = "last_changes_poll"
MAX_PARENT_DEPTH = 8


def ensure_start_token(drive) -> str:
    row = services.get_cursor(SyncSystem.DRIVE, CURSOR_PAGE)
    drive_id = store.get("drive.root_drive_id", None)
    if row is not None and row.value and (row.meta or {}).get("drive_id") == drive_id:
        return row.value
    token = drive.start_page_token(drive_id)
    services.set_cursor(
        SyncSystem.DRIVE,
        CURSOR_PAGE,
        token,
        {"drive_id": drive_id, "set_at": timezone.now().isoformat(), "baseline": True},
    )
    # Schluesselname ohne "token": der Audit-Scrubber schwaerzt solche Schluessel, das Page-Token ist aber ein
    # Cursor (steht im Klartext in sync_cursors) und soll im Protokoll nachvollziehbar bleiben.
    record("sync.drive_changes", entity_type="sync", after={"baseline_cursor": token, "drive_id": drive_id})
    return token


class Resolver:
    """Ordnet Drive-Eltern einem Objekt zu (Objektwurzeln, drive_nodes, Eingangsordner) mit Zwischenspeicher."""

    def __init__(self, drive):
        self.drive = drive
        self.cache: dict[str, int | None] = {}
        self.inbox_id = inbox.inbox_folder_id()
        self.inbox_obj = inbox.get_inbox_object()
        roots = ManagedObject.active.exclude(drive_root_folder_id__isnull=True).values_list(
            "drive_root_folder_id", "pk"
        )
        self.root_map = dict(roots)
        self.node_map = dict(
            DriveNodeRow.objects.filter(status="active", object__isnull=False).values_list(
                "drive_file_id", "object_id"
            )
        )

    def object_for_parent(self, parent_id: str | None, depth: int = 0) -> int | None:
        if not parent_id or depth > MAX_PARENT_DEPTH:
            return None
        if parent_id in self.cache:
            return self.cache[parent_id]
        if self.inbox_id and parent_id == self.inbox_id and self.inbox_obj is not None:
            self.cache[parent_id] = self.inbox_obj.pk
            return self.inbox_obj.pk
        if parent_id in self.root_map:
            self.cache[parent_id] = self.root_map[parent_id]
            return self.root_map[parent_id]
        if parent_id in self.node_map:
            self.cache[parent_id] = self.node_map[parent_id]
            return self.node_map[parent_id]
        try:
            node = self.drive.get(parent_id)
        except DriveError:
            node = None
        result = self.object_for_parent(node.parent_id, depth + 1) if node is not None else None
        self.cache[parent_id] = result
        return result


def _register(node, obj_id: int, parent_node_row) -> Document | None:
    obj = ManagedObject.objects.get(pk=obj_id)
    doc = Document.objects.filter(object=obj, drive_file_id=node.id).first()
    if doc is not None:
        return None
    if node.mime_type == FOLDER_MIME or node.is_shortcut:
        return None
    with transaction.atomic():
        doc = Document.objects.create(
            object=obj,
            drive_md5=node.md5,
            size_bytes=node.size or 0,
            mime_type=node.mime_type or "application/octet-stream",
            original_name=node.name,
            current_name=node.name,
            source=DocumentSource.DRIVE_EXISTING,
            drive_file_id=node.id,
            drive_node=parent_node_row,
            status=DocumentStatus.REGISTERED,
            first_seen_at=timezone.now(),
        )
        record(
            "sync.drive_changes",
            entity_type="document",
            entity_id=doc.pk,
            object_id=obj.pk,
            after={
                "drive_file_id": node.id,
                "registered": True,
                "inbox": getattr(obj, "is_system_inbox", False),
            },
        )
    enqueue(
        JobType.DISCOVER,
        obj,
        key=idempotency_key(JobType.DISCOVER, obj.pk, node.id, node.md5 or node.modified_time or ""),
        document=doc,
        payload={"drive_file_id": node.id, "md5": node.md5},
    )
    return doc


def _handle_change(change, resolver: Resolver, counters: dict) -> None:
    node = change.node
    link = (
        ExternalLink.objects.filter(
            system=SyncSystem.DRIVE, external_id=change.file_id, role=LinkRole.ORIGINAL
        )
        .select_related("document")
        .first()
    )
    doc = (
        link.document
        if link
        else Document.objects.filter(drive_file_id=change.file_id, deleted_at__isnull=True)
        .select_related("object")
        .first()
    )
    if change.removed or node is None:
        if doc is not None:
            counters["removed_known"] += 1
            if link is not None:
                mark_link(link, LinkState.MISSING, "in Drive entfernt oder kein Zugriff")
            conflict(
                doc, "drive_removed", change.file_id, {"drive_file_id": change.file_id, "time": change.time}
            )
        else:
            counters["removed_unknown"] += 1
        return
    if node.trashed:
        if doc is not None:
            counters["trashed_known"] += 1
            if link is not None:
                mark_link(link, LinkState.TRASHED, "in Drive im Papierkorb")
            if doc.status not in ("duplicate", "moved_out"):
                conflict(
                    doc,
                    "drive_trashed",
                    change.file_id,
                    {"drive_file_id": change.file_id, "time": change.time, "name": node.name},
                )
        else:
            counters["trashed_unknown"] += 1
        return
    if node.mime_type == FOLDER_MIME:
        counters["folders"] += 1
        return
    if doc is not None:
        if node.md5 and doc.drive_md5 and node.md5 != doc.drive_md5:
            counters["changed_known"] += 1
            conflict(
                doc,
                "drive_content_changed",
                node.md5[:16],
                {
                    "drive_file_id": change.file_id,
                    "old_md5": doc.drive_md5,
                    "new_md5": node.md5,
                    "name": node.name,
                    "version": node.version,
                },
            )
            if link is not None:
                mark_link(
                    link,
                    LinkState.CHANGED_REMOTE,
                    "Inhalt in Drive geändert",
                    checksum_md5=node.md5,
                    external_version=node.head_revision_id or node.version,
                )
        else:
            counters["unchanged_known"] += 1
            if link is not None and (
                node.parent_id != link.parent_external_id
                or node.name != (link.synced_fields or {}).get("name")
            ):
                synced = dict(link.synced_fields or {})
                synced["name"] = node.name
                mark_link(
                    link,
                    link.state,
                    link.state_reason,
                    parent_external_id=node.parent_id,
                    synced_fields=synced,
                    last_seen_at=timezone.now(),
                )
        return
    obj_id = resolver.object_for_parent(node.parent_id)
    if obj_id is None:
        counters["out_of_scope"] += 1
        return
    parent_row = DriveNodeRow.objects.filter(drive_file_id=node.parent_id, status="active").first()
    if _register(node, obj_id, parent_row) is not None:
        counters["registered"] += 1


def poll(*, force: bool = False, max_pages: int = 20) -> dict:
    interval = int(store.get("sync.drive_changes_interval_minutes", 5))
    if not force and not services.interval_elapsed(SyncSystem.DRIVE, CURSOR_LAST, interval):
        return {"skipped": "Intervall nicht erreicht"}
    drive = services.get_drive()
    if drive is None:
        return {"skipped": "keine Google-Verbindung"}
    row = services.get_cursor(SyncSystem.DRIVE, CURSOR_PAGE)
    drive_id = store.get("drive.root_drive_id", None)
    if row is None or not row.value or (row.meta or {}).get("drive_id") != drive_id:
        token = ensure_start_token(drive)
        services.mark_now(SyncSystem.DRIVE, CURSOR_LAST, {"baseline": True})
        return {"baseline": token}
    token = row.value
    resolver = Resolver(drive)
    counters = {
        k: 0
        for k in (
            "registered",
            "changed_known",
            "unchanged_known",
            "trashed_known",
            "trashed_unknown",
            "removed_known",
            "removed_unknown",
            "folders",
            "out_of_scope",
            "pages",
        )
    }
    new_start = None
    try:
        for _ in range(max_pages):
            page = drive.list_changes(token, drive_id=drive_id)
            counters["pages"] += 1
            for change in page.changes:
                try:
                    _handle_change(change, resolver, counters)
                except Exception:  # eine fehlerhafte Aenderung blockiert nicht das Protokoll
                    logger.exception("Drive-Änderung %s nicht verarbeitet", change.file_id)
                    counters.setdefault("errors", 0)
                    counters["errors"] += 1
            if page.next_page_token:
                token = page.next_page_token
                services.set_cursor(
                    SyncSystem.DRIVE,
                    CURSOR_PAGE,
                    token,
                    {"drive_id": drive_id, "set_at": timezone.now().isoformat()},
                )
                continue
            new_start = page.new_start_page_token or token
            break
    except DriveCursorInvalid:
        services.set_cursor(
            SyncSystem.DRIVE,
            CURSOR_PAGE,
            None,
            {"drive_id": drive_id, "invalid_at": timezone.now().isoformat()},
        )
        record(
            "sync.drive_changes",
            entity_type="sync",
            after={
                "cursor_invalid": True,
                "hint": "neuer Ausgangspunkt beim nächsten Lauf; Bestandslauf empfohlen",
            },
        )
        services.mark_now(SyncSystem.DRIVE, CURSOR_LAST, {"cursor_invalid": True})
        return {"cursor_invalid": True}
    except DriveError as exc:
        services.mark_now(SyncSystem.DRIVE, CURSOR_LAST, {"error": str(exc)[:200]})
        return {"error": str(exc)[:200], **counters}
    if new_start:
        services.set_cursor(
            SyncSystem.DRIVE,
            CURSOR_PAGE,
            new_start,
            {"drive_id": drive_id, "set_at": timezone.now().isoformat()},
        )
    services.mark_now(SyncSystem.DRIVE, CURSOR_LAST, counters)
    if any(counters[k] for k in ("registered", "changed_known", "trashed_known", "removed_known")):
        record("sync.drive_changes", entity_type="sync", after=counters)
    return {"cursor": new_start, **counters}


def enabled_and_ready() -> bool:
    return config.drive_changes_enabled() and bool(store.get("drive.root_folder_id", None))
