"""In-Memory-Fake eines Paperless-ngx-Servers fuer Tests und Trockenlaeufe.

Bietet dieselben Methoden wie PaperlessClient, haelt Dokumente, Tags, Felder, Dokumenttypen, Korrespondenten,
Notizen und Aufgaben im Speicher. post_document und update_version legen Aufgaben im Status PENDING an,
process_tasks() fuehrt sie aus (Dublettenpruefung ueber die SHA-256-Pruefsumme, konfigurierbar). Alle Aufrufe
werden in calls aufgezeichnet, inject(method, exc, times) laesst die naechsten Aufrufe einer Methode scheitern
(Muster wie InMemoryDriveAdapter). modified wird bei jeder Aenderung fortgeschrieben.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from apps.sync.paperless.client import Page, ServerInfo, normalize_task
from apps.sync.paperless.errors import PaperlessError, PaperlessNotFound, PaperlessUnsupported

DOCUMENT_FIELDS = (
    "id",
    "title",
    "content",
    "created",
    "added",
    "modified",
    "original_file_name",
    "archived_file_name",
    "checksum",
    "archive_checksum",
    "mime_type",
    "tags",
    "custom_fields",
    "document_type",
    "correspondent",
    "storage_path",
    "archive_serial_number",
    "notes",
    "owner",
    "root_document",
    "versions",
    "deleted_at",
)


def _parse_dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class FakePaperless:
    """Fake nach dem Muster des InMemoryDriveAdapter; Zaehler fuer IDs laufen fortlaufend ab 1."""

    def __init__(
        self,
        *,
        server_version: str = "2.15.0",
        api_version: int = 10,
        features: dict[str, bool] | None = None,
        reject_duplicates: bool = True,
        page_size: int = 100,
        start_time: datetime | None = None,
    ) -> None:
        self.server_version = server_version
        self.api_version = api_version
        self.features = {
            "document_versions": True,
            "custom_fields": True,
            "bulk_edit": True,
            "tasks_v10": api_version >= 10,
            "schema_available": True,
        }
        self.features.update(features or {})
        self.reject_duplicates = reject_duplicates
        self.page_size = page_size
        self._clock = start_time or datetime(2026, 9, 12, 8, 0, tzinfo=UTC)
        self.documents: dict[int, dict] = {}
        self.tags: dict[int, dict] = {}
        self.custom_fields: dict[int, dict] = {}
        self.document_types: dict[int, dict] = {}
        self.correspondents: dict[int, dict] = {}
        self.storage_paths: dict[int, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self._faults: dict[str, list[Exception]] = {}
        self._seq: dict[str, int] = {}
        self._task_order: list[str] = []

    # --- Infrastruktur ---------------------------------------------------------------------------
    def _next(self, kind: str) -> int:
        self._seq[kind] = self._seq.get(kind, 0) + 1
        return self._seq[kind]

    def _now(self) -> str:
        self._clock += timedelta(seconds=1)
        return self._clock.isoformat()

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))
        queue = self._faults.get(method)
        if queue:
            raise queue.pop(0)

    def inject(self, method: str, exc: Exception, times: int = 1) -> None:
        """Laesst die naechsten times Aufrufe von method mit exc scheitern."""
        self._faults.setdefault(method, []).extend([exc] * times)

    def reset_calls(self) -> None:
        self.calls.clear()

    def call_names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def _doc(self, document_id: int) -> dict:
        doc = self.documents.get(int(document_id))
        if doc is None:
            raise PaperlessNotFound(f"Paperless GET /api/documents/{document_id}/: HTTP 404", status_code=404)
        return doc

    def _touch(self, doc: dict) -> None:
        doc["modified"] = self._now()

    @staticmethod
    def checksum_of(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _find_duplicate(self, checksum: str) -> dict | None:
        for doc in self.documents.values():
            if doc["checksum"] == checksum or doc.get("archive_checksum") == checksum:
                return doc
        return None

    # --- Aufbau fuer Tests -----------------------------------------------------------------------
    def add_document(
        self,
        title: str,
        content: bytes = b"",
        *,
        created: datetime | str | None = None,
        tags: Iterable[int] = (),
        custom_fields: Iterable[dict] | dict[int, Any] | None = None,
        document_type: int | None = None,
        correspondent: int | None = None,
        storage_path: int | None = None,
        original_file_name: str | None = None,
        mime_type: str = "application/pdf",
        archive_checksum: str | None = None,
        archive_serial_number: int | None = None,
        owner: int | None = None,
        notes: Iterable[str] = (),
        text: str = "",
        root_document: int | None = None,
    ) -> int:
        """Legt ein Dokument direkt an (ohne Aufgabe) und liefert die ID."""
        doc_id = self._next("document")
        now = self._now()
        created_value = _parse_dt(created) or self._clock
        if isinstance(custom_fields, dict):
            fields = [{"field": int(k), "value": v} for k, v in custom_fields.items()]
        else:
            fields = [dict(f) for f in (custom_fields or [])]
        self.documents[doc_id] = {
            "id": doc_id,
            "title": title,
            "content": text,
            "created": created_value.date().isoformat(),
            "added": now,
            "modified": now,
            "original_file_name": original_file_name or f"{title}.pdf",
            "archived_file_name": None,
            "checksum": self.checksum_of(content),
            "archive_checksum": archive_checksum,
            "mime_type": mime_type,
            "tags": sorted({int(t) for t in tags}),
            "custom_fields": fields,
            "document_type": document_type,
            "correspondent": correspondent,
            "storage_path": storage_path,
            "archive_serial_number": archive_serial_number,
            "notes": [{"id": self._next("note"), "note": n, "created": now, "user": owner} for n in notes],
            "owner": owner,
            "root_document": root_document,
            "versions": [],
            "deleted_at": None,
            "_bytes": bytes(content),
        }
        return doc_id

    def trash_document(self, document_id: int) -> None:
        """Legt ein Dokument in den Papierkorb (deleted_at gesetzt, weiter per ID lesbar, wie in Paperless-ngx)."""
        doc = self._doc(document_id)
        doc["deleted_at"] = self._now()
        self._touch(doc)

    def restore_document(self, document_id: int) -> None:
        doc = self._doc(document_id)
        doc["deleted_at"] = None
        self._touch(doc)

    def set_content(self, document_id: int, content: bytes) -> None:
        doc = self._doc(document_id)
        doc["_bytes"] = bytes(content)
        doc["checksum"] = self.checksum_of(content)
        self._touch(doc)

    def delete_document(self, document_id: int) -> None:
        self._doc(document_id)
        del self.documents[int(document_id)]

    def _public(self, doc: dict, fields: Iterable[str] | None = None) -> dict:
        wanted = list(fields) if fields else DOCUMENT_FIELDS
        out = {k: deepcopy(doc.get(k)) for k in wanted if k in doc and not k.startswith("_")}
        if not self.features.get("document_versions"):
            out.pop("root_document", None)
            out.pop("versions", None)
        return out

    # --- Serverinformationen ---------------------------------------------------------------------
    def server_info(self, *, refresh: bool = False) -> ServerInfo:
        self._record("server_info", refresh=refresh)
        return ServerInfo(
            server_version=self.server_version,
            api_version=self.api_version,
            features=dict(self.features),
            endpoints_seen={"/api/documents/": 200},
        )

    # --- Dokumente ---------------------------------------------------------------------------------
    def _filtered(
        self,
        *,
        modified_after: datetime | str | None,
        added_after: datetime | str | None,
        ids: Iterable[int] | None,
        ordering: str,
    ) -> list[dict]:
        docs = list(self.documents.values())
        if ids is not None:
            wanted = {int(i) for i in ids}
            docs = [d for d in docs if d["id"] in wanted]
        threshold = _parse_dt(modified_after)
        if threshold is not None:
            docs = [d for d in docs if _parse_dt(d["modified"]) > threshold]
        threshold = _parse_dt(added_after)
        if threshold is not None:
            docs = [d for d in docs if _parse_dt(d["added"]) > threshold]
        key = ordering.lstrip("-") or "id"
        docs.sort(key=lambda d: (d.get(key) is None, d.get(key), d["id"]), reverse=ordering.startswith("-"))
        return docs

    def iter_pages(
        self,
        *,
        modified_after: datetime | str | None = None,
        added_after: datetime | str | None = None,
        ids: Iterable[int] | None = None,
        ordering: str = "id",
        fields: Iterable[str] | None = None,
        page_size: int | None = None,
        start_page: int = 1,
    ) -> Iterator[Page]:
        self._record(
            "iter_pages",
            modified_after=modified_after,
            added_after=added_after,
            ids=None if ids is None else list(ids),
            ordering=ordering,
            page_size=page_size,
            start_page=start_page,
        )
        ids_list = None if ids is None else [int(i) for i in ids]
        docs = self._filtered(
            modified_after=modified_after, added_after=added_after, ids=ids_list, ordering=ordering
        )
        size = int(page_size or self.page_size)
        field_list = list(fields) if fields else None
        total = len(docs)
        number = max(1, int(start_page))
        while True:
            start = (number - 1) * size
            chunk = docs[start : start + size]
            has_next = start + size < total
            if not chunk and number > 1:
                return
            yield Page(
                number=number,
                count=total,
                results=[self._public(d, field_list) for d in chunk],
                has_next=has_next,
            )
            if not has_next:
                return
            number += 1

    def list_documents(
        self,
        *,
        modified_after: datetime | str | None = None,
        added_after: datetime | str | None = None,
        ids: Iterable[int] | None = None,
        ordering: str = "id",
        fields: Iterable[str] | None = None,
        page_size: int | None = None,
    ) -> Iterator[dict]:
        self._record(
            "list_documents", modified_after=modified_after, added_after=added_after, ordering=ordering
        )
        for page in self.iter_pages(
            modified_after=modified_after,
            added_after=added_after,
            ids=ids,
            ordering=ordering,
            fields=fields,
            page_size=page_size,
        ):
            yield from page.results

    def find_by_custom_field(self, field_name: str, value: str) -> dict | None:
        self._record("find_by_custom_field", field_name, value)
        field = next(
            (f for f in self.custom_fields.values() if f["name"].casefold() == field_name.casefold()), None
        )
        if field is None:
            return None
        for doc in self.documents.values():
            for cf in doc["custom_fields"]:
                if int(cf["field"]) == field["id"] and str(cf.get("value")) == str(value):
                    return self._public(doc)
        return None

    def get_document(self, document_id: int) -> dict:
        self._record("get_document", document_id)
        return self._public(self._doc(document_id))

    def get_metadata(self, document_id: int) -> dict:
        self._record("get_metadata", document_id)
        doc = self._doc(document_id)
        has_archive = doc.get("archive_checksum") is not None
        return {
            "original_checksum": doc["checksum"],
            "original_size": len(doc["_bytes"]),
            "original_mime_type": doc["mime_type"],
            "original_filename": doc["original_file_name"],
            "media_filename": f"{doc['id']:07d}.pdf",
            "has_archive_version": has_archive,
            "archive_checksum": doc.get("archive_checksum"),
            "archive_size": len(doc["_bytes"]) if has_archive else None,
            "archive_media_filename": f"{doc['id']:07d}.pdf" if has_archive else None,
            "original_metadata": [],
            "archive_metadata": [],
            "lang": "de",
        }

    def get_notes(self, document_id: int) -> list[dict]:
        self._record("get_notes", document_id)
        return deepcopy(self._doc(document_id)["notes"])

    def download(self, document_id: int, target: Path, *, original: bool = True) -> Path:
        self._record("download", document_id, str(target), original=original)
        doc = self._doc(document_id)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(doc["_bytes"])
        return target

    def download_thumbnail(self, document_id: int, target: Path) -> Path:
        self._record("download_thumbnail", document_id, str(target))
        doc = self._doc(document_id)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG-vorschau-" + str(doc["id"]).encode())
        return target

    def post_document(
        self,
        path: Path,
        *,
        title: str,
        created: datetime | str | None = None,
        tags: Iterable[int] = (),
        custom_fields: dict[int, Any] | Iterable[int] | None = None,
        document_type: int | None = None,
        correspondent: int | None = None,
        filename: str | None = None,
        storage_path: int | None = None,
        archive_serial_number: int | None = None,
    ) -> str:
        """Legt eine Aufgabe PENDING an; erst process_tasks() erzeugt das Dokument."""
        path = Path(path)
        self._record("post_document", str(path), title=title, tags=list(tags))
        content = path.read_bytes()
        if isinstance(custom_fields, dict):
            fields: list[dict] = [{"field": int(k), "value": v} for k, v in custom_fields.items()]
        else:
            fields = [{"field": int(f), "value": None} for f in (custom_fields or [])]
        return self._enqueue(
            "consume_file",
            {
                "title": title,
                "content": content,
                "created": created,
                "tags": [int(t) for t in tags],
                "custom_fields": fields,
                "document_type": document_type,
                "correspondent": correspondent,
                "storage_path": storage_path,
                "archive_serial_number": archive_serial_number,
                "filename": filename or path.name,
            },
        )

    def update_version(self, document_id: int, path: Path, label: str | None = None) -> str:
        self._record("update_version", document_id, str(path), label=label)
        if not self.features.get("document_versions"):
            raise PaperlessUnsupported("Der Paperless-Server bietet keine Dokumentversionen an")
        root = self._doc(document_id)
        root_id = root.get("root_document") or root["id"]
        content = Path(path).read_bytes()
        return self._enqueue(
            "update_version",
            {"root_document": root_id, "content": content, "label": label, "filename": Path(path).name},
        )

    def _enqueue(self, task_type: str, payload: dict) -> str:
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = {
            "task_id": task_id,
            "task_file_name": payload.get("filename"),
            "task_type": task_type if task_type == "consume_file" else "file",
            "status": "PENDING",
            "result": None,
            "date_created": self._now(),
            "date_done": None,
            "acknowledged": False,
            "related_document_ids": [],
            "_payload": payload,
            "_kind": task_type,
        }
        self._task_order.append(task_id)
        return task_id

    def process_tasks(self, *, limit: int | None = None) -> list[dict]:
        """Fuehrt ausstehende Aufgaben aus; liefert die normalisierten Ergebnisse."""
        done: list[dict] = []
        for task_id in list(self._task_order):
            task = self.tasks[task_id]
            if task["status"] != "PENDING":
                continue
            if limit is not None and len(done) >= limit:
                break
            task["status"] = "STARTED"
            try:
                self._execute(task)
            except PaperlessError as exc:
                task["status"] = "FAILURE"
                task["result"] = str(exc)
            task["date_done"] = self._now()
            done.append(normalize_task(self._task_view(task)))
        return done

    def _execute(self, task: dict) -> None:
        payload = task["_payload"]
        content: bytes = payload["content"]
        checksum = self.checksum_of(content)
        duplicate = self._find_duplicate(checksum)
        if duplicate is not None and self.reject_duplicates and task["_kind"] == "consume_file":
            task["status"] = "FAILURE"
            task["result"] = (
                f"Not consuming {payload['filename']}: It is a duplicate of "
                f"{duplicate['title']} (#{duplicate['id']})."
            )
            return
        if task["_kind"] == "consume_file":
            doc_id = self.add_document(
                payload["title"],
                content,
                created=payload.get("created"),
                tags=payload["tags"],
                custom_fields=payload["custom_fields"],
                document_type=payload.get("document_type"),
                correspondent=payload.get("correspondent"),
                storage_path=payload.get("storage_path"),
                original_file_name=payload["filename"],
                archive_serial_number=payload.get("archive_serial_number"),
            )
            task["result"] = f"Success. New document id {doc_id} created"
        else:
            root = self._doc(payload["root_document"])
            doc_id = self.add_document(
                root["title"],
                content,
                created=root["created"],
                tags=root["tags"],
                custom_fields=deepcopy(root["custom_fields"]),
                document_type=root.get("document_type"),
                correspondent=root.get("correspondent"),
                storage_path=root.get("storage_path"),
                original_file_name=payload["filename"],
                root_document=root["id"],
            )
            self.documents[doc_id]["version_label"] = payload.get("label")
            root["versions"].append(doc_id)
            self._touch(root)
            task["result"] = f"Success. New version {doc_id} of document {root['id']} created"
        task["status"] = "SUCCESS"
        task["related_document_ids"] = [doc_id]

    def _task_view(self, task: dict) -> dict:
        view = {k: v for k, v in task.items() if not k.startswith("_")}
        ids = view.pop("related_document_ids")
        if self.api_version >= 10:
            view["related_document_ids"] = list(ids)
        else:
            view["related_document"] = ",".join(str(i) for i in ids) if ids else None
        return view

    def get_task(self, task_id: str) -> dict | None:
        self._record("get_task", task_id)
        task = self.tasks.get(task_id)
        return normalize_task(self._task_view(task)) if task else None

    def patch_document(self, document_id: int, **fields: Any) -> dict:
        self._record("patch_document", document_id, **fields)
        if not fields:
            raise ValueError("patch_document ohne Felder")
        doc = self._doc(document_id)
        for key, value in fields.items():
            if key == "tags":
                doc["tags"] = sorted({int(t) for t in value})
            elif key == "custom_fields":
                doc["custom_fields"] = [{"field": int(f["field"]), "value": f.get("value")} for f in value]
            elif key in DOCUMENT_FIELDS and key not in ("id", "added", "modified", "checksum", "versions"):
                doc[key] = deepcopy(value)
            else:
                raise PaperlessError(f"Paperless PATCH: Feld {key} unbekannt", status_code=400)
        self._touch(doc)
        return self._public(doc)

    def bulk_edit(self, document_ids: Iterable[int], method: str, parameters: dict | None = None) -> dict:
        ids = [int(i) for i in document_ids]
        params = dict(parameters or {})
        self._record("bulk_edit", ids, method, params)
        docs = [self._doc(i) for i in ids]  # alles oder nichts: erst pruefen, dann aendern
        if method == "add_tag":
            for doc in docs:
                doc["tags"] = sorted(set(doc["tags"]) | {int(params["tag"])})
        elif method == "remove_tag":
            for doc in docs:
                doc["tags"] = sorted(set(doc["tags"]) - {int(params["tag"])})
        elif method == "modify_tags":
            add = {int(t) for t in params.get("add_tags", [])}
            remove = {int(t) for t in params.get("remove_tags", [])}
            for doc in docs:
                doc["tags"] = sorted((set(doc["tags"]) | add) - remove)
        elif method == "modify_custom_fields":
            add = params.get("add_custom_fields") or {}
            if isinstance(add, list):
                add = {int(f): None for f in add}
            remove = {int(f) for f in params.get("remove_custom_fields", [])}
            for doc in docs:
                current = {int(f["field"]): f.get("value") for f in doc["custom_fields"]}
                for field_id in remove:
                    current.pop(field_id, None)
                for field_id, value in add.items():
                    current[int(field_id)] = value
                doc["custom_fields"] = [{"field": k, "value": v} for k, v in current.items()]
        elif method == "set_document_type":
            for doc in docs:
                doc["document_type"] = params.get("document_type")
        elif method == "set_correspondent":
            for doc in docs:
                doc["correspondent"] = params.get("correspondent")
        elif method == "set_storage_path":
            for doc in docs:
                doc["storage_path"] = params.get("storage_path")
        elif method == "set_permissions":
            for doc in docs:
                if "owner" in params:
                    doc["owner"] = params["owner"]
        elif method == "delete":
            for doc in docs:
                del self.documents[doc["id"]]
            return {"result": "OK"}
        elif method == "reprocess":
            pass
        else:
            raise PaperlessError(f"Paperless bulk_edit: Methode {method} unbekannt", status_code=400)
        for doc in docs:
            self._touch(doc)
        return {"result": "OK"}

    def add_tags(self, document_id: int, tag_ids: Iterable[int]) -> dict:
        ids = [int(t) for t in tag_ids]
        if not ids:
            return {"result": "OK"}
        return self.bulk_edit([document_id], "modify_tags", {"add_tags": ids, "remove_tags": []})

    def set_custom_field_values(self, document_id: int, values: dict[int, Any]) -> dict:
        if not values:
            return {"result": "OK"}
        return self.bulk_edit(
            [document_id],
            "modify_custom_fields",
            {"add_custom_fields": {str(int(k)): v for k, v in values.items()}, "remove_custom_fields": []},
        )

    # --- Stammdaten --------------------------------------------------------------------------------
    def _create_named(self, store: dict[int, dict], kind: str, name: str, **extra: Any) -> dict:
        for row in store.values():
            if row["name"].casefold() == name.casefold():
                raise PaperlessError(
                    f"Paperless POST: {kind} mit dem Namen {name} existiert bereits", status_code=400
                )
        row_id = self._next(kind)
        row = {"id": row_id, "name": name, "slug": name.casefold().replace(" ", "-"), **extra}
        store[row_id] = row
        return deepcopy(row)

    def list_tags(self) -> list[dict]:
        self._record("list_tags")
        return [deepcopy(t) for t in self.tags.values()]

    def create_tag(self, name: str, **extra: Any) -> dict:
        self._record("create_tag", name)
        return self._create_named(self.tags, "tag", name, **extra)

    def list_custom_fields(self) -> list[dict]:
        self._record("list_custom_fields")
        return [deepcopy(f) for f in self.custom_fields.values()]

    def create_custom_field(self, name: str, data_type: str, extra_data: dict | None = None) -> dict:
        self._record("create_custom_field", name, data_type)
        if not self.features.get("custom_fields"):
            raise PaperlessNotFound("Paperless POST /api/custom_fields/: HTTP 404", status_code=404)
        return self._create_named(
            self.custom_fields, "custom_field", name, data_type=data_type, extra_data=extra_data or {}
        )

    def list_document_types(self) -> list[dict]:
        self._record("list_document_types")
        return [deepcopy(t) for t in self.document_types.values()]

    def create_document_type(self, name: str) -> dict:
        self._record("create_document_type", name)
        return self._create_named(self.document_types, "document_type", name)

    def list_correspondents(self) -> list[dict]:
        self._record("list_correspondents")
        return [deepcopy(c) for c in self.correspondents.values()]

    def create_correspondent(self, name: str) -> dict:
        self._record("create_correspondent", name)
        return self._create_named(self.correspondents, "correspondent", name)
