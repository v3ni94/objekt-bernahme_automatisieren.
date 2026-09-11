"""Bestand aus Drive uebernehmen (Auftrag vom 11.09.2026).

Der Anwender sieht die vorhandene Ordnerstruktur im Programm, klickt sich Ordner fuer Ordner durch und uebernimmt
Dateien in ein Objekt. Die Dateien werden als Bestandsdokumente (source drive_existing) registriert; die
Verarbeitungskette (Hash, OCR, Klassifikation, Ablage per Elternwechsel) ueberfuehrt sie in die neue Struktur des
Objekts. Nichts wird geloescht: Quellordner bleiben stehen, eine Datei wird nur verschoben (D 1 Nr. 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document
from apps.drive.adapter import DriveAdapter, DriveNode, sort_deterministic
from apps.objects.models import ManagedObject
from apps.pipeline.jobs import JobType, enqueue, idempotency_key
from apps.pipeline.models import ProcessingRun, RunStatus, RunType
from apps.pipeline.runs import start_run

MAX_BREADCRUMB = 12
_LINK_ID = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]{5,})")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{5,}$")


class TakeoverError(Exception):
    pass


@dataclass
class FileEntry:
    node: DriveNode
    path: str
    registered_in: str | None = None  # Objektnummer, wenn die Datei bereits einem Objekt gehoert

    @property
    def selectable(self) -> bool:
        return self.registered_in is None and not self.node.is_shortcut


@dataclass
class TakeoverResult:
    registered: int = 0
    skipped_registered: int = 0
    skipped_shortcuts: int = 0
    run_id: int | None = None
    document_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_folder_ref(value: str) -> str | None:
    """Ordner-ID aus einer Eingabe: nackte ID oder Drive-Link (…/folders/<id>, …?id=<id>)."""
    text = (value or "").strip()
    if not text:
        return None
    m = _LINK_ID.search(text)
    if m:
        return m.group(1)
    return text if _BARE_ID.match(text) else None


def breadcrumb(drive: DriveAdapter, folder: DriveNode, stop_id: str | None) -> list[DriveNode]:
    """Pfad vom Wurzelordner bis zum aktuellen Ordner; endet am Wurzelordner der Anwendung oder ohne Elternordner."""
    chain = [folder]
    current = folder
    for _ in range(MAX_BREADCRUMB):
        if current.id == stop_id or current.parent_id is None:
            break
        parent = drive.get(current.parent_id)
        if parent is None:
            break
        chain.append(parent)
        current = parent
    chain.reverse()
    return chain


def _registered_map(file_ids: list[str]) -> dict[str, str]:
    rows = Document.objects.filter(drive_file_id__in=file_ids, deleted_at__isnull=True).values_list(
        "drive_file_id", "object__object_number"
    )
    return dict(rows)


def list_folder(drive: DriveAdapter, folder_id: str) -> tuple[list[DriveNode], list[FileEntry]]:
    """Unterordner und Dateien eines Ordners fuer die Ansicht; Dateien mit Hinweis, wenn bereits registriert."""
    children = sort_deterministic(drive.list_children(folder_id))
    folders = [c for c in children if c.is_folder]
    files = [c for c in children if not c.is_folder]
    registered = _registered_map([f.id for f in files])
    return folders, [FileEntry(node=f, path=f.name, registered_in=registered.get(f.id)) for f in files]


def collect_files(
    drive: DriveAdapter, folder: DriveNode, *, recursive: bool, limit: int, only_ids: set[str] | None = None
) -> list[FileEntry]:
    """Dateien eines Ordners, wahlweise mit allen Unterordnern (Pfad relativ zum gewaehlten Ordner)."""
    out: list[FileEntry] = []
    if recursive:
        names: dict[str, str] = {folder.id: folder.name}
        for node, path_ids in drive.walk(folder.id):
            if node.is_folder:
                names[node.id] = node.name
                continue
            if node.trashed:
                continue
            parts = [names.get(p, "?") for p in path_ids[1:]]
            out.append(FileEntry(node=node, path="/".join([*parts, node.name])))
            if len(out) > limit:
                raise TakeoverError(
                    f"Mehr als {limit} Dateien unterhalb von „{folder.name}“. Bitte Unterordner einzeln übernehmen "
                    "oder drive.takeover_max_files anpassen."
                )
    else:
        for node in sort_deterministic(drive.list_children(folder.id)):
            if not node.is_folder and not node.trashed:
                out.append(FileEntry(node=node, path=node.name))
        if len(out) > limit:
            raise TakeoverError(
                f"Mehr als {limit} Dateien im Ordner „{folder.name}“; drive.takeover_max_files anpassen."
            )
    if only_ids is not None:
        out = [e for e in out if e.node.id in only_ids]
    return out


def register_files(
    obj: ManagedObject,
    entries: list[FileEntry],
    *,
    source_folder: DriveNode,
    user=None,
    request=None,
) -> TakeoverResult:
    """Dateien als Bestandsdokumente des Objekts registrieren und die Verarbeitung anstossen.

    Bereits registrierte Dateien (in diesem oder einem anderen Objekt) werden uebersprungen, ebenso Verknuepfungen.
    Laeuft fuer das Objekt bereits ein Lauf, haengen sich die Jobs an ihn; sonst startet ein inkrementeller Lauf.
    """
    result = TakeoverResult()
    registered = _registered_map([e.node.id for e in entries])
    now = timezone.now()
    with transaction.atomic():
        active_run = ProcessingRun.objects.filter(
            object=obj,
            status__in=[RunStatus.PENDING, RunStatus.RUNNING],
            run_type__in=(RunType.FULL, RunType.INCREMENTAL),
        ).first()
        for e in entries:
            node = e.node
            if node.is_shortcut:
                result.skipped_shortcuts += 1
                continue
            if node.id in registered:
                result.skipped_registered += 1
                continue
            doc = Document.objects.create(
                object=obj,
                drive_file_id=node.id,
                drive_md5=node.md5,
                size_bytes=node.size or 0,
                mime_type=node.mime_type or "application/octet-stream",
                original_name=node.name,
                current_name=node.name,
                source="drive_existing",
                source_path=f"{source_folder.name}/{e.path}"[:1000],
                status="registered",
                first_seen_at=now,
            )
            key = idempotency_key(JobType.DISCOVER, obj.pk, node.id, node.md5 or node.modified_time or "")
            enqueue(
                JobType.DISCOVER,
                obj,
                key=key,
                document=doc,
                run=active_run,
                payload={
                    "drive_file_id": node.id,
                    "md5": node.md5,
                    "modified_time": node.modified_time,
                    "name": node.name,
                    "takeover_folder_id": source_folder.id,
                },
                dispatch=active_run is not None,
            )
            registered[node.id] = obj.object_number
            result.registered += 1
            result.document_ids.append(doc.pk)
        record(
            "drive.takeover",
            entity_type="object",
            entity_id=obj.pk,
            object_id=obj.pk,
            actor=user,
            request=request,
            after={
                "source_folder_id": source_folder.id,
                "source_folder_name": source_folder.name,
                "registered": result.registered,
                "skipped_registered": result.skipped_registered,
                "skipped_shortcuts": result.skipped_shortcuts,
            },
        )
        if active_run is not None:
            result.run_id = active_run.pk
    if result.registered and result.run_id is None:
        run = start_run(obj, run_type=RunType.INCREMENTAL, user=user)
        result.run_id = run.pk
    if result.skipped_shortcuts:
        result.notes.append(
            f"{result.skipped_shortcuts} Verknüpfung(en) übersprungen; das Original liegt an anderer Stelle."
        )
    if result.skipped_registered:
        result.notes.append(f"{result.skipped_registered} Datei(en) waren bereits einem Objekt zugeordnet.")
    return result


def max_files() -> int:
    return int(store.get("drive.takeover_max_files", 500))
