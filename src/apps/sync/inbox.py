"""Technisches Eingangsobjekt und geschuetzter Eingangsordner fuer nicht zugeordnete Dokumente.

Dokumente ohne Objektbezug (aus Paperless oder aus dem Drive-Eingangsordner) laufen als Dokumente des
Eingangsobjekts durch die vorhandene Pipeline (Hash, Text, Entitaeten, Klassifikation) und erhalten danach einen
Zuordnungsvorschlag. Nach bestaetigter Zuordnung wandert das Dokument mit documents.transfer.transfer_document
in das Zielobjekt; die Drive-Datei wird verschoben, nie kopiert. Das Eingangsobjekt erscheint in keiner Objektliste,
bekommt keine Ordnerstruktur, keine Listen und keine Vollstaendigkeitsbewertung."""

from __future__ import annotations

from django.db import transaction

from apps.config import store
from apps.objects.models import ManagedObject


def inbox_object_number() -> str:
    return str(store.get("sync.inbox_object_number", "0"))


def get_inbox_object() -> ManagedObject | None:
    return ManagedObject.objects.filter(is_system_inbox=True, deleted_at__isnull=True).first()


def is_inbox(obj) -> bool:
    return bool(obj is not None and getattr(obj, "is_system_inbox", False))


def ensure_inbox_object(*, user=None) -> ManagedObject:
    """Legt das Eingangsobjekt einmalig an (idempotent). Verwaltungsart rental, Status active, ohne Einheiten."""
    from apps.audit.services import record

    with transaction.atomic():
        existing = (
            ManagedObject.objects.select_for_update()
            .filter(is_system_inbox=True, deleted_at__isnull=True)
            .first()
        )
        if existing is not None:
            return existing
        obj = ManagedObject.objects.create(
            object_number=inbox_object_number(),
            name=str(store.get("sync.inbox_folder_name", "_Eingang_Nicht_zugeordnet")),
            management_type="rental",
            status="active",
            is_system_inbox=True,
            is_test=False,
            created_by=user if getattr(user, "pk", None) else None,
        )
        record(
            "sync.inbox_object_created",
            entity_type="object",
            entity_id=obj.pk,
            object_id=obj.pk,
            after={"object_number": obj.object_number, "name": obj.name},
            actor=user,
        )
        return obj


def inbox_folder_id() -> str | None:
    value = store.get("sync.inbox_folder_id", None)
    return value if isinstance(value, str) and value else None


def ensure_inbox_folder(drive, *, user=None) -> str:
    """Legt den Eingangsordner unter der bestaetigten Drive-Wurzel an oder verwendet den vorhandenen gleichen Namens;
    speichert die Ordner-ID (sync.inbox_folder_id) und am Eingangsobjekt (drive_root_folder_id)."""
    from django.utils import timezone

    from apps.audit.services import record

    folder_id = inbox_folder_id()
    if folder_id:
        node = drive.get(folder_id)
        if node is not None and node.is_folder and not node.trashed:
            return folder_id
    root_id = store.get("drive.root_folder_id", None)
    if not root_id:
        raise ValueError("Drive-Wurzel ist nicht bestätigt; Eingangsordner kann nicht angelegt werden")
    name = str(store.get("sync.inbox_folder_name", "_Eingang_Nicht_zugeordnet"))
    node = next((c for c in drive.list_children(root_id, folders_only=True) if c.name == name), None)
    created = False
    if node is None:
        node = drive.create_folder(root_id, name)
        created = True
    store.set("sync.inbox_folder_id", node.id, user=user, reason="Eingangsordner eingerichtet")
    obj = ensure_inbox_object(user=user)
    ManagedObject.objects.filter(pk=obj.pk).update(
        drive_root_folder_id=node.id, drive_root_folder_name=node.name, drive_root_verified_at=timezone.now()
    )
    record(
        "sync.inbox_folder_set",
        entity_type="object",
        entity_id=obj.pk,
        object_id=obj.pk,
        after={"folder_id": node.id, "name": node.name, "created": created},
        actor=user,
    )
    return node.id
