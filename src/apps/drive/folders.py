"""Eigentuemerakten-Ordner in Drive (Fachentwurf F 6.3): verzoegerte Anlage, zwoelf drive_nodes je Akte, Vorpruefung
per Namensvergleich (Wiederaufnahme), Prozess-Cache im Django-Cache (ANNAHME A-28, zehn Minuten)."""

from __future__ import annotations

from django.core.cache import cache
from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import DocumentSubfolder
from apps.drive.adapter import DriveAdapter, DriveNode
from apps.drive.models import DriveNode as DriveNodeRow
from apps.drive.models import NodeKind, NodeStatus
from apps.drive.reconcile import nfc
from apps.parties.models import OwnerFile

CACHE_SECONDS = 600


class FolderError(Exception):
    pass


def _find_or_create(
    drive: DriveAdapter, parent_id: str, name: str, *, object_id: int, user=None
) -> tuple[DriveNode, bool]:
    existing = [
        c
        for c in drive.list_children(parent_id, folders_only=True)
        if c.is_folder and nfc(c.name) == nfc(name)
    ]
    if existing:
        return existing[0], False
    node = drive.create_folder(parent_id, name)
    record(
        "drive.create_folder",
        entity_type="drive_node",
        object_id=object_id,
        actor=user,
        actor_type=None if user else "system",
        after={"name": name, "drive_file_id": node.id, "parent": parent_id},
    )
    return node, True


def _register(
    owner_file: OwnerFile, node: DriveNode, *, node_kind: str, subfolder=None, parent_row=None, created: bool
) -> DriveNodeRow:
    row = DriveNodeRow.objects.filter(drive_file_id=node.id).first()
    stale = DriveNodeRow.objects.filter(
        owner_file=owner_file, node_kind=node_kind, subfolder=subfolder, status=NodeStatus.ACTIVE
    ).exclude(drive_file_id=node.id)
    stale.update(status=NodeStatus.MISSING)
    if row is None:
        row = DriveNodeRow.objects.create(
            object=owner_file.object,
            node_kind=node_kind,
            category_id="05",
            subfolder=subfolder,
            owner_file=owner_file,
            parent_node=parent_row,
            drive_file_id=node.id,
            drive_parent_id=node.parent_id,
            drive_name=node.name,
            expected_name=owner_file.folder_name if subfolder is None else subfolder.folder_name,
            mime_type=node.mime_type,
            is_folder=True,
            created_by_app=created,
            status=NodeStatus.ACTIVE,
            last_verified_at=timezone.now(),
        )
    else:
        row.drive_name = node.name
        row.status = NodeStatus.ACTIVE
        row.last_verified_at = timezone.now()
        row.save(update_fields=["drive_name", "status", "last_verified_at", "updated_at"])
    return row


