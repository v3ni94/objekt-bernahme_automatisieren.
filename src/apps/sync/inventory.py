"""Bestandslauf (Auftrag Abschnitt 7): wiederaufnehmbares Inventar von Paperless und Drive in begrenzten Paketen mit
Manifest je Datei (InventoryItem), Trockenlauf, Pilotumfang, Pausieren und Fortsetzen. Vor dem Drive-Inventar wird
der Ausgangscursor der Changes-API gesichert (cursor_before), vor dem Paperless-Inventar der juengste modified-Stand.
Der Abschluss zaehlt nur, was tatsaechlich erfasst wurde; offene Fehler bleiben sichtbar."""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.audit.services import record
from apps.documents.models import Document
from apps.drive.adapter import FOLDER_MIME, DriveError
from apps.objects.models import ManagedObject
from apps.sync import config, inbox, services
from apps.sync.flows.paperless_pull import CURSOR_MODIFIED
from apps.sync.models import (
    Disposition,
    ExternalLink,
    InventoryItem,
    InventoryRun,
    InventoryStatus,
    LinkRole,
    OperationKind,
    SyncSystem,
)
from apps.sync.operations import enqueue, op_key

logger = logging.getLogger(__name__)


class InventoryError(Exception):
    pass


def start(
    kind: str, *, dry_run: bool = True, scope: dict | None = None, user=None, request=None
) -> InventoryRun:
    if kind not in (SyncSystem.PAPERLESS, SyncSystem.DRIVE):
        raise InventoryError("Bestandslauf nur für paperless oder drive")
    if InventoryRun.objects.filter(
        kind=kind, status__in=[InventoryStatus.RUNNING, InventoryStatus.PLANNED]
    ).exists():
        raise InventoryError("Es läuft bereits ein Bestandslauf dieser Art")
    cursor_before: dict = {}
    if kind == SyncSystem.DRIVE:
        drive = services.get_drive()
        if drive is None:
            raise InventoryError("keine Google-Verbindung")
        from apps.sync.drive_changes import ensure_start_token

        cursor_before = {"drive_page_token": ensure_start_token(drive)}
    else:
        if services.get_client() is None:
            raise InventoryError("Paperless nicht konfiguriert")
        row = services.get_cursor(SyncSystem.PAPERLESS, CURSOR_MODIFIED)
        cursor_before = {
            "paperless_modified": row.value if row else None,
            "started": timezone.now().isoformat(),
        }
    run = InventoryRun.objects.create(
        kind=kind,
        dry_run=dry_run,
        status=InventoryStatus.RUNNING,
        scope=scope or {},
        cursor_before=cursor_before,
        page_state={},
        counters={"seen": 0, "steps": 0},
        started_by=user if getattr(user, "pk", None) else None,
        started_at=timezone.now(),
    )
    record(
        "sync.inventory_start",
        entity_type="sync_inventory_run",
        entity_id=run.pk,
        after={"kind": kind, "dry_run": dry_run, "scope": scope or {}},
        actor=user,
        request=request,
    )
    from django.conf import settings

    if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "celery":
        from apps.sync.tasks import inventory_step_task

        inventory_step_task.apply_async(args=[run.pk], queue="io")
    return run


def pause(run: InventoryRun, *, user=None, request=None) -> None:
    InventoryRun.objects.filter(pk=run.pk, status=InventoryStatus.RUNNING).update(
        status=InventoryStatus.PAUSED
    )
    record(
        "sync.inventory_pause",
        entity_type="sync_inventory_run",
        entity_id=run.pk,
        actor=user,
        request=request,
    )


def resume(run: InventoryRun, *, user=None, request=None) -> None:
    InventoryRun.objects.filter(
        pk=run.pk, status__in=[InventoryStatus.PAUSED, InventoryStatus.FAILED]
    ).update(status=InventoryStatus.RUNNING, error_message=None)
    record(
        "sync.inventory_resume",
        entity_type="sync_inventory_run",
        entity_id=run.pk,
        actor=user,
        request=request,
    )
    from django.conf import settings

    if settings.OBJEKTAKTE.get("JOB_DISPATCH", "celery") == "celery":
        from apps.sync.tasks import inventory_step_task

        inventory_step_task.apply_async(args=[run.pk], queue="io")


def abort(run: InventoryRun, *, user=None, request=None) -> None:
    InventoryRun.objects.filter(pk=run.pk).exclude(status=InventoryStatus.DONE).update(
        status=InventoryStatus.ABORTED, finished_at=timezone.now()
    )
    record(
        "sync.inventory_done",
        entity_type="sync_inventory_run",
        entity_id=run.pk,
        after={"aborted": True},
        actor=user,
        request=request,
    )


