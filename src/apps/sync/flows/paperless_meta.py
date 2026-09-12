"""Metadaten der Anwendung in Paperless: Tag und benutzerdefinierte Felder (UUID, Objekt, Zuordnungsstatus,
Drive-Link). Es werden nur diese Felder geschrieben; fremde Tags, Notizen und Felder bleiben unberuehrt. Ein
unveraenderter Stand loest keinen Schreibzugriff aus (Vergleich mit synced_fields)."""

from __future__ import annotations

from django.utils import timezone

from apps.audit.services import record
from apps.sync import config, services
from apps.sync.flows.common import PAPERLESS, SYNCED, client_or_defer, link_for, mark_link, raise_mapped
from apps.sync.models import OperationKind
from apps.sync.operations import Block, Defer, Skip, handler


def desired_fields(doc) -> dict:
    """Sollwerte der Anwendungsfelder (ohne Personendaten)."""
    return {
        "uuid": str(doc.uuid),
        "object": services.object_label(doc.object),
        "status": services.assignment_status(doc),
        "drive": services.drive_link(doc.drive_file_id) or "",
    }


def _setup_or_defer() -> dict:
    meta = services.connection_meta()
    if not meta.get("ok") or not meta.get("field_ids"):
        raise Defer(
            "Kennzeichnung in Paperless nicht eingerichtet (Verbindungstest mit Einrichtung ausführen)", 900
        )
    return meta


def write_fields(client, external_id: int, fields: dict, meta: dict) -> dict:
    ids = meta["field_ids"]
    values = {int(ids[k]): v for k, v in fields.items() if k in ids and v is not None}
    client.set_custom_field_values(int(external_id), values)
    if meta.get("tag_id"):
        client.add_tags(int(external_id), [int(meta["tag_id"])])
    return values


@handler(OperationKind.PAPERLESS_PUSH_META)
def push_meta(op) -> dict:
    doc = op.document
    if doc is None:
        raise Skip("kein Dokument")
    link = link_for(doc, PAPERLESS)
    if link is None:
        raise Skip("keine Paperless-Verknüpfung")
    if not config.writes_allowed(doc.object):
        raise Block("Schreiben nach Paperless für dieses Objekt nicht erlaubt (Modus oder Pilotumfang)")
    client = client_or_defer()
    meta = _setup_or_defer()
    wanted = desired_fields(doc)
    current = dict((link.synced_fields or {}).get("app_fields") or {})
    if current == wanted:
        raise Skip("Metadaten unverändert")
    try:
        write_fields(client, link.external_id, wanted, meta)
    except Exception as exc:
        raise_mapped(exc)
    synced = dict(link.synced_fields or {})
    synced["app_fields"] = wanted
    mark_link(link, SYNCED, None, synced_fields=synced, last_synced_at=timezone.now())
    record(
        "sync.paperless_meta",
        entity_type="document",
        entity_id=doc.pk,
        object_id=doc.object_id,
        before={"app_fields": current},
        after={"app_fields": wanted, "paperless_id": link.external_id},
    )
    return {"paperless_id": link.external_id, "fields": list(wanted)}
