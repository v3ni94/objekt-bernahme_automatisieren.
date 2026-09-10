"""Drive-Adapter-Schnittstelle, In-Memory-Fake und aufzeichnender Dekorator (Fachentwurf F 2.8, 9.1; docs/architektur.md 7.2).

Der Fake bildet Drive fuer Tests ab: Baum aus Knoten, mehrere Kinder mit gleichem Namen, Papierkorb, Verknuepfungen,
Revisionen, Operationsprotokoll, Fehlerinjektion, deterministische IDs, Szenario-Lader. count_tree ist unabhaengig
vom Abgleich nachrechenbar. Der RecordingDriveAdapter protokolliert alle Aufrufe fuer den Idempotenznachweis
(zweiter Lauf und Dry-Run enthalten nur Lesezugriffe).
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
READ_METHODS = {"get", "list_children", "list_children_including_trashed", "walk", "download", "count_tree"}
WRITE_METHODS = {"create_folder", "rename", "move", "copy", "upload", "update_content", "trash"}


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

    def to_node(self) -> DriveNode:
        md5 = (
            hashlib.md5(self.content, usedforsecurity=False).hexdigest()
            if self.mime_type != FOLDER_MIME and not self.mime_type.startswith(GOOGLE_DOC_PREFIX)
            else None
        )
        return DriveNode(
            self.id,
            self.name,
            self.mime_type,
            tuple(self.parents),
            self.trashed,
            len(self.content) if md5 is not None else None,
            md5,
            self.modified_time,
            self.created_time,
            self.shortcut_target_id,
            dict(self.app_properties),
        )


class InMemoryDriveAdapter:
    """Fake nach F 9.1. Fehlerinjektion: inject(op, error, times) laesst die naechsten Aufrufe der Operation scheitern."""

    def __init__(self, *, seed: str = "fake", start_time: datetime | None = None) -> None:
        self._entries: dict[str, _Entry] = {}
        self._seq = 0
        self._seed = seed
        self._clock = start_time or datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        self.ops: list[tuple[str, tuple]] = []
        self._faults: dict[str, list[DriveError]] = {}
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

    # --- Aufbau fuer Tests -----------------------------------------------------------------------
    def add_folder(self, parent_id: str, name: str, *, trashed: bool = False) -> str:
        fid = self._new_id()
        self._entries[fid] = _Entry(
            fid, name, FOLDER_MIME, [parent_id], trashed, created_time=self._now(), modified_time=self._now()
        )
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

    def trash(self, file_id: str) -> None:
        self._record("trash", file_id)
        self._entry(file_id).trashed = True

    def revisions(self, file_id: str) -> int:
        return self._entry(file_id).revisions

    def children_ids(self, parent_id: str) -> list[str]:
        return [e.id for e in self._entries.values() if parent_id in e.parents]

    # --- Schnittstelle ---------------------------------------------------------------------------
    def get(self, file_id: str) -> DriveNode | None:
        self._record("get", file_id)
        entry = self._entries.get(file_id)
        return entry.to_node() if entry else None

    def list_children(self, parent_id: str, folders_only: bool = False) -> list[DriveNode]:
        self._record("list_children", parent_id, folders_only)
        nodes = [e.to_node() for e in self._entries.values() if parent_id in e.parents and not e.trashed]
        if folders_only:
            nodes = [n for n in nodes if n.is_folder or n.is_shortcut]
        return sort_deterministic(nodes)

    def list_children_including_trashed(self, parent_id: str) -> list[DriveNode]:
        self._record("list_children_including_trashed", parent_id)
        return sort_deterministic([e.to_node() for e in self._entries.values() if parent_id in e.parents])

    def create_folder(self, parent_id: str, name: str) -> DriveNode:
        self._record("create_folder", parent_id, name)
        self._entry(parent_id)
        return self._entries[self.add_folder(parent_id, name)].to_node()

    def rename(self, file_id: str, new_name: str) -> DriveNode:
        self._record("rename", file_id, new_name)
        e = self._entry(file_id)
        e.name = new_name
        e.modified_time = self._now()
        return e.to_node()

    def move(self, file_id: str, from_parent_id: str, to_parent_id: str) -> DriveNode:
        self._record("move", file_id, from_parent_id, to_parent_id)
        e = self._entry(file_id)
        self._entry(to_parent_id)
        e.parents = [p for p in e.parents if p != from_parent_id] + [to_parent_id]
        e.modified_time = self._now()
        return e.to_node()

    def copy(self, file_id: str, to_parent_id: str, new_name: str) -> DriveNode:
        self._record("copy", file_id, to_parent_id, new_name)
        src = self._entry(file_id)
        fid = self.add_file(
            to_parent_id, new_name, src.content, src.mime_type, app_properties=src.app_properties
        )
        return self._entries[fid].to_node()

    def upload(
        self, parent_id: str, local_path: Path, name: str, mime_type: str, app_properties: dict | None = None
    ) -> DriveNode:
        self._record("upload", parent_id, str(local_path), name, mime_type)
        self._entry(parent_id)
        fid = self.add_file(
            parent_id, name, Path(local_path).read_bytes(), mime_type, app_properties=app_properties
        )
        return self._entries[fid].to_node()

    def update_content(self, file_id: str, local_path: Path, mime_type: str) -> DriveNode:
        self._record("update_content", file_id, str(local_path), mime_type)
        e = self._entry(file_id)
        e.content = Path(local_path).read_bytes()
        e.mime_type = mime_type
        e.revisions += 1
        e.modified_time = self._now()
        return e.to_node()

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
            [e.to_node() for e in self._entries.values() if folder_id in e.parents and not e.trashed]
        ):
            yield node, list(path)
            if node.is_folder:
                yield from self._walk(node.id, [*path, node.id])


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