def _item(run: InventoryRun, **fields) -> InventoryItem:
    item, _ = InventoryItem.objects.update_or_create(
        run=run, system=fields.pop("system"), external_id=fields.pop("external_id"), defaults=fields
    )
    return item


def _finish(run: InventoryRun, status: str = InventoryStatus.DONE, error: str | None = None) -> None:
    # Zaehler aus der Datenbank lesen: das uebergebene Objekt stammt vom Beginn des Schritts, die Seite hat die
    # Zaehler inzwischen per UPDATE fortgeschrieben (sonst ginge der Stand der letzten Seite verloren).
    stored = InventoryRun.objects.filter(pk=run.pk).values_list("counters", flat=True).first()
    counters = dict(stored if stored is not None else (run.counters or {}))
    by_disp = {}
    for row in InventoryItem.objects.filter(run=run).values("disposition"):
        by_disp[row["disposition"]] = by_disp.get(row["disposition"], 0) + 1
    counters["dispositions"] = by_disp
    counters["complete"] = (
        status == InventoryStatus.DONE
        and not by_disp.get(Disposition.ERROR)
        and not by_disp.get(Disposition.PENDING)
    )
    InventoryRun.objects.filter(pk=run.pk).update(
        status=status, finished_at=timezone.now(), counters=counters, error_message=error
    )
    record(
        "sync.inventory_done",
        entity_type="sync_inventory_run",
        entity_id=run.pk,
        after={"status": status, **counters},
    )


def step(run_id: int) -> dict:
    run = InventoryRun.objects.filter(pk=run_id).first()
    if run is None or run.status != InventoryStatus.RUNNING:
        return {"continue": False, "status": run.status if run else "missing"}
    try:
        if run.kind == SyncSystem.PAPERLESS:
            result = _paperless_step(run)
        else:
            result = _drive_step(run)
    except Exception as exc:  # Fehler bleibt sichtbar, Lauf pausiert statt Endlosschleife
        logger.exception("Bestandslauf %s abgebrochen", run.pk)
        _finish(run, InventoryStatus.FAILED, f"{type(exc).__name__}: {exc}"[:500])
        return {"continue": False, "error": str(exc)[:200]}
    if not result.get("continue"):
        _finish(run)
    return result


# ---------------------------------------------------------------- Paperless
def _paperless_step(run: InventoryRun) -> dict:
    client = services.get_client()
    if client is None:
        raise InventoryError("Paperless nicht konfiguriert")
    state = dict(run.page_state or {})
    page_no = int(state.get("next_page") or 1)
    page_size = min(config.inventory_page_size(), 200)
    page = next(iter(client.iter_pages(ordering="id", page_size=page_size, start_page=page_no)), None)
    counters = dict(run.counters or {})
    if page is None:
        return {"continue": False}
    for remote in page.results:
        counters["seen"] = counters.get("seen", 0) + 1
        _classify_paperless(run, client, remote)
    counters["steps"] = counters.get("steps", 0) + 1
    state["next_page"] = page.next_number
    state["count"] = page.count
    InventoryRun.objects.filter(pk=run.pk).update(page_state=state, counters=counters)
    return {"continue": page.has_next, "page": page.number, "count": page.count}


def _classify_paperless(run: InventoryRun, client, remote: dict) -> None:
    remote_id = str(remote["id"])
    fields = {
        "name": remote.get("original_file_name") or remote.get("title"),
        "mime_type": remote.get("mime_type"),
        "remote_modified_at": _dt(remote.get("modified")),
    }
    link = (
        ExternalLink.objects.filter(
            system=SyncSystem.PAPERLESS, external_id=remote_id, role=LinkRole.ORIGINAL
        )
        .select_related("document")
        .first()
    )
    if link is not None:
        _item(
            run,
            system=SyncSystem.PAPERLESS,
            external_id=remote_id,
            document=link.document,
            object=link.document.object,
            disposition=Disposition.LINK_EXISTING,
            details={"reason": "bereits verknüpft"},
            **fields,
        )
        return
    if remote.get("deleted_at"):
        _item(
            run,
            system=SyncSystem.PAPERLESS,
            external_id=remote_id,
            disposition=Disposition.OUT_OF_SCOPE,
            details={"reason": "im Papierkorb von Paperless"},
            **fields,
        )
        return
    try:
        metadata = client.get_metadata(int(remote_id)) or {}
    except Exception as exc:
        _item(
            run,
            system=SyncSystem.PAPERLESS,
            external_id=remote_id,
            disposition=Disposition.ERROR,
            error_message=str(exc)[:500],
            **fields,
        )
        return
    checksum = metadata.get("original_checksum")
    fields.update(
        {
            "checksum_sha256": checksum,
            "size_bytes": metadata.get("original_size"),
            "mime_type": metadata.get("original_mime_type") or fields["mime_type"],
        }
    )
    same = (
        list(
            Document.objects.filter(sha256=checksum, deleted_at__isnull=True)
            .exclude(status__in=["moved_out", "duplicate"])
            .order_by("id")[:2]
        )
        if checksum
        else []
    )
    if same:
        disp = Disposition.LINK_EXISTING if len(same) == 1 else Disposition.DUPLICATE
        item = _item(
            run,
            system=SyncSystem.PAPERLESS,
            external_id=remote_id,
            document=same[0],
            object=same[0].object,
            disposition=disp,
            details={"reason": "identische Datei", "matches": [d.pk for d in same]},
            **fields,
        )
    else:
        item = _item(
            run,
            system=SyncSystem.PAPERLESS,
            external_id=remote_id,
            disposition=Disposition.IMPORT_NEW,
            details={"reason": "nur in Paperless"},
            **fields,
        )
    if not run.dry_run:
        op, _ = enqueue(
            OperationKind.PAPERLESS_PULL,
            system=SyncSystem.PAPERLESS,
            key=op_key(OperationKind.PAPERLESS_PULL, remote_id, "inventory", run.pk),
            source_system=SyncSystem.PAPERLESS,
            payload={"paperless_id": int(remote_id), "inventory_run_id": run.pk},
            priority=120,
        )
        InventoryItem.objects.filter(pk=item.pk).update(
            operation=op,
            disposition=Disposition.IN_PROGRESS
            if item.disposition == Disposition.IMPORT_NEW
            else item.disposition,
        )


