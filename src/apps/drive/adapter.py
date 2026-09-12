"""Drive-Adapter-Schnittstelle, In-Memory-Fake und aufzeichnender Dekorator (Fachentwurf F 2.8, 9.1; docs/architektur.md 7.2).

Der Fake bildet Drive fuer Tests ab: Baum aus Knoten, mehrere Kinder mit gleichem Namen, Papierkorb, Verknuepfungen,
Revisionen, Operationsprotokoll, Fehlerinjektion, deterministische IDs, Szenario-Lader. count_tree ist unabhaengig
vom Abgleich nachrechenbar. Der RecordingDriveAdapter protokolliert alle Aufrufe fuer den Idempotenznachweis
(zweiter Lauf und Dry-Run enthalten nur Lesezugriffe).

Erweiterung fuer die Synchronisation (12.09.2026): Aenderungsprotokoll mit Cursor (start_page_token, list_changes
nach dem Muster von changes.getStartPageToken und changes.list), appProperties setzen, Export von Google-Dokumenten,
Revisionsangaben (headRevisionId, version, sha256Checksum). Der Fake fuehrt dazu je Mutation eine fortlaufende
Aenderungsnummer; list_changes liefert je Datei nur den letzten Stand, endgueltig geloeschte Eintraege als removed und
Papierkorb-Eintraege wie die echte API als Datei mit trashed=True.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
GOOGLE_DOC_PREFIX = "application/vnd.google-apps."
READ_METHODS = {
    "get",
    "list_children",
    "list_children_including_trashed",
    "walk",
    "download",
    "count_tree",
    "start_page_token",
    "list_changes",
    "export",
    "get_revision_info",
}
WRITE_METHODS = {
    "create_folder",
    "rename",
    "move",
    "copy",
    "upload",
    "update_content",
    "trash",
    "set_app_properties",
}
EXPORT_PLACEHOLDER_HEADER = b"%PDF-1.4\n"


class DriveError(Exception):
    """Basisklasse; status und reason wie von der API geliefert."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.retry_after = retry_after


class NotFound(DriveError):
    pass


class RateLimited(DriveError):
    pass


class TransientError(DriveError):
    pass


class AuthError(DriveError):
    pass


class PermanentError(DriveError):
    pass


class DriveCursorInvalid(DriveError):
    """Der Cursor des Aenderungsprotokolls ist abgelaufen oder ungueltig (404 bei changes.list); der Aufrufer
    holt mit start_page_token einen neuen Cursor und gleicht den Bestand vollstaendig ab."""


@dataclass(frozen=True)
class DriveNode:
    id: str
    name: str
    mime_type: str
    parents: tuple[str, ...] = ()
    trashed: bool = False
    size: int | None = None
    md5: str | None = None
    modified_time: str = ""
    created_time: str = ""
    shortcut_target_id: str | None = None
    app_properties: dict = field(default_factory=dict)
    sha256: str | None = None
    head_revision_id: str | None = None
    version: str | None = None
    drive_id: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_shortcut(self) -> bool:
        return self.mime_type == SHORTCUT_MIME

    @property
    def is_google_doc(self) -> bool:
        return self.mime_type.startswith(GOOGLE_DOC_PREFIX) and not self.is_folder and not self.is_shortcut

    @property
    def parent_id(self) -> str | None:
        return self.parents[0] if self.parents else None


@dataclass(frozen=True)
class DriveChange:
    """Ein Eintrag des Aenderungsprotokolls. removed=True: Datei endgueltig geloescht oder der Zugriff entfallen,
    node ist dann None. Papierkorb kommt als node.trashed=True mit removed=False (Verhalten der Drive-API)."""

    file_id: str
    removed: bool
    time: str
    node: DriveNode | None
    drive_id: str | None = None


