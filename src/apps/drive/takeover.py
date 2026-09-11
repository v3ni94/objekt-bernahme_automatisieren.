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
    extra: dict | None = None,
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
                **(extra or {}),
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


# ---------------------------------------------------------------- Altbestand-Tabelle (Quellordner)
def parse_folder_refs(text: str) -> list[str]:
    """Alle Ordner-IDs aus einem eingefuegten Text (Links oder IDs, durch Leerraum, Komma oder Semikolon getrennt),
    ohne Doppelte, in Eingabereihenfolge."""
    ids: list[str] = []
    for raw in re.split(r"[\s,;]+", text or ""):
        fid = parse_folder_ref(raw)
        if fid and fid not in ids:
            ids.append(fid)
    return ids


def add_sources(refs: list[str], *, user=None) -> tuple[list, int]:
    """Quellordner in die Tabelle aufnehmen; vorhandene Folder-IDs werden nicht doppelt angelegt."""
    from apps.drive.models import TakeoverSource

    created = []
    existing = 0
    for fid in refs:
        src, made = TakeoverSource.objects.get_or_create(drive_folder_id=fid, defaults={"created_by": user})
        if made:
            created.append(src)
        else:
            existing += 1
    return created, existing


def _number_config() -> dict:
    return {
        "digits_min": int(store.get("drive.object_number_digits_min", 2)),
        "digits_max": int(store.get("drive.object_number_digits_max", 6)),
        "separators": tuple(store.get("drive.object_number_separators", [" ", "_", ",", ".", "-"])),
    }


def link_source(src) -> bool:
    """Zielobjekt ueber den Zahlenwert der erkannten Objektnummer zuordnen (0623 gleich 623), wenn noch offen."""
    from apps.drive.models import TakeoverStatus

    if src.object_id is not None or not src.detected_object_number:
        return False
    obj = ManagedObject.active.filter(object_number_numeric=int(src.detected_object_number)).first()
    if obj is None:
        return False
    src.object = obj
    if src.status == TakeoverStatus.NEW:
        src.status = TakeoverStatus.LINKED
    return True


def link_sources_for_object(obj: ManagedObject) -> int:
    """Nach Objektanlage: offene Quellordner mit derselben Nummer an das Objekt binden."""
    from apps.drive.models import TakeoverSource

    count = 0
    for src in TakeoverSource.objects.filter(object__isnull=True, detected_object_number__isnull=False):
        if src.numeric == obj.object_number_numeric and link_source(src):
            src.save(update_fields=["object", "status", "updated_at"])
            count += 1
    return count


def resolve_source(src, drive: DriveAdapter) -> bool:
    """Ordnername aus Drive lesen, Objektnummer erkennen, Zielobjekt zuordnen. False, wenn der Ordner fehlt."""
    from apps.drive.object_numbers import parse_object_number

    node = drive.get(src.drive_folder_id)
    if node is None or node.trashed or not node.is_folder:
        src.last_error = (
            "Ordner in Drive nicht gefunden"
            if node is None
            else (
                "Ordner liegt im Papierkorb"
                if node.trashed
                else "Die ID gehört zu einer Datei, nicht zu einem Ordner"
            )
        )
        src.save(update_fields=["last_error", "updated_at"])
        return False
    src.name = node.name[:255]
    match = parse_object_number(node.name, **_number_config())
    src.detected_object_number = match.normalized if match else None
    src.resolved_at = timezone.now()
    src.last_error = None
    link_source(src)
    src.save(
        update_fields=[
            "name",
            "detected_object_number",
            "resolved_at",
            "last_error",
            "object",
            "status",
            "updated_at",
        ]
    )
    return True


def sorted_sources(sources) -> list:
    """Anzeige: nach Objektnummer (Zahlenwert), dann Name; Zeilen ohne Nummer am Ende."""
    return sorted(
        sources, key=lambda s: (s.numeric is None, s.numeric or 0, (s.name or s.drive_folder_id).casefold())
    )


def run_source(src, drive: DriveAdapter, *, user=None, request=None) -> TakeoverResult:
    """„Aufarbeiten“: alle Dateien des Quellordners samt Unterordnern in das Zielobjekt uebernehmen. Fehlt die
    Zielstruktur des Objekts in Drive, wird der Ordnerabgleich angestossen; die Ablage wartet, bis er steht."""
    from apps.drive.models import DriveNode as DriveNodeRow
    from apps.drive.models import NodeKind, NodeStatus, TakeoverStatus

    if src.object_id is None:
        link_source(src)
        if src.object_id is None:
            raise TakeoverError(
                "Kein Zielobjekt. Erst das Objekt anlegen oder die Objektnummer im Ordnernamen prüfen."
            )
        src.save(update_fields=["object", "status", "updated_at"])
    obj = src.object
    if obj.deleted_at is not None:
        raise TakeoverError(f"Objekt {obj.object_number} ist archiviert; erst wiederherstellen.")
    folder = drive.get(src.drive_folder_id)
    if folder is None or folder.trashed or not folder.is_folder:
        src.last_error = "Ordner in Drive nicht gefunden"
        src.status = TakeoverStatus.FAILED
        src.save(update_fields=["last_error", "status", "updated_at"])
        raise TakeoverError(src.last_error)
    if src.name != folder.name:
        src.name = folder.name[:255]
    try:
        entries = collect_files(drive, folder, recursive=True, limit=max_files())
    except TakeoverError as exc:
        src.last_error = str(exc)[:500]
        src.status = TakeoverStatus.FAILED
        src.save(update_fields=["name", "last_error", "status", "updated_at"])
        raise
    result = register_files(
        obj, entries, source_folder=folder, user=user, request=request, extra={"source_id": src.pk}
    )
    structure = DriveNodeRow.objects.filter(
        object=obj, node_kind=NodeKind.OBJECT_ROOT, status=NodeStatus.ACTIVE
    ).exists()
    if not structure:
        from apps.drive.tasks import trigger_object_folders

        outcome = trigger_object_folders(
            obj.pk, user_id=getattr(user, "pk", None), trigger="takeover", force=True
        )
        result.notes.append(
            "Der Objektordner mit der neuen Struktur wird jetzt angelegt; die Ablage wartet, bis er steht."
            if outcome == "queued"
            else f"Objektordner fehlt noch und konnte nicht angestoßen werden ({outcome}); Ordnerabgleich von Hand starten."
        )
    src.files_registered += result.registered
    src.files_skipped += result.skipped_registered + result.skipped_shortcuts
    src.last_run_id = result.run_id or src.last_run_id
    src.taken_at = timezone.now()
    src.status = TakeoverStatus.DONE
    src.last_error = None
    src.save(
        update_fields=[
            "name",
            "files_registered",
            "files_skipped",
            "last_run_id",
            "taken_at",
            "status",
            "last_error",
            "updated_at",
        ]
    )
    return result