# ---------------------------------------------------------------- Drive
def _drive_step(run: InventoryRun) -> dict:
    drive = services.get_drive()
    if drive is None:
        raise InventoryError("keine Google-Verbindung")
    state = dict(run.page_state or {})
    if "roots" not in state:
        scope = run.scope or {}
        numbers = {str(n) for n in scope.get("object_numbers") or []}
        objs = ManagedObject.active.exclude(drive_root_folder_id__isnull=True).exclude(
            drive_root_folder_id=""
        )
        if numbers:
            objs = objs.filter(object_number__in=numbers)
        roots = [
            {"object_id": o.pk, "folder_id": o.drive_root_folder_id}
            for o in objs.order_by("object_number_numeric")
        ]
        inbox_obj = inbox.get_inbox_object()
        if inbox_obj is not None and inbox.inbox_folder_id() and not numbers:
            roots.append({"object_id": inbox_obj.pk, "folder_id": inbox.inbox_folder_id()})
        state = {"roots": roots, "index": 0, "stack": []}
    counters = dict(run.counters or {})
    limit = config.inventory_page_size()
    processed = 0
    while processed < limit:
        if not state["stack"]:
            if state["index"] >= len(state["roots"]):
                InventoryRun.objects.filter(pk=run.pk).update(page_state=state, counters=counters)
                return {"continue": False}
            root = state["roots"][state["index"]]
            state["index"] += 1
            state["stack"] = [root["folder_id"]]
            state["object_id"] = root["object_id"]
        folder_id = state["stack"].pop()
        try:
            children = drive.list_children(folder_id)
        except DriveError as exc:
            _item(
                run,
                system=SyncSystem.DRIVE,
                external_id=folder_id,
                disposition=Disposition.ERROR,
                error_message=str(exc)[:500],
                object_id=state["object_id"],
            )
            continue
        for child in children:
            if child.is_folder:
                state["stack"].append(child.id)
                continue
            processed += 1
            counters["seen"] = counters.get("seen", 0) + 1
            _classify_drive(run, child, folder_id, state["object_id"])
    counters["steps"] = counters.get("steps", 0) + 1
    InventoryRun.objects.filter(pk=run.pk).update(page_state=state, counters=counters)
    return {"continue": True, "processed": processed}