@dataclass(frozen=True)
class ChangePage:
    """Seite von changes.list. Der Aufrufer folgt next_page_token, bis new_start_page_token vorliegt, und speichert
    diesen als naechsten Cursor."""

    changes: list[DriveChange]
    next_page_token: str | None
    new_start_page_token: str | None


def normalize_app_properties(properties: dict) -> dict[str, str | None]:
    """Prueft Werte fuer appProperties: Zeichenketten bleiben, None entfernt den Schluessel (Drive erwartet null).
    Andere Typen werden abgewiesen, bevor ein Aufruf erfolgt."""
    result: dict[str, str | None] = {}
    for key, value in dict(properties).items():
        if not isinstance(key, str) or not key:
            raise PermanentError(
                "appProperties: Schluessel muessen nicht leere Zeichenketten sein", status=400
            )
        if value is not None and not isinstance(value, str):
            raise PermanentError(
                f"appProperties: Wert fuer {key!r} muss eine Zeichenkette oder None sein, nicht {type(value).__name__}",
                status=400,
                reason="badRequest",
            )
        result[key] = value
    return result


def revision_info(node: DriveNode) -> dict:
    """Revisionsangaben eines Knotens in der Form, die get_revision_info liefert."""
    return {
        "head_revision_id": node.head_revision_id,
        "version": node.version,
        "modified_time": node.modified_time,
        "md5": node.md5,
        "sha256": node.sha256,
        "size": node.size,
    }


def export_placeholder(name: str, mime_type: str) -> bytes:
    """Deterministischer PDF-Platzhalter des Fakes fuer den Export eines Google-Dokuments."""
    return EXPORT_PLACEHOLDER_HEADER + f"% Export {name} als {mime_type}\n".encode()


class DriveAdapter(Protocol):
    def get(self, file_id: str) -> DriveNode | None: ...
    def list_children(self, parent_id: str, folders_only: bool = False) -> list[DriveNode]: ...
    def list_children_including_trashed(self, parent_id: str) -> list[DriveNode]: ...
    def create_folder(self, parent_id: str, name: str) -> DriveNode: ...
    def rename(self, file_id: str, new_name: str) -> DriveNode: ...
    def move(self, file_id: str, from_parent_id: str, to_parent_id: str) -> DriveNode: ...
    def copy(self, file_id: str, to_parent_id: str, new_name: str) -> DriveNode: ...
    def upload(
        self, parent_id: str, local_path: Path, name: str, mime_type: str, app_properties: dict | None = None
    ) -> DriveNode: ...
    def update_content(self, file_id: str, local_path: Path, mime_type: str) -> DriveNode: ...
    def download(self, file_id: str, target_path: Path) -> None: ...
    def walk(self, folder_id: str) -> Iterator[tuple[DriveNode, list[str]]]: ...
    def trash(self, file_id: str) -> None: ...  # Papierkorb, nie endgueltig (Aufraeumen 12.09.2026)

    # --- Synchronisation (12.09.2026) ---
    def start_page_token(self, drive_id: str | None = None) -> str: ...
    def list_changes(
        self, page_token: str, *, drive_id: str | None = None, page_size: int = 1000
    ) -> ChangePage: ...
    def set_app_properties(self, file_id: str, properties: dict) -> DriveNode: ...
    def export(self, file_id: str, mime_type: str, target_path: Path) -> Path: ...
    def get_revision_info(self, file_id: str) -> dict: ...


def sort_deterministic(nodes: list[DriveNode]) -> list[DriveNode]:
    return sorted(nodes, key=lambda n: (unicodedata.normalize("NFC", n.name).casefold(), n.id))


def count_tree(drive: DriveAdapter, folder_id: str) -> tuple[int, str]:
    """Anzahl aller nicht geloeschten Nicht-Ordner-Eintraege rekursiv und SHA-256 ueber die sortierten IDs (F 4.2)."""
    ids: list[str] = []
    for node, _path in drive.walk(folder_id):
        if not node.is_folder and not node.trashed:
            ids.append(node.id)
    ids.sort()
    return len(ids), hashlib.sha256("\n".join(ids).encode("ascii")).hexdigest()


