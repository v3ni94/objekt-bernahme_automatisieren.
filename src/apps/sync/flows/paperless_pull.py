"""Eingang aus Paperless: regelmaessiger Abgleich (modified-Cursor) und Webhook-Ereignisse legen je Dokument eine
Operation an. Bekannte Dokumente werden mit dem zuletzt abgeglichenen Stand verglichen (Inhalt, Objektfeld);
neue Dokumente werden in das Objekt aus dem Feld Objekt oder in das Eingangsobjekt uebernommen; eine identische
Datei (UUID-Feld oder Pruefsumme) wird nur verknuepft, eine zweite Kopie zu einem bereits verknuepften Dokument
als Konfliktfall gemeldet und nie still umgehaengt; Loeschungen und Papierkorb erzeugen einen Konfliktfall, nie
eine lokale Loeschung."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.documents.ingest import ensure_run, safe_filename
from apps.documents.models import Document, DocumentSource, DocumentStatus
from apps.objects.models import ManagedObject
from apps.pipeline import storage
from apps.sync import config, services
from apps.sync.flows.common import (
    PAPERLESS,
    SYNCED,
    client_or_defer,
    conflict,
    link_for,
    mark_link,
    raise_mapped,
    upsert_link,
)
from apps.sync.models import ExternalLink, LinkRole, LinkState, OperationKind, SyncSystem
from apps.sync.operations import Block, Skip, enqueue, handler, op_key

CURSOR_MODIFIED = "modified_cursor"
CURSOR_LAST_POLL = "last_poll"
_CORRESPONDENTS: dict[int, str] = {}
_CORRESPONDENTS_LOADED_AT: list[float] = []
CORRESPONDENT_CACHE_S = 300


def correspondent_name(client, remote: dict) -> str | None:
    """Name des Korrespondenten aus Paperless (Kennung im Dokument, Aufloesung ueber die Stammdatenliste mit
    Zwischenspeicher je Prozess); dient der Lernfunktion als Lieferantenmerkmal."""
    import time

    raw = remote.get("correspondent")
    if raw in (None, ""):
        return None
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        return None
    now = time.monotonic()
    stale = not _CORRESPONDENTS_LOADED_AT or now - _CORRESPONDENTS_LOADED_AT[0] > CORRESPONDENT_CACHE_S
    if cid not in _CORRESPONDENTS or stale:
        try:
            rows = list(client.list_correspondents())
        except Exception:  # Name ist ein Hilfsmerkmal, kein Pflichtschritt
            return _CORRESPONDENTS.get(cid)
        _CORRESPONDENTS.clear()
        _CORRESPONDENTS.update(
            {int(r["id"]): str(r.get("name") or "") for r in rows if r.get("id") is not None}
        )
        _CORRESPONDENTS_LOADED_AT[:] = [now]
    return _CORRESPONDENTS.get(cid) or None


def poll(*, force: bool = False, limit: int = 2000) -> dict:
    """Liest Dokumente mit modified nach dem Cursor und reiht je Dokument eine Pull-Operation ein."""
    interval = int(config.store.get("paperless.poll_interval_minutes", 5))
    if not force and not services.interval_elapsed(SyncSystem.PAPERLESS, CURSOR_LAST_POLL, interval):
        return {"skipped": "Intervall nicht erreicht"}
    client = services.get_client()
    if client is None:
        return {"skipped": "nicht konfiguriert"}
    cursor = services.get_cursor(SyncSystem.PAPERLESS, CURSOR_MODIFIED)
    since = cursor.value if cursor and cursor.value else None
    newest = since
    count = 0
    try:
        for remote in client.list_documents(modified_after=since, ordering="modified"):
            count += 1
            modified = str(remote.get("modified") or "")
            enqueue(
                OperationKind.PAPERLESS_PULL,
                system=SyncSystem.PAPERLESS,
                key=op_key(OperationKind.PAPERLESS_PULL, remote.get("id"), modified or "x"),
                source_system=SyncSystem.PAPERLESS,
                source_revision=modified or None,
                payload={
                    "paperless_id": remote.get("id"),
                    "modified": modified,
                    "deleted_at": remote.get("deleted_at"),
                },
            )
            if modified and (newest is None or modified > newest):
                newest = modified
            if count >= limit:
                break
    except Exception as exc:
        services.mark_now(
            SyncSystem.PAPERLESS, CURSOR_LAST_POLL, {"error": f"{type(exc).__name__}: {exc}"[:300]}
        )
        return {"error": f"{type(exc).__name__}: {exc}"[:300], "enqueued": count}
    if newest and newest != since:
        services.set_cursor(SyncSystem.PAPERLESS, CURSOR_MODIFIED, newest)
    services.mark_now(SyncSystem.PAPERLESS, CURSOR_LAST_POLL, {"enqueued": count})
    return {"enqueued": count, "cursor": newest}


def enqueue_from_webhook(paperless_id: int, *, event: str = "webhook") -> tuple:
    return enqueue(
        OperationKind.PAPERLESS_PULL,
        system=SyncSystem.PAPERLESS,
        key=op_key(OperationKind.PAPERLESS_PULL, paperless_id, event, timezone.now().strftime("%Y%m%d%H%M")),
        source_system=SyncSystem.PAPERLESS,
        payload={"paperless_id": paperless_id, "event": event},
        priority=50,
    )


def _object_from_field(remote: dict, meta: dict) -> ManagedObject | None:
    field_id = (meta.get("field_ids") or {}).get("object")
    if not field_id:
        return None
    for cf in remote.get("custom_fields") or []:
        if int(cf.get("field", 0)) == int(field_id) and cf.get("value"):
            number = str(cf["value"]).split(",")[0].split(" ")[0].strip()
            if number.isdigit():
                return ManagedObject.active.filter(
                    object_number_numeric=int(number), is_system_inbox=False
                ).first()
    return None


def _uuid_from_field(remote: dict, meta: dict) -> str | None:
    field_id = (meta.get("field_ids") or {}).get("uuid")
    if not field_id:
        return None
    for cf in remote.get("custom_fields") or []:
        if int(cf.get("field", 0)) == int(field_id) and cf.get("value"):
            return str(cf["value"]).strip()
    return None


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def _second_copy(local: Document, existing: ExternalLink, remote: dict, remote_id: int, match: str) -> dict:
    """Zweite Kopie in Paperless zu einem bereits verknuepften Dokument: die Verknuepfung bleibt auf der bekannten
    Kopie (sonst ginge die Kennung der eigenen Uebertragung verloren), die zweite Kopie wird als Konfliktfall
    duplicate_remote gemeldet; nichts wird geladen, nichts in Paperless veraendert."""
    case = conflict(
        local,
        "duplicate_remote",
        str(remote_id),
        {
            "paperless_id": str(remote_id),
            "linked_paperless_id": existing.external_id,
            "match": match,
            "title": remote.get("title"),
        },
    )
    record(
        "sync.paperless_pull",
        entity_type="document",
        entity_id=local.pk,
        object_id=local.object_id,
        after={
            "paperless_id": remote_id,
            "duplicate_remote": True,
            "linked_paperless_id": existing.external_id,
            "match": match,
        },
    )
    return {
        "duplicate_remote": True,
        "paperless_id": remote_id,
        "document_id": local.pk,
        "linked_paperless_id": existing.external_id,
        "case_id": case.pk if case else None,
    }


def _import_new(client, remote: dict, metadata: dict, meta: dict) -> dict:
    """Neues Paperless-Dokument uebernehmen: identische Datei verknuepfen, sonst Original laden und registrieren."""
    from apps.sync.inbox import ensure_inbox_object

    checksum = metadata.get("original_checksum")
    remote_id = int(remote["id"])
    uuid_value = _uuid_from_field(remote, meta)
    if uuid_value:
        local = (
            Document.objects.filter(uuid=uuid_value, deleted_at__isnull=True)
            .exclude(status="moved_out")
            .first()
        )
        if local is not None:
            existing = link_for(local, PAPERLESS)
            if existing is not None and existing.external_id != str(remote_id):
                return _second_copy(local, existing, remote, remote_id, "uuid")
            upsert_link(
                local,
                system=PAPERLESS,
                external_id=str(remote_id),
                checksum_sha256=checksum,
                state=LinkState.LINKED,
                state_reason="UUID-Feld",
            )
            record(
                "sync.paperless_pull",
                entity_type="document",
                entity_id=local.pk,
                object_id=local.object_id,
                after={"paperless_id": remote_id, "linked_existing": True, "reason": "uuid"},
            )
            return {"linked": local.pk, "reason": "uuid"}
    if checksum:
        same = list(
            Document.objects.filter(sha256=checksum, deleted_at__isnull=True)
            .exclude(status__in=["moved_out", "duplicate"])
            .order_by("id")[:2]
        )
        if same:
            local = same[0]
            existing = link_for(local, PAPERLESS)
            if existing is not None and existing.external_id != str(remote_id):
                return _second_copy(local, existing, remote, remote_id, "checksum")
            upsert_link(
                local,
                system=PAPERLESS,
                external_id=str(remote_id),
                checksum_sha256=checksum,
                state=LinkState.LINKED,
                state_reason="identische Datei (Prüfsumme)" + (", weitere Treffer" if len(same) > 1 else ""),
                synced_fields={"title": remote.get("title"), "tags": remote.get("tags")},
            )
            record(
                "sync.paperless_pull",
                entity_type="document",
                entity_id=local.pk,
                object_id=local.object_id,
                after={"paperless_id": remote_id, "linked_existing": True, "ambiguous": len(same) > 1},
            )
            return {"linked": local.pk, "reason": "checksum"}
    if not config.import_new_documents():
        raise Skip("Übernahme neuer Paperless-Dokumente ist abgeschaltet")
    target = _object_from_field(remote, meta) or ensure_inbox_object()
    name = safe_filename(
        remote.get("original_file_name") or metadata.get("original_filename") or f"paperless-{remote_id}.pdf"
    )
    if not Path(name).suffix:
        name += ".pdf"
    storage.ensure_disk_reserve()
    target_dir = storage.upload_dir(target.pk)
    path = target_dir / name
    try:
        client.download(remote_id, path, original=True)
    except Exception as exc:
        raise_mapped(exc)
    size = path.stat().st_size
    if size > int(config.store.get("documents.max_download_bytes", 524288000)):
        path.unlink(missing_ok=True)
        raise Block(f"Paperless-Dokument {remote_id} überschreitet die Größengrenze")
    mime = metadata.get("original_mime_type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    with transaction.atomic():
        doc = Document.objects.create(
            object=target,
            size_bytes=size,
            mime_type=mime,
            original_name=name,
            current_name=name,
            source=DocumentSource.PAPERLESS,
            source_path=str(path),
            status=DocumentStatus.REGISTERED,
            first_seen_at=_parse_dt(remote.get("added")) or timezone.now(),
            document_date=_parse_dt(remote.get("created")).date()
            if _parse_dt(remote.get("created"))
            else None,
        )
        upsert_link(
            doc,
            system=PAPERLESS,
            external_id=str(remote_id),
            checksum_sha256=checksum,
            mime_type=mime,
            size_bytes=size,
            remote_modified_at=_parse_dt(remote.get("modified")),
            state=LinkState.LINKED,
            state_reason="aus Paperless übernommen",
            synced_fields={
                "title": remote.get("title"),
                "tags": remote.get("tags"),
                "modified": remote.get("modified"),
                "correspondent": correspondent_name(client, remote),
                "object_field": (_object_from_field(remote, meta) or ManagedObject()).object_number or None,
            },
        )
        record(
            "sync.paperless_pull",
            entity_type="document",
            entity_id=doc.pk,
            object_id=target.pk,
            after={
                "paperless_id": remote_id,
                "size_bytes": size,
                "mime_type": mime,
                "inbox": target.is_system_inbox,
            },
        )
    ensure_run(target)
    return {"document_id": doc.pk, "object_id": target.pk, "inbox": target.is_system_inbox}


def _check_known(client, link: ExternalLink, remote: dict, metadata: dict, meta: dict) -> dict:
    """Bekanntes Dokument: Vergleich mit dem zuletzt abgeglichenen Stand."""
    doc = link.document
    result: dict = {"paperless_id": link.external_id}
    remote_checksum = metadata.get("original_checksum")
    if remote_checksum and link.checksum_sha256 and remote_checksum != link.checksum_sha256:
        case = conflict(
            doc,
            "content_changed_remote",
            remote_checksum[:16],
            {
                "paperless_id": link.external_id,
                "local_sha256": doc.sha256,
                "known_remote_checksum": link.checksum_sha256,
                "new_remote_checksum": remote_checksum,
                "versions": remote.get("versions"),
            },
        )
        mark_link(
            link,
            LinkState.CHANGED_REMOTE,
            "Inhalt in Paperless geändert",
            remote_modified_at=_parse_dt(remote.get("modified")),
        )
        result["conflict"] = "content_changed_remote"
        result["case_id"] = case.pk if case else None
        return result
    wanted_object = _object_from_field(remote, meta)
    if wanted_object is not None and wanted_object.pk != doc.object_id and not doc.object.is_system_inbox:
        case = conflict(
            doc,
            "object_changed_remote",
            f"{wanted_object.pk}",
            {
                "paperless_id": link.external_id,
                "local_object_id": doc.object_id,
                "remote_object_id": wanted_object.pk,
            },
        )
        result["conflict"] = "object_changed_remote"
        result["case_id"] = case.pk if case else None
    elif wanted_object is not None and doc.object.is_system_inbox:
        # Zuordnung im DMS getroffen: als Vorschlag mit hoher Prioritaet in den Eingang
        from apps.review.models import CaseType
        from apps.sync.flows.common import open_case

        open_case(
            doc.object,
            case_type=CaseType.OBJECT_ASSIGNMENT,
            subtype="from_paperless",
            key=f"object_assignment:{doc.pk}",
            document=doc,
            context={
                "source": "paperless",
                "paperless_id": link.external_id,
                "remote_object_id": wanted_object.pk,
            },
            candidates=[
                {
                    "object_id": wanted_object.pk,
                    "object_number": wanted_object.object_number,
                    "score": 0.9,
                    "evidence": [{"kind": "paperless_field", "text": "Feld Objekt in Paperless"}],
                }
            ],
            proposed_action={"action": "assign_object", "object_id": wanted_object.pk},
        )
        result["proposal"] = wanted_object.pk
    synced = dict(link.synced_fields or {})
    synced.update(
        {
            "title": remote.get("title"),
            "tags": remote.get("tags"),
            "modified": remote.get("modified"),
            "correspondent": correspondent_name(client, remote),
        }
    )
    mark_link(
        link,
        SYNCED if "conflict" not in result else LinkState.CONFLICT,
        None if "conflict" not in result else result["conflict"],
        synced_fields=synced,
        remote_modified_at=_parse_dt(remote.get("modified")),
        last_seen_at=timezone.now(),
    )
    return result


@handler(OperationKind.PAPERLESS_PULL)
def pull(op) -> dict:
    payload = op.payload or {}
    remote_id = payload.get("paperless_id")
    if not remote_id:
        raise Skip("keine Paperless-ID")
    client = client_or_defer()
    meta = services.connection_meta()
    link = (
        ExternalLink.objects.filter(system=PAPERLESS, external_id=str(remote_id), role=LinkRole.ORIGINAL)
        .select_related("document", "document__object")
        .first()
    )
    if link is not None and link.state == LinkState.TOMBSTONE:
        raise Skip("Löschung bestätigt, keine erneute Übernahme")
    from apps.sync.paperless import errors as e

    try:
        remote = client.get_document(int(remote_id))
    except e.PaperlessNotFound:
        if link is not None:
            mark_link(link, LinkState.MISSING, "in Paperless nicht mehr vorhanden")
            conflict(link.document, "deleted_remote", "missing", {"paperless_id": link.external_id})
            return {"missing": True, "paperless_id": remote_id}
        raise Skip("in Paperless nicht (mehr) vorhanden") from None
    except Exception as exc:
        raise_mapped(exc)
    if remote.get("deleted_at"):
        if link is not None and link.state != LinkState.TRASHED:
            mark_link(link, LinkState.TRASHED, "in Paperless im Papierkorb")
            conflict(
                link.document,
                "trashed_remote",
                str(remote.get("deleted_at"))[:10],
                {"paperless_id": link.external_id},
            )
        return {"trashed": True, "paperless_id": remote_id}
    try:
        metadata = client.get_metadata(int(remote_id)) or {}
    except Exception as exc:
        raise_mapped(exc)
    if link is not None:
        return _check_known(client, link, remote, metadata, meta)
    return _import_new(client, remote, metadata, meta)