def _classify_drive(run: InventoryRun, node, parent_id: str, object_id: int) -> None:
    fields = {
        "name": node.name,
        "mime_type": node.mime_type,
        "size_bytes": node.size,
        "checksum_md5": node.md5,
        "checksum_sha256": getattr(node, "sha256", None),
        "parent_external_id": parent_id,
        "object_id": object_id,
    }
    if node.is_shortcut:
        _item(
            run,
            system=SyncSystem.DRIVE,
            external_id=node.id,
            disposition=Disposition.OUT_OF_SCOPE,
            details={"reason": "Verknüpfung"},
            **fields,
        )
        return
    doc = Document.objects.filter(drive_file_id=node.id, deleted_at__isnull=True).first()
    if doc is not None:
        paperless = ExternalLink.objects.filter(document=doc, system=SyncSystem.PAPERLESS).exists()
        disp = Disposition.LINK_EXISTING if paperless else Disposition.IMPORT_NEW
        item = _item(
            run,
            system=SyncSystem.DRIVE,
            external_id=node.id,
            document=doc,
            disposition=disp,
            details={
                "reason": "registriert" + (", in Paperless" if paperless else ", noch nicht in Paperless")
            },
            **fields,
        )
        if not run.dry_run and not paperless and doc.sha256 and config.writes_allowed(doc.object):
            op, _ = enqueue(
                OperationKind.PAPERLESS_PUSH,
                system=SyncSystem.PAPERLESS,
                key=op_key(OperationKind.PAPERLESS_PUSH, doc.uuid, doc.sha256),
                document=doc,
                source_system=SyncSystem.APP,
                priority=120,
            )
            InventoryItem.objects.filter(pk=item.pk).update(operation=op, disposition=Disposition.IN_PROGRESS)
        return
    if node.mime_type == FOLDER_MIME:
        return
    if node.is_google_doc:
        _item(
            run,
            system=SyncSystem.DRIVE,
            external_id=node.id,
            disposition=Disposition.EXPORT_SNAPSHOT,
            details={"reason": "Google-Dokument, Exportfassung nach Registrierung"},
            **fields,
        )
    else:
        _item(
            run,
            system=SyncSystem.DRIVE,
            external_id=node.id,
            disposition=Disposition.IMPORT_NEW,
            details={"reason": "nicht registriert"},
            **fields,
        )
    if not run.dry_run:
        from apps.drive.models import DriveNode as DriveNodeRow
        from apps.sync.drive_changes import _register

        parent_row = DriveNodeRow.objects.filter(drive_file_id=parent_id, status="active").first()
        try:
            created = _register(node, object_id, parent_row)
        except Exception as exc:
            InventoryItem.objects.filter(run=run, system=SyncSystem.DRIVE, external_id=node.id).update(
                disposition=Disposition.ERROR, error_message=str(exc)[:500]
            )
            return
        if created is not None:
            InventoryItem.objects.filter(run=run, system=SyncSystem.DRIVE, external_id=node.id).update(
                document=created, disposition=Disposition.IN_PROGRESS
            )


def _dt(value):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def manifest_rows(run: InventoryRun, limit: int = 500):
    return (
        InventoryItem.objects.filter(run=run)
        .select_related("document", "object")
        .order_by("disposition", "id")[:limit]
    )


def summary(run: InventoryRun) -> dict:
    from django.db.models import Count

    rows = InventoryItem.objects.filter(run=run).values("disposition").annotate(n=Count("id"))
    out = {row["disposition"]: row["n"] for row in rows}
    out["total"] = sum(out.values())
    out["open"] = (
        out.get(Disposition.PENDING, 0) + out.get(Disposition.IN_PROGRESS, 0) + out.get(Disposition.ERROR, 0)
    )
    return out


def refresh_dispositions(run: InventoryRun) -> int:
    """Uebertraegt den Ausgang der Operationen in das Manifest (in_progress -> link_existing/import_new/error)."""
    updated = 0
    for item in InventoryItem.objects.filter(run=run, disposition=Disposition.IN_PROGRESS).select_related(
        "operation", "document"
    ):
        op = item.operation
        if op is None:
            # Drive-Zeile ohne Operation: die Datei wurde registriert und laeuft ueber die Pipeline; sobald sie den
            # Eingangszustand verlassen hat, gilt die Uebernahme als erfolgt
            doc = item.document
            if doc is not None and doc.status not in ("registered", "hashed"):
                InventoryItem.objects.filter(pk=item.pk).update(
                    disposition=Disposition.ERROR if doc.status == "error" else Disposition.IMPORT_NEW,
                    error_message="Verarbeitung fehlgeschlagen" if doc.status == "error" else None,
                )
                updated += 1
            continue
        if op.status == "done":
            result = op.result or {}
            if result.get("duplicate_remote"):
                disp = Disposition.DUPLICATE
            elif result.get("linked") or result.get("linked_existing"):
                disp = Disposition.LINK_EXISTING
            else:
                disp = Disposition.IMPORT_NEW
            InventoryItem.objects.filter(pk=item.pk).update(
                disposition=disp,
                target_external_id=str(result.get("paperless_id") or result.get("document_id") or ""),
            )
            updated += 1
        elif op.status in ("failed", "blocked", "cancelled"):
            InventoryItem.objects.filter(pk=item.pk).update(
                disposition=Disposition.ERROR,
                error_message=(op.last_error or op.blocked_reason or op.status)[:500],
            )
            updated += 1
        elif op.status == "skipped":
            InventoryItem.objects.filter(pk=item.pk).update(
                disposition=Disposition.LINK_EXISTING,
                details={**(item.details or {}), "skipped": (op.result or {}).get("skipped")},
            )
            updated += 1
    return updated