def ensure_owner_folder(owner_file: OwnerFile, *, drive: DriveAdapter, user=None) -> list[DriveNodeRow]:
    """Aktenordner plus elf Unterordner anlegen oder bestaetigen; Ergebnis sind zwoelf drive_nodes-Zeilen."""
    main = DriveNodeRow.objects.filter(
        object=owner_file.object, node_kind=NodeKind.MAIN_FOLDER, category_id="05", status=NodeStatus.ACTIVE
    ).first()
    if main is None:
        raise FolderError(
            "Hauptordner der Eigentümerakte ist nicht registriert; zuerst Ordnerabgleich ausführen"
        )
    cache_key = f"drive:node:{owner_file.pk}"
    cached = cache.get(cache_key)
    if cached:
        rows = list(DriveNodeRow.objects.filter(owner_file=owner_file, status=NodeStatus.ACTIVE))
        if len(rows) >= 12:
            return rows
    existing_row = DriveNodeRow.objects.filter(
        owner_file=owner_file, node_kind=NodeKind.OWNER_FILE_FOLDER, status=NodeStatus.ACTIVE
    ).first()
    folder: DriveNode | None = None
    if existing_row is not None:
        folder = drive.get(existing_row.drive_file_id)
        if folder is None or folder.trashed:
            existing_row.status = NodeStatus.TRASHED if folder is not None else NodeStatus.MISSING
            existing_row.save(update_fields=["status", "updated_at"])
            folder = None
        elif nfc(folder.name) != nfc(existing_row.drive_name):
            existing_row.drive_name = (
                folder.name
            )  # manuell umbenannt: Ist-Name uebernehmen, nicht zurueckbenennen
            existing_row.save(update_fields=["drive_name", "updated_at"])
    created = False
    if folder is None:
        folder, created = _find_or_create(
            drive, main.drive_file_id, owner_file.folder_name, object_id=owner_file.object_id, user=user
        )
    folder_row = _register(
        owner_file, folder, node_kind=NodeKind.OWNER_FILE_FOLDER, parent_row=main, created=created
    )
    rows = [folder_row]
    children = drive.list_children(folder.id, folders_only=True)
    for sub in DocumentSubfolder.objects.filter(category_id="05", is_active=True).order_by("sort_order"):
        hit = [c for c in children if c.is_folder and nfc(c.name) == nfc(sub.folder_name)]
        if hit:
            node, made = hit[0], False
        else:
            node = drive.create_folder(folder.id, sub.folder_name)
            made = True
            record(
                "drive.create_folder",
                entity_type="drive_node",
                object_id=owner_file.object_id,
                actor=user,
                actor_type=None if user else "system",
                after={"name": sub.folder_name, "drive_file_id": node.id, "parent": folder.id},
            )
        rows.append(
            _register(
                owner_file,
                node,
                node_kind=NodeKind.OWNER_FILE_SUBFOLDER,
                subfolder=sub,
                parent_row=folder_row,
                created=made,
            )
        )
    cache.set(cache_key, True, CACHE_SECONDS)
    return rows


def invalidate_owner_folder_cache(owner_file_id: int) -> None:
    cache.delete(f"drive:node:{owner_file_id}")


def ensure_category_folder(
    obj, category_code: str, subfolder_code: str | None, *, drive: DriveAdapter, user=None
) -> DriveNodeRow:
    """Hauptordner 01 bis 06 und Unterordner von 06 als drive_nodes; setzt den Objektordner aus dem Abgleich voraus."""
    from apps.documents.models import DocumentCategory
    from apps.drive.reconcile import _register_node

    root = DriveNodeRow.objects.filter(
        object=obj, node_kind=NodeKind.OBJECT_ROOT, status=NodeStatus.ACTIVE
    ).first()
    if root is None:
        raise FolderError("Objektordner unbekannt: Ordnerabgleich zuerst ausführen (CR 9)")
    category = DocumentCategory.objects.get(code=category_code)
    main = DriveNodeRow.objects.filter(
        object=obj, node_kind=NodeKind.MAIN_FOLDER, category=category, status=NodeStatus.ACTIVE
    ).first()
    if main is None:
        node, created = _find_or_create(
            drive, root.drive_file_id, category.folder_name, object_id=obj.pk, user=user
        )
        main = _register_node(
            obj,
            node,
            node_kind=NodeKind.MAIN_FOLDER,
            category_code=category_code,
            subfolder_id=None,
            expected_name=category.folder_name,
            parent_row=root,
            created=created,
        )
    if not subfolder_code:
        return main
    sub = DocumentSubfolder.objects.get(category=category, code=subfolder_code)
    row = DriveNodeRow.objects.filter(
        object=obj,
        node_kind=NodeKind.SUBFOLDER,
        subfolder=sub,
        owner_file__isnull=True,
        tenant_file__isnull=True,
        status=NodeStatus.ACTIVE,
    ).first()
    if row is None:
        node, created = _find_or_create(
            drive, main.drive_file_id, sub.folder_name, object_id=obj.pk, user=user
        )
        row = _register_node(
            obj,
            node,
            node_kind=NodeKind.SUBFOLDER,
            category_code=category_code,
            subfolder_id=sub.pk,
            expected_name=sub.folder_name,
            parent_row=main,
            created=created,
        )
    return row
