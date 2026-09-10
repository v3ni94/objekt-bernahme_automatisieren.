"""Veroeffentlichung der Listen in Drive ueber die gespeicherte File-ID (Fachentwurf F 7.2, CR 12a): genau eine Datei
je Liste und Format im Hauptordner 05 (Eigentuemerliste) bzw. 04 (Mieterliste), Sollname aus lists.*_name_pattern,
neue Revision derselben ID bei geaendertem Inhalt, skipped_unchanged bei gleichem content_hash; geloescht oder im
Papierkorb ergibt eine neue Datei (alte Zeile missing oder trashed), verschoben oder umbenannt wird zurueckgefuehrt
(lists.restore_position) oder als Fall gemeldet. Kein Datum im Dateinamen."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.utils import timezone

from apps.config import store
from apps.drive.adapter import DriveAdapter, DriveError
from apps.drive.folders import ensure_category_folder
from apps.drive.models import DriveNode as DriveNodeRow
from apps.drive.models import NodeKind, NodeStatus
from apps.lists.models import GenerationStatus, ListGeneration
from apps.objects.models import ManagedObject

logger = logging.getLogger(__name__)
MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
CATEGORY_FOR = {"owner_list": "05", "tenant_list": "04"}
PATTERN_KEY = {"owner_list": "lists.owner_list_name_pattern", "tenant_list": "lists.tenant_list_name_pattern"}
DEFAULT_PATTERN = {
    "owner_list": "00_Eigentuemerliste_{number}.{ext}",
    "tenant_list": "00_Mieterliste_{number}.{ext}",
}


class PublishError(Exception):
    pass


class ReviewRequired(PublishError):
    pass


def object_number_for_files(obj: ManagedObject) -> str:
    """NNN in der Schreibweise des Objektordners (F4): fuehrende Ziffern des Ordnernamens, sonst Objektnummer."""
    m = re.match(r"^\s*(\d+)", obj.drive_root_folder_name or "")
    return m.group(1) if m else obj.object_number


def render_name(obj: ManagedObject, list_type: str, fmt: str) -> str:
    pattern = str(store.get(PATTERN_KEY[list_type], DEFAULT_PATTERN[list_type]) or DEFAULT_PATTERN[list_type])
    return pattern.format(number=object_number_for_files(obj), ext=fmt)


def _log(messages: list[str], text: str) -> None:
    messages.append(text)
    logger.info("Listen: %s", text)


def publish_list(
    obj: ManagedObject,
    list_type: str,
    fmt: str,
    local_path: Path,
    content_hash: str,
    *,
    drive: DriveAdapter,
    user=None,
    messages: list[str] | None = None,
) -> tuple[DriveNodeRow, str]:
    """Rueckgabe: (drive_nodes-Zeile, Status done oder skipped_unchanged); wirft DriveError oder ReviewRequired."""
    messages = messages if messages is not None else []
    parent = ensure_category_folder(obj, CATEGORY_FOR[list_type], None, drive=drive, user=user)
    name = render_name(obj, list_type, fmt)
    node = DriveNodeRow.objects.filter(
        object=obj,
        node_kind=NodeKind.LIST_FILE,
        list_type=list_type,
        list_format=fmt,
        status=NodeStatus.ACTIVE,
    ).first()
    if node is None:
        return _create_new(obj, parent, list_type, fmt, name, local_path, drive), GenerationStatus.DONE
    remote = drive.get(node.drive_file_id)
    if remote is None:
        node.status = NodeStatus.MISSING
        node.save(update_fields=["status", "updated_at"])
        _log(messages, f"{name}: in Drive nicht mehr vorhanden, neu angelegt")
        return _create_new(obj, parent, list_type, fmt, name, local_path, drive), GenerationStatus.DONE
    if remote.trashed:
        node.status = NodeStatus.TRASHED
        node.save(update_fields=["status", "updated_at"])
        _log(messages, f"{name}: im Papierkorb, neu angelegt, Papierkorb unverändert")
        return _create_new(obj, parent, list_type, fmt, name, local_path, drive), GenerationStatus.DONE
    restore = bool(store.get("lists.restore_position", True))
    if remote.parent_id and remote.parent_id != parent.drive_file_id:
        if restore:
            drive.move(remote.id, remote.parent_id, parent.drive_file_id)
            _log(messages, f"{name}: zurück in den Zielordner verschoben")
        else:
            _review_case(obj, node, "list_moved", f"Listen-Datei {name} liegt außerhalb des Zielordners")
    if remote.name != name:
        if restore:
            drive.rename(remote.id, name)
            _log(messages, f"{remote.name}: auf Sollnamen {name} zurückbenannt")
        else:
            _review_case(
                obj, node, "list_renamed", f"Listen-Datei {remote.name} weicht vom Sollnamen {name} ab"
            )
    last = (
        ListGeneration.objects.filter(
            object=obj, list_type=list_type, list_format=fmt, status=GenerationStatus.DONE, drive_node=node
        )
        .order_by("-generated_at")
        .first()
    )
    if last is not None and last.content_hash == content_hash:
        _log(messages, f"{name}: Inhalt unverändert, keine neue Version")
        return node, GenerationStatus.SKIPPED_UNCHANGED
    result = drive.update_content(remote.id, local_path, MIME[fmt])
    _verify(result, local_path)
    node.drive_name = name
    node.drive_parent_id = parent.drive_file_id
    node.parent_node = parent
    node.expected_name = name
    node.last_verified_at = timezone.now()
    node.save(
        update_fields=[
            "drive_name",
            "drive_parent_id",
            "parent_node",
            "expected_name",
            "last_verified_at",
            "updated_at",
        ]
    )
    _log(messages, f"{name}: neue Version derselben Datei")
    return node, GenerationStatus.DONE


def _create_new(obj, parent, list_type, fmt, name, local_path, drive) -> DriveNodeRow:
    existing = [
        c
        for c in drive.list_children(parent.drive_file_id)
        if c.name == name and not c.trashed and not c.is_folder
    ]
    if len(existing) > 1:
        _review_case(
            obj, None, "list_duplicates", f"Mehrere Listen-Dateien mit Sollnamen {name} im Zielordner"
        )
        raise ReviewRequired(f"mehrere Listen-Dateien mit Sollnamen {name}, Auswahl im Review Center")
    if len(existing) == 1:  # Wiederaufnahme: Datei ohne Datenbankzeile
        result = drive.update_content(existing[0].id, local_path, MIME[fmt])
    else:
        result = drive.upload(
            parent.drive_file_id, local_path, name, MIME[fmt], app_properties={"objektakte_list": list_type}
        )
    _verify(result, local_path)
    now = timezone.now()
    row = DriveNodeRow.objects.filter(drive_file_id=result.id).first()
    values = {
        "object": obj,
        "node_kind": NodeKind.LIST_FILE,
        "category_id": CATEGORY_FOR[list_type],
        "list_type": list_type,
        "list_format": fmt,
        "parent_node": parent,
        "drive_parent_id": parent.drive_file_id,
        "drive_name": name,
        "expected_name": name,
        "mime_type": MIME[fmt],
        "is_folder": False,
        "created_by_app": True,
        "status": NodeStatus.ACTIVE,
        "last_verified_at": now,
    }
    if row is None:
        row = DriveNodeRow.objects.create(drive_file_id=result.id, **values)
    else:
        for k, v in values.items():
            setattr(row, k, v)
        row.save()
    return row


def _verify(result, local_path: Path) -> None:
    md5 = getattr(result, "md5", None)
    if not md5:
        return
    import hashlib

    local = hashlib.md5(Path(local_path).read_bytes(), usedforsecurity=False).hexdigest()
    if local != md5:
        raise DriveError(
            f"Prüfsumme nach Upload abweichend ({local} gegen {md5})", status=None, reason="checksum"
        )


def _review_case(obj, node, subtype: str, reason: str) -> None:
    from apps.review.models import CaseStatus, CaseType, ReviewCase

    key = f"lists:{obj.pk}:{subtype}"
    if ReviewCase.objects.filter(
        batch_key=key, status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS]
    ).exists():
        return
    ReviewCase.objects.create(
        object=obj,
        case_type=CaseType.DRIVE_STRUCTURE,
        case_subtype=subtype[:32],
        context={"reason": reason, "drive_node_id": node.pk if node else None},
        batch_key=key,
        priority=80,
    )
