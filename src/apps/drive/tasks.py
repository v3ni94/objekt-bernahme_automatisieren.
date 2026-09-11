"""Drive-Jobs in der Queue io (F 4.1 Punkt 6, 2.2): Ordnerabgleich je Objekt und Sammellauf, stuendlicher Lesetest,
taeglicher erzwungener Refresh (Nachweis T9), Aktenordner anlegen. Ohne Verbindung wird nichts geschrieben."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.audit.services import record
from apps.drive import oauth
from apps.drive.models import DriveSyncRun
from apps.objects.models import ManagedObject

logger = logging.getLogger(__name__)


def _adapter_or_fail(obj: ManagedObject | None, dry_run: bool, user=None, trigger: str = "task") -> tuple:
    adapter = oauth.get_adapter()
    if adapter is None and obj is not None:
        from django.utils import timezone

        run = DriveSyncRun.objects.create(
            object=obj,
            dry_run=dry_run,
            status="failed",
            triggered_by=user,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            error_message="Keine Google-Verbindung (Token fehlt oder widerrufen)",
            summary={"trigger": trigger},
        )
        return None, run
    return adapter, None


@shared_task(name="drive.reconcile_object", queue="io")
def reconcile_object_task(
    object_id: int, dry_run: bool = True, user_id: int | None = None, trigger: str = "task"
) -> dict:
    from apps.accounts.models import User
    from apps.drive.reconcile import reconcile_object

    obj = ManagedObject.objects.get(pk=object_id)
    user = User.objects.filter(pk=user_id).first() if user_id else None
    adapter, failed = _adapter_or_fail(obj, dry_run, user, trigger)
    if adapter is None:
        return {"run_id": failed.pk, "status": failed.status}
    run = reconcile_object(obj, drive=adapter, dry_run=dry_run, user=user, trigger=trigger)
    result = {
        "run_id": run.pk,
        "status": run.status,
        "actions_executed": run.actions_executed,
        "no_changes": run.no_changes,
    }
    if not dry_run and run.status == "done" and trigger != "bulk":
        # Akten-Vorlage: nach dem Objektordner folgen die Akten je Einheit (Eigentuemer unter 05, Mieter unter 04).
        # Der Sammellauf bleibt davon frei; dort zaehlt die Ordnerstruktur, nicht die Akten.
        result["unit_folders"] = _unit_folders_after_reconcile(obj, adapter, user)
    return result


WRITE_LOCK_SECONDS = 600


def _write_lock_key(object_id: int) -> str:
    """Dieselbe Sperre wie die Ablage (file_to_drive): kein zweiter Schreiber legt gleichzeitig Ordner im Objekt an."""
    return f"drive:write:{object_id}"


def _unit_folders_after_reconcile(obj: ManagedObject, adapter, user) -> dict | None:
    from django.core.cache import cache

    from apps.config import store

    if not store.get("owner_file.create_folders_eagerly", False):
        return None
    from apps.drive.adapter import DriveError
    from apps.drive.folders import FolderError, ensure_unit_folders
    from apps.parties.unit_files import ensure_unit_files

    lock = _write_lock_key(obj.pk)
    if not cache.add(lock, "unit-folders", timeout=WRITE_LOCK_SECONDS):
        # Ablage schreibt gerade im Objekt: Aktenordner als eigenen Auftrag nachziehen statt doppelt anzulegen
        ensure_unit_folders_task.apply_async(args=[obj.pk, getattr(user, "pk", None)], countdown=30)
        return {"deferred": True}
    try:
        files = ensure_unit_files(obj)
        return {**files, **ensure_unit_folders(obj, drive=adapter, user=user)}
    except (FolderError, DriveError) as exc:
        logger.warning("Aktenordner für Objekt %s nicht vollständig angelegt: %s", obj.pk, exc)
        return {"error": str(exc)[:300]}
    finally:
        cache.delete(lock)


@shared_task(name="drive.reconcile_all", queue="io")
def reconcile_all_task(dry_run: bool = True, user_id: int | None = None) -> dict:
    """Sammellauf fuer die Definition of Done (F 10.3): alle aktiven Objekte nacheinander, danach Sammelfassung."""
    results = {"runs": 0, "failed": 0, "with_changes": 0}
    for obj in ManagedObject.active.filter(status__in=["new", "takeover", "active"]).order_by(
        "object_number_numeric"
    ):
        result = reconcile_object_task(obj.pk, dry_run, user_id, trigger="bulk")
        results["runs"] += 1
        if result.get("status") == "failed":
            results["failed"] += 1
        if result.get("no_changes") is False:
            results["with_changes"] += 1
    from apps.drive.protocol import write_summary_xlsx

    results["summary_file"] = str(write_summary_xlsx())
    return results


@shared_task(name="drive.hourly_read_test", queue="io")
def hourly_read_test() -> dict:
    """Stuendlicher Lesetest auf den Wurzelordner (G 10, B-16): Metadaten lesen, Ergebnis protokollieren."""
    from apps.config import store
    from apps.drive.adapter import DriveError

    adapter = oauth.get_adapter()
    root_id = store.get("drive.root_folder_id")
    if adapter is None or not root_id:
        return {"ok": False, "reason": "not_connected" if adapter is None else "no_root"}
    try:
        node = adapter.get(root_id)
    except DriveError as exc:
        record(
            "drive.token_refresh",
            entity_type="oauth_token",
            actor_type="system",
            after={"result": "read_test_failed", "error_class": type(exc).__name__},
        )
        return {"ok": False, "reason": type(exc).__name__}
    token = oauth.current_token()
    if token is not None:
        oauth.persist_credentials(
            token,
            adapter.service._http.credentials
            if hasattr(adapter.service, "_http") and hasattr(adapter.service._http, "credentials")
            else _NoCreds(token),
        )
    record(
        "drive.token_refresh",
        entity_type="oauth_token",
        actor_type="system",
        after={"result": "read_test_ok", "root_name": node.name if node else None},
    )
    return {"ok": node is not None, "root_name": node.name if node else None}


class _NoCreds:
    """Platzhalter, wenn der Client keine aktualisierten Anmeldedaten exponiert: bestehender Token bleibt."""

    def __init__(self, token):
        self.token = None
        self.expiry = None


@shared_task(name="drive.daily_forced_refresh", queue="io")
def daily_forced_refresh() -> dict:
    return oauth.refresh_access_token(force=True, reason="daily_proof")


@shared_task(name="drive.ensure_owner_folder", queue="io")
def ensure_owner_folder_task(owner_file_id: int) -> dict:
    from apps.drive.folders import ensure_owner_folder
    from apps.parties.models import OwnerFile

    adapter = oauth.get_adapter()
    if adapter is None:
        return {"ok": False, "reason": "not_connected"}
    rows = ensure_owner_folder(OwnerFile.objects.get(pk=owner_file_id), drive=adapter)
    return {"ok": True, "nodes": len(rows)}


def trigger_object_folders(
    object_id: int, *, user_id: int | None = None, trigger: str = "object_create", force: bool = False
) -> str:
    """Objektordner samt Unterstruktur direkt nach der Objektanlage erzeugen (Ausfuehrungslauf, kein Probelauf).

    Der Abgleich erkennt einen bereits vorhandenen Ordner mit derselben Objektnummer und uebernimmt ihn, statt
    einen zweiten anzulegen; erst ohne Treffer entsteht ein neuer Ordner nach drive.object_folder_name_pattern.
    Rueckgabe sagt dem Aufrufer, was der Anwender erfahren muss; die Objektanlage selbst scheitert nie daran.
    """
    from apps.config import store

    if not force and not store.get("drive.create_folders_on_object_create", True):
        return "disabled"
    if oauth.token_status().get("status") != "active":
        return "not_connected"
    if not store.get("drive.root_folder_id"):
        return "no_root"
    try:
        reconcile_object_task.delay(object_id, False, user_id, trigger)
    except Exception:  # Broker nicht erreichbar: Anlage bleibt gueltig, Ordner spaeter ueber den Abgleich
        logger.exception("Ordneranlage für Objekt %s konnte nicht angestoßen werden", object_id)
        return "error"
    return "queued"


@shared_task(name="drive.ensure_unit_folders", queue="io", bind=True, max_retries=30)
def ensure_unit_folders_task(self, object_id: int, user_id: int | None = None) -> dict:
    """Akten-Vorlage in Drive fuer ein Objekt: Akten je Einheit als Datensaetze sicherstellen, Ordner unter 05 und
    04 anlegen oder bestaetigen, Namen abgleichen (Platzhalter WE01 wird nach Zuordnung zu WE01_Mustermann).
    Laeuft unter der Objekt-Schreibsperre; ist sie belegt oder antwortet Drive mit einem Fehler, wird der Auftrag
    spaeter wiederholt (kein Ordner doppelt, keine stille Luecke)."""
    from django.core.cache import cache

    from apps.accounts.models import User
    from apps.drive.adapter import DriveError
    from apps.drive.folders import FolderError, ensure_unit_folders
    from apps.parties.unit_files import ensure_unit_files

    obj = ManagedObject.active.filter(pk=object_id).first()
    if obj is None:
        return {"ok": False, "reason": "object_missing"}
    adapter = oauth.get_adapter()
    if adapter is None:
        return {"ok": False, "reason": "not_connected"}
    user = User.objects.filter(pk=user_id).first() if user_id else None
    lock = _write_lock_key(object_id)
    if not cache.add(lock, "unit-folders", timeout=WRITE_LOCK_SECONDS):
        raise self.retry(countdown=20)
    try:
        files = ensure_unit_files(obj)
        stats = ensure_unit_folders(obj, drive=adapter, user=user)
    except FolderError as exc:
        # Struktur fehlt (kein Ordnerabgleich): Hinweis im Log, der Abgleich zieht die Akten nach
        logger.warning("Aktenordner für Objekt %s: %s", object_id, exc)
        return {"ok": False, "reason": str(exc)[:300]}
    except DriveError as exc:
        logger.warning(
            "Aktenordner für Objekt %s: Drive-Fehler %s (Versuch %s)", object_id, exc, self.request.retries
        )
        if self.request.retries < 5:
            raise self.retry(countdown=60 * (self.request.retries + 1), exc=exc) from exc
        record(
            "drive.create_folder",
            entity_type="object",
            entity_id=object_id,
            object_id=object_id,
            actor_type="system",
            after={
                "result": "unit_folders_failed",
                "error": f"{exc.status or ''} {exc.reason or ''} {exc}"[:300],
            },
        )
        return {"ok": False, "reason": f"Drive-Fehler {exc.status or ''} {exc.reason or ''}: {exc}"[:300]}
    finally:
        cache.delete(lock)
    return {"ok": True, **files, **stats}


UNIT_FOLDER_DEBOUNCE_SECONDS = 15


def trigger_unit_folders(object_id: int, *, user_id: int | None = None, force: bool = False) -> str:
    """Aktenordner eines Objekts nachziehen (nach Zuordnung, Import oder Knopf „Akten anlegen").

    Entprellt je Objekt: innerhalb von UNIT_FOLDER_DEBOUNCE_SECONDS wird nur ein Auftrag eingereiht, der mit
    derselben Verzoegerung startet und dann alle bis dahin geaenderten Akten in einem Durchgang behandelt. Ohne
    force gilt der Schalter owner_file.create_folders_eagerly. Rueckgabe wie trigger_object_folders.
    """
    from django.core.cache import cache

    from apps.config import store

    if not force and not store.get("owner_file.create_folders_eagerly", False):
        return "disabled"
    if oauth.token_status().get("status") != "active":
        return "not_connected"
    if not store.get("drive.root_folder_id"):
        return "no_root"
    key = f"drive:unit-folders:{object_id}"
    if not cache.add(key, "1", timeout=UNIT_FOLDER_DEBOUNCE_SECONDS):
        return "debounced"
    try:
        ensure_unit_folders_task.apply_async(
            args=[object_id, user_id], countdown=UNIT_FOLDER_DEBOUNCE_SECONDS
        )
    except Exception:  # Broker nicht erreichbar: Akten sind gespeichert, Ordner folgen mit dem Abgleich
        logger.exception("Aktenordner für Objekt %s konnten nicht eingereiht werden", object_id)
        cache.delete(key)
        return "error"
    return "queued"
