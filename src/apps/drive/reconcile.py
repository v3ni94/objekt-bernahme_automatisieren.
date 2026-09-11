"""Ordnerabgleich nach CR 9 (Fachentwurf F 3, 4, 5.1; docs/architektur.md 7.3).

Zwei Phasen: plan (nur Lesen) und execute (Schreiben in seq_no-Reihenfolge); dry_run endet nach plan. Jeder Lauf ist
eine Zeile drive_sync_runs, jede Aktion eine Zeile drive_sync_actions. Ordnernamen kommen ausschliesslich aus dem
Katalog (document_categories, document_subfolders) und der Konfiguration (drive.legacy_folder_aliases); im Code steht
kein Ordnername als Literal. Nichts wird geloescht, bestehende Ordner werden nie umbenannt (Ausnahme: Altbezeichnung
des Auffangbereichs laut Konfiguration, CR 9.3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.config import store
from apps.documents.models import Document, DocumentCategory, DocumentSubfolder
from apps.drive.adapter import DriveAdapter, DriveError, DriveNode, count_tree, sort_deterministic
from apps.drive.models import DriveNode as DriveNodeRow
from apps.drive.models import DriveSyncAction, DriveSyncRun, NodeKind, NodeStatus, SyncActionType
from apps.drive.naming import transliterate
from apps.drive.object_numbers import object_folder_name, parse_object_number
from apps.objects.models import ManagedObject
from apps.pipeline.models import JobType, ProcessingJob
from apps.review.models import CaseStatus, CaseType, ReviewCase

logger = logging.getLogger(__name__)


class ReconcileError(Exception):
    pass


@dataclass
class DriveConfig:
    root_folder_id: str | None = None
    root_drive_id: str | None = None
    digits_min: int = 2
    digits_max: int = 6
    separators: tuple[str, ...] = (" ", "_", ",", ".", "-")
    zero_pad_to: int | None = None
    folder_pattern: str = "{number} {city}, {street} {house_number}"
    legacy_aliases: dict[str, list[str]] = field(default_factory=dict)
    legacy_conflict_rename_pattern: str | None = None

    @classmethod
    def from_settings(cls, **overrides) -> DriveConfig:
        cfg = cls(
            root_folder_id=store.get("drive.root_folder_id"),
            root_drive_id=store.get("drive.root_drive_id"),
            digits_min=int(store.get("drive.object_number_digits_min", 2)),
            digits_max=int(store.get("drive.object_number_digits_max", 6)),
            separators=tuple(store.get("drive.object_number_separators", [" ", "_", ",", ".", "-"])),
            zero_pad_to=store.get("drive.object_number_zero_pad_to"),
            folder_pattern=store.get(
                "drive.object_folder_name_pattern", "{number} {city}, {street} {house_number}"
            ),
            legacy_aliases=dict(store.get("drive.legacy_folder_aliases", {}) or {}),
            legacy_conflict_rename_pattern=store.get("drive.legacy_conflict_rename_pattern"),
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg


# ---------------------------------------------------------------- Namensvergleich (F 4.1 Punkt 3)
def nfc(name: str) -> str:
    return unicodedata.normalize("NFC", name or "").strip()


def norm(name: str) -> str:
    text = nfc(name).replace("_", " ")
    text = transliterate(text).casefold()
    return " ".join(text.split())


_PREFIX = re.compile(r"^\s*\d{1,2}[\s_.-]*")


def loose(name: str) -> str:
    return norm(_PREFIX.sub("", nfc(name))).replace(" ", "")


def loose_match(name: str, expected: str) -> bool:
    return loose(name) == loose(expected) and loose(name) != ""


def render_object_folder_name(obj: ManagedObject, cfg: DriveConfig) -> str:
    """Anlage nach Namensmuster (F 3.4): fehlende Stammdaten verhindern die Anlage, kein Platzhalter."""
    number = str(
        obj.object_number_numeric if obj.object_number_numeric is not None else int(obj.object_number)
    )
    if cfg.zero_pad_to:
        number = number.zfill(int(cfg.zero_pad_to))
    fields = {"number": number, "city": obj.city, "street": obj.street, "house_number": obj.house_number}
    used = set(re.findall(r"\{(\w+)\}", cfg.folder_pattern))
    missing = [k for k in used if k in fields and not fields[k]]
    if missing:
        raise ReconcileError(f"Objektordner nicht angelegt, Stammdaten fehlen: {', '.join(sorted(missing))}")
    name = object_folder_name(
        cfg.folder_pattern,
        number=number,
        city=obj.city or "",
        street=obj.street or "",
        house_number=obj.house_number or "",
    )
    name = re.sub(r"[\x00-\x1f/]", "", nfc(name))
    match = parse_object_number(
        name, digits_min=cfg.digits_min, digits_max=cfg.digits_max, separators=cfg.separators
    )
    if match is None or match.numeric != int(number):
        raise ReconcileError("Erzeugter Ordnername erfüllt das Erkennungsmuster nicht")
    return name


# ---------------------------------------------------------------- Plan
@dataclass
class Action:
    action_type: str
    category_code: str | None = None
    subfolder_id: int | None = None
    node: DriveNode | None = None  # betroffener Drive-Knoten (bei register, rename, review)
    parent_ref: str | None = None  # Drive-ID oder Platzhalter "object_root" bzw. "category:<code>"
    name_before: str | None = None
    name_after: str | None = None
    file_count_before: int | None = None
    id_hash_before: str | None = None
    node_kind: str | None = None
    note: str | None = None
    review: dict | None = None
    inventory: list[dict] | None = None
    seq_no: int = 0
    # Ergebnis
    row: DriveSyncAction | None = None
    result: str | None = None
    created_node: DriveNode | None = None
    file_count_after: int | None = None
    id_hash_after: str | None = None
    error: str | None = None

    @property
    def is_write(self) -> bool:
        return self.action_type in (
            SyncActionType.CREATE_FOLDER,
            SyncActionType.RENAME_FOLDER,
            SyncActionType.MOVE_FILE,
        )


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    blocked: set[str] = field(default_factory=set)
    reviewed: set[str] = field(default_factory=set)
    renamed_to: dict[str, DriveNode] = field(default_factory=dict)  # erwarteter Name -> umbenannter Knoten
    resolved: dict[str, str] = field(default_factory=dict)  # Platzhalter -> Drive-ID
    inventory_stats: dict = field(
        default_factory=lambda: {
            "new": 0,
            "unchanged": 0,
            "changed": 0,
            "google_docs": 0,
            "skipped_lists": 0,
            "external_shortcuts": 0,
            "bytes": 0,
        }
    )

    def add(self, action: Action) -> Action:
        action.seq_no = len(self.actions) + 1
        self.actions.append(action)
        return action

    def review(
        self,
        case_type: str,
        subtype: str,
        *,
        category_code: str | None = None,
        candidates: list[DriveNode] | None = None,
        node: DriveNode | None = None,
        note: str | None = None,
        options: list[str] | None = None,
        drive: DriveAdapter | None = None,
    ) -> Action:
        cands = []
        for c in candidates or []:
            entry = {
                "drive_file_id": c.id,
                "name": c.name,
                "trashed": c.trashed,
                "modified_time": c.modified_time,
                "created_time": c.created_time,
            }
            if drive is not None and c.is_folder:
                try:
                    entry["file_count"] = count_tree(drive, c.id)[0]
                except DriveError:
                    entry["file_count"] = None
            cands.append(entry)
        if category_code:
            self.reviewed.add(category_code)
        return self.add(
            Action(
                SyncActionType.CREATE_REVIEW,
                category_code=category_code,
                node=node,
                note=note or subtype,
                review={
                    "case_type": case_type,
                    "subtype": subtype,
                    "candidates": cands,
                    "options": options or [],
                },
            )
        )

    def has_review_for(self, category_code: str) -> bool:
        return category_code in self.reviewed or category_code in self.blocked

    @property
    def write_count(self) -> int:
        return sum(1 for a in self.actions if a.is_write)


def _folder_or_shortcut_target(drive: DriveAdapter, node: DriveNode, plan: Plan) -> DriveNode | None:
    if node.is_folder:
        return node
    if node.is_shortcut and node.shortcut_target_id:
        target = drive.get(node.shortcut_target_id)
        if target is not None and target.is_folder and not target.trashed:
            plan.hints.append(
                f"Verknüpfung {node.name} ({node.id}) zeigt auf Ordner {target.name} ({target.id})"
            )
            return target
    return None


# ---------------------------------------------------------------- Hauptablauf
def reconcile_object(
    obj: ManagedObject,
    *,
    drive: DriveAdapter,
    dry_run: bool,
    user=None,
    cfg: DriveConfig | None = None,
    trigger: str = "manual",
) -> DriveSyncRun:
    cfg = cfg or DriveConfig.from_settings()
    run = DriveSyncRun.objects.create(
        object=obj,
        dry_run=dry_run,
        status="running",
        triggered_by=user,
        started_at=timezone.now(),
        summary={"trigger": trigger},
    )
    plan = Plan()
    try:
        obj_folder, root_created = _plan(obj, drive, cfg, plan, run)
        if dry_run:
            return _finish(run, plan, status="done", executed=False)
        _execute(obj, drive, cfg, plan, run, user, obj_folder, root_created)
        return _finish(
            run,
            plan,
            status="failed" if plan.hints and any(h.startswith("ABBRUCH") for h in plan.hints) else "done",
            executed=True,
        )
    except ReconcileError as exc:
        run.error_message = str(exc)
        return _finish(run, plan, status="failed", executed=not dry_run)
    except DriveError as exc:
        run.error_message = f"Drive-Fehler {exc.status or ''} {exc.reason or ''}: {exc}"[:2000]
        return _finish(run, plan, status="failed", executed=not dry_run)
    except Exception as exc:  # noqa: BLE001  Befund 11.09.2026: Datenbankfehler liess Lauf 6 dauerhaft "running"
        logger.exception("Ordnerabgleich %s Objekt %s abgebrochen", run.pk, obj.pk)
        run.error_message = f"Abbruch: {type(exc).__name__}: {exc}"[:2000]
        return _finish(run, plan, status="failed", executed=not dry_run)


def _plan(
    obj: ManagedObject, drive: DriveAdapter, cfg: DriveConfig, plan: Plan, run: DriveSyncRun
) -> tuple[DriveNode | None, bool]:
    # Schritt 0: Wurzel
    if not cfg.root_folder_id:
        raise ReconcileError("drive.root_folder_id ist nicht gesetzt (Einrichtung, docs/betrieb.md 7.7)")
    root = drive.get(cfg.root_folder_id)
    if root is None or root.trashed or not root.is_folder:
        raise ReconcileError("Wurzelordner nicht erreichbar oder im Papierkorb")
    # Schritt 1: Objektordner
    obj_folder, root_created = _resolve_object_root(obj, root, drive, cfg, plan, run)
    if obj_folder is None and not root_created:
        return None, False
    cats = list(DocumentCategory.objects.filter(is_active=True).order_by("sort_order"))
    children: list[DriveNode] = []
    if obj_folder is not None:
        raw_children = drive.list_children(obj_folder.id, folders_only=True)
        for c in raw_children:
            target = _folder_or_shortcut_target(drive, c, plan)
            if target is not None:
                children.append(target if target.id != c.id else c)
        children = sort_deterministic(children)
        n_before, h_before = count_tree(drive, obj_folder.id)
        run.file_count_before = n_before
        run.summary["id_hash_before"] = h_before
    parent_ref = obj_folder.id if obj_folder is not None else "object_root"
    # Schritt 3: Alias-Umbenennungen zuerst (CR 9.3)
    for cat in cats:
        expected = cat.folder_name
        exact = [c for c in children if nfc(c.name) == nfc(expected)]
        alias_norms = {norm(a) for a in cfg.legacy_aliases.get(expected, [])}
        aliases = [c for c in children if alias_norms and norm(c.name) in alias_norms]
        if aliases and exact:
            plan.review(
                CaseType.DRIVE_STRUCTURE,
                "legacy_and_target_both_exist",
                category_code=cat.code,
                candidates=exact + aliases,
                drive=drive,
                options=[
                    "files_as_loose_documents",
                    "rename_legacy_to_archive" if cfg.legacy_conflict_rename_pattern else "decide_manually",
                ],
            )
            plan.blocked.add(cat.code)
            # Zielordner ist eindeutig vorhanden und wird registriert; Anlage von Kategorie 05 wird zurueckgestellt (F 4.4 Fall A)
            plan.add(
                Action(
                    SyncActionType.REGISTER_FOLDER,
                    category_code=cat.code,
                    node=exact[0],
                    node_kind=NodeKind.MAIN_FOLDER,
                    name_before=exact[0].name,
                    name_after=expected,
                )
            )
            legacy_code = _leading_code(aliases[0].name)
            if legacy_code:
                plan.blocked.add(legacy_code)
                plan.hints.append(
                    f"Anlage der Kategorie {legacy_code} zurückgestellt: Altordner {aliases[0].name} und Zielordner {expected} bestehen nebeneinander"
                )
        elif len(aliases) > 1:
            plan.review(
                CaseType.DRIVE_STRUCTURE,
                "multiple_legacy_folders",
                category_code=cat.code,
                candidates=aliases,
                drive=drive,
            )
            plan.blocked.add(cat.code)
            legacy_code = _leading_code(aliases[0].name)
            if legacy_code:
                plan.blocked.add(legacy_code)
        elif len(aliases) == 1:
            legacy = aliases[0]
            n_leg, h_leg = count_tree(drive, legacy.id)
            plan.add(
                Action(
                    SyncActionType.RENAME_FOLDER,
                    category_code=cat.code,
                    node=legacy,
                    parent_ref=parent_ref,
                    name_before=legacy.name,
                    name_after=expected,
                    file_count_before=n_leg,
                    id_hash_before=h_leg,
                    node_kind=NodeKind.MAIN_FOLDER,
                    note="Altbezeichnung laut Konfiguration",
                )
            )
            plan.renamed_to[expected] = legacy
    # Schritt 4: Hauptordner
    for cat in cats:
        if cat.code in plan.blocked:
            continue
        expected = cat.folder_name
        exact = [c for c in children if nfc(c.name) == nfc(expected)]
        if expected in plan.renamed_to:
            exact.append(plan.renamed_to[expected])
        rest = [c for c in children if c not in exact]
        normalized = [c for c in rest if norm(c.name) == norm(expected)]
        loose_hits = [c for c in rest if c not in normalized and loose_match(c.name, expected)]
        if len(exact) == 1:
            if exact[0] is not plan.renamed_to.get(expected):
                plan.add(
                    Action(
                        SyncActionType.REGISTER_FOLDER,
                        category_code=cat.code,
                        node=exact[0],
                        node_kind=NodeKind.MAIN_FOLDER,
                        name_before=exact[0].name,
                        name_after=expected,
                    )
                )
        elif len(exact) > 1:
            plan.review(
                CaseType.DRIVE_STRUCTURE,
                "multiple_folders_same_name",
                category_code=cat.code,
                candidates=exact,
                drive=drive,
                options=["choose_leading_folder"],
            )
        elif len(normalized) == 1 and not loose_hits:
            plan.add(
                Action(
                    SyncActionType.REGISTER_FOLDER,
                    category_code=cat.code,
                    node=normalized[0],
                    node_kind=NodeKind.MAIN_FOLDER,
                    name_before=normalized[0].name,
                    name_after=expected,
                    note="Schreibweise abweichend, nicht umbenannt",
                )
            )
            plan.hints.append(
                f"Schreibweise abweichend: {normalized[0].name} gilt als {expected}, nicht umbenannt"
            )
        elif normalized or loose_hits:
            plan.review(
                CaseType.DRIVE_STRUCTURE,
                "similar_folder_name",
                category_code=cat.code,
                candidates=normalized + loose_hits,
                drive=drive,
                options=["use_this_folder", "create_catalog_folder"],
            )
        else:
            plan.add(
                Action(
                    SyncActionType.CREATE_FOLDER,
                    category_code=cat.code,
                    parent_ref=parent_ref,
                    name_after=expected,
                    node_kind=NodeKind.MAIN_FOLDER,
                )
            )
    # Schritt 5: Unterordner der Kategorien mit Katalog-Unterordnern auf Objektebene (Sonstiges); Aktenunterordner folgen der Akte
    for cat in cats:
        if cat.scope not in ("object", "misc") or plan.has_review_for(cat.code):
            continue
        subs = list(DocumentSubfolder.objects.filter(category=cat, is_active=True).order_by("sort_order"))
        if not subs:
            continue
        parent_node = _planned_parent(plan, cat.code, children, cat.folder_name)
        sub_children = (
            drive.list_children(parent_node.id, folders_only=True) if parent_node is not None else []
        )
        sub_ref = parent_node.id if parent_node is not None else f"category:{cat.code}"
        for sub in subs:
            expected = sub.folder_name
            exact = [c for c in sub_children if nfc(c.name) == nfc(expected)]
            normalized = [c for c in sub_children if c not in exact and norm(c.name) == norm(expected)]
            loose_hits = [
                c
                for c in sub_children
                if c not in exact and c not in normalized and loose_match(c.name, expected)
            ]
            if len(exact) == 1:
                plan.add(
                    Action(
                        SyncActionType.REGISTER_FOLDER,
                        category_code=cat.code,
                        subfolder_id=sub.pk,
                        node=exact[0],
                        node_kind=NodeKind.SUBFOLDER,
                        name_before=exact[0].name,
                        name_after=expected,
                    )
                )
            elif len(exact) > 1:
                plan.review(
                    CaseType.DRIVE_STRUCTURE,
                    "multiple_folders_same_name",
                    category_code=cat.code,
                    candidates=exact,
                    drive=drive,
                    note=f"Unterordner {expected}",
                )
            elif len(normalized) == 1 and not loose_hits:
                plan.add(
                    Action(
                        SyncActionType.REGISTER_FOLDER,
                        category_code=cat.code,
                        subfolder_id=sub.pk,
                        node=normalized[0],
                        node_kind=NodeKind.SUBFOLDER,
                        name_before=normalized[0].name,
                        name_after=expected,
                        note="Schreibweise abweichend, nicht umbenannt",
                    )
                )
            elif normalized or loose_hits:
                plan.review(
                    CaseType.DRIVE_STRUCTURE,
                    "similar_folder_name",
                    category_code=cat.code,
                    candidates=normalized + loose_hits,
                    drive=drive,
                    note=f"Unterordner {expected}",
                )
            else:
                plan.add(
                    Action(
                        SyncActionType.CREATE_FOLDER,
                        category_code=cat.code,
                        subfolder_id=sub.pk,
                        parent_ref=sub_ref,
                        name_after=expected,
                        node_kind=NodeKind.SUBFOLDER,
                    )
                )
    # Zusaetzliche Ordner (Fall H)
    known_names = {nfc(c.folder_name) for c in cats} | {
        norm(a) for al in cfg.legacy_aliases.values() for a in al
    }
    for c in children:
        if (
            nfc(c.name) not in known_names
            and norm(c.name) not in known_names
            and not any(
                norm(c.name) == norm(cat.folder_name) or loose_match(c.name, cat.folder_name) for cat in cats
            )
        ):
            plan.hints.append(f"Zusätzlicher Ordner bleibt unverändert: {c.name} ({c.id})")
    # Schritt 6: Inventur
    if obj_folder is not None:
        _plan_inventory(obj, drive, obj_folder, plan)
    return obj_folder, root_created


def _leading_code(name: str) -> str | None:
    m = re.match(r"^\s*(\d{2})[\s_.-]", nfc(name))
    return m.group(1) if m else None


def _planned_parent(
    plan: Plan, category_code: str, children: list[DriveNode], expected: str
) -> DriveNode | None:
    for a in plan.actions:
        if (
            a.category_code == category_code
            and a.subfolder_id is None
            and a.action_type in (SyncActionType.REGISTER_FOLDER, SyncActionType.RENAME_FOLDER)
            and a.node is not None
        ):
            return a.node
    return None


def _resolve_object_root(
    obj: ManagedObject, root: DriveNode, drive: DriveAdapter, cfg: DriveConfig, plan: Plan, run: DriveSyncRun
) -> tuple[DriveNode | None, bool]:
    if obj.drive_root_folder_id:
        node = drive.get(obj.drive_root_folder_id)
        if node is None or node.trashed or not node.is_folder:
            status = NodeStatus.TRASHED if node is not None and node.trashed else NodeStatus.MISSING
            plan.add(
                Action(
                    SyncActionType.FIND_ROOT,
                    node=node,
                    name_before=obj.drive_root_folder_name,
                    note=f"gespeicherter Objektordner {status}",
                    result="failed",
                )
            )
            plan.review(
                CaseType.DRIVE_STRUCTURE,
                "drive_folder_missing",
                candidates=[node] if node else [],
                options=["create_new_folder", "restored_in_drive_retry"],
                note=f"Objektordner {obj.drive_root_folder_id} {status}",
            )
            plan.hints.append(f"drive_nodes object_root Status {status}")
            run.root_matches = 0
            return None, False
        plan.add(
            Action(
                SyncActionType.FIND_ROOT,
                node=node,
                name_before=node.name,
                name_after=node.name,
                node_kind=NodeKind.OBJECT_ROOT,
                note="gespeicherte ID bestätigt",
                result="ok",
            )
        )
        run.root_matches = 1
        return node, False
    number = obj.object_number_numeric if obj.object_number_numeric is not None else int(obj.object_number)
    active: list[DriveNode] = []
    trashed: list[DriveNode] = []
    ignored: list[str] = []
    for c in drive.list_children_including_trashed(root.id):
        target = _folder_or_shortcut_target(drive, c, plan) if not c.trashed else (c if c.is_folder else None)
        if target is None:
            continue
        match = parse_object_number(
            c.name, digits_min=cfg.digits_min, digits_max=cfg.digits_max, separators=cfg.separators
        )
        if match is None:
            if c.is_folder and not c.trashed:
                ignored.append(f"{c.name} ({c.id})")
            continue
        if match.numeric != number:
            continue
        (trashed if c.trashed else active).append(target)
    active = sort_deterministic(active)
    trashed = sort_deterministic(trashed)
    run.root_matches = len(active)
    run.summary["ignored_root_folders"] = ignored
    if len(active) == 1:
        plan.add(
            Action(
                SyncActionType.FIND_ROOT,
                node=active[0],
                name_before=active[0].name,
                name_after=active[0].name,
                node_kind=NodeKind.OBJECT_ROOT,
                note="Treffer über Objektnummer",
                result="ok",
            )
        )
        return active[0], False
    if len(active) > 1:
        plan.review(
            CaseType.DUPLICATE_OBJECT_NUMBER,
            "multiple_active",
            candidates=active,
            drive=drive,
            options=["choose_leading_folder"],
            note="mehrere Objektordner mit dieser Nummer",
        )
        plan.add(
            Action(SyncActionType.FIND_ROOT, note="mehrere Treffer, Entscheidung im Review", result="skipped")
        )
        return None, False
    if trashed:
        plan.review(
            CaseType.DUPLICATE_OBJECT_NUMBER,
            "candidate_in_trash",
            candidates=trashed,
            options=["create_new_folder", "restored_in_drive_retry"],
            note="Treffer nur im Papierkorb",
        )
        plan.add(
            Action(SyncActionType.FIND_ROOT, note="Treffer nur im Papierkorb, keine Anlage", result="skipped")
        )
        return None, False
    name = render_object_folder_name(obj, cfg)
    plan.add(Action(SyncActionType.FIND_ROOT, note="kein Treffer", result="skipped"))
    plan.add(
        Action(
            SyncActionType.CREATE_FOLDER,
            parent_ref=root.id,
            name_after=name,
            node_kind=NodeKind.OBJECT_ROOT,
            note="Objektordner nach Namensmuster",
        )
    )
    return None, True


def _plan_inventory(obj: ManagedObject, drive: DriveAdapter, obj_folder: DriveNode, plan: Plan) -> None:
    list_ids = set(
        DriveNodeRow.objects.filter(object=obj, node_kind=NodeKind.LIST_FILE).values_list(
            "drive_file_id", flat=True
        )
    )
    known = {d.drive_file_id: d for d in Document.objects.filter(object=obj, drive_file_id__isnull=False)}
    names = {obj_folder.id: obj_folder.name}
    entries: list[dict] = []
    for node, path in drive.walk(obj_folder.id):
        if node.is_folder:
            names[node.id] = node.name
            continue
        if node.id in list_ids:
            plan.inventory_stats["skipped_lists"] += 1
            continue
        target = node
        if node.is_shortcut:
            target = drive.get(node.shortcut_target_id) if node.shortcut_target_id else None
            if (
                target is None
                or not any(p == obj_folder.id for p in path)
                or (target.parent_id and target.parent_id not in names and target.parent_id != obj_folder.id)
            ):
                plan.inventory_stats["external_shortcuts"] += 1
                plan.hints.append(f"Externe Verknüpfung {node.name} ({node.id}) wird nur protokolliert")
                continue
        if target.is_google_doc:
            plan.inventory_stats["google_docs"] += 1
        path_names = "/".join(names.get(p, p) for p in path)
        existing = known.get(target.id)
        if existing is None:
            state = "new"
        elif (existing.drive_md5 or "") == (target.md5 or "") and not (
            target.md5 is None
            and existing.updated_at
            and existing.updated_at.isoformat() < target.modified_time
        ):
            state = "unchanged"
        else:
            state = "changed"
        plan.inventory_stats[state] += 1
        plan.inventory_stats["bytes"] += target.size or 0
        entries.append(
            {
                "drive_file_id": target.id,
                "name": target.name,
                "mime_type": target.mime_type,
                "size": target.size,
                "md5": target.md5,
                "modified_time": target.modified_time,
                "parent_id": path[-1] if path else obj_folder.id,
                "path": path_names,
                "state": state,
            }
        )
    entries.sort(key=lambda e: (e["path"], e["name"].casefold(), e["drive_file_id"]))
    plan.add(
        Action(
            SyncActionType.INVENTORY,
            inventory=entries,
            note=f"{len(entries)} Dateien, davon neu {plan.inventory_stats['new']}, geändert {plan.inventory_stats['changed']}",
        )
    )


# ---------------------------------------------------------------- Ausfuehrung
def _register_node(
    obj: ManagedObject,
    node: DriveNode,
    *,
    node_kind: str,
    category_code: str | None,
    subfolder_id: int | None,
    expected_name: str | None,
    parent_row: DriveNodeRow | None,
    created: bool,
) -> DriveNodeRow:
    row = DriveNodeRow.objects.filter(drive_file_id=node.id).first()
    position = DriveNodeRow.objects.filter(
        object=obj,
        node_kind=node_kind,
        category_id=category_code,
        subfolder_id=subfolder_id,
        owner_file__isnull=True,
        tenant_file__isnull=True,
        list_type__isnull=True,
        status=NodeStatus.ACTIVE,
    )
    for other in position.exclude(drive_file_id=node.id):
        other.status = NodeStatus.MISSING
        other.save(update_fields=["status", "updated_at"])
    now = timezone.now()
    if row is None:
        row = DriveNodeRow.objects.create(
            object=obj,
            node_kind=node_kind,
            category_id=category_code,
            subfolder_id=subfolder_id,
            parent_node=parent_row,
            drive_file_id=node.id,
            drive_parent_id=node.parent_id,
            drive_name=node.name,
            expected_name=expected_name,
            mime_type=node.mime_type,
            is_folder=True,
            created_by_app=created,
            status=NodeStatus.ACTIVE,
            last_verified_at=now,
        )
    else:
        row.object = obj
        row.node_kind = node_kind
        row.category_id = category_code
        row.subfolder_id = subfolder_id
        row.parent_node = parent_row or row.parent_node
        row.drive_name = node.name
        row.drive_parent_id = node.parent_id
        row.expected_name = expected_name
        row.status = NodeStatus.ACTIVE
        row.last_verified_at = now
        row.save()
    return row


def _resolve_parent(plan: Plan, ref: str | None, obj: ManagedObject) -> str:
    if ref is None:
        raise ReconcileError("Elternordner unbekannt")
    if ref in plan.resolved:
        return plan.resolved[ref]
    if ref.startswith("category:") or ref == "object_root":
        raise ReconcileError(f"Elternordner {ref} wurde in diesem Lauf nicht angelegt")
    return ref


def _execute(
    obj: ManagedObject,
    drive: DriveAdapter,
    cfg: DriveConfig,
    plan: Plan,
    run: DriveSyncRun,
    user,
    obj_folder: DriveNode | None,
    root_created: bool,
) -> None:
    stop_writes = False
    root_row: DriveNodeRow | None = None
    if obj_folder is not None:
        root_row = _register_node(
            obj,
            obj_folder,
            node_kind=NodeKind.OBJECT_ROOT,
            category_code=None,
            subfolder_id=None,
            expected_name=None,
            parent_row=None,
            created=False,
        )
        _store_object_root(obj, obj_folder)
    main_rows: dict[str, DriveNodeRow] = {}
    for action in plan.actions:
        if action.action_type == SyncActionType.FIND_ROOT:
            action.result = action.result or "ok"
            continue
        if action.is_write and stop_writes:
            action.result = "skipped"
            action.error = "abgebrochen nach rename_count_mismatch"
            continue
        try:
            if action.action_type == SyncActionType.REGISTER_FOLDER:
                parent_row = (
                    root_row
                    if action.node_kind == NodeKind.MAIN_FOLDER
                    else main_rows.get(action.category_code or "")
                )
                row = _register_node(
                    obj,
                    action.node,
                    node_kind=action.node_kind,
                    category_code=action.category_code,
                    subfolder_id=action.subfolder_id,
                    expected_name=action.name_after,
                    parent_row=parent_row,
                    created=False,
                )
                if action.node_kind == NodeKind.MAIN_FOLDER:
                    main_rows[action.category_code] = row
                    plan.resolved[f"category:{action.category_code}"] = action.node.id
                action.result = "ok"
            elif action.action_type == SyncActionType.CREATE_FOLDER:
                parent_id = _resolve_parent(plan, action.parent_ref, obj)
                existing = [
                    c
                    for c in drive.list_children(parent_id, folders_only=True)
                    if nfc(c.name) == nfc(action.name_after) and c.is_folder
                ]
                if existing:
                    node = existing[0]
                    action.result = "skipped"
                    action.note = (action.note or "") + " Ordner bereits vorhanden"
                else:
                    node = drive.create_folder(parent_id, action.name_after)
                    action.result = "ok"
                    record(
                        "drive.create_folder",
                        entity_type="drive_node",
                        object_id=obj.pk,
                        actor=user,
                        actor_type=None if user else "system",
                        after={"name": node.name, "drive_file_id": node.id, "parent": parent_id},
                    )
                action.created_node = node
                if action.node_kind == NodeKind.OBJECT_ROOT:
                    obj_folder = node
                    plan.resolved["object_root"] = node.id
                    root_row = _register_node(
                        obj,
                        node,
                        node_kind=NodeKind.OBJECT_ROOT,
                        category_code=None,
                        subfolder_id=None,
                        expected_name=None,
                        parent_row=None,
                        created=action.result == "ok",
                    )
                    _store_object_root(obj, node)
                else:
                    parent_row = (
                        root_row
                        if action.node_kind == NodeKind.MAIN_FOLDER
                        else main_rows.get(action.category_code or "")
                    )
                    row = _register_node(
                        obj,
                        node,
                        node_kind=action.node_kind,
                        category_code=action.category_code,
                        subfolder_id=action.subfolder_id,
                        expected_name=action.name_after,
                        parent_row=parent_row,
                        created=action.result == "ok",
                    )
                    if action.node_kind == NodeKind.MAIN_FOLDER:
                        main_rows[action.category_code] = row
                        plan.resolved[f"category:{action.category_code}"] = node.id
            elif action.action_type == SyncActionType.RENAME_FOLDER:
                current = drive.get(action.node.id)
                if current is None:
                    raise ReconcileError(f"Altordner {action.node.id} nicht mehr vorhanden")
                if nfc(current.name) == nfc(action.name_after):
                    action.result = "skipped"
                    renamed = current
                else:
                    renamed = drive.rename(action.node.id, action.name_after)
                    record(
                        "drive.rename",
                        entity_type="drive_node",
                        object_id=obj.pk,
                        actor=user,
                        actor_type=None if user else "system",
                        after={
                            "drive_file_id": renamed.id,
                            "name_before": action.name_before,
                            "name_after": action.name_after,
                        },
                    )
                    n_after, h_after = count_tree(drive, renamed.id)
                    action.file_count_after, action.id_hash_after = n_after, h_after
                    if (n_after, h_after) != (action.file_count_before, action.id_hash_before):
                        action.result = "failed"
                        action.error = f"Dateizählung nach Umbenennung abweichend: vorher {action.file_count_before}, nachher {n_after}"
                        stop_writes = True
                        plan.hints.append(
                            "ABBRUCH: rename_count_mismatch, keine weiteren Schreibaktionen in diesem Lauf"
                        )
                        plan.review(
                            CaseType.DRIVE_STRUCTURE,
                            "rename_count_mismatch",
                            category_code=action.category_code,
                            candidates=[renamed],
                            note=action.error,
                        )
                        continue
                    action.result = "ok"
                action.created_node = renamed
                row = _register_node(
                    obj,
                    renamed,
                    node_kind=NodeKind.MAIN_FOLDER,
                    category_code=action.category_code,
                    subfolder_id=None,
                    expected_name=action.name_after,
                    parent_row=root_row,
                    created=False,
                )
                main_rows[action.category_code] = row
                plan.resolved[f"category:{action.category_code}"] = renamed.id
            elif action.action_type == SyncActionType.CREATE_REVIEW:
                action.result = _create_review(obj, action, run)
            elif action.action_type == SyncActionType.INVENTORY:
                action.result = _execute_inventory(obj, action, run, main_rows, root_row)
        except DriveError as exc:
            action.result = "failed"
            action.error = f"{type(exc).__name__}: {exc}"[:2000]
            if action.is_write:
                stop_writes = True
                plan.hints.append(
                    f"ABBRUCH: Schreibaktion {action.seq_no} fehlgeschlagen ({type(exc).__name__})"
                )
        except ReconcileError as exc:
            action.result = "failed"
            action.error = str(exc)[:2000]
    if obj_folder is not None:
        n_after, h_after = count_tree(drive, obj_folder.id)
        run.file_count_after = n_after
        run.summary["id_hash_after"] = h_after
        if run.file_count_before is None:
            run.file_count_before = n_after
            run.summary["id_hash_before"] = h_after
    executed_writes = sum(1 for a in plan.actions if a.is_write and a.result == "ok")
    run.no_changes = executed_writes == 0 and not any(
        a.action_type == SyncActionType.CREATE_REVIEW and a.result == "ok" for a in plan.actions
    )
    _verify_postconditions(obj, plan, run)


def _store_object_root(obj: ManagedObject, node: DriveNode) -> None:
    obj.drive_root_folder_id = node.id
    obj.drive_root_folder_name = node.name
    obj.drive_root_verified_at = timezone.now()
    obj.save(
        update_fields=[
            "drive_root_folder_id",
            "drive_root_folder_name",
            "drive_root_verified_at",
            "updated_at",
        ]
    )


def _create_review(obj: ManagedObject, action: Action, run: DriveSyncRun) -> str:
    review = action.review or {}
    key = f"drive:{obj.pk}:{action.category_code or 'root'}:{review.get('subtype')}"
    if ReviewCase.objects.filter(
        batch_key=key, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists():
        return "skipped"
    node_row = DriveNodeRow.objects.filter(drive_file_id=action.node.id).first() if action.node else None
    ReviewCase.objects.create(
        object=obj,
        case_type=review.get("case_type", CaseType.DRIVE_STRUCTURE),
        case_subtype=(review.get("subtype") or "")[:32],
        drive_node=node_row,
        candidates=review.get("candidates"),
        proposed_action={"options": review.get("options", []), "category_code": action.category_code},
        context={"sync_run_id": run.pk, "seq_no": action.seq_no, "note": action.note},
        batch_key=key,
        priority=60,
    )
    return "ok"


def _execute_inventory(
    obj: ManagedObject,
    action: Action,
    run: DriveSyncRun,
    main_rows: dict[str, DriveNodeRow],
    root_row: DriveNodeRow | None,
) -> str:
    node_rows = {
        r.drive_file_id: r
        for r in DriveNodeRow.objects.filter(object=obj, is_folder=True, status=NodeStatus.ACTIVE)
    }
    now = timezone.now()
    for e in action.inventory or []:
        doc = Document.objects.filter(object=obj, drive_file_id=e["drive_file_id"]).first()
        parent_row = node_rows.get(e["parent_id"])
        if doc is None:
            doc = Document.objects.create(
                object=obj,
                drive_file_id=e["drive_file_id"],
                drive_md5=e["md5"],
                size_bytes=e["size"] or 0,
                mime_type=e["mime_type"] or "application/octet-stream",
                original_name=e["name"],
                current_name=e["name"],
                source="drive_existing",
                source_path=e["path"][:1000],
                drive_node=parent_row,
                status="registered",
                first_seen_at=now,
            )
            e["document_id"] = doc.pk
        elif e["state"] == "changed":
            doc.drive_md5 = e["md5"]
            doc.current_name = e["name"]
            doc.drive_node = parent_row
            doc.save(update_fields=["drive_md5", "current_name", "drive_node", "updated_at"])
        if e["state"] in ("new", "changed"):
            key = f"discover:{obj.pk}:{e['drive_file_id']}:{e['md5'] or e['modified_time']}"
            ProcessingJob.objects.get_or_create(
                idempotency_key=key,
                defaults={
                    "object": obj,
                    "document": doc,
                    "job_type": JobType.DISCOVER,
                    "status": "pending",
                    "payload": {
                        "drive_file_id": e["drive_file_id"],
                        "md5": e["md5"],
                        "modified_time": e["modified_time"],
                        "name": e["name"],
                        "sync_run_id": run.pk,
                    },
                },
            )
    return "ok"


def _verify_postconditions(obj: ManagedObject, plan: Plan, run: DriveSyncRun) -> None:
    problems = []
    for cat in DocumentCategory.objects.filter(is_active=True):
        if plan.has_review_for(cat.code):
            continue
        count = DriveNodeRow.objects.filter(
            object=obj, node_kind=NodeKind.MAIN_FOLDER, category=cat, status=NodeStatus.ACTIVE
        ).count()
        if count != 1 and obj.drive_root_folder_id:
            problems.append(f"Kategorie {cat.code}: {count} aktive Ordner registriert")
    if problems:
        plan.hints.extend(f"Nachbedingung verletzt: {p}" for p in problems)
    run.summary["postconditions_ok"] = not problems


def _finish(run: DriveSyncRun, plan: Plan, *, status: str, executed: bool) -> DriveSyncRun:
    with transaction.atomic():
        DriveSyncAction.objects.filter(sync_run=run).delete()
        rows = []
        notes: dict[str, str] = {}
        for a in plan.actions:
            if a.note:
                notes[str(a.seq_no)] = a.note
            target_id = a.created_node.id if a.created_node else (a.node.id if a.node else None)
            rows.append(
                DriveSyncAction(
                    sync_run=run,
                    seq_no=a.seq_no,
                    action_type=a.action_type,
                    target_drive_id=target_id,
                    parent_drive_id=a.parent_ref
                    if a.parent_ref
                    and not a.parent_ref.startswith("category:")
                    and a.parent_ref != "object_root"
                    else plan.resolved.get(a.parent_ref or "", None),
                    name_before=a.name_before,
                    name_after=a.name_after,
                    file_count_before=a.file_count_before,
                    file_count_after=a.file_count_after,
                    id_hash_before=a.id_hash_before,
                    id_hash_after=a.id_hash_after,
                    planned=True,
                    executed=executed
                    and a.result is not None
                    and a.result != "skipped"
                    or (executed and a.result == "ok"),
                    executed_at=timezone.now() if executed and a.result else None,
                    result=a.result if executed or a.action_type == SyncActionType.FIND_ROOT else None,
                    error_message=a.error,
                )
            )
        DriveSyncAction.objects.bulk_create(rows)
        run.actions_planned = len(plan.actions)
        run.actions_executed = sum(1 for a in plan.actions if executed and a.result == "ok" and a.is_write)
        run.status = status
        run.finished_at = timezone.now()
        run.summary = {
            **(run.summary or {}),
            "hints": plan.hints,
            "notes": notes,
            "inventory": plan.inventory_stats,
            "categories": {str(a.seq_no): a.category_code for a in plan.actions if a.category_code},
            "reviews": [
                a.review | {"seq_no": a.seq_no, "result": a.result} for a in plan.actions if a.review
            ],
            "write_actions_planned": plan.write_count,
            "blocked_categories": sorted(plan.blocked),
        }
        if run.no_changes is None:
            # Dry-Run: keine geplante Schreibaktion und kein geplanter Review-Fall bedeutet unveraenderter Zustand
            run.no_changes = (
                (run.actions_executed == 0)
                if executed
                else (plan.write_count == 0 and not any(a.review for a in plan.actions))
            )
        run.save()
    logger.info(
        "Ordnerabgleich %s Objekt %s: %s, %s Aktionen, %s Schreibaktionen ausgeführt",
        run.pk,
        run.object_id,
        status,
        run.actions_planned,
        run.actions_executed,
    )
    return run


def undo_rename(action: DriveSyncAction, *, drive: DriveAdapter, user=None) -> DriveNode:
    """Admin-Befehl drive:undo-rename (F 4.3 Punkt 6): Umbenennung mit name_before zuruecknehmen."""
    if (
        action.action_type != SyncActionType.RENAME_FOLDER
        or action.result != "ok"
        or not action.target_drive_id
        or not action.name_before
    ):
        raise ReconcileError("Nur ausgeführte Umbenennungen lassen sich zurücknehmen")
    node = drive.rename(action.target_drive_id, action.name_before)
    DriveNodeRow.objects.filter(drive_file_id=node.id).update(drive_name=node.name)
    record(
        "drive.rename",
        entity_type="drive_node",
        object_id=action.sync_run.object_id,
        actor=user,
        actor_type=None if user else "system",
        reason=f"undo-rename Aktion {action.pk}",
        after={"drive_file_id": node.id, "name_before": action.name_after, "name_after": action.name_before},
    )
    return node


def run_summary_hash(run: DriveSyncRun) -> str:
    return hashlib.sha256(
        json.dumps(run.summary or {}, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def should_reconcile_on_open(obj: ManagedObject) -> bool:
    minutes = int(store.get("drive.reconcile_on_open_min_interval_minutes", 15))
    last = DriveSyncRun.objects.filter(object=obj).order_by("-started_at").first()
    return last is None or last.started_at < timezone.now() - timedelta(minutes=minutes)
