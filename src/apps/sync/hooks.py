"""Einhaengepunkte der Pipeline in die Synchronisation. Jeder Aufruf ist gegen Ausnahmen gesichert, damit die
Verarbeitung nie an der Synchronisation scheitert; ohne aktive Anbindung passiert nichts."""

from __future__ import annotations

import logging

from apps.sync import config
from apps.sync.models import ExternalLink, LinkRole, OperationKind, SyncSystem

logger = logging.getLogger(__name__)


def _paperless_link(doc) -> ExternalLink | None:
    return ExternalLink.objects.filter(
        document=doc, system=SyncSystem.PAPERLESS, role=LinkRole.ORIGINAL
    ).first()


def on_document_hashed(doc) -> None:
    """Nach dem Hash: Uebertragung nach Paperless vormerken (Upload oder Verknuepfung einer vorhandenen Kopie)."""
    from apps.sync import operations

    if doc.source == "paperless" or not doc.sha256:
        return
    if not config.writes_allowed(doc.object):
        return
    if _paperless_link(doc) is not None:
        return
    operations.enqueue(
        OperationKind.PAPERLESS_PUSH,
        system=SyncSystem.PAPERLESS,
        key=operations.op_key(OperationKind.PAPERLESS_PUSH, doc.uuid, doc.sha256),
        document=doc,
        source_system=SyncSystem.APP,
        source_revision=doc.sha256,
        payload={"reason": "hashed"},
    )


def on_document_filed(doc) -> None:
    """Nach der Ablage in Drive: Kennzeichen an der Drive-Datei setzen und Drive-Link sowie Objektbezug in
    Paperless nachtragen."""
    from apps.sync import operations

    if not doc.drive_file_id:
        return
    if config.active() or config.drive_changes_enabled():
        operations.enqueue(
            OperationKind.DRIVE_SET_PROPS,
            system=SyncSystem.DRIVE,
            key=operations.op_key(OperationKind.DRIVE_SET_PROPS, doc.uuid, doc.drive_file_id),
            document=doc,
            source_system=SyncSystem.APP,
            source_revision=doc.drive_file_id,
        )
    if _paperless_link(doc) is not None and config.writes_allowed(doc.object):
        operations.enqueue(
            OperationKind.PAPERLESS_PUSH_META,
            system=SyncSystem.PAPERLESS,
            key=operations.op_key(OperationKind.PAPERLESS_PUSH_META, doc.uuid, "filed", doc.drive_file_id),
            document=doc,
            source_system=SyncSystem.APP,
            payload={"reason": "filed"},
        )


def on_document_transferred(old_doc, new_doc) -> None:
    """Objektuebernahme: Identitaet und externe Verknuepfungen wandern zur Nachfolgezeile; Paperless erhaelt den
    neuen Objektbezug."""
    from apps.sync import operations
    from apps.sync.models import DocumentVersion

    ExternalLink.objects.filter(document=old_doc).update(document=new_doc)
    DocumentVersion.objects.filter(document=old_doc).update(document=new_doc)
    if _paperless_link(new_doc) is not None and config.writes_allowed(new_doc.object):
        operations.enqueue(
            OperationKind.PAPERLESS_PUSH_META,
            system=SyncSystem.PAPERLESS,
            key=operations.op_key(
                OperationKind.PAPERLESS_PUSH_META, new_doc.uuid, "object", new_doc.object_id
            ),
            document=new_doc,
            source_system=SyncSystem.APP,
            payload={"reason": "object"},
        )


def safe(name: str, *args) -> None:
    """Aufruf eines Einhaengepunkts ohne Rueckwirkung auf den Aufrufer."""
    try:
        globals()[name](*args)
    except Exception:  # Synchronisation darf die Pipeline nie scheitern lassen
        logger.exception("Synchronisations-Hook %s fehlgeschlagen", name)