# ---------------------------------------------------------------- Fake
@dataclass
class _Entry:
    id: str
    name: str
    mime_type: str
    parents: list[str]
    trashed: bool = False
    content: bytes = b""
    revisions: int = 1
    created_time: str = ""
    modified_time: str = ""
    shortcut_target_id: str | None = None
    app_properties: dict = field(default_factory=dict)

    @property
    def has_binary_content(self) -> bool:
        return self.mime_type != FOLDER_MIME and not self.mime_type.startswith(GOOGLE_DOC_PREFIX)

    def to_node(self, drive_id: str | None = None) -> DriveNode:
        binary = self.has_binary_content
        md5 = hashlib.md5(self.content, usedforsecurity=False).hexdigest() if binary else None
        sha256 = hashlib.sha256(self.content).hexdigest() if binary else None
        return DriveNode(
            self.id,
            self.name,
            self.mime_type,
            tuple(self.parents),
            self.trashed,
            len(self.content) if binary else None,
            md5,
            self.modified_time,
            self.created_time,
            self.shortcut_target_id,
            dict(self.app_properties),
            sha256=sha256,
            head_revision_id=f"rev-{self.revisions}",
            version=str(self.revisions),
            drive_id=drive_id,
        )


class InMemoryDriveAdapter:
    """Fake nach F 9.1. Fehlerinjektion: inject(op, error, times) laesst die naechsten Aufrufe der Operation scheitern.

    Aenderungsprotokoll: jede Mutation erhaelt eine fortlaufende Nummer; start_page_token liefert die naechste
    Nummer, list_changes alle Eintraege ab dem Cursor (je Datei nur der letzte Stand). drive_id wird, wenn gesetzt,
    wie bei einer geteilten Ablage an jedem Knoten und jeder Aenderung mitgegeben."""

    def __init__(
        self, *, seed: str = "fake", start_time: datetime | None = None, drive_id: str | None = None
    ) -> None:
        self._entries: dict[str, _Entry] = {}
        self._seq = 0
        self._seed = seed
        self._clock = start_time or datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        self.drive_id = drive_id
        self.ops: list[tuple[str, tuple]] = []
        self._faults: dict[str, list[DriveError]] = {}
        self._change_seq = 0
        self._change_log: dict[str, tuple[int, str, bool]] = {}  # file_id -> (Nummer, Zeit, entfernt)
        self.root_id = self._new_id()
        self._entries[self.root_id] = _Entry(
            self.root_id, "Meine Ablage", FOLDER_MIME, [], created_time=self._now(), modified_time=self._now()
        )

    # --- Infrastruktur ---------------------------------------------------------------------------
    def _new_id(self) -> str:
        self._seq += 1
        return f"{self._seed}-{self._seq:04d}"

    def _now(self) -> str:
        self._clock += timedelta(seconds=1)
        return self._clock.isoformat().replace("+00:00", "Z")

    def _fail_if_injected(self, op: str) -> None:
        queue = self._faults.get(op)
        if queue:
            raise queue.pop(0)

    def inject(self, op: str, error: DriveError, times: int = 1) -> None:
        self._faults.setdefault(op, []).extend([error] * times)

    def _record(self, op: str, *args) -> None:
        self.ops.append((op, args))
        self._fail_if_injected(op)

    def _entry(self, file_id: str) -> _Entry:
        entry = self._entries.get(file_id)
        if entry is None:
            raise NotFound(f"{file_id} nicht gefunden", status=404, reason="notFound")
        return entry

    def _node(self, entry: _Entry) -> DriveNode:
        return entry.to_node(self.drive_id)

    def _touch(self, file_id: str, *, removed: bool = False, time: str | None = None) -> None:
        """Vergibt die naechste Aenderungsnummer fuer die Datei; nur der letzte Stand je Datei bleibt erhalten."""
        self._change_seq += 1
        entry = self._entries.get(file_id)
        when = time or (entry.modified_time if entry is not None else self._now())
        self._change_log[file_id] = (self._change_seq, when, removed)

    # --- Aufbau fuer Tests -----------------------------------------------------------------------
    def add_folder(self, parent_id: str, name: str, *, trashed: bool = False) -> str:
        fid = self._new_id()
        self._entries[fid] = _Entry(
            fid, name, FOLDER_MIME, [parent_id], trashed, created_time=self._now(), modified_time=self._now()
        )
        self._touch(fid)
        return fid

    def add_file(
        self,
        parent_id: str,
        name: str,
        content: bytes = b"x",
        mime_type: str = "application/pdf",
        *,
        trashed: bool = False,
        app_properties: dict | None = None,
    ) -> str:
        fid = self._new_id()
        self._entries[fid] = _Entry(
            fid,
            name,
            mime_type,
            [parent_id],
            trashed,
            content,
            created_time=self._now(),
            modified_time=self._now(),
            app_properties=dict(app_properties or {}),
        )
        self._touch(fid)
        return fid

    def add_google_doc(self, parent_id: str, name: str, kind: str = "document") -> str:
        fid = self._new_id()
        self._entries[fid] = _Entry(
            fid,
            name,
            f"{GOOGLE_DOC_PREFIX}{kind}",
            [parent_id],
            created_time=self._now(),
            modified_time=self._now(),
        )
        self._touch(fid)
        return fid

    def add_shortcut(self, parent_id: str, name: str, target_id: str) -> str:
        fid = self._new_id()
        self._entries[fid] = _Entry(
            fid,
            name,
            SHORTCUT_MIME,
            [parent_id],
            created_time=self._now(),
            modified_time=self._now(),
            shortcut_target_id=target_id,
        )
        self._touch(fid)
        return fid

    def load_scenario(self, parent_id: str, tree: dict) -> dict[str, str]:
        """Baum aus Namen: {"Ordner": {...}, "datei.pdf": b"inhalt" oder "inhalt", "~Papierkorb": {...}} (Tilde = im Papierkorb)."""
        ids: dict[str, str] = {}
        for name, value in tree.items():
            trashed = name.startswith("~")
            clean = name[1:] if trashed else name
            if isinstance(value, dict):
                fid = self.add_folder(parent_id, clean, trashed=trashed)
                ids[clean] = fid
                for sub_name, sub_id in self.load_scenario(fid, value).items():
                    ids[f"{clean}/{sub_name}"] = sub_id
            else:
                data = value.encode() if isinstance(value, str) else (value or b"x")
                ids[clean] = self.add_file(parent_id, clean, data, trashed=trashed)
        return ids

    def set_content(self, file_id: str, content: bytes) -> None:
        e = self._entry(file_id)
        e.content = content
        e.revisions += 1
        e.modified_time = self._now()
        self._touch(file_id)

    def trash(self, file_id: str) -> None:
        self._record("trash", file_id)
        self.set_trashed(file_id, True)

    def set_trashed(self, file_id: str, trashed: bool) -> None:
        """Testhilfe: Papierkorbzustand setzen, wie es ein Nutzer in Drive tut (erscheint im Aenderungsprotokoll)."""
        e = self._entry(file_id)
        e.trashed = trashed
        e.modified_time = self._now()
        self._touch(file_id)

    def restore(self, file_id: str) -> None:
        """Testhilfe: aus dem Papierkorb wiederherstellen."""
        self.set_trashed(file_id, False)

    def delete_permanently(self, file_id: str) -> None:
        """Testhilfe: endgueltige Loeschung durch einen Nutzer in Drive nachbilden; der Adapter selbst loescht nie.
        Erscheint im Aenderungsprotokoll als removed=True ohne Knoten."""
        self._entry(file_id)
        del self._entries[file_id]
        self._touch(file_id, removed=True)

    def revisions(self, file_id: str) -> int:
        return self._entry(file_id).revisions

    def children_ids(self, parent_id: str) -> list[str]:
        return [e.id for e in self._entries.values() if parent_id in e.parents]

    # --- Schnittstelle ---------------------------------------------------------------------------
    def get(self, file_id: str) -> DriveNode | None:
        self._record("get", file_id)
        entry = self._entries.get(file_id)
        return self._node(entry) if entry else None

    def list_children(self, parent_id: str, folders_only: bool = False) -> list[DriveNode]:
        self._record("list_children", parent_id, folders_only)
        nodes = [self._node(e) for e in self._entries.values() if parent_id in e.parents and not e.trashed]
        if folders_only:
            nodes = [n for n in nodes if n.is_folder or n.is_shortcut]
        return sort_deterministic(nodes)

    def list_children_including_trashed(self, parent_id: str) -> list[DriveNode]:
        self._record("list_children_including_trashed", parent_id)
        return sort_deterministic([self._node(e) for e in self._entries.values() if parent_id in e.parents])

    def create_folder(self, parent_id: str, name: str) -> DriveNode:
        self._record("create_folder", parent_id, name)
        self._entry(parent_id)
        return self._node(self._entries[self.add_folder(parent_id, name)])

    def rename(self, file_id: str, new_name: str) -> DriveNode:
        self._record("rename", file_id, new_name)
        e = self._entry(file_id)
        e.name = new_name
        e.modified_time = self._now()
        self._touch(file_id)
        return self._node(e)

    def move(self, file_id: str, from_parent_id: str, to_parent_id: str) -> DriveNode:
        self._record("move", file_id, from_parent_id, to_parent_id)
        e = self._entry(file_id)
        self._entry(to_parent_id)
        e.parents = [p for p in e.parents if p != from_parent_id] + [to_parent_id]
        e.modified_time = self._now()
        self._touch(file_id)
        return self._node(e)

    def copy(self, file_id: str, to_parent_id: str, new_name: str) -> DriveNode:
        self._record("copy", file_id, to_parent_id, new_name)
        src = self._entry(file_id)
        fid = self.add_file(
            to_parent_id, new_name, src.content, src.mime_type, app_properties=src.app_properties
        )
        return self._node(self._entries[fid])

    def upload(
        self, parent_id: str, local_path: Path, name: str, mime_type: str, app_properties: dict | None = None
    ) -> DriveNode:
        self._record("upload", parent_id, str(local_path), name, mime_type)
        self._entry(parent_id)
        fid = self.add_file(
            parent_id, name, Path(local_path).read_bytes(), mime_type, app_properties=app_properties
        )
        return self._node(self._entries[fid])

    def update_content(self, file_id: str, local_path: Path, mime_type: str) -> DriveNode:
        self._record("update_content", file_id, str(local_path), mime_type)
        e = self._entry(file_id)
        e.content = Path(local_path).read_bytes()
        e.mime_type = mime_type
        e.revisions += 1
        e.modified_time = self._now()
        self._touch(file_id)
        return self._node(e)

    def download(self, file_id: str, target_path: Path) -> None:
        self._record("download", file_id, str(target_path))
        e = self._entry(file_id)
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(e.content)

    def walk(self, folder_id: str) -> Iterator[tuple[DriveNode, list[str]]]:
        self._record("walk", folder_id)
        yield from self._walk(folder_id, [folder_id])

    def _walk(self, folder_id: str, path: list[str]) -> Iterator[tuple[DriveNode, list[str]]]:
        for node in sort_deterministic(
            [self._node(e) for e in self._entries.values() if folder_id in e.parents and not e.trashed]
        ):
            yield node, list(path)
            if node.is_folder:
                yield from self._walk(node.id, [*path, node.id])

    # --- Synchronisation (12.09.2026) -----------------------------------------------------------
    def start_page_token(self, drive_id: str | None = None) -> str:
        """Naechste Aenderungsnummer als Cursor; Aenderungen ab diesem Stand liefert list_changes."""
        self._record("start_page_token", drive_id)
        return str(self._change_seq + 1)

    def list_changes(
        self, page_token: str, *, drive_id: str | None = None, page_size: int = 1000
    ) -> ChangePage:
        """Aenderungen mit Nummer >= page_token, je Datei nur der letzte Stand, paginiert nach page_size.
        Ein nicht numerischer oder in der Zukunft liegender Cursor gilt wie bei Drive als ungueltig."""
        self._record("list_changes", page_token, drive_id, page_size)
        if not str(page_token).isdigit() or int(page_token) < 1 or int(page_token) > self._change_seq + 1:
            raise DriveCursorInvalid(
                f"Cursor {page_token!r} ist ungueltig oder abgelaufen", status=404, reason="notFound"
            )
        if page_size < 1:
            raise PermanentError("page_size muss mindestens 1 sein", status=400, reason="badRequest")
        start = int(page_token)
        pending = sorted(
            (
                (no, when, removed, fid)
                for fid, (no, when, removed) in self._change_log.items()
                if no >= start
            ),
        )
        page, rest = pending[:page_size], pending[page_size:]
        changes = []
        for _no, when, removed, fid in page:
            entry = self._entries.get(fid)
            node = self._node(entry) if entry is not None and not removed else None
            changes.append(DriveChange(fid, removed or node is None, when, node, drive_id or self.drive_id))
        if rest:
            return ChangePage(changes, str(rest[0][0]), None)
        return ChangePage(changes, None, str(self._change_seq + 1))

    def set_app_properties(self, file_id: str, properties: dict) -> DriveNode:
        """Setzt appProperties; None entfernt einen Schluessel (Drive-Semantik: Wert null)."""
        self._record("set_app_properties", file_id, dict(properties))
        cleaned = normalize_app_properties(properties)
        e = self._entry(file_id)
        for key, value in cleaned.items():
            if value is None:
                e.app_properties.pop(key, None)
            else:
                e.app_properties[key] = value
        e.modified_time = self._now()
        self._touch(file_id)
        return self._node(e)

    def export(self, file_id: str, mime_type: str, target_path: Path) -> Path:
        """Export eines Google-Dokuments als deterministischer PDF-Platzhalter; andere Dateien sind nicht exportierbar."""
        self._record("export", file_id, mime_type, str(target_path))
        e = self._entry(file_id)
        if not self._node(e).is_google_doc:
            raise PermanentError(
                f"{file_id} ist kein Google-Dokument ({e.mime_type}); export nicht moeglich, download verwenden",
                status=403,
                reason="fileNotExportable",
            )
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_bytes(export_placeholder(e.name, mime_type))
        return Path(target_path)

    def get_revision_info(self, file_id: str) -> dict:
        self._record("get_revision_info", file_id)
        return revision_info(self._node(self._entry(file_id)))


class RecordingDriveAdapter:
    """Dekorator: zeichnet Methodennamen und Argumente auf; write_calls zeigt Schreibzugriffe (F 4.6)."""

    def __init__(self, inner: DriveAdapter) -> None:
        self._inner = inner
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name: str) -> Callable:
        attr = getattr(self._inner, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        def wrapper(*args, **kwargs):
            self.calls.append((name, args))
            result = attr(*args, **kwargs)
            if name == "walk":
                return list(result)
            return result

        return wrapper

    @property
    def write_calls(self) -> list[tuple[str, tuple]]:
        return [c for c in self.calls if c[0] in WRITE_METHODS]

    @property
    def read_only(self) -> bool:
        return not self.write_calls

    def reset(self) -> None:
        self.calls.clear()
