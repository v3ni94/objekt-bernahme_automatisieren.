"""Automatisches Nachraeumen der Drive-Ordner (Anforderung 13.09.2026): Nach jedem abgeschlossenen Verarbeitungslauf
eines Objekts, sobald kein Job mehr offen ist, prueft die Anwendung den Objektordner und die aufgearbeiteten
Altbestand-Quellordner. In den Drive-Papierkorb gehen ausschliesslich Ordner ausserhalb der Sollstruktur, die leer
sind (keine Dateien, keine nicht leeren Unterordner; Kinder vor Eltern, live nachgeprueft), sowie nicht registrierte
Temporaer- und Systemdateien nach drive.takeover_ignore_patterns. Ordner der Sollstruktur, die Objektwurzel, der
Eingangsordner, jede Datei mit Dokument (auch Dubletten und Dateien in Pruefung), Google-Dokumente und unbekannte
Dateien bleiben stehen und schuetzen ihren Ordner. Dubletten bleiben dem Aufraeumen von Hand vorbehalten. Nichts wird
endgueltig geloescht. Schalter drive.auto_cleanup_enabled."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document
from apps.drive import oauth
from apps.drive.adapter import DriveAdapter, DriveError
from apps.drive.takeover import (
    _protected_folder_ids,
    _source_snapshot,
    ignore_patterns,
    is_ignored,
    max_files,
)
from apps.objects.models import ManagedObject

logger = logging.getLogger(__name__)

LOCK_SECONDS = 600


def enabled() -> bool:
    return bool(store.get("drive.auto_cleanup_enabled", True))


@dataclass
class TreePlan:
    root_id: str
    root_name: str
    allow_root: bool
    junk: list[tuple[str, str]] = field(default_factory=list)  # (Datei-ID, Pfad)
    empty_folders: list[tuple[str, str]] = field(default_factory=list)  # (Ordner-ID, Pfad), Kinder vor Eltern
    kept: list[tuple[str, str, str]] = field(default_factory=list)  # (ID, Pfad, Grund)
    total_files: int = 0
    root_empty: bool = False
    blocker: str | None = None

    @property
    def actionable(self) -> bool:
        return bool(self.junk or self.empty_folders)


def plan_tree(
    obj: ManagedObject,
    drive: DriveAdapter,
    root_id: str,
    *,
    allow_root: bool,
    protected: set[str],
    patterns: list[str],
    limit: int,
) -> TreePlan:
    """Einen Baum lesen und einteilen. allow_root: darf die Wurzel selbst in den Papierkorb (Altbestand-Quellordner),
    nie fuer den Objektordner."""
    folder = drive.get(root_id)
    plan = TreePlan(root_id=root_id, root_name=folder.name if folder else root_id, allow_root=allow_root)
    if folder is None or folder.trashed or not folder.is_folder:
        plan.blocker = "Ordner in Drive nicht vorhanden"
        return plan
    paths: dict[str, str] = {folder.id: folder.name}
    folders: list[str] = []
    parent_of: dict[str, str] = {}
    keep: dict[str, int] = {}
    files = []
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
            plan.blocker = f"mehr als {limit} Dateien (drive.takeover_max_files)"
            return plan
    plan.total_files = len(files)
    registered = set(
        Document.objects.filter(
            drive_file_id__in=[n.id for n, _p, _e in files], deleted_at__isnull=True
        ).values_list("drive_file_id", flat=True)
    )
    for node, path, parent in files:
        if node.id in registered:
            plan.kept.append((node.id, path, "Dokument"))
        elif node.is_google_doc:
            plan.kept.append((node.id, path, "Google-Dokument"))
        elif is_ignored(node.name, patterns):
            plan.junk.append((node.id, path))
            continue
        else:
            plan.kept.append((node.id, path, "nicht registriert"))
        keep[parent] = keep.get(parent, 0) + 1
    empty: set[str] = set()
    for fid in reversed(folders):  # Kinder vor Eltern
        if fid in protected or keep.get(fid, 0):
            continue
        children = [c for c in folders if parent_of.get(c) == fid]
        if all(c in empty for c in children):
            empty.add(fid)
            plan.empty_folders.append((fid, paths[fid]))
    if allow_root and folder.id not in protected and not keep.get(folder.id, 0):
        root_children = [c for c in folders if parent_of.get(c) == folder.id]
        if all(c in empty for c in root_children):
            plan.root_empty = True
            plan.empty_folders.append((folder.id, paths[folder.id]))
    return plan


def _skip(result: dict, reason: str) -> dict:
    result["status"] = "skipped"
    result["reason"] = reason
    return result


def sweep(
    obj: ManagedObject,
    *,
    drive: DriveAdapter | None = None,
    user=None,
    dry_run: bool = False,
    trigger: str = "run",
) -> dict:
    """Objektordner und aufgearbeitete Quellordner nachraeumen. Laeuft nur ohne offene Jobs des Objekts, unter der
    Objektsperre der Uebernahme, und protokolliert jede Bewegung (drive.trash) sowie das Ergebnis (drive.auto_cleanup)."""
    from apps.drive.models import TakeoverStatus
    from apps.pipeline.models import JobStatus, ProcessingJob

    result: dict = {
        "object": obj.pk,
        "status": "done",
        "reason": None,
        "trigger": trigger,
        "dry_run": dry_run,
        "files": 0,
        "folders": 0,
        "kept": 0,
        "errors": [],
        "trees": [],
        "sources_pruned": 0,
    }
    if not enabled():
        return _skip(result, "drive.auto_cleanup_enabled aus")
    if getattr(obj, "is_system_inbox", False) or obj.deleted_at is not None:
        return _skip(result, "Eingangsobjekt oder archiviert")
    if ProcessingJob.objects.filter(object=obj, status__in=[JobStatus.PENDING, JobStatus.RUNNING]).exists():
        return _skip(result, "offene Verarbeitungsjobs")
    drive = drive or oauth.get_adapter()
    if drive is None:
        return _skip(result, "keine Google-Verbindung")
    targets: list[tuple[str, bool, object]] = []
    if obj.drive_root_folder_id:
        targets.append((obj.drive_root_folder_id, False, None))
    for src in obj.takeover_sources.filter(status=TakeoverStatus.DONE).order_by("id"):
        targets.append((src.drive_folder_id, True, src))
    if not targets:
        return _skip(result, "kein Objektordner und keine aufgearbeiteten Quellordner")
    lock_key = f"takeover:{obj.pk}"
    if not cache.add(lock_key, "auto_cleanup", timeout=LOCK_SECONDS):
        return _skip(result, "Objekt gesperrt (Übernahme oder Aufräumen läuft)")
    try:
        protected = _protected_folder_ids()
        inbox_folder = str(store.get("sync.inbox_folder_id", "") or "")
        if inbox_folder:
            protected.add(inbox_folder)
        patterns = ignore_patterns()
        limit = max_files()
        for root_id, allow_root, src in targets:
            try:
                plan = plan_tree(
                    obj,
                    drive,
                    root_id,
                    allow_root=allow_root,
                    protected=protected,
                    patterns=patterns,
                    limit=limit,
                )
            except DriveError as exc:
                result["errors"].append(f"{root_id}: {exc}")
                continue
            tree = {
                "root": plan.root_name,
                "files": plan.total_files,
                "junk": len(plan.junk),
                "empty_folders": len(plan.empty_folders),
                "kept": len(plan.kept),
                "blocker": plan.blocker,
            }
            result["trees"].append(tree)
            result["kept"] += len(plan.kept)
            if plan.blocker or dry_run:
                continue
            _execute(obj, drive, plan, src, result, user=user, trigger=trigger)
        if result["files"] or result["folders"] or result["errors"] or result["sources_pruned"]:
            record(
                "drive.auto_cleanup",
                entity_type="object",
                entity_id=obj.pk,
                object_id=obj.pk,
                actor=user,
                after={k: v for k, v in result.items() if k != "object"},
            )
    finally:
        cache.delete(lock_key)
    return result


def _execute(obj, drive: DriveAdapter, plan: TreePlan, src, result: dict, *, user, trigger: str) -> None:
    source_id = getattr(src, "pk", None)
    for file_id, path in plan.junk:
        try:
            drive.trash(file_id)
        except DriveError as exc:
            result["errors"].append(f"{path}: {exc}")
            continue
        result["files"] += 1
        record(
            "drive.trash",
            entity_type="drive_file",
            object_id=obj.pk,
            actor=user,
            after={
                "drive_file_id": file_id,
                "path": path,
                "kind": "junk",
                "trigger": trigger,
                "source_id": source_id,
            },
        )
    for folder_id, path in plan.empty_folders:
        try:
            live = [c for c in drive.list_children_including_trashed(folder_id) if not c.trashed]
        except DriveError as exc:
            result["errors"].append(f"{path}: nicht geprüft, bleibt bestehen ({exc})")
            continue
        if live:
            result["errors"].append(f"{path}: nicht mehr leer, bleibt bestehen")
            continue
        try:
            drive.trash(folder_id)
        except DriveError as exc:
            result["errors"].append(f"{path}: {exc}")
            continue
        result["folders"] += 1
        record(
            "drive.trash",
            entity_type="drive_folder",
            object_id=obj.pk,
            actor=user,
            after={
                "drive_file_id": folder_id,
                "path": path,
                "kind": "empty_folder",
                "trigger": trigger,
                "source_id": source_id,
            },
        )
        if src is not None and folder_id == src.drive_folder_id:
            record(
                "drive.takeover_source_prune",
                entity_type="takeover_source",
                entity_id=src.pk,
                object_id=obj.pk,
                actor=user,
                before=_source_snapshot(src),
                after={"reason": "nach der Aufarbeitung leer, automatisch in den Papierkorb verschoben"},
            )
            src.delete()
            result["sources_pruned"] += 1


def trigger_auto_cleanup(object_id: int, *, trigger: str = "run") -> str:
    """Nach Abschluss eines Verarbeitungslaufs: Nachraeumen als Aufgabe (Celery, Warteschlange io) oder sofort
    (Tests, lokaler Runner). Scheitert nie fuer den Aufrufer."""
    if not enabled():
        return "disabled"
    try:
        if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "celery":
            from apps.drive.tasks import auto_cleanup_task

            auto_cleanup_task.apply_async(args=[object_id, trigger], queue="io", countdown=5)
            return "queued"
        obj = ManagedObject.objects.filter(pk=object_id).first()
        if obj is None:
            return "missing"
        return sweep(obj, trigger=trigger)["status"]
    except Exception:  # Broker nicht erreichbar oder Drive-Fehler: der naechste Lauf holt es nach
        logger.exception("Nachräumen für Objekt %s konnte nicht angestoßen werden", object_id)
        return "error"


def last_result(obj: ManagedObject) -> dict | None:
    from apps.audit.models import AuditEvent

    ev = (
        AuditEvent.objects.filter(action="drive.auto_cleanup", object_id=obj.pk)
        .order_by("-occurred_at")
        .first()
    )
    return dict(ev.after_state or {}) | {"at": ev.occurred_at.isoformat()} if ev else None
