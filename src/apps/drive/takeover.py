"""Bestand aus Drive uebernehmen (Auftrag vom 11.09.2026).

Der Anwender sieht die vorhandene Ordnerstruktur im Programm, klickt sich Ordner fuer Ordner durch und uebernimmt
Dateien in ein Objekt. Die Dateien werden als Bestandsdokumente (source drive_existing) registriert; die
Verarbeitungskette (Hash, OCR, Klassifikation, Ablage per Elternwechsel) ueberfuehrt sie in die neue Struktur des
Objekts. Nichts wird geloescht: Quellordner bleiben stehen, eine Datei wird nur verschoben (D 1 Nr. 6).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document
from apps.drive.adapter import DriveAdapter, DriveNode, sort_deterministic
from apps.objects.models import ManagedObject
from apps.pipeline.jobs import JobType, enqueue, idempotency_key, send
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

    ignored: bool = False

    @property
    def selectable(self) -> bool:
        return self.registered_in is None and not self.node.is_shortcut and not self.ignored


DEFAULT_IGNORE = ["*.tmp", "*.TMP", "~$*", "Thumbs.db", "desktop.ini", ".DS_Store", "*.lnk"]


def ignore_patterns() -> list[str]:
    return list(store.get("drive.takeover_ignore_patterns", DEFAULT_IGNORE) or [])


def is_ignored(name: str, patterns: list[str] | None = None) -> bool:
    """Temporaer- und Systemdateien (Word-Sicherungen, Explorer-Reste) sind kein Aktenbestand."""
    return any(
        fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(name.lower(), p.lower())
        for p in (patterns or ignore_patterns())
    )


@dataclass
class TakeoverResult:
    registered: int = 0
    skipped_registered: int = 0
    skipped_shortcuts: int = 0
    skipped_ignored: int = 0
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
    patterns = ignore_patterns()
    return folders, [
        FileEntry(
            node=f, path=f.name, registered_in=registered.get(f.id), ignored=is_ignored(f.name, patterns)
        )
        for f in files
    ]


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
        if only_ids is not None:
            out = [e for e in out if e.node.id in only_ids]
        if len(out) > limit:
            raise TakeoverError(
                f"Mehr als {limit} Dateien im Ordner „{folder.name}“; drive.takeover_max_files anpassen."
            )
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
    lock_key = f"takeover:{obj.pk}"
    if not cache.add(lock_key, "1", timeout=300):
        raise TakeoverError(
            "Für dieses Objekt läuft gerade eine Übernahme; bitte kurz warten und die Seite neu laden."
        )
    try:
        return _register_locked(
            obj, entries, result, source_folder=source_folder, user=user, request=request, extra=extra
        )
    finally:
        cache.delete(lock_key)


def _register_locked(obj, entries, result, *, source_folder, user, request, extra) -> TakeoverResult:
    registered = _registered_map([e.node.id for e in entries])
    now = timezone.now()
    jobs = []
    with transaction.atomic():
        # Nur ein echter Lauf zaehlt; ein Dry-Run wuerde die Ablage nie ausfuehren
        active_run = ProcessingRun.objects.filter(
            object=obj,
            status__in=[RunStatus.PENDING, RunStatus.RUNNING],
            run_type__in=(RunType.FULL, RunType.INCREMENTAL),
            dry_run=False,
        ).first()
        patterns = ignore_patterns()
        for e in entries:
            node = e.node
            if node.is_shortcut:
                result.skipped_shortcuts += 1
                continue
            if is_ignored(node.name, patterns):
                result.skipped_ignored += 1
                continue
            if node.id in registered:
                result.skipped_registered += 1
                continue
            try:
                with transaction.atomic():
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
            except IntegrityError:  # zeitgleich registriert (Doppelklick, Inventur des Abgleichs)
                result.skipped_registered += 1
                continue
            # Schluessel wie in dispatch_run, damit ein spaeterer Lauf keinen zweiten Discover-Job anlegt
            key = idempotency_key(JobType.DISCOVER, obj.pk, node.id, node.md5 or "")
            job, _ = enqueue(
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
                dispatch=False,
            )
            jobs.append(job)
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
                "skipped_ignored": result.skipped_ignored,
                **(extra or {}),
            },
        )
        if active_run is not None:
            result.run_id = active_run.pk
    if active_run is not None and active_run.status == RunStatus.RUNNING:
        # Lauf arbeitet bereits: Jobs nach dem Commit einreihen; ein wartender Lauf nimmt sie beim Start mit
        for job in jobs:
            send(job)
    if result.registered and result.run_id is None:
        run = start_run(obj, run_type=RunType.INCREMENTAL, user=user)
        result.run_id = run.pk
    if result.skipped_shortcuts:
        result.notes.append(
            f"{result.skipped_shortcuts} Verknüpfung(en) übersprungen; das Original liegt an anderer Stelle."
        )
    if result.skipped_registered:
        result.notes.append(f"{result.skipped_registered} Datei(en) waren bereits einem Objekt zugeordnet.")
    if result.skipped_ignored:
        result.notes.append(
            f"{result.skipped_ignored} Temporär- oder Systemdatei(en) übersprungen (drive.takeover_ignore_patterns)."
        )
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
    before = (src.object_id, src.status)
    link_source(src)
    fields = ["name", "detected_object_number", "resolved_at", "last_error", "updated_at"]
    if (src.object_id, src.status) != before:
        fields += [
            "object",
            "status",
        ]  # sonst koennte ein parallel gesetzter Stand (done) ueberschrieben werden
    src.save(update_fields=fields)
    return True


@dataclass
class CleanupItem:
    file_id: str
    path: str
    reason: str = ""
    document_id: int | None = None


@dataclass
class CleanupPlan:
    """Vorschau des Aufraeumens (12.09.2026): was in den Papierkorb geht, was bleibt und warum."""

    total_files: int = 0
    duplicates: list[CleanupItem] = field(default_factory=list)
    junk: list[CleanupItem] = field(default_factory=list)
    remaining: list[CleanupItem] = field(default_factory=list)
    empty_folders: list[tuple[str, str]] = field(default_factory=list)  # (folder_id, Pfad), Kinder vor Eltern
    root_empty: bool = False
    dubletten_folder: list[CleanupItem] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return bool(self.duplicates or self.junk or self.empty_folders or self.dubletten_folder)

    @property
    def file_count(self) -> int:
        return len(self.duplicates) + len(self.junk) + len(self.dubletten_folder)


def _protected_folder_ids() -> set[str]:
    """Ordner der neuen Struktur (registrierte Knoten) und Objektwurzeln werden nie in den Papierkorb verschoben."""
    from apps.drive.models import DriveNode as DriveNodeRow
    from apps.drive.models import NodeStatus

    ids = set(DriveNodeRow.objects.filter(status=NodeStatus.ACTIVE).values_list("drive_file_id", flat=True))
    ids |= set(
        ManagedObject.objects.filter(drive_root_folder_id__isnull=False).values_list(
            "drive_root_folder_id", flat=True
        )
    )
    return ids


def _original_reachable(doc, drive: DriveAdapter, obj, cache_: dict) -> tuple[bool, str]:
    """Eine Dublette geht nur in den Papierkorb, wenn ihr Original im Objekt aktiv ist und in Drive erreichbar bleibt
    (Gegenpruefung 12.09.2026: Original verschoben, fehlerhaft oder ohne Datei -> Dublette bleibt)."""
    orig = doc.duplicate_of
    if orig is None or orig.deleted_at is not None:
        return False, "Dublette ohne aktives Original"
    if orig.object_id != obj.pk:
        return False, "Original gehört zu einem anderen Objekt"
    if orig.status in ("moved_out", "error") or not orig.drive_file_id:
        return False, f"Original nicht abgelegt (Status {orig.status})"
    if orig.drive_file_id not in cache_:
        node = drive.get(orig.drive_file_id)
        cache_[orig.drive_file_id] = bool(node is not None and not node.trashed)
    return (True, "") if cache_[orig.drive_file_id] else (False, "Original in Drive nicht erreichbar")


def plan_cleanup(
    src,
    drive: DriveAdapter,
    *,
    include_junk: bool = True,
    include_folders: bool = True,
    include_dubletten: bool = False,
) -> CleanupPlan:
    """Aufraeumen nach der Aufarbeitung planen. In den Papierkorb: Dubletten (Dokumente mit Status duplicate im
    Quellbaum, nur mit erreichbarem Original), Temporaer- und Systemdateien (drive.takeover_ignore_patterns), danach
    leere Ordner (Kinder vor Eltern, Quellordner zuletzt), wahlweise der Ordner 06/03_Dubletten des Objekts. Alles
    andere bleibt und schuetzt seinen Ordner. Ordner der neuen Struktur sind tabu. Nichts wird endgueltig geloescht."""
    from apps.drive.models import DriveNode as DriveNodeRow
    from apps.drive.models import NodeKind, NodeStatus, TakeoverStatus
    from apps.pipeline.models import JobStatus, ProcessingJob

    plan = CleanupPlan()
    obj = src.object
    if obj is None:
        plan.blockers.append("Kein Zielobjekt zugeordnet.")
    if src.status != TakeoverStatus.DONE:
        plan.blockers.append("Der Ordner wurde noch nicht aufgearbeitet (Status nicht „übernommen“).")
    if obj is not None:
        offen = ProcessingJob.objects.filter(
            object=obj, status__in=[JobStatus.PENDING, JobStatus.RUNNING]
        ).count()
        if offen:
            plan.blockers.append(
                f"{offen} Verarbeitungsjobs des Objekts sind noch offen (Ablage läuft noch); bitte warten."
            )
    folder = drive.get(src.drive_folder_id)
    if folder is None or folder.trashed or not folder.is_folder:
        plan.blockers.append("Der Quellordner ist in Drive nicht mehr vorhanden.")
    if plan.blockers:
        return plan
    patterns = ignore_patterns()
    protected = _protected_folder_ids()
    limit = max_files()
    paths: dict[str, str] = {folder.id: folder.name}
    folders: list[str] = []  # Reihenfolge des Durchlaufs (Eltern vor Kindern)
    parent_of: dict[str, str] = {}
    keep: dict[str, int] = {}  # Ordner-ID -> verbleibende Dateien direkt darin
    files: list[tuple[DriveNode, str, str]] = []  # (Knoten, Pfad, Elternordner)
    for node, path_ids in drive.walk(folder.id):
        parent = path_ids[-1]
        if node.is_folder:
            paths[node.id] = f"{paths.get(parent, '')}/{node.name}"
            folders.append(node.id)
            parent_of[node.id] = parent
            keep.setdefault(node.id, 0)
            continue
        if node.trashed:
            continue
        files.append((node, f"{paths.get(parent, '')}/{node.name}", parent))
        if len(files) > limit:
            plan.blockers.append(
                f"Mehr als {limit} Dateien unterhalb des Quellordners; drive.takeover_max_files anpassen oder "
                "Unterordner einzeln aufnehmen."
            )
            return plan
    plan.total_files = len(files)
    docs = {
        d.drive_file_id: d
        for d in Document.objects.filter(
            drive_file_id__in=[n.id for n, _p, _e in files], deleted_at__isnull=True
        ).select_related("object", "duplicate_of")
    }
    original_cache: dict[str, bool] = {}
    for node, path, parent in files:
        doc = docs.get(node.id)
        if doc is not None and doc.object_id != obj.pk:
            plan.remaining.append(
                CleanupItem(node.id, path, f"gehört zu Objekt {doc.object.object_number}", doc.pk)
            )
        elif doc is not None and doc.status == "duplicate":
            ok, why = _original_reachable(doc, drive, obj, original_cache)
            if ok:
                plan.duplicates.append(
                    CleanupItem(node.id, path, f"Dublette von Dokument {doc.duplicate_of_id}", doc.pk)
                )
                continue
            plan.remaining.append(CleanupItem(node.id, path, why, doc.pk))
        elif doc is None and node.is_google_doc:
            plan.remaining.append(CleanupItem(node.id, path, "Google-Dokument, nicht registriert"))
        elif doc is None and is_ignored(node.name, patterns):
            if include_junk:
                plan.junk.append(CleanupItem(node.id, path, "Temporär- oder Systemdatei"))
                continue
            plan.remaining.append(CleanupItem(node.id, path, "Temporär- oder Systemdatei (ausgenommen)"))
        elif doc is None:
            plan.remaining.append(CleanupItem(node.id, path, "nicht registriert"))
        elif doc.status == "filed":
            plan.remaining.append(
                CleanupItem(node.id, path, "abgelegt, Zielordner liegt im Quellordner", doc.pk)
            )
        else:
            plan.remaining.append(CleanupItem(node.id, path, f"Status {doc.status}", doc.pk))
        keep[parent] = keep.get(parent, 0) + 1
    if include_folders:
        empty: set[str] = set()
        for fid in reversed(folders):  # Kinder vor Eltern
            if fid in protected or keep.get(fid, 0):
                continue
            children = [c for c in folders if parent_of.get(c) == fid]
            if all(c in empty for c in children):
                empty.add(fid)
                plan.empty_folders.append((fid, paths[fid]))
        root_children = [c for c in folders if parent_of.get(c) == folder.id]
        if (
            folder.id not in protected
            and not keep.get(folder.id, 0)
            and all(c in empty for c in root_children)
        ):
            plan.root_empty = True
            plan.empty_folders.append((folder.id, paths[folder.id]))
    if include_dubletten:
        node = (
            DriveNodeRow.objects.filter(
                object=obj,
                node_kind=NodeKind.SUBFOLDER,
                category_id="06",
                subfolder__code="03",
                status=NodeStatus.ACTIVE,
            )
            .order_by("id")
            .first()
        )
        if node is not None:
            children = [
                c for c in drive.list_children(node.drive_file_id) if not c.is_folder and not c.trashed
            ]
            dubl = {
                d.drive_file_id: d
                for d in Document.objects.filter(
                    drive_file_id__in=[c.id for c in children], deleted_at__isnull=True
                ).select_related("duplicate_of")
            }
            for child in children:
                doc = dubl.get(child.id)
                if doc is None or doc.status != "duplicate" or doc.object_id != obj.pk:
                    continue
                ok, _why = _original_reachable(doc, drive, obj, original_cache)
                if ok:
                    plan.dubletten_folder.append(
                        CleanupItem(child.id, f"06_Sonstiges/03_Dubletten/{child.name}", "Dublette", doc.pk)
                    )
    return plan


def _trash_failed(obj, src, path: str, file_id: str, error: str, *, user, request) -> None:
    record(
        "drive.trash_failed",
        entity_type="drive_file",
        object_id=obj.pk,
        request=request,
        actor=user,
        after={"drive_file_id": file_id, "path": path, "error": error[:300], "source_id": src.pk},
    )


def run_cleanup(
    src, drive: DriveAdapter, plan: CleanupPlan, *, user=None, request=None, reason: str = ""
) -> dict:
    """Plan ausfuehren: Dateien und Ordner in den Papierkorb (Ordner nur, wenn sie live noch leer sind), Dokumente der
    Dubletten stilllegen (Drive-ID wird geloest, damit eine Wiederherstellung aus dem Papierkorb als neue Datei
    erkannt wird), alles protokollieren (drive.trash, drive.trash_failed). Ein Fehler je Eintrag wird gesammelt, die
    Datenbankaenderung je Eintrag ist eine Transaktion. Sperre je Objekt gegen parallele Uebernahme oder Aufraeumen."""
    from apps.drive.adapter import DriveError
    from apps.review.models import CaseStatus, ReviewCase

    obj = src.object
    lock_key = f"takeover:{obj.pk}"
    if not cache.add(lock_key, "cleanup", timeout=600):
        raise TakeoverError(
            "Für dieses Objekt läuft gerade eine Übernahme oder ein Aufräumen; bitte kurz warten."
        )
    result = {"files": 0, "folders": 0, "documents": 0, "errors": [], "root_trashed": False}
    try:
        for kind, items in (
            ("duplicate", plan.duplicates),
            ("junk", plan.junk),
            ("duplicate", plan.dubletten_folder),
        ):
            for item in items:
                try:
                    drive.trash(item.file_id)
                except DriveError as exc:
                    result["errors"].append(f"{item.path}: {exc}")
                    _trash_failed(obj, src, item.path, item.file_id, str(exc), user=user, request=request)
                    continue
                result["files"] += 1
                try:
                    with transaction.atomic():
                        record(
                            "drive.trash",
                            entity_type="document" if item.document_id else "drive_file",
                            entity_id=item.document_id,
                            object_id=obj.pk,
                            request=request,
                            actor=user,
                            reason=reason or None,
                            after={
                                "drive_file_id": item.file_id,
                                "path": item.path,
                                "kind": kind,
                                "source_id": src.pk,
                            },
                        )
                        if item.document_id:
                            doc = Document.objects.filter(pk=item.document_id).first()
                            if doc is not None and doc.deleted_at is None:
                                doc.deleted_at = timezone.now()
                                doc.deleted_by = user if getattr(user, "pk", None) else None
                                doc.delete_reason = f"Dublette beim Aufräumen in den Papierkorb verschoben (Drive {item.file_id})"
                                doc.drive_file_id = None
                                doc.save(
                                    update_fields=[
                                        "deleted_at",
                                        "deleted_by",
                                        "delete_reason",
                                        "drive_file_id",
                                        "updated_at",
                                    ]
                                )
                                ReviewCase.objects.filter(
                                    document=doc, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
                                ).update(
                                    status=CaseStatus.RESOLVED,
                                    resolved_at=timezone.now(),
                                    resolution={"decision": "cleanup_trash", "reason": reason or None},
                                )
                                result["documents"] += 1
                except Exception as exc:  # Datei liegt im Papierkorb, Buchung fehlgeschlagen: sichtbar machen
                    result["errors"].append(
                        f"{item.path}: Datei im Papierkorb, Buchung fehlgeschlagen ({exc})"
                    )
                    _trash_failed(
                        obj, src, item.path, item.file_id, f"Buchung: {exc}", user=user, request=request
                    )
        for folder_id, path in plan.empty_folders:
            try:
                live = [c for c in drive.list_children_including_trashed(folder_id) if not c.trashed]
            except DriveError as exc:
                result["errors"].append(f"{path}: Ordner nicht geprüft, bleibt bestehen ({exc})")
                _trash_failed(obj, src, path, folder_id, str(exc), user=user, request=request)
                continue
            if live:
                result["errors"].append(f"{path}: Ordner ist nicht mehr leer, bleibt bestehen")
                continue
            try:
                drive.trash(folder_id)
            except DriveError as exc:
                result["errors"].append(f"{path}: {exc}")
                _trash_failed(obj, src, path, folder_id, str(exc), user=user, request=request)
                continue
            result["folders"] += 1
            record(
                "drive.trash",
                entity_type="drive_folder",
                object_id=obj.pk,
                request=request,
                actor=user,
                reason=reason or None,
                after={"drive_file_id": folder_id, "path": path, "kind": "empty_folder", "source_id": src.pk},
            )
            if folder_id == src.drive_folder_id:
                result["root_trashed"] = True
        if result["root_trashed"]:
            record(
                "drive.takeover_source_prune",
                entity_type="takeover_source",
                entity_id=src.pk,
                object_id=obj.pk,
                request=request,
                actor=user,
                before=_source_snapshot(src),
                after={"reason": "nach dem Aufräumen leer, in den Papierkorb verschoben"},
            )
            src.delete()
    finally:
        cache.delete(lock_key)
    return result


def _source_snapshot(src) -> dict:
    return {
        "drive_folder_id": src.drive_folder_id,
        "name": src.name,
        "status": src.status,
        "detected_object_number": src.detected_object_number,
        "object_id": src.object_id,
        "files_registered": src.files_registered,
        "files_skipped": src.files_skipped,
        "last_run_id": src.last_run_id,
        "taken_at": src.taken_at.isoformat() if src.taken_at else None,
    }


def refresh_sources(drive: DriveAdapter, *, user=None, request=None) -> dict[str, int]:
    """„Aktualisieren“: alle Quellordner neu aus Drive lesen. Ordner, die es nicht mehr gibt (geloescht oder im
    Papierkorb), verschwinden aus der Tabelle; in Drive wird nichts veraendert. Ein voruebergehender Drive-Fehler
    entfernt nichts, die Zeile behaelt einen Hinweis."""
    from apps.drive.adapter import DriveError
    from apps.drive.models import TakeoverSource

    result = {"checked": 0, "updated": 0, "removed": 0, "errors": 0}
    for src in TakeoverSource.objects.select_related("object").order_by("pk"):
        result["checked"] += 1
        try:
            node = drive.get(src.drive_folder_id)
        except DriveError as exc:
            src.last_error = f"Drive nicht erreichbar: {exc}"[:500]
            src.save(update_fields=["last_error", "updated_at"])
            result["errors"] += 1
            continue
        if node is None or node.trashed:
            record(
                "drive.takeover_source_prune",
                entity_type="takeover_source",
                entity_id=src.pk,
                object_id=src.object_id,
                request=request,
                actor=user,
                before=_source_snapshot(src),
                after={
                    "reason": "in Drive nicht gefunden oder kein Zugriff" if node is None else "im Papierkorb"
                },
            )
            src.delete()
            result["removed"] += 1
            continue
        if resolve_source(src, drive):
            result["updated"] += 1
        else:
            result["errors"] += 1
    return result


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
    src.files_skipped += result.skipped_registered + result.skipped_shortcuts + result.skipped_ignored
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
